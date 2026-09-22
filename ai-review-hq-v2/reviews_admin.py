"""口コミ管制室 Blueprint。既存Flaskアプリに: app.register_blueprint(reviews_bp)"""
import os, datetime
from flask import Blueprint, render_template_string, request, redirect, url_for, jsonify, session
from google_reviews import db, post_reply, mark_posted, upsert
from review_judge import judge

reviews_bp = Blueprint("reviews", __name__, url_prefix="/admin/replies")

def _auth():
    return request.headers.get("X-Run-Token") == os.environ.get("REPLY_RUN_TOKEN")

API_PATHS = ("/admin/replies/run", "/admin/replies/import", "/admin/replies/tasks")

@reviews_bp.before_request
def _guard():
    """管理画面は既存アプリのログインセッションを流用。API系はトークン認証"""
    if request.path.startswith(API_PATHS):
        return None
    if not session.get("admin"):
        return redirect(url_for("login", next=request.path))

TPL = """
<style>body{font-family:sans-serif;max-width:900px;margin:auto}.card{border:1px solid #ccc;padding:12px;margin:12px 0;border-radius:8px}
.hp{border-left:6px solid #e60012}.gg{border-left:6px solid #4285f4}.rep{background:#fff3f3}.tag{font-size:12px;padding:2px 6px;border-radius:4px;background:#eee}</style>
<h2>口コミ管制室</h2>
<p><a href="{{url_for('google.index')}}">Google連携設定</a> ／ <a href="{{url_for('instagram.index')}}">SNS管制室</a></p>
<p>承認待ち {{pending|length}}件 ／ 違反報告候補 {{reports|length}}件 ／ ホットペッパー返信待ち {{hp_todo|length}}件</p>

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
        posted=q("SELECT * FROM reviews WHERE reply_status='posted' ORDER BY posted_at DESC LIMIT 20"))

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
    from google_reviews import _state
    if not (_state().get("stores") or os.environ.get("REVIEW_STORES")):
        return "not configured yet", 200
    import reply_worker; reply_worker.main()
    return "ok", 200

@reviews_bp.route("/import", methods=["POST"])
def import_reviews():
    """拡張機能がホットペッパー管理画面から取り込んだ口コミを登録 → 判定
    body: [{"store":"南薩農場","reviewer":"...","rating":2,"comment":"...","review_url":"...","external_id":"..."}]"""
    if not _auth(): return "forbidden", 403
    con = db(); n = 0
    for item in request.get_json(force=True):
        rid = "hp:" + (item.get("external_id") or item.get("review_url") or "")
        if not rid.strip("hp:") or con.execute("SELECT 1 FROM reviews WHERE review_id=?", (rid,)).fetchone():
            continue
        j = judge("hotpepper", item["store"], int(item["rating"]), item.get("comment", ""), item.get("reviewer", ""),
                  external_id=item.get("external_id"), posted_at=item.get("posted_at"))
        if j.get("evidence_needed"):
            j["report_reason"] += "\n\n【店側で用意する証憑】" + "／".join(j["evidence_needed"])
        auto = int(item["rating"]) >= int(os.environ.get("AUTO_POST_MIN_RATING", "6")) and not j["report"]
        upsert(con, review_id=rid, platform="hotpepper", store=item["store"], location="",
               reviewer=item.get("reviewer", ""), rating=int(item["rating"]), comment=item.get("comment", ""),
               review_url=item.get("review_url", ""), reply=j["reply"],
               reply_status="todo" if auto else "pending",
               report=int(j["report"]), policy_clause=j.get("policy_clause", ""),
               report_reason=j.get("report_reason", ""), created_at=datetime.datetime.now().isoformat())
        n += 1
    return jsonify({"imported": n})

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
