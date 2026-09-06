"""SNS管制室（Instagram）Blueprint: app.register_blueprint(instagram_bp)
- POST /admin/instagram/run      フィード投稿（Cron 月水金18:00 JST）
- POST /admin/instagram/story    ストーリーズ投稿（Cron 毎日17:00 JST）
- GET  /admin/instagram/preview  次の投稿を確認（管理画面ログイン）
- GET/POST /admin/instagram/webhook  MetaからのDM・コメント受信 → 返信案生成 → 通知
- GET  /admin/instagram/media/<token>.jpg  Instagramが画像を取りに来る一時URL
- GET  /admin/instagram/         ダッシュボード（投稿ログ・受信箱）
"""
import io, os, json, secrets, sqlite3, datetime
from datetime import date, timedelta, timezone
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, abort, render_template_string, redirect, url_for, session
from PIL import Image, ImageOps
import ig_config as C, ig_onedrive, ig_caption, ig_instagram

instagram_bp = Blueprint("instagram", __name__, url_prefix="/admin/instagram")
JST = timezone(timedelta(hours=9))
DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
MEDIA_DIR = DATA_DIR / "ig_media"
DB_PATH = DATA_DIR / "instagram.db"
MENU = json.loads((Path(__file__).parent / "ig_menu.json").read_text(encoding="utf-8"))
OPEN = ("/admin/instagram/run", "/admin/instagram/story", "/admin/instagram/webhook", "/admin/instagram/media/")

def _tok(): return request.headers.get("X-Run-Token") == os.environ.get("REPLY_RUN_TOKEN")

@instagram_bp.before_request
def _guard():
    if request.path.startswith(OPEN): return None
    if not session.get("admin"): return redirect(url_for("login", next=request.path))

