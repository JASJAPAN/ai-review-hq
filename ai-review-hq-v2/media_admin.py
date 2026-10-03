"""媒体管制室 Blueprint（ヒトサラ ⇄ ホットペッパーのズレ検出と貼り付け用原稿）
既存Flaskアプリに: app.register_blueprint(media_bp)

- POST /admin/media/run            全店チェックを開始（Cron 月1回。X-Run-Token または管理画面ログイン）
- GET  /admin/media/               ダッシュボード（店舗一覧・ズレ件数・実行状況）
- GET  /admin/media/store/<key>    店舗のズレ一覧と、ヒトサラ入力欄ごとの貼り付け用原稿
- GET  /admin/media/store/<key>/snapshot  読み取った内容（確認用JSON）
- POST /admin/media/stores/save    店舗の追加・編集
- POST /admin/media/item/<id>/<done|ignore|reopen>
- POST /admin/media/item/<id>/approve     自動反映を承認（ヒトサラ無人ワーカーが次回実行時に書き換える）
- GET  /admin/media/tasks  ／ POST /admin/media/tasks/done   ワーカー用（X-Run-Token）
- GET/POST /admin/media/recon              ワーカーが送る画面の記録（スクリーンショット）

流れ: 公開ページを取得（media_fetch）→ AIが項目を抜き出す（media_ai）→ 機械的に比較（media_diff）
      → 制限を超える文言だけAIが縮める → 管理画面に「どこに・何を貼るか」を出す
このファイル（Webアプリ側）はヒトサラ・ホットペッパーの管理画面にログインしない（公開ページを読むだけ）。
管理画面への書き込みは、承認されたものだけを hitosara-worker/hs_worker.py（別のCron）が行う。
"""
import hashlib, json, math, os, re, sqlite3, threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import (Blueprint, abort, flash, jsonify, redirect, render_template_string,
                   request, session, url_for)
import media_ai as AI, media_diff as D, media_fetch as F

media_bp = Blueprint("media", __name__, url_prefix="/admin/media")
JST = timezone(timedelta(hours=9))
DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
DB_PATH = DATA_DIR / "media.db"
SEED = Path(__file__).parent / "media_stores.json"
STALE_MIN = 60   # これ以上「実行中」のままなら落ちたとみなす


def _now(): return datetime.now(JST).isoformat(timespec="seconds")
def _tok():
    want = os.environ.get("REPLY_RUN_TOKEN", "").strip()   # 前後の空白・改行の入り込みは無視する
    return bool(want) and request.headers.get("X-Run-Token", "").strip() == want


TOKEN_PATHS = ("/admin/media/run", "/admin/media/tasks", "/admin/media/tasks/done", "/admin/media/recon", "/admin/media/notify")
RECON_PATH = DATA_DIR / "hs_recon.json"
# 自動反映できるズレの種類 → ワーカーの作業の種類
WRITABLE = {"catch": "intro_catch", "intro": "intro_text", "course_name": "plan_name",
            "price": "plan_price", "course_extra": "plan_off"}


@media_bp.before_request
def _raise_limit():
    if request.path == "/admin/media/recon":   # スクリーンショット入りの記録だけ上限を緩める（全体は64KBのまま）
        try: request.max_content_length = 16 * 1024 * 1024
        except Exception: pass


@media_bp.before_request
def _guard():
    if request.path in TOKEN_PATHS and request.headers.get("X-Run-Token"):
        return None   # トークンの正否は各ルート側で判定
    if not session.get("admin"):
        return redirect(url_for("login", next=request.path))


