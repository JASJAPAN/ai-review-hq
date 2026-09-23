"""口コミ管制室 Blueprint。既存Flaskアプリに: app.register_blueprint(reviews_bp)"""
import os, datetime
from flask import Blueprint, render_template_string, request, redirect, url_for, jsonify, session
from google_reviews import db, post_reply, mark_posted, upsert
from review_judge import judge

reviews_bp = Blueprint("reviews", __name__, url_prefix="/admin/replies")

def _auth():
    return request.headers.get("X-Run-Token") == os.environ.get("REPLY_RUN_TOKEN")

API_PATHS = ("/admin/replies/run", "/admin/replies/import", "/admin/replies/tasks", "/admin/replies/hp_config", "/admin/replies/recon", "/admin/replies/notify")

@reviews_bp.before_request
def _raise_limit():
    # 偵察結果（スクショ入り）だけアップロード上限を緩める（アプリ全体は64KBのまま）
    if request.path.startswith(("/admin/replies/recon", "/admin/replies/import")):
        try: request.max_content_length = 16 * 1024 * 1024
        except Exception: pass

@reviews_bp.before_request
def _guard():
    """管理画面は既存アプリのログインセッションを流用。API系はトークン認証"""
    if request.path.startswith(API_PATHS):
        # GET の設定画面/偵察表示と POST の設定保存は管理者セッション、それ以外はトークン
        if request.headers.get("X-Run-Token") or request.path.endswith(("/run", "/import", "/tasks", "/tasks/done", "/notify")):
            return None
        if not session.get("admin"): return redirect(url_for("login", next=request.path))
        return None
    if not session.get("admin"):
        return redirect(url_for("login", next=request.path))

TPL = """
<style>body{font-family:sans-serif;max-width:900px;margin:auto}.card{border:1px solid #ccc;padding:12px;margin:12px 0;border-radius:8px}
.hp{border-left:6px solid #e60012}.gg{border-left:6px solid #4285f4}.rep{background:#fff3f3}.tag{font-size:12px;padding:2px 6px;border-radius:4px;background:#eee}</style>
<h2>口コミ管制室</h2>
<p><a href="{{url_for('google.index')}}">Google連携設定</a> ／ <a href="{{url_for('reviews.hp_settings')}}">ホットペッパー設定</a> ／ <a href="{{url_for('instagram.index')}}">SNS管制室</a></p>
<p>判定待ち {{new_count}}件 ／ 承認待ち {{pending|length}}件 ／ 違反報告候補 {{reports|length}}件 ／ ホットペッパー返信待ち {{hp_todo|length}}件</p>

<h3>承認待ち（返信内容の確認）</h3>
{% for r in pending %}
<div class="card {{'hp' if r.platform=='hotpepper' else 'gg'}}">
  <span class="tag">{{r.platform}}</span> <b>{{r.store}}</b> ★{{r.rating}} {{r.reviewer}} <small>{{r.created_at[:16]}}</small>
  {% if r.review_url %}<a href="{{r.review_url}}" target="_blank">口コミを開く</a>{% endif %}
  <p style="background:#f5f5f5;padding:8px">{{r.comment or "（本文なし）"}}</p>
  <form method="post" action="{{url_for('reviews.approve', review_id=r.review_id)}}">
    <textarea name="reply" rows="5" style="width:100%">{{r.reply}}</textarea><br>
    <button type="submit">{{'この内容でGoogleに投稿' if r.platform=='google' else 'この内容で確定（拡張機能が投稿）'}}</button>
    <button type="submit" formaction="{{url_for('reviews.skip', review_id=r.review_id)}}">返信しない</button>
  </form>
</div>
{% endfor %}

<h3>違反報告候補（規約該当と判定されたもの）</h3>
{% for r in reports %}
<div class="card rep">
  <span class="tag">{{r.platform}}</span> <b>{{r.store}}</b> ★{{r.rating}} {{r.reviewer}}
  {% if r.review_url %}<a href="{{r.review_url}}" target="_blank">口コミを開く</a>{% endif %}
  <p style="background:#fff;padding:8px">{{r.comment}}</p>
  <b>該当条項:</b> {{r.policy_clause}}<br>
  <b>報告理由（コピーして管理画面の報告フォームへ）:</b>
  <textarea rows="4" style="width:100%">{{r.report_reason}}</textarea>
  <form method="post" action="{{url_for('reviews.report_done', review_id=r.review_id)}}" style="display:inline">
    <button>報告済みにする</button></form>
  <form method="post" action="{{url_for('reviews.report_reject', review_id=r.review_id)}}" style="display:inline">
    <button>違反ではない</button></form>
</div>
{% endfor %}

<h3>投稿済み（直近20件）</h3>
{% for r in posted %}<div><span class="tag">{{r.platform}}</span> {{r.posted_at[:16]}} {{r.store}} ★{{r.rating}} {{r.reviewer}}</div>{% endfor %}
"""

