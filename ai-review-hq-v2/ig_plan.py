"""カレンダー型の自動投稿（店舗ごとに ig_plans/<account>.json の投稿を1日1本、順番に出す）

- POST /admin/instagram/plan/<account>/run      その日の1本を投稿（Cron。X-Run-Token必須）
- GET  /admin/instagram/plan/<account>/preview  次に出る投稿と画像を確認（管理画面ログイン）
- GET  /admin/instagram/plan/<account>/         30日カレンダーと投稿状況（管理画面ログイン）

アカウントごとの環境変数（ACCOUNT は英大文字。例 HIGONOUGUISU）
  IG_USER_ID__ACCOUNT / IG_ACCESS_TOKEN__ACCOUNT   必須
  IG_DRY_RUN__ACCOUNT                              任意。未設定なら IG_DRY_RUN と同じ
"""
import io, os, json, datetime
from pathlib import Path
import requests
from flask import jsonify, abort, request, render_template_string, Response
import ig_config as C, ig_instagram, ig_onedrive
from instagram_admin import instagram_bp, db, prepare, stage, JST

PLAN_DIR = Path(__file__).parent / "ig_plans"


def _run_ok():
    t = request.headers.get("X-Run-Token")
    return bool(t) and t in (os.environ.get("REPLY_RUN_TOKEN"), os.environ.get("PLAN_RUN_TOKEN"))


def load_plan(account):
    p = PLAN_DIR / f"{account}.json"
    if "/" in account or not p.exists(): abort(404)
    return json.loads(p.read_text(encoding="utf-8"))


def creds(account):
    k = account.upper()
    dry = os.environ.get(f"IG_DRY_RUN__{k}")
    return {"user_id": os.environ.get(f"IG_USER_ID__{k}", ""), "token": os.environ.get(f"IG_ACCESS_TOKEN__{k}", ""),
            "dry_run": C.DRY_RUN if dry is None else dry.lower() == "true"}


def _ensure_cols(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(ig_posts)")]
    if "account" not in cols:
        con.execute("ALTER TABLE ig_posts ADD COLUMN account TEXT DEFAULT ''"); con.commit()


def posted_days(con, account):
    _ensure_cols(con)
    rows = con.execute("SELECT photo FROM ig_posts WHERE account=? AND kind='feed' AND dry_run=0", (account,)).fetchall()
    return {int(r["photo"].split(":")[0]) for r in rows if r["photo"] and r["photo"].split(":")[0].isdigit()}


def next_post(plan, con):
    done = posted_days(con, plan["account"])
    for p in plan["posts"]:
        if p["day"] not in done: return p
    return None


def caption_of(plan, p):
    return f"{p['caption']}\n\n{plan['footer']}\n\n{' '.join(plan['hashtags'])}"


def fetch_image(url):
    if url.startswith("onedrive:"):   # onedrive:<driveId>/<itemId>
        drive, item = url[len("onedrive:"):].rsplit("/", 1)
        return ig_onedrive.download(item, drive)
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status(); return r.content


@instagram_bp.route("/plan/<account>/run", methods=["POST"])
def plan_run(account):
    if not _run_ok(): return "forbidden", 403
    plan, cr = load_plan(account), creds(account)
    if not (cr["user_id"] and cr["token"]): return f"{account}: IG_USER_ID / IG_ACCESS_TOKEN 未設定", 200
    con = db(); _ensure_cols(con)
    today_s = datetime.datetime.now(JST).date().isoformat()
    if not cr["dry_run"] and con.execute("SELECT 1 FROM ig_posts WHERE account=? AND kind='feed' AND dry_run=0 AND created_at LIKE ?",
                                         (account, today_s + "%")).fetchone():
        return jsonify({"account": account, "skipped": "本日分は投稿済み"})
    p = next_post(plan, con)
    if not p:
        ig_instagram.notify(f"[Instagram {account}] {len(plan['posts'])}日分の投稿がすべて完了しました。次の投稿計画を追加してください。")
        return jsonify({"account": account, "skipped": "計画分はすべて投稿済み"})
    text = caption_of(plan, p)
    try:
        token = ig_instagram.refresh_token(con, account, cr["token"])
        url = stage(prepare(fetch_image(p["image"]), tuple(plan.get("image_size", (1080, 1080)))))
        media_id = "" if cr["dry_run"] else ig_instagram.publish_image(url, text, cr["user_id"], token)
    except Exception as e:
        ig_instagram.notify(f"[Instagram自動投稿 失敗] {account} {p['day']}日目「{p['title']}」\n{type(e).__name__}: {e}")
        raise
    con.execute("INSERT INTO ig_posts(kind,photo,caption,media_id,dry_run,created_at,account) VALUES(?,?,?,?,?,?,?)",
                ("feed", f"{p['day']}:{p['title']}", text, media_id, int(cr["dry_run"]), datetime.datetime.now(JST).isoformat(), account))
    con.commit()
    return jsonify({"account": account, "day": p["day"], "title": p["title"], "dry_run": cr["dry_run"], "media_id": media_id})


@instagram_bp.route("/plan/<account>/preview")
def plan_preview(account):
    plan = load_plan(account); p = next_post(plan, db())
    if not p: return "計画分はすべて投稿済みです"
    if request.args.get("img"):   # 実際に投稿される加工後の画像
        return Response(prepare(fetch_image(p["image"]), tuple(plan.get("image_size", (1080, 1080)))), mimetype="image/jpeg")
    return jsonify({"day": p["day"], "title": p["title"], "caption": caption_of(plan, p), "dry_run": creds(account)["dry_run"]})


UPLOAD_DIR = Path(os.environ.get("DATA_DIR") or ("/var/data" if Path("/var/data").is_dir() else ".")) / "ig_plan_imgs"


def _cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return r


@instagram_bp.route("/plan/<account>/upload/<int:day>.jpg", methods=["POST", "OPTIONS"])
def plan_upload(account, day):
    """ブラウザで加工した投稿画像を受け取る（?t=PLAN_RUN_TOKEN）。OneDriveの写真をサーバーが直接読めないため"""
    if request.method == "OPTIONS": return _cors(Response(""))
    if not _tok_q(): return _cors(Response("forbidden", 403))
    load_plan(account)
    d = UPLOAD_DIR / account; d.mkdir(parents=True, exist_ok=True)
    n = request.content_length or 0
    if n > 8 * 1024 * 1024: return _cors(Response("too large", 413))
    data = request.environ["wsgi.input"].read(n)   # アプリ全体の64KB上限を避けて、この受け口だけ8MBまで受ける
    if len(data) < 1000 or data[:2] != b"\xff\xd8": return _cors(Response("not a jpeg", 400))
    (d / f"{day}.jpg").write_bytes(data)
    return _cors(jsonify({"ok": True, "day": day, "bytes": len(data)}))


@instagram_bp.route("/plan/<account>/image/<int:day>.jpg", methods=["GET", "OPTIONS"])
def plan_image(account, day):
    """予約投稿ツールに渡すための加工済み画像。アップロード済みならそれを、なければ計画の画像URLから作る。CORS許可"""
    if request.method == "OPTIONS": return _cors(Response(""))
    plan = load_plan(account)
    up = UPLOAD_DIR / account / f"{day}.jpg"
    if up.exists():
        r = Response(up.read_bytes(), mimetype="image/jpeg")
        r.headers["Cache-Control"] = "no-store"; return _cors(r)
    p = next((x for x in plan["posts"] if x["day"] == day), None)
    if not p or not p.get("image", "").startswith("http"): abort(404)
    r = Response(prepare(fetch_image(p["image"]), tuple(plan.get("image_size", (1080, 1080)))), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "no-store"
    return _cors(r)


@instagram_bp.route("/plan/<account>/posts.json")
def plan_json(account):
    plan = load_plan(account)
    r = jsonify([{"day": p["day"], "title": p["title"], "caption": caption_of(plan, p)} for p in plan["posts"]])
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


def _tok_q():
    t = request.args.get("t")
    return bool(t) and t == os.environ.get("PLAN_RUN_TOKEN")


THUMB_DIR = Path(os.environ.get("DATA_DIR") or ("/var/data" if Path("/var/data").is_dir() else ".")) / "ig_thumbs"


@instagram_bp.route("/plan/_thumb")
def plan_thumb():
    if not _tok_q(): abort(403)
    drive, item = request.args["drive"], request.args["item"]
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    p = THUMB_DIR / f"{item}.jpg"
    if not p.exists():
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(ig_onedrive.download(item, drive)))).convert("RGB")
        im.thumbnail((420, 420)); im.save(p, "JPEG", quality=80)
    return Response(p.read_bytes(), mimetype="image/jpeg")