def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS ig_posts(id INTEGER PRIMARY KEY, kind TEXT, photo TEXT, caption TEXT,
                   media_id TEXT, dry_run INTEGER, created_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS ig_inbox(id TEXT PRIMARY KEY, kind TEXT, sender_id TEXT, sender_name TEXT,
                   text TEXT, reply TEXT, status TEXT, created_at TEXT, replied_at TEXT)""")
    return con

# ---------- 写真ローテーション・画像準備 ----------
def pick_photo(photos, today: date, kind):
    start = date(2026, 9, 7)
    if kind == "feed":
        n = sum(1 for i in range((today - start).days + 1) if (start + timedelta(days=i)).weekday() in (0, 2, 4))
    else:
        n = (today - start).days + 1
        photos = photos[::-1]   # ストーリーズはフィードと逆順にして同日重複を避ける
    return photos[max(n - 1, 0) % len(photos)]

def prepare(raw, size):
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    im = ImageOps.fit(im, size, method=Image.LANCZOS, centering=(0.5, 0.5))
    out = io.BytesIO(); im.save(out, "JPEG", quality=88, optimize=True); return out.getvalue()

def stage(jpeg):
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    for f in MEDIA_DIR.glob("*.jpg"):   # 1時間より古い一時画像を掃除
        if f.stat().st_mtime < datetime.datetime.now().timestamp() - 3600: f.unlink(missing_ok=True)
    token = secrets.token_urlsafe(24); (MEDIA_DIR / f"{token}.jpg").write_bytes(jpeg)
    return f"{C.PUBLIC_BASE_URL}/admin/instagram/media/{token}.jpg"

@instagram_bp.route("/media/<token>.jpg")
def media(token):
    if "/" in token or ".." in token: abort(404)
    p = MEDIA_DIR / f"{token}.jpg"
    if not p.exists(): abort(404)
    return send_file(p, mimetype="image/jpeg")

def _next(kind):
    today = datetime.datetime.now(JST).date()
    photos = ig_onedrive.list_photos()
    if not photos: raise RuntimeError("HP掲載中フォルダに画像がありません")
    photo = pick_photo(photos, today, kind)
    meta = MENU.get(photo["name"], {"name": Path(photo["name"]).stem, "category": "料理"})
    return today, photo, meta

def _post(kind):
    if not _tok(): return "forbidden", 403
    if not C.configured(): return "not configured yet", 200
    today, photo, meta = _next(kind)
    con = db()
    if kind == "feed":
        jpeg = prepare(ig_onedrive.download(photo["id"]), (1080, 1350)); text = ig_caption.feed(meta, today)
    else:
        jpeg = prepare(ig_onedrive.download(photo["id"]), (1080, 1920)); text = ig_caption.story(meta, today)
    url = stage(jpeg)
    media_id = ""
    if not C.DRY_RUN:
        media_id = ig_instagram.publish_image(url, text) if kind == "feed" else ig_instagram.publish_story(url)
    con.execute("INSERT INTO ig_posts(kind,photo,caption,media_id,dry_run,created_at) VALUES(?,?,?,?,?,?)",
                (kind, photo["name"], text, media_id, int(C.DRY_RUN), datetime.datetime.now(JST).isoformat())); con.commit()
    return jsonify({"kind": kind, "photo": photo["name"], "dry_run": C.DRY_RUN, "media_id": media_id, "caption": text})

@instagram_bp.route("/run", methods=["POST"])
def run(): return _post("feed")

@instagram_bp.route("/story", methods=["POST"])
def story(): return _post("story")

@instagram_bp.route("/preview")
def preview():
    if not C.configured(): return "Instagram/OneDrive の環境変数が未設定です", 200
    today, photo, meta = _next("feed")
    return jsonify({"date": str(today), "photo": photo["name"], "caption": ig_caption.feed(meta, today)})

# ---------- DM・コメント受信（Meta Webhook） ----------
@instagram_bp.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":   # Meta の検証
        if request.args.get("hub.verify_token") == C.IG_VERIFY_TOKEN:
            return request.args.get("hub.challenge", ""), 200
        return "forbidden", 403
    data = request.get_json(force=True, silent=True) or {}
    con = db(); new = []
    for entry in data.get("entry", []):
        for m in entry.get("messaging", []):            # DM
            msg = m.get("message", {})
            if msg.get("is_echo") or not msg.get("text"): continue
            new.append(("dm", msg.get("mid"), m["sender"]["id"], "", msg["text"]))
        for ch in entry.get("changes", []):             # コメント
            if ch.get("field") == "comments":
                v = ch["value"]
                if str(v.get("from", {}).get("id")) == str(C.IG_USER_ID): continue
                new.append(("comment", v.get("id"), v.get("from", {}).get("id", ""), v.get("from", {}).get("username", ""), v.get("text", "")))
    for kind, mid, sid, sname, text in new:
        if not mid or con.execute("SELECT 1 FROM ig_inbox WHERE id=?", (mid,)).fetchone(): continue
        try: draft = ig_caption.reply(kind, text, sname)
        except Exception as e: draft = f"(生成失敗: {e})"
        con.execute("INSERT INTO ig_inbox(id,kind,sender_id,sender_name,text,reply,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (mid, kind, sid, sname, text, draft, "pending", datetime.datetime.now(JST).isoformat()))
        ig_instagram.notify(f"[Instagram {'DM' if kind=='dm' else 'コメント'}] {sname or sid}: {text}\n\n返信案: {draft}\n\n確認: {C.PUBLIC_BASE_URL}/admin/instagram/")
    con.commit()
    return "ok", 200

@instagram_bp.route("/inbox/<path:mid>/send", methods=["POST"])
def inbox_send(mid):
    con = db(); r = con.execute("SELECT * FROM ig_inbox WHERE id=?", (mid,)).fetchone()
    text = request.form["reply"].strip()
    if not C.DRY_RUN:
        (ig_instagram.send_dm(r["sender_id"], text) if r["kind"] == "dm" else ig_instagram.reply_comment(mid, text))
    con.execute("UPDATE ig_inbox SET reply=?, status='sent', replied_at=? WHERE id=?", (text, datetime.datetime.now(JST).isoformat(), mid)); con.commit()
    return redirect(url_for("instagram.index"))

@instagram_bp.route("/inbox/<path:mid>/skip", methods=["POST"])
def inbox_skip(mid):
    con = db(); con.execute("UPDATE ig_inbox SET status='skipped' WHERE id=?", (mid,)); con.commit()
    return redirect(url_for("instagram.index"))

# ---------- ダッシュボード ----------
TPL = """
<style>body{font-family:sans-serif;max-width:900px;margin:auto}.card{border:1px solid #ccc;padding:12px;margin:12px 0;border-radius:8px}.tag{font-size:12px;padding:2px 6px;border-radius:4px;background:#eee}</style>
<h2>SNS管制室（Instagram）</h2>
<p>状態: {{'設定済み' if configured else '環境変数 未設定'}} ／ DRY_RUN: {{dry}} ／ <a href="{{url_for('instagram.preview')}}">次のフィード投稿を確認</a> ／ <a href="{{url_for('reviews.index')}}">口コミ管制室へ</a></p>
<h3>受信箱（DM・コメント 返信待ち {{inbox|length}}件）</h3>
{% for r in inbox %}<div class="card"><span class="tag">{{r.kind}}</span> {{r.sender_name or r.sender_id}} <small>{{r.created_at[:16]}}</small>
<p style="background:#f5f5f5;padding:8px">{{r.text}}</p>
<form method="post" action="{{url_for('instagram.inbox_send', mid=r.id)}}"><textarea name="reply" rows="4" style="width:100%">{{r.reply}}</textarea><br>
<button>送信</button> <button formaction="{{url_for('instagram.inbox_skip', mid=r.id)}}">返信しない</button></form></div>{% endfor %}
<h3>投稿ログ（直近30件）</h3>
{% for p in posts %}<div><span class="tag">{{p.kind}}</span> {{p.created_at[:16]}} {{p.photo}} {{'(DRY)' if p.dry_run else ''}}</div>{% endfor %}
"""
@instagram_bp.route("/")
def index():
    con = db()
    return render_template_string(TPL, configured=C.configured(), dry=C.DRY_RUN,
        inbox=con.execute("SELECT * FROM ig_inbox WHERE status='pending' ORDER BY created_at DESC").fetchall(),
        posts=con.execute("SELECT * FROM ig_posts ORDER BY created_at DESC LIMIT 30").fetchall())