@reviews_bp.route("/")
def index():
    con = db()
    q = lambda s, *a: con.execute(s, a).fetchall()
    return render_template_string(TPL,
        pending=q("SELECT * FROM reviews WHERE reply_status='pending' ORDER BY created_at DESC"),
        reports=q("SELECT * FROM reviews WHERE report=1 AND report_status='todo' ORDER BY created_at DESC"),
        hp_todo=q("SELECT * FROM reviews WHERE platform='hotpepper' AND reply_status='todo'"),
        posted=q("SELECT * FROM reviews WHERE reply_status='posted' ORDER BY posted_at DESC LIMIT 20"),
        new_count=con.execute("SELECT COUNT(*) FROM reviews WHERE reply_status='new'").fetchone()[0])

@reviews_bp.route("/<path:review_id>/approve", methods=["POST"])
def approve(review_id):
    con = db()
    r = con.execute("SELECT * FROM reviews WHERE review_id=?", (review_id,)).fetchone()
    text = request.form["reply"].strip()
    if r["platform"] == "google":
        post_reply(r["location"], review_id, text)
        mark_posted(con, review_id, text)
    else:  # hotpepper: 拡張機能が /tasks から拾って投稿する
        con.execute("UPDATE reviews SET reply=?, reply_status='todo' WHERE review_id=?", (text, review_id)); con.commit()
    return redirect(url_for("reviews.index"))

@reviews_bp.route("/<path:review_id>/skip", methods=["POST"])
def skip(review_id):
    con = db(); con.execute("UPDATE reviews SET reply_status='skipped' WHERE review_id=?", (review_id,)); con.commit()
    return redirect(url_for("reviews.index"))

@reviews_bp.route("/<path:review_id>/report_done", methods=["POST"])
def report_done(review_id):
    con = db(); con.execute("UPDATE reviews SET report_status='reported' WHERE review_id=?", (review_id,)); con.commit()
    return redirect(url_for("reviews.index"))

@reviews_bp.route("/<path:review_id>/report_reject", methods=["POST"])
def report_reject(review_id):
    con = db(); con.execute("UPDATE reviews SET report_status='rejected', report=0 WHERE review_id=?", (review_id,)); con.commit()
    return redirect(url_for("reviews.index"))


# ---------- Cron / 拡張機能用 API ----------
@reviews_bp.route("/run", methods=["POST"])
def run():
    """Render Cron Job から15分おき。Google口コミの取得〜自動返信"""
    if not _auth(): return "forbidden", 403
    judged = judge_pending(budget_sec=15)
    from google_reviews import _state
    if not (_state().get("stores") or os.environ.get("REVIEW_STORES")):
        return f"google not configured; judged {judged}", 200
    import reply_worker; reply_worker.main()
    return f"ok; judged {judged}", 200

# ---------- 取り込み（即時保存）と判定（バックグラウンド）を分離 ----------
import threading, time
_judge_lock = threading.Lock()

