"""Google連携の管理画面: /admin/google/  接続ボタン → OAuth → 店舗選択 → 保存（DATA_DIR/google.json）"""
import os, json, datetime
from pathlib import Path
from flask import Blueprint, request, redirect, url_for, session, render_template_string
import requests
from google_auth_oauthlib.flow import Flow

google_bp = Blueprint("google", __name__, url_prefix="/admin/google")
DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
STATE_PATH = DATA_DIR / "google.json"
SCOPES = ["https://www.googleapis.com/auth/business.manage"]
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

def load():
    try: return json.loads(STATE_PATH.read_text())
    except Exception: return {}

def save(d):
    DATA_DIR.mkdir(parents=True, exist_ok=True); STATE_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1))

def redirect_uri():
    return os.environ.get("PUBLIC_BASE_URL", "https://kuchikomi-hq.onrender.com").rstrip("/") + "/admin/google/callback"

def _flow():
    return Flow.from_client_config({"web": {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"], "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES, redirect_uri=redirect_uri())

@google_bp.before_request
def _guard():
    if request.path.endswith("/callback"): return None
    if not session.get("admin"): return redirect(url_for("login", next=request.path))

@google_bp.route("/connect")
def connect():
    url, state = _flow().authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    session["g_state"] = state
    return redirect(url)

@google_bp.route("/callback")
def callback():
    if request.args.get("state") != session.get("g_state"): return "state mismatch", 400
    f = _flow(); f.fetch_token(code=request.args["code"])
    d = load(); d["refresh_token"] = f.credentials.refresh_token or d.get("refresh_token")
    d["connected_at"] = datetime.datetime.now().isoformat(); save(d)
    return redirect(url_for("google.index"))

def _token():
    d = load()
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"], "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "refresh_token": d["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
    r.raise_for_status(); return r.json()["access_token"]

def discover():
    h = {"Authorization": f"Bearer {_token()}"}
    out = []
    accts = requests.get("https://mybusinessaccountmanagement.googleapis.com/v1/accounts", headers=h, timeout=30).json()
    for a in accts.get("accounts", []):
        url = f"https://mybusinessbusinessinformation.googleapis.com/v1/{a['name']}/locations"
        params = {"readMask": "name,title,storefrontAddress", "pageSize": 100}
        while url:
            j = requests.get(url, headers=h, params=params, timeout=30).json()
            for l in j.get("locations", []):
                addr = " ".join((l.get("storefrontAddress") or {}).get("addressLines", []))
                out.append({"account": a["name"], "location": l["name"], "title": l.get("title", ""), "addr": addr})
            params["pageToken"] = j.get("nextPageToken"); url = url if params["pageToken"] else None
    return out

TPL = """
<style>body{font-family:sans-serif;max-width:900px;margin:auto}.card{border:1px solid #ccc;padding:12px;margin:12px 0;border-radius:8px}</style>
<h2>Google連携</h2>
{% if not has_client %}<p>環境変数 GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET が未設定です。</p>
{% elif not d.refresh_token %}<p><a href="{{url_for('google.connect')}}"><button style="font-size:16px">Googleと接続する（fujitakagosaku15@gmail.com で許可）</button></a></p>
{% else %}
<p>接続済み（{{d.connected_at[:16]}}）　<a href="{{url_for('google.connect')}}">再接続</a></p>
{% if error %}<p style="color:red">店舗一覧の取得に失敗: {{error}}<br>APIライブラリで「Google My Business API」「My Business Account Management API」「My Business Business Information API」が有効か確認してください。</p>{% endif %}
<form method="post" action="{{url_for('google.select')}}">
<h3>自動返信する店舗を選んで、システム上の店舗名を入力</h3>
{% for l in locs %}<div class="card"><label><input type="checkbox" name="loc" value="{{l.account}}|{{l.location}}" {{'checked' if l.location in selected}}> <b>{{l.title}}</b> <small>{{l.addr}}</small></label><br>
店舗名（返信文の店舗プロフィール用）: <input name="name_{{l.location}}" value="{{selected.get(l.location, l.title)}}" style="width:60%"></div>{% endfor %}
<button style="font-size:16px">この店舗で自動返信を有効にする</button></form>
<p>現在の設定: {{d.stores or '未設定'}}</p>
{% endif %}
<p><a href="{{url_for('reviews.index')}}">口コミ管制室へ</a></p>
"""
@google_bp.route("/")
def index():
    d = load(); locs, error = [], ""
    if d.get("refresh_token"):
        try: locs = discover()
        except Exception as e: error = str(e)[:300]
    selected = {s["location"]: s["name"] for s in d.get("stores", [])}
    return render_template_string(TPL, d=d, locs=locs, error=error, selected=selected,
                                  has_client=bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID")))

@google_bp.route("/select", methods=["POST"])
def select():
    d = load(); stores = []
    for v in request.form.getlist("loc"):
        acct, loc = v.split("|", 1)
        stores.append({"account": acct.split("/")[-1], "location": loc, "name": request.form.get(f"name_{loc}", "").strip() or loc})
    d["stores"] = stores; save(d)
    return redirect(url_for("google.index"))