@instagram_bp.route("/plan/_sheet")
def plan_sheet():
    """OneDriveフォルダの写真を一覧表示（素材選び用。?t=PLAN_RUN_TOKEN&drive=&folder=）"""
    if not _tok_q(): abort(403)
    drive, folder, t = request.args["drive"], request.args["folder"], request.args["t"]
    try: items = ig_onedrive.list_folder(drive, folder)
    except Exception as e: return f"<pre>{e}</pre>", 500
    return render_template_string("""<meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font-family:sans-serif;margin:8px}.g{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}.g div{font-size:11px;word-break:break-all}.g img{width:100%;aspect-ratio:1;object-fit:cover}</style>
{% for i in items if i.folder %}<p><a href="?t={{t}}&drive={{drive}}&folder={{i.id}}">📁 {{i.name}}</a></p>{% endfor %}
<div class=g>{% for i in items if not i.folder %}<div><img loading=lazy src="_thumb?t={{t}}&drive={{drive}}&item={{i.id}}">{{loop.index}}. {{i.name}}<br><small>{{i.id}}</small></div>{% endfor %}</div>""",
        items=items, drive=drive, t=t)


TPL = """<meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font-family:sans-serif;max-width:960px;margin:auto;padding:12px}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.c{border:1px solid #ddd;border-radius:8px;overflow:hidden}.c img{width:100%;aspect-ratio:1;object-fit:cover}.c div{padding:8px;font-size:13px;white-space:pre-wrap}
.done{opacity:.45}.tag{font-size:11px;background:#eee;padding:1px 6px;border-radius:4px}</style>
<h2>@{{plan.account}} 投稿カレンダー（{{plan.posts|length}}日）</h2>
<p>コンセプト：{{plan.concept}} ／ 投稿済み {{done|length}} 本 ／ 本番投稿: {{'OFF（試運転）' if dry else 'ON'}}</p>
<div class=g>{% for p in plan.posts %}<div class="c {{'done' if p.day in done}}"><img src="image/{{p.day}}.jpg" loading=lazy>
<div><b>{{p.day}}日目</b> <span class=tag>{{p.theme}}</span> {{p.title}}{{' ✓投稿済み' if p.day in done}}

{{p.caption}}</div></div>{% endfor %}</div>"""


@instagram_bp.route("/plan/<account>/")
def plan_index(account):
    plan = load_plan(account)
    return render_template_string(TPL, plan=plan, done=posted_days(db(), account), dry=creds(account)["dry_run"])