def judge_pending(budget_sec=20, limit=50):
    """reply_status='new' の行を1件ずつ判定して pending/todo に進める。時間予算内で打ち切り。"""
    if not _judge_lock.acquire(blocking=False): return 0
    try:
        con = db(); t0 = time.time(); n = 0
        rows = con.execute("SELECT * FROM reviews WHERE reply_status='new' ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        for r in rows:
            if time.time() - t0 > budget_sec: break
            try:
                j = judge(r["platform"], r["store"], r["rating"], r["comment"] or "", r["reviewer"] or "",
                          external_id=r["review_id"].replace("hp:", "", 1), posted_at="")
                reason = j.get("report_reason", "")
                if j.get("evidence_needed"): reason += "\n\n【店側で用意する証憑】" + "／".join(j["evidence_needed"])
                auto = r["rating"] >= int(os.environ.get("AUTO_POST_MIN_RATING", "6")) and not j["report"]
                status = ("todo" if r["platform"] == "hotpepper" else "auto") if auto else "pending"
                con.execute("""UPDATE reviews SET reply=?, reply_status=?, report=?, policy_clause=?, report_reason=?, report_status=?
                               WHERE review_id=?""", (j["reply"], status, int(j["report"]), j.get("policy_clause", ""), reason,
                                                       "todo" if j["report"] else "", r["review_id"]))
                con.commit(); n += 1
            except Exception as e:
                # 1回目は 'new' のまま次回リトライ、2回目も失敗したら pending に落として人が見る
                again = (r["reply"] or "").startswith("(判定失敗")
                con.execute("UPDATE reviews SET reply=?, reply_status=? WHERE review_id=?",
                            (f"(判定失敗: {str(e)[:100]})", "pending" if again else "new", r["review_id"])); con.commit()
                if not again: time.sleep(2)
        return n
    finally:
        _judge_lock.release()

def _judge_bg():
    # 残りがある限り回し続ける（1回20秒×繰り返し、最大10分）
    for _ in range(30):
        if judge_pending(budget_sec=20) == 0: break

@reviews_bp.route("/import", methods=["POST"])
def import_reviews():
    """拡張機能/ワーカーが取り込んだ口コミを保存だけして即返す。判定はバックグラウンド。
    body: [{"store":"南薩農場","reviewer":"...","rating":2,"comment":"...","review_url":"...","external_id":"...","posted_at":"..."}]"""
    if not _auth(): return "forbidden", 403
    con = db(); n = 0
    for item in request.get_json(force=True):
        rid = "hp:" + (item.get("external_id") or item.get("review_url") or "")
        if rid == "hp:" or con.execute("SELECT 1 FROM reviews WHERE review_id=?", (rid,)).fetchone():
            continue
        upsert(con, review_id=rid, platform="hotpepper", store=item["store"], location="",
               reviewer=item.get("reviewer", ""), rating=int(item["rating"]), comment=item.get("comment", ""),
               review_url=item.get("review_url", ""), reply="", reply_status="new",
               created_at=item.get("posted_at") or datetime.datetime.now().isoformat())
        n += 1
    if n: threading.Thread(target=_judge_bg, daemon=True).start()
    return jsonify({"imported": n, "judging": "background"})

@reviews_bp.route("/tasks", methods=["GET"])
def tasks():
    """拡張機能が実行すべきタスク一覧（ホットペッパー返信・違反報告）"""
    if not _auth(): return "forbidden", 403
    con = db()
    rows = lambda s: [dict(r) for r in con.execute(s).fetchall()]
    return jsonify({
        "hotpepper_replies": rows("SELECT review_id,store,reviewer,rating,comment,review_url,reply FROM reviews WHERE platform='hotpepper' AND reply_status='todo'"),
        "reports": rows("SELECT review_id,platform,store,reviewer,rating,comment,review_url,policy_clause,report_reason FROM reviews WHERE report=1 AND report_status='todo'"),
    })

@reviews_bp.route("/tasks/done", methods=["POST"])
def tasks_done():
    """拡張機能が完了報告。body: {"review_id":"...","kind":"reply"|"report"}"""
    if not _auth(): return "forbidden", 403
    d = request.get_json(force=True); con = db()
    if d["kind"] == "reply":
        con.execute("UPDATE reviews SET reply_status='posted', posted_at=? WHERE review_id=?",
                    (datetime.datetime.now().isoformat(), d["review_id"]))
    else:
        con.execute("UPDATE reviews SET report_status='reported' WHERE review_id=?", (d["review_id"],))
    con.commit()
    return "ok"


# ---------- ホットペッパー無人ワーカー用（設定・偵察結果・通知） ----------
import json as _json
from pathlib import Path as _P
_HP_CFG = _P(os.environ.get("DATA_DIR", ".")) / "hp_config.json"
_HP_RECON = _P(os.environ.get("DATA_DIR", ".")) / "hp_recon.json"
_HP_DEFAULT = {
    "login_url": "", "sel_login_id": "input[type=text], input[type=email]", "sel_login_pw": "input[type=password]",
    "sel_login_submit": "button[type=submit], input[type=submit]",
    "reviews_url": "", "sel_item": "", "sel_reviewer": "", "sel_rating": "", "rating_regex": "", "sel_comment": "", "sel_link": "a",
    "sel_reply_textarea": "", "sel_reply_submit": "", "sel_reply_confirm": "",
    "stores": [],
}
def _hp_load():
    try: return {**_HP_DEFAULT, **_json.loads(_HP_CFG.read_text())}
    except Exception: return dict(_HP_DEFAULT)

@reviews_bp.route("/hp_config", methods=["GET", "POST"])
def hp_config():
    if request.method == "GET":
        if not _auth(): return "forbidden", 403
        return jsonify(_hp_load())
    cfg = _hp_load()
    for k in _HP_DEFAULT:
        if k == "stores":
            cfg["stores"] = [{"name": n.strip(), "reviews_url": u.strip()} for n, u in
                             zip(request.form.getlist("store_name"), request.form.getlist("store_url")) if n.strip() and u.strip()]
        elif k in request.form: cfg[k] = request.form[k].strip()
    _HP_CFG.parent.mkdir(parents=True, exist_ok=True); _HP_CFG.write_text(_json.dumps(cfg, ensure_ascii=False, indent=1))
    return redirect(url_for("reviews.hp_settings"))

@reviews_bp.route("/recon", methods=["GET", "POST"])
def recon():
    if request.method == "POST":
        if not _auth(): return "forbidden", 403
        _HP_RECON.parent.mkdir(parents=True, exist_ok=True); _HP_RECON.write_text(_json.dumps(request.get_json(force=True), ensure_ascii=False))
        return "ok"
    try: d = _json.loads(_HP_RECON.read_text())
    except Exception: return "まだ偵察結果がありません。Cron「cron-hotpepper-recon」を手動実行してください。"
    html = f"<style>body{{font-family:sans-serif;max-width:1100px;margin:auto}}pre{{background:#f5f5f5;padding:8px;font-size:11px;max-height:400px;overflow:auto}}img{{max-width:100%;border:1px solid #ccc}}</style><h2>ホットペッパー偵察結果 {d.get('at','')[:16]}</h2>"
    if d.get("error"): html += f"<p style='color:red'>エラー: {d['error']}</p>"
    for pg in d.get("pages", []):
        html += f"<h3>{pg['name']} — {pg['url']}</h3><img src='data:image/jpeg;base64,{pg['png']}'><pre>{pg['outline'].replace('<','&lt;')}</pre>"
    return html + f"<p><a href='{url_for('reviews.hp_settings')}'>設定へ</a></p>"

@reviews_bp.route("/notify", methods=["POST"])
def notify_ep():
    if not _auth(): return "forbidden", 403
    from ig_instagram import notify as _n; _n(request.get_json(force=True).get("text", "")); return "ok"

_HP_TPL = """
<style>body{font-family:sans-serif;max-width:900px;margin:auto}label{display:block;margin-top:10px;font-size:13px;color:#444}input{width:100%;box-sizing:border-box;padding:6px}
fieldset{margin-top:16px}</style>
<h2>ホットペッパー無人ワーカー 設定</h2>
<p><a href="{{url_for('reviews.recon')}}">偵察結果を見る</a> ／ <a href="{{url_for('reviews.index')}}">口コミ管制室へ</a></p>
<form method="post" action="{{url_for('reviews.hp_config')}}">
<fieldset><legend>ログイン</legend>
<label>ログインページURL<input name="login_url" value="{{c.login_url}}"></label>
<label>IDの入力欄セレクタ<input name="sel_login_id" value="{{c.sel_login_id}}"></label>
<label>パスワード入力欄セレクタ<input name="sel_login_pw" value="{{c.sel_login_pw}}"></label>
<label>ログインボタンセレクタ<input name="sel_login_submit" value="{{c.sel_login_submit}}"></label></fieldset>
<fieldset><legend>口コミ一覧（店舗ごと）</legend>
{% for s in (c.stores + [{'name':'','reviews_url':''}]) %}<label>店舗名 / 口コミ管理ページURL
<input name="store_name" value="{{s.name}}" placeholder="南薩農場" style="width:30%"> <input name="store_url" value="{{s.reviews_url}}" placeholder="https://..." style="width:68%"></label>{% endfor %}
<label>口コミ1件の要素<input name="sel_item" value="{{c.sel_item}}" placeholder="例: li.review-item"></label>
<label>投稿者名<input name="sel_reviewer" value="{{c.sel_reviewer}}"></label>
<label>星評価の要素<input name="sel_rating" value="{{c.sel_rating}}"></label>
<label>星評価の抽出正規表現（1〜5を1グループ目に）<input name="rating_regex" value="{{c.rating_regex}}" placeholder="例: 総合\\s*([1-5])"></label>
<label>本文<input name="sel_comment" value="{{c.sel_comment}}"></label>
<label>詳細/返信ページへのリンク<input name="sel_link" value="{{c.sel_link}}"></label></fieldset>
<fieldset><legend>返信</legend>
<label>返信テキストエリア<input name="sel_reply_textarea" value="{{c.sel_reply_textarea}}"></label>
<label>送信ボタン<input name="sel_reply_submit" value="{{c.sel_reply_submit}}"></label>
<label>確認画面の確定ボタン（あれば）<input name="sel_reply_confirm" value="{{c.sel_reply_confirm}}"></label></fieldset>
<button style="margin-top:16px;font-size:16px">保存</button></form>
"""
@reviews_bp.route("/hp_settings")
def hp_settings():
    return render_template_string(_HP_TPL, c=_hp_load())
