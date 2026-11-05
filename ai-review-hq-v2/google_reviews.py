"""Google Business Profile 口コミの取得・返信 + 共通DB"""
import os, sys, sqlite3, datetime
import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

DATA_DIR = os.environ.get("DATA_DIR", ".")
DB_PATH = os.path.join(DATA_DIR, "review_replies.db")
V4 = "https://mybusiness.googleapis.com/v4"
ACCOUNT_ID = os.environ.get("GOOGLE_ACCOUNT_ID", "")
STAR = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}


# ---------- Google ----------
def _state():
    try:
        import json; return json.load(open(os.path.join(DATA_DIR, "google.json")))
    except Exception:
        return {}

def _creds():
    c = Credentials(
        None,
        refresh_token=os.environ.get("GOOGLE_REFRESH_TOKEN") or _state().get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/business.manage"],
    )
    c.refresh(Request())
    return c


def _headers():
    return {"Authorization": f"Bearer {_creds().token}", "Content-Type": "application/json"}


def discover():
    h = _headers()
    accts = requests.get("https://mybusinessaccountmanagement.googleapis.com/v1/accounts", headers=h).json()
    for a in accts.get("accounts", []):
        print(f"[account] {a['name']}  ({a.get('accountName')})")
        locs = requests.get(
            f"https://mybusinessbusinessinformation.googleapis.com/v1/{a['name']}/locations",
            headers=h, params={"readMask": "name,title", "pageSize": 100},
        ).json()
        for l in locs.get("locations", []):
            print(f"   {l['name']}  {l.get('title')}")


def fetch_reviews(location, account_id=None):
    account_id = account_id or ACCOUNT_ID
    """未返信の口コミを返す。location は 'locations/xxxx'"""
    url = f"{V4}/accounts/{account_id}/{location}/reviews"
    out, token = [], None
    while True:
        r = requests.get(url, headers=_headers(), params={"pageSize": 50, "pageToken": token}).json()
        for rv in r.get("reviews", []):
            if "reviewReply" not in rv:
                out.append(rv)
        token = r.get("nextPageToken")
        if not token:
            return out


def fetch_all_reviews(location, account_id=None):
    """返信済みも含めた全口コミ（日付の補正用）"""
    account_id = account_id or ACCOUNT_ID
    url = f"{V4}/accounts/{account_id}/{location}/reviews"
    out, token = [], None
    while True:
        r = requests.get(url, headers=_headers(), params={"pageSize": 50, "pageToken": token}).json()
        out.extend(r.get("reviews", []))
        token = r.get("nextPageToken")
        if not token:
            return out


def post_reply(location, review_id, text, account_id=None):
    account_id = account_id or ACCOUNT_ID or next((s["account"] for s in _state().get("stores", []) if s["location"] == location), "")
    url = f"{V4}/accounts/{account_id}/{location}/reviews/{review_id}/reply"
    r = requests.put(url, headers=_headers(), json={"comment": text})
    r.raise_for_status()
    return r.json()


# ---------- DB（Google / ホットペッパー共通） ----------
def db():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS reviews(
        review_id TEXT PRIMARY KEY,      -- google: reviewId / hotpepper: 'hp:'+口コミURL or ID
        platform TEXT,                   -- google / hotpepper
        store TEXT, location TEXT,
        reviewer TEXT, rating INTEGER, comment TEXT, review_url TEXT,
        reply TEXT, reply_status TEXT,   -- pending / posted / skipped / todo(拡張機能待ち)
        report INTEGER DEFAULT 0, policy_clause TEXT, report_reason TEXT,
        report_status TEXT,              -- '' / todo / reported / rejected
        created_at TEXT, posted_at TEXT)""")
    return con


def upsert(con, **kw):
    kw.setdefault("review_url", "")
    kw.setdefault("report", 0); kw.setdefault("policy_clause", ""); kw.setdefault("report_reason", "")
    kw.setdefault("report_status", "todo" if kw["report"] else "")
    con.execute("""INSERT OR IGNORE INTO reviews
        (review_id,platform,store,location,reviewer,rating,comment,review_url,reply,reply_status,
         report,policy_clause,report_reason,report_status,created_at)
        VALUES(:review_id,:platform,:store,:location,:reviewer,:rating,:comment,:review_url,:reply,:reply_status,
         :report,:policy_clause,:report_reason,:report_status,:created_at)""", kw)
    con.commit()


def mark_posted(con, review_id, reply):
    con.execute("UPDATE reviews SET reply=?, reply_status='posted', posted_at=? WHERE review_id=?",
                (reply, datetime.datetime.now().isoformat(), review_id))
    con.commit()



def fetch_customer_media(location, account_id=None):
    """お客様が投稿した写真の一覧（写真付き口コミの判定用）。attribution.profileName と createTime で口コミと突き合わせる"""
    account_id = account_id or ACCOUNT_ID
    url = f"{V4}/accounts/{account_id}/{location}/media/customers"
    out, token = [], None
    while True:
        r = requests.get(url, headers=_headers(), params={"pageSize": 100, "pageToken": token}).json()
        if "error" in r:
            raise RuntimeError(str(r["error"])[:300])
        out.extend(r.get("mediaItems", []))
        token = r.get("nextPageToken")
        if not token or len(out) >= 2000:
            return out


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "discover":
        discover()