def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30); con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS media_stores(key TEXT PRIMARY KEY, name TEXT NOT NULL, hp_url TEXT DEFAULT '',
        hs_id TEXT DEFAULT '', use_reserve INTEGER DEFAULT 1, enabled INTEGER DEFAULT 0, note TEXT DEFAULT '',
        sort INTEGER DEFAULT 0, checked_at TEXT DEFAULT '', last_error TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS media_runs(id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT DEFAULT '',
        status TEXT, log TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS media_items(id INTEGER PRIMARY KEY, run_id INTEGER, store_key TEXT, sig TEXT,
        level TEXT, kind TEXT, title TEXT, hp TEXT, hs TEXT, field TEXT, note TEXT, drafts TEXT DEFAULT '[]',
        status TEXT DEFAULT 'open', created_at TEXT, closed_at TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS media_snaps(store_key TEXT, side TEXT, data TEXT, fetched_at TEXT,
        PRIMARY KEY(store_key, side));
    """)
    have = {r["name"] for r in con.execute("PRAGMA table_info(media_items)")}
    for col in ("ref", "write_text", "write_result"):   # 自動反映用の列（先に入れた環境にも後から足す）
        if col not in have:
            con.execute(f"ALTER TABLE media_items ADD COLUMN {col} TEXT DEFAULT ''")
    if con.execute("SELECT COUNT(*) FROM media_stores").fetchone()[0] == 0 and SEED.exists():
        for n, s in enumerate(json.loads(SEED.read_text(encoding="utf-8"))):
            con.execute("INSERT INTO media_stores(key,name,hp_url,hs_id,use_reserve,enabled,note,sort) VALUES(?,?,?,?,?,?,?,?)",
                        (s["key"], s["name"], s.get("hp_url", ""), s.get("hs_id", ""), int(s.get("use_reserve", 1)),
                         int(s.get("enabled", 0)), s.get("note", ""), n))
        con.commit()
    return con


# ---------------------------------------------------------------- 読み取り

def _clean_courses(rows):
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or not str(r.get("name") or "").strip():
            continue
        def num(v):
            if v in (None, ""): return None
            m = re.sub(r"[^\d]", "", str(v))
            return int(m) if m else None
        out.append({"name": str(r["name"]).strip(), "price": num(r.get("price")),
                    "items_count": num(r.get("items_count")), "link": str(r.get("link") or "").strip("⟦⟧ ")})
    return out


def snapshot(side, store):
    """1店舗・片側の公開ページを読んで、比較できる形（基本情報・コース・クーポン）にする"""
    urls = F.hp_urls(store["hp_url"]) if side == "hp" else F.hs_urls(store["hs_id"])
    snap, site = {"urls": urls, "warnings": []}, AI.SIDE[side]

    def step(label, fn, url):   # どの段階で止まったかを、エラー文に残す
        try:
            text = F.page_text(url)
        except Exception as e:
            raise RuntimeError(f"{site}の{label}ページを取得できませんでした（{type(e).__name__}: {str(e)[:120]}）")
        try:
            return fn(side, text)
        except Exception as e:
            raise RuntimeError(f"{site}の{label}の読み取りに失敗しました（{type(e).__name__}: {str(e)[:160]}）")

    basics = step("店舗トップ", AI.extract_basics, urls["top"])
    snap["basics"] = {k: ("" if v is None else str(v)) for k, v in (basics or {}).items()}

    course_text = {}
    def courses(side_, text):
        course_text["t"] = text
        return AI.extract_courses(side_, text)
    snap["courses"] = _clean_courses(step("コース一覧", courses, urls["course"]))
    links = F.course_links(course_text.get("t", ""))
    snap["course_links"] = len(links)
    snap["course_incomplete"] = len(snap["courses"]) < len(links)
    if snap["course_incomplete"]:
        snap["warnings"].append(f"コース一覧：ページのリンクは{len(links)}件ですが、読み取れたのは{len(snap['courses'])}件です（読み落としの可能性）")
    snap["course_text"] = D.norm(course_text.get("t", ""))   # 「ヒトサラにだけある」の裏取り用（保存前に取り除く）

    def coupons(text):
        return [c for c in AI.extract_coupons(side, text) if isinstance(c, dict) and c.get("title")]
    try:
        snap["coupons"] = coupons(F.page_text(urls["coupon"]))
    except Exception as e:   # クーポンページが無い店もある。全体は止めない
        snap["coupons"] = []
        snap["warnings"].append(f"クーポンページを読めませんでした（{type(e).__name__}）")
    if side == "hp" and not snap["coupons"] and "クーポン" in course_text.get("t", ""):
        try:   # クーポンページから取れないときは、コース一覧ページに載っているクーポンで補う
            snap["coupons"] = coupons(course_text["t"])
        except Exception:
            pass
        snap["warnings"].append("クーポン：専用ページから読み取れなかったため、コース一覧ページの記載で補いました"
                                if snap["coupons"] else "クーポン：ページに記載がありそうですが0件でした（読み落としの可能性）")
    return snap


def verify_extras(items, hp):
    """「ヒトサラにだけあるコース」の裏取り。プラン名の主な語句がホットペッパーのコースページに載っていれば、
    読み落としの可能性が高いので「要確認」に下げ、自動で非掲載にできないようにする"""
    page = hp.get("course_text", "")
    for it in items:
        if it["kind"] != "course_extra":
            continue
        words = [w for w in re.split(r"[【】・/／、,!！?？◆◎♪()（）]|など|が", D.norm(it.get("ref") or "")) if len(w) >= 5]
        found = [w for w in words if w in page]
        if hp.get("course_incomplete") or (words and len(found) * 2 >= len(words)):
            it.update(kind="course_extra_unsure", level="info",
                      title="要確認（読み落としの可能性）：" + it["title"],
                      note="ホットペッパーのページに同じ語句が載っている、または読み取りに注意が出ています。"
                           "ホットペッパーに本当に無いかを確認してください。自動で非掲載にはできません。")
    return items


# ---------------------------------------------------------------- 原稿づくり

def fit(text, limit, label, side="owner"):
    """ヒトサラの入力欄に入る長さにする。収まっていればホットペッパーの文言をそのまま使う。
    長さはヒトサラの画面と同じ数え方（全角1・半角0.5）で測る"""
    text = (text or "").strip()
    if not limit or D.hs_len(text, side) <= limit:
        return text, "そのまま"
    if AI.available():
        for _ in range(2):
            try:
                short = AI.shorten(text, limit, label).strip()
            except Exception:
                break
            if short and D.hs_len(short, side) <= limit:
                return short, "AIで短縮"
    cut = text
    while cut and D.hs_len(cut, side) > limit - 1:
        cut = cut[:-1]
    return cut + "…", "末尾を切り詰め（要手直し）"


def _draft(field, text, use_reserve, label=None):
    where, limit = D.target(field, use_reserve)
    side = D.side_of(field, use_reserve)
    body, how = fit(text, limit, label or where.split("＞")[-1].strip(), side)
    return {"where": where, "limit": limit, "text": body, "how": how, "side": side,
            "count": math.ceil(D.hs_len(body, side))}


def make_drafts(item, store):
    """ズレ1件について「どの画面のどの欄に何を入れるか」を作る"""
    ur, k = bool(store["use_reserve"]), item["kind"]
    if k == "course_missing":
        name, _, price = item["hp"].rpartition("／")
        drafts = [_draft("plan_name", name, ur), _draft("plan_price", price, ur)]
        if item.get("note"):
            try:
                d = AI.extract_course_detail(F.page_text(F.absolute("hp", item["note"])))
                for field, key in (("plan_desc", "description"), ("course_menu", "menu"), ("drink_menu", "drink")):
                    if d.get(key):
                        drafts.append(_draft(field, d[key], ur))
                extra = "／".join(f"{lab}：{d[key]}" for lab, key in
                                 (("利用人数", "people"), ("滞在時間", "stay"), ("曜日・時間", "days"), ("予約締切", "deadline"))
                                 if d.get(key))
                if extra:
                    drafts.append({"where": "プランの条件（利用人数・滞在可能時間・提供曜日・予約受付締切日に設定）",
                                   "limit": None, "text": extra, "how": "そのまま", "count": len(extra)})
            except Exception as e:
                drafts.append({"where": "コース詳細", "limit": None, "how": "取得失敗", "count": 0,
                               "text": f"コース詳細ページを読めませんでした（{type(e).__name__}）。ホットペッパーの該当コースを直接確認してください。"})
        return drafts
    if k in ("course_name", "catch", "intro", "coupon_text", "coupon_missing", "price", "budget", "hours"):
        return [_draft(item["field"], item["hp"], ur)]
    return []


def _sig(store_key, item):
    raw = "|".join([store_key, item["kind"], D.norm(item["title"]), D.norm(item["hp"]), D.norm(item["hs"])])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 実行

def run_store(con, run_id, store):
    # 時間のかかる取得とAI処理を先に全部済ませ、DBへの書き込みは最後に一度で行う（画面操作を待たせない）
    hp, hs = snapshot("hp", store), snapshot("hs", store)
    if not hp["courses"] and not hp["basics"].get("name"):
        raise RuntimeError("ホットペッパーのページから何も読み取れませんでした")
    skip = {r["sig"] for r in con.execute(   # 「対象外」と「承認済み・反映待ち」は作り直さない
        "SELECT sig FROM media_items WHERE store_key=? AND status IN ('ignored','approved')", (store["key"],))}
    rows = []
    items = verify_extras(D.diff_all(hp, hs), hp)
    hp.pop("course_text", None); hs.pop("course_text", None)
    for item in sorted(items, key=lambda x: D.LEVEL_ORDER[x["level"]]):
        sig = _sig(store["key"], item)
        if sig in skip:
            continue
        rows.append((run_id, store["key"], sig, item["level"], item["kind"], item["title"], item["hp"], item["hs"],
                     item["field"], item["note"], item.get("ref", ""),
                     json.dumps(make_drafts(item, store), ensure_ascii=False), "open", _now()))
    for side, snap in (("hp", hp), ("hs", hs)):
        con.execute("INSERT OR REPLACE INTO media_snaps(store_key,side,data,fetched_at) VALUES(?,?,?,?)",
                    (store["key"], side, json.dumps(snap, ensure_ascii=False), _now()))
    con.execute("DELETE FROM media_items WHERE store_key=? AND status IN ('open','failed')", (store["key"],))
    con.executemany("""INSERT INTO media_items(run_id,store_key,sig,level,kind,title,hp,hs,field,note,ref,drafts,status,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    con.execute("UPDATE media_stores SET checked_at=?, last_error='' WHERE key=?", (_now(), store["key"]))
    con.commit()
    return len(rows)


def run_all(run_id, keys=None):
    con = db(); log = []
    try:
        if not AI.available():
            raise RuntimeError("ANTHROPIC_API_KEY が未設定のため読み取りができません")
        stores = con.execute("SELECT * FROM media_stores WHERE enabled=1 ORDER BY sort, name").fetchall()
        if keys:
            stores = con.execute(f"SELECT * FROM media_stores WHERE key IN ({','.join('?' * len(keys))})", list(keys)).fetchall()
        for s in stores:
            try:
                if not (F.HP_URL_RE.match(s["hp_url"] or "") and F.HS_ID_RE.match(s["hs_id"] or "")):
                    raise RuntimeError("ホットペッパーURLかヒトサラIDが未登録です")
                log.append(f"{s['name']}：ズレ {run_store(con, run_id, s)}件")
            except Exception as e:
                con.rollback()
                msg = f"{type(e).__name__}: {e}"[:300]
                con.execute("UPDATE media_stores SET last_error=? WHERE key=?", (msg, s["key"]))
                log.append(f"{s['name']}：失敗（{msg}）")
            con.execute("UPDATE media_runs SET log=? WHERE id=?", ("\n".join(log), run_id)); con.commit()
        status = "done"
    except Exception as e:
        log.append(f"中断：{type(e).__name__}: {e}"); status = "error"
    con.execute("UPDATE media_runs SET status=?, finished_at=?, log=? WHERE id=?", (status, _now(), "\n".join(log), run_id))
    con.commit()
    try:   # 通知先（Teams等）が設定されていれば結果を送る
        import ig_instagram
        ig_instagram.notify("[媒体管制室] ヒトサラ チェック結果\n" + "\n".join(log))
    except Exception:
        pass
    return log


def start_run(keys=None):
    """実行中でなければ開始。返り値は (run_id, 開始できたか)"""
    con = db()
    r = con.execute("SELECT * FROM media_runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
    if r:
        age = datetime.now(JST) - datetime.fromisoformat(r["started_at"])
        if age < timedelta(minutes=STALE_MIN):
            return r["id"], False
        con.execute("UPDATE media_runs SET status='error', finished_at=?, log=log||? WHERE id=?",
                    (_now(), "\n中断：時間切れ", r["id"]))
    run_id = con.execute("INSERT INTO media_runs(started_at,status) VALUES(?, 'running')", (_now(),)).lastrowid
    con.commit()
    if os.environ.get("MEDIA_SYNC_INLINE") == "1":   # テスト用：その場で実行
        run_all(run_id, keys)
    else:   # 数分かかるので裏で実行し、リクエストはすぐ返す
        threading.Thread(target=run_all, args=(run_id, keys), daemon=True).start()
    return run_id, True


@media_bp.route("/run", methods=["POST"])
def run():
    if not (_tok() or session.get("admin")):
        return "forbidden", 403
    key = request.values.get("store")
    run_id, started = start_run([key] if key else None)
    if request.headers.get("X-Run-Token"):
        return jsonify(run_id=run_id, started=started), (202 if started else 409)
    flash("チェックを開始しました。数分後にこの画面を再読み込みしてください。" if started else "すでに実行中です。")
    return redirect(url_for("media.store", key=key) if key else url_for("media.index"))


# ---------------------------------------------------------------- 画面

STYLE = """<style>
.lv{display:inline-block;font-size:.75rem;font-weight:700;border-radius:6px;padding:1px 8px;margin-right:6px}
.lv.high{background:#FBE3DE;color:#B3402F}.lv.mid{background:#FCEFD6;color:#8A5A0B}
.lv.low{background:#E4EEE9;color:#173F35}.lv.info{background:#ECEFF1;color:#5C6B66}
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:8px 0}
@media(max-width:700px){.cmp{grid-template-columns:1fr}}
.cmp div{background:var(--paper);border-radius:8px;padding:8px 10px;font-size:.88rem;white-space:pre-wrap;word-break:break-word}
.cmp b{display:block;font-size:.75rem;color:var(--sub);font-weight:500}
.draft{border:1.5px solid var(--line);border-radius:10px;padding:10px 12px;margin-top:8px}
.draft .where{font-size:.82rem;color:var(--sub)}
.draft textarea{width:100%;border:0;background:transparent;font:inherit;font-size:.92rem;resize:vertical;margin-top:4px}
.draft .meta{display:flex;align-items:center;gap:10px;font-size:.8rem;color:var(--sub)}
.over{color:var(--warn);font-weight:700}.inline{display:inline}
.panel-head>div{white-space:nowrap;flex-shrink:0;margin-left:12px}
.pre{white-space:pre-wrap;font-size:.85rem;color:var(--sub)}
</style>"""

NAV = """<p class="crumb"><a href="{{url_for('dashboard')}}">本部管理</a> ／ <a href="{{url_for('reviews.index')}}">口コミ管制室</a>
 ／ <a href="{{url_for('instagram.index')}}">SNS管制室</a> ／ <a href="{{url_for('media.index')}}">媒体管制室</a></p>"""

TPL_INDEX = """{% extends "admin/base.html" %}{% block title %}媒体管制室{% endblock %}{% block content %}""" + STYLE + NAV + """
<h1>媒体管制室（ヒトサラ ⇄ ホットペッパー）</h1>
<div class="kpis">
  <div class="kpi"><div class="v">{{stores|selectattr('enabled')|list|length}}</div><div class="k">チェック対象の店舗</div></div>
  <div class="kpi"><div class="v {{'warn' if total.high}}">{{total.high}}</div><div class="k">要対応</div></div>
  <div class="kpi"><div class="v">{{total.mid}}</div><div class="k">確認</div></div>
  <div class="kpi"><div class="v">{{total.low}}</div><div class="k">文言の差</div></div>
</div>
<div class="panel">
  <div class="panel-head"><h2>チェックの実行</h2>
    <form method="post" action="{{url_for('media.run')}}" onsubmit="return confirm('対象店舗をすべてチェックします（数分かかります）。よろしいですか？')">
      <button class="btn primary small">今すぐ全店チェック</button></form></div>
  {% if not ai %}<p class="warn">ANTHROPIC_API_KEY が未設定のため実行できません。</p>{% endif %}
  {% if last %}<p class="note">直近の実行：{{last.started_at[:16].replace('T',' ')}} ／
    {{ {'running':'実行中…（再読み込みで更新）','done':'完了','error':'中断'}[last.status] }}</p>
    {% if last.log %}<p class="pre">{{last.log}}</p>{% endif %}
  {% else %}<p class="empty">まだ実行していません。</p>{% endif %}
  <p class="note">毎月1日 9:00 に自動実行（Render Cron）。チェックで読むのは公開ページだけです。</p>
  <p class="note">自動反映：承認済み（反映待ち）{{auto.approved}}件 ／ 失敗 {{auto.failed}}件 ／ 反映済み {{auto.written}}件
    ／ <a href="{{url_for('media.recon')}}">ワーカーの画面記録を見る</a></p>
</div>
<div class="panel">
  <h2>店舗</h2>
  <table><tr><th>店舗</th><th>対象</th><th>要対応</th><th>確認</th><th>文言</th><th>参考</th><th>最終チェック</th><th></th></tr>
  {% for s in stores %}{% set c = counts.get(s.key, {}) %}
  <tr><td><a href="{{url_for('media.store', key=s.key)}}">{{s.name}}</a>
        {% if s.note %}<span class="slug">{{s.note}}</span>{% endif %}
        {% if s.last_error %}<span class="improve">前回エラー：{{s.last_error}}</span>{% endif %}</td>
      <td>{{'○' if s.enabled else '—'}}</td>
      <td class="{{'warn' if c.get('high')}}">{{c.get('high', 0)}}</td><td>{{c.get('mid', 0)}}</td>
      <td>{{c.get('low', 0)}}</td><td>{{c.get('info', 0)}}</td>
      <td class="nowrap">{{s.checked_at[:10] or '未実施'}}</td>
      <td class="nowrap"><a class="btn small" href="{{url_for('media.index', edit=s.key)}}#form">編集</a></td></tr>
  {% endfor %}</table>
</div>
<div class="panel narrow settings" id="form">
  <h2>{{'店舗を編集：' + edit.name if edit else '店舗を追加'}}</h2>
  <form method="post" action="{{url_for('media.stores_save')}}">
    <input type="hidden" name="key" value="{{edit.key if edit else ''}}">
    <label>店舗名<input name="name" required value="{{edit.name if edit else ''}}"></label>
    <label>ホットペッパーの店舗ページURL<input name="hp_url" placeholder="https://www.hotpepper.jp/strJ000000000/" value="{{edit.hp_url if edit else ''}}"></label>
    <label>ヒトサラの店舗番号（URLの10桁）<input name="hs_id" placeholder="0020004633" value="{{edit.hs_id if edit else ''}}"></label>
    <label>コースの登録先<select name="use_reserve">
      <option value="1" {{'selected' if not edit or edit.use_reserve}}>即時予約管理（プラン管理・コース管理）</option>
      <option value="0" {{'selected' if edit and not edit.use_reserve}}>オーナー管理（コースメニュー）</option></select></label>
    <label>メモ<input name="note" value="{{edit.note if edit else ''}}"></label>
    <label><input type="checkbox" name="enabled" value="1" style="display:inline;width:auto" {{'checked' if edit and edit.enabled}}> 月1チェックの対象にする</label>
    <button class="btn primary">保存</button>
  </form>
</div>
{% endblock %}"""

TPL_STORE = """{% extends "admin/base.html" %}{% block title %}{{s.name}}｜媒体管制室{% endblock %}{% block content %}""" + STYLE + NAV + """
<h1>{{s.name}}</h1>
<div class="row-actions">
  {% if s.hp_url %}<a class="btn small" href="{{s.hp_url}}" target="_blank" rel="noopener">ホットペッパーを開く</a>{% endif %}
  {% if s.hs_id %}<a class="btn small" href="https://hitosara.com/{{s.hs_id}}/" target="_blank" rel="noopener">ヒトサラを開く</a>{% endif %}
  <a class="btn small" href="https://owner.hitosara.com/" target="_blank" rel="noopener">ヒトサラ管理画面</a>
  <a class="btn small" href="{{url_for('media.snapshot_view', key=s.key)}}">読み取った内容を見る</a>
  <form class="inline" method="post" action="{{url_for('media.run')}}"><input type="hidden" name="store" value="{{s.key}}">
    <button class="btn small primary">この店だけチェック</button></form>
</div>
<p class="note">最終チェック：{{s.checked_at[:16].replace('T',' ') or '未実施'}}
  {% if snap %}／ 読み取り：ホットペッパー コース{{snap.hp.courses|length}}件・クーポン{{snap.hp.coupons|length}}件、
  ヒトサラ コース{{snap.hs.courses|length}}件・クーポン{{snap.hs.coupons|length}}件{% endif %}</p>
{% if s.last_error %}<p class="flash">前回エラー：{{s.last_error}}</p>{% endif %}
{% if snap %}{% for side, label in (('hp', 'ホットペッパー'), ('hs', 'ヒトサラ')) %}{% for w in snap[side].get('warnings', []) %}
<p class="flash">読み取りの注意（{{label}}）：{{w}}</p>{% endfor %}{% endfor %}{% endif %}
{% if not items and s.checked_at %}<div class="panel"><p class="empty">ズレはありません。</p></div>{% endif %}
{% for it in items %}
<div class="panel">
  <div class="panel-head"><h2><span class="lv {{it.level}}">{{labels[it.level]}}</span>{{it.title}}</h2>
    <div><form class="inline" method="post" action="{{url_for('media.item_action', item_id=it.id, action='done')}}"><button class="btn small">対応済み</button></form>
      <form class="inline" method="post" action="{{url_for('media.item_action', item_id=it.id, action='ignore')}}"
            onsubmit="return confirm('このズレを今後のチェックで出さないようにします。よろしいですか？')"><button class="btn small">対象外にする</button></form></div></div>
  {% if it.hp or it.hs %}<div class="cmp"><div><b>ホットペッパー</b>{{it.hp or '（なし）'}}</div><div><b>ヒトサラ</b>{{it.hs or '（なし）'}}</div></div>{% endif %}
  {% if it.note and it.kind != 'course_missing' %}<p class="note">{{it.note}}</p>{% endif %}
  {% if not it.drafts and it.where %}<p class="note">対応先：{{it.where}}</p>{% endif %}
  {% if it.status == 'approved' %}<p class="flash">自動反映を承認済みです。次のワーカー実行でヒトサラに書き込みます。
    <form class="inline" method="post" action="{{url_for('media.item_action', item_id=it.id, action='reopen')}}"><button class="btn small">承認を取り消す</button></form></p>{% endif %}
  {% if it.status == 'failed' %}<p class="improve">自動反映に失敗しました：{{it.write_result}}</p>{% endif %}
  {% for d in it.drafts %}
  {% set editable = it.auto in ('intro_catch', 'intro_text', 'plan_name') and it.status != 'approved' and loop.first %}
  <div class="draft">
    <div class="where">入力先：{{d.where}}{% if d.limit %}（全角{{d.limit}}文字まで。半角は0.5文字）{% endif %}</div>
    {% if editable %}<form method="post" action="{{url_for('media.item_approve', item_id=it.id)}}"
          onsubmit="return confirm('この文言でヒトサラを書き換えます。よろしいですか？')">{% endif %}
    <textarea name="text" rows="{{[2, (d.text|length // 38) + d.text.count('\\n') + 1]|max}}" {{'' if editable else 'readonly'}}
              id="d{{it.id}}-{{loop.index}}" data-side="{{d.get('side', 'owner')}}" data-limit="{{d.limit or 0}}"
              {% if editable %}oninput="cnt(this)"{% endif %}>{{d.text if not (it.status == 'approved' and loop.first and it.write_text) else it.write_text}}</textarea>
    <div class="meta"><button type="button" class="btn small" onclick="cp('d{{it.id}}-{{loop.index}}', this)">コピー</button>
      <span id="d{{it.id}}-{{loop.index}}-n">{{d.get('count', d.text|length)}}文字{% if d.limit %} / {{d.limit}}{% endif %}</span>
      <span class="{{'over' if '要手直し' in d.how or d.how == '取得失敗'}}">{{d.how}}</span>
      {% if editable %}<button class="btn small primary">承認して自動反映</button>{% endif %}</div>
    {% if editable %}</form>{% endif %}
  </div>{% endfor %}
  {% if it.auto in ('plan_price', 'plan_off') and it.status != 'approved' %}
  <form method="post" action="{{url_for('media.item_approve', item_id=it.id)}}"
        onsubmit="return confirm('{{'ヒトサラのこのプランの販売価格を ' + it.hp + ' に書き換えます。' if it.auto == 'plan_price' else 'ヒトサラのこのプランを非掲載にします（ネット予約もできなくなります）。'}}よろしいですか？')">
    <button class="btn small primary" style="margin-top:8px">{{'承認して料金を自動反映' if it.auto == 'plan_price' else '承認して非掲載にする'}}</button></form>
  {% endif %}
</div>
{% endfor %}
{% if closed %}<div class="panel"><h2>対応済み・対象外（直近）</h2><table>
  {% for it in closed %}<tr><td class="nowrap">{{ {'done':'対応済み','ignored':'対象外','written':'自動反映済み'}.get(it.status, it.status) }}</td><td>{{it.title}}{% if it.write_result %}<span class="slug">{{it.write_result}}</span>{% endif %}</td>
    <td class="nowrap">{{it.closed_at[:10]}}</td>
    <td><form method="post" action="{{url_for('media.item_action', item_id=it.id, action='reopen')}}"><button class="btn small">戻す</button></form></td></tr>{% endfor %}
</table></div>{% endif %}
<script>function cp(id, b){var t=document.getElementById(id);t.select();
  (navigator.clipboard?navigator.clipboard.writeText(t.value):Promise.reject()).catch(function(){document.execCommand('copy')});
  var o=b.textContent;b.textContent='コピーしました';setTimeout(function(){b.textContent=o},1500)}
// ヒトサラの画面と同じ数え方：全角1・半角0.5（半角カナはオーナー管理だけ1文字）
function hsLen(t, side){var n=0;for(var i=0;i<t.length;i++){var c=t.charCodeAt(i);
  if(c===13)continue; if(c===10){n+=1;continue}
  if(c>=0xFF61&&c<=0xFF9F){n+=(side==='reserve'?0.5:1);continue}
  n+=(c<=0x7E||c===0xA5||c===0x203E)?0.5:1} return n}
function cnt(t){var n=hsLen(t.value,t.dataset.side), lim=+t.dataset.limit, el=document.getElementById(t.id+'-n');
  el.textContent=Math.ceil(n)+'文字'+(lim?' / '+lim:''); el.className=(lim&&n>lim)?'over':''}</script>
{% endblock %}"""


@media_bp.route("/")
def index():
    con = db()
    stores = con.execute("SELECT * FROM media_stores ORDER BY enabled DESC, sort, name").fetchall()
    counts, total = {}, {"high": 0, "mid": 0, "low": 0, "info": 0}
    auto = {"approved": 0, "failed": 0, "written": 0}
    for r in con.execute("SELECT status, COUNT(*) n FROM media_items WHERE status IN ('approved','failed','written') GROUP BY status"):
        auto[r["status"]] = r["n"]
    for r in con.execute("SELECT store_key, level, COUNT(*) n FROM media_items WHERE status IN ('open','failed') GROUP BY store_key, level"):
        counts.setdefault(r["store_key"], {})[r["level"]] = r["n"]; total[r["level"]] += r["n"]
    edit = con.execute("SELECT * FROM media_stores WHERE key=?", (request.args.get("edit", ""),)).fetchone()
    return render_template_string(TPL_INDEX, stores=stores, counts=counts, total=total, edit=edit, ai=AI.available(), auto=auto,
                                  last=con.execute("SELECT * FROM media_runs ORDER BY id DESC LIMIT 1").fetchone())


def _store(con, key):
    s = con.execute("SELECT * FROM media_stores WHERE key=?", (key,)).fetchone()
    if not s: abort(404)
    return s


@media_bp.route("/store/<key>")
def store(key):
    con = db(); s = _store(con, key)
    order = "CASE level WHEN 'high' THEN 0 WHEN 'mid' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, id"
    def auto(r):   # プランの自動反映は、即時予約管理でコースを登録している店だけ
        kind = WRITABLE.get(r["kind"], "")
        return "" if (kind.startswith("plan_") and not s["use_reserve"]) else kind
    items = [dict(r, drafts=json.loads(r["drafts"] or "[]"), auto=auto(r),
                  where=D.target(r["field"], bool(s["use_reserve"]))[0] if r["field"] in D.TARGETS else "")
             for r in con.execute(
        f"SELECT * FROM media_items WHERE store_key=? AND status IN ('open','approved','failed') ORDER BY {order}", (key,))]
    closed = con.execute("SELECT * FROM media_items WHERE store_key=? AND status NOT IN ('open','approved','failed') "
                         "ORDER BY closed_at DESC LIMIT 30", (key,)).fetchall()
    snaps = {r["side"]: json.loads(r["data"]) for r in con.execute("SELECT * FROM media_snaps WHERE store_key=?", (key,))}
    return render_template_string(TPL_STORE, s=s, items=items, closed=closed, labels=D.LEVEL_LABEL,
                                  snap=snaps if len(snaps) == 2 else None)


@media_bp.route("/store/<key>/snapshot")
def snapshot_view(key):
    con = db(); _store(con, key)
    return jsonify({r["side"]: {"fetched_at": r["fetched_at"], **json.loads(r["data"])}
                    for r in con.execute("SELECT * FROM media_snaps WHERE store_key=?", (key,))})


@media_bp.route("/store/<key>/debug")
def debug_view(key):
    """読み取りの不具合調査用。?side=hp|hs&page=top|course|coupon&q=探す語句"""
    con = db(); s = _store(con, key)
    side, page = request.args.get("side", "hp"), request.args.get("page", "course")
    urls = F.hp_urls(s["hp_url"]) if side == "hp" else F.hs_urls(s["hs_id"])
    if page not in urls: abort(404)
    try:
        return jsonify(F.debug_page(urls[page], request.args.get("q", "")[:60]))
    except Exception as e:
        return jsonify(error=f"{type(e).__name__}: {str(e)[:200]}", url=urls[page]), 502


@media_bp.route("/stores/save", methods=["POST"])
def stores_save():
    con = db(); f = request.form
    name = f.get("name", "").strip()[:60]
    hp_url = F.normalize_hp_url(f.get("hp_url", ""))
    hs_id = re.sub(r"\D", "", f.get("hs_id", ""))
    hs_id = hs_id if F.HS_ID_RE.match(hs_id) else ""
    enabled = 1 if f.get("enabled") else 0
    if not name:
        flash("店舗名を入れてください。"); return redirect(url_for("media.index"))
    if enabled and not (hp_url and hs_id):
        enabled = 0
        flash("ホットペッパーURLとヒトサラの店舗番号（10桁）の両方が必要です。対象にはせず保存しました。")
    key = f.get("key") or ("s" + hashlib.sha1((name + _now()).encode()).hexdigest()[:8])
    vals = (name, hp_url, hs_id, int(f.get("use_reserve", "1") == "1"), enabled, f.get("note", "").strip()[:200])
    if con.execute("SELECT 1 FROM media_stores WHERE key=?", (key,)).fetchone():
        con.execute("UPDATE media_stores SET name=?,hp_url=?,hs_id=?,use_reserve=?,enabled=?,note=? WHERE key=?", vals + (key,))
    else:
        con.execute("INSERT INTO media_stores(name,hp_url,hs_id,use_reserve,enabled,note,key,sort) VALUES(?,?,?,?,?,?,?,999)", vals + (key,))
    con.commit()
    flash(f"「{name}」を保存しました。")
    return redirect(url_for("media.index"))


@media_bp.route("/item/<int:item_id>/<action>", methods=["POST"])
def item_action(item_id, action):
    status = {"done": "done", "ignore": "ignored", "reopen": "open"}.get(action)
    if not status: abort(404)
    con = db(); r = con.execute("SELECT * FROM media_items WHERE id=?", (item_id,)).fetchone()
    if not r: abort(404)
    con.execute("UPDATE media_items SET status=?, closed_at=? WHERE id=?", (status, "" if status == "open" else _now(), item_id))
    con.commit()
    return redirect(url_for("media.store", key=r["store_key"]))


# ---------------------------------------------------------------- 自動反映（承認 → ヒトサラ無人ワーカー）

@media_bp.route("/item/<int:item_id>/approve", methods=["POST"])
def item_approve(item_id):
    """人が確認して承認したものだけが、ワーカーの作業対象になる"""
    con = db(); r = con.execute("SELECT * FROM media_items WHERE id=?", (item_id,)).fetchone()
    if not r or r["kind"] not in WRITABLE: abort(404)
    s = _store(con, r["store_key"]); back = redirect(url_for("media.store", key=r["store_key"]))
    kind, ur, text = WRITABLE[r["kind"]], bool(s["use_reserve"]), ""
    if not F.HS_ID_RE.match(s["hs_id"] or ""):
        flash("ヒトサラの店舗番号が未登録のため、自動反映できません。"); return back
    if kind.startswith("plan_"):
        if not ur:
            flash("この店はコースをオーナー管理で登録しているため、プランの自動反映は未対応です。"); return back
        if not r["ref"]:
            flash("書き換え先のプラン名が記録されていません。「この店だけチェック」をやり直してください。"); return back
    if kind in ("intro_catch", "intro_text", "plan_name"):
        text = request.form.get("text", "").replace("\r\n", "\n").strip()
        limit, side = D.target(r["field"], ur)[1], D.side_of(r["field"], ur)
        if not text:
            flash("文言が空です。"); return back
        if kind != "intro_text" and "\n" in text:
            flash("この欄は改行を入れられません。"); return back
        if limit and D.hs_len(text, side) > limit:
            flash(f"全角{limit}文字を超えています（現在{math.ceil(D.hs_len(text, side))}文字）。縮めてから承認してください。"); return back
    elif kind == "plan_price":
        text = re.sub(r"[^\d]", "", r["hp"])
        if not text or int(text) < 1:
            flash("ホットペッパー側の価格を読み取れませんでした。"); return back
    con.execute("UPDATE media_items SET status='approved', write_text=?, write_result='', closed_at='' WHERE id=?", (text, item_id))
    con.commit()
    flash("承認しました。次のワーカー実行でヒトサラに反映します。")
    return back


@media_bp.route("/tasks")
def tasks():
    """ワーカーが実行する作業（承認済みのものだけ）"""
    if not _tok(): return "forbidden", 403
    con = db()
    rows = con.execute("""SELECT i.id, i.kind, i.ref, i.hs, i.write_text, s.key store_key, s.name store, s.hs_id
                          FROM media_items i JOIN media_stores s ON s.key=i.store_key
                          WHERE i.status='approved' ORDER BY s.sort, i.id""").fetchall()
    return jsonify(tasks=[{"id": r["id"], "type": WRITABLE[r["kind"]], "store_key": r["store_key"], "store": r["store"],
                           "hs_id": r["hs_id"], "ref": r["ref"], "current": r["hs"], "text": r["write_text"]} for r in rows])


@media_bp.route("/tasks/done", methods=["POST"])
def tasks_done():
    """ワーカーの結果報告。body: {"id": 1, "ok": true, "message": "..."}"""
    if not _tok(): return "forbidden", 403
    d = request.get_json(force=True); con = db()
    con.execute("UPDATE media_items SET status=?, write_result=?, closed_at=? WHERE id=? AND status='approved'",
                ("written" if d.get("ok") else "failed", str(d.get("message", ""))[:500], _now(), int(d["id"])))
    con.commit()
    return "ok"


@media_bp.route("/notify", methods=["POST"])
def notify_ep():
    if not _tok(): return "forbidden", 403
    try:
        import ig_instagram
        ig_instagram.notify(request.get_json(force=True).get("text", ""))
    except Exception:
        pass
    return "ok"


@media_bp.route("/recon", methods=["GET", "POST"])
def recon():
    """ワーカーが送る画面の記録（スクリーンショットと画面の骨組み）。試運転と失敗時の確認用"""
    if request.method == "POST":
        if not _tok(): return "forbidden", 403
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        RECON_PATH.write_text(json.dumps(request.get_json(force=True), ensure_ascii=False), encoding="utf-8")
        return "ok"
    try:
        d = json.loads(RECON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return "まだ記録がありません。Cron「cron-hitosara-worker」を実行すると、ここに画面の記録が出ます。"
    return render_template_string("""<style>body{font-family:sans-serif;max-width:1100px;margin:auto;padding:12px}
pre{background:#f5f5f5;padding:8px;font-size:11px;max-height:400px;overflow:auto;white-space:pre-wrap}img{max-width:100%;border:1px solid #ccc}</style>
<h2>ヒトサラ ワーカーの画面記録 {{d.get('at','')[:16]}}（モード: {{d.get('mode','')}}）</h2>
{% if d.get('error') %}<p style="color:#B3402F">エラー: {{d.error}}</p>{% endif %}
{% for line in d.get('log', []) %}<div>{{line}}</div>{% endfor %}
{% for pg in d.get('pages', []) %}<h3>{{pg.name}} — {{pg.url}}</h3>
{% if pg.png %}<img src="data:image/jpeg;base64,{{pg.png}}">{% endif %}<pre>{{pg.outline}}</pre>{% endfor %}
<p><a href="{{url_for('media.index')}}">媒体管制室へ</a></p>""", d=d)
