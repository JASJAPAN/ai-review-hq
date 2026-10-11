"""Instagram Graph API: フィード投稿・ストーリーズ投稿・コメント返信・DM送信・通知"""
import time, datetime, requests, ig_config as C
# Instagram API with Instagram Login（Facebookページ不要）
BASE = C.IG_API_BASE
_TOKEN = {"v": C.IG_ACCESS_TOKEN}

def tok(): return _TOKEN["v"]

def ensure_token(con):
    """長期トークン（60日で失効）を7日ごとに自動更新し、DBに保存。環境変数を貼り替えずに毎日投稿を続けられる"""
    row = dict(con.execute("SELECT key, value FROM ig_settings").fetchall())
    if row.get("env_token") != C.IG_ACCESS_TOKEN:   # 環境変数を新しく貼り替えたら、そちらを正とする
        row = {"env_token": C.IG_ACCESS_TOKEN, "token": C.IG_ACCESS_TOKEN, "refreshed_at": ""}
    _TOKEN["v"] = row["token"]
    last = row.get("refreshed_at") or ""
    if last and datetime.datetime.fromisoformat(last) > datetime.datetime.now() - datetime.timedelta(days=7):
        return
    try:
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": row["token"]}, timeout=30)
        r.raise_for_status(); _TOKEN["v"] = r.json()["access_token"]
    except Exception as e:
        notify(f"[Instagram] アクセストークンの自動更新に失敗しました（失効すると投稿が止まります）: {e}")
    for k, v in (("env_token", C.IG_ACCESS_TOKEN), ("token", _TOKEN["v"]), ("refreshed_at", datetime.datetime.now().isoformat())):
        con.execute("INSERT OR REPLACE INTO ig_settings(key,value) VALUES(?,?)", (k, v))
    con.commit()

def _publish(params):
    r = requests.post(f"{BASE}/{C.IG_USER_ID}/media", data={**params, "access_token": tok()}, timeout=60)
    r.raise_for_status(); container = r.json()["id"]
    for _ in range(20):
        s = requests.get(f"{BASE}/{container}", params={"fields": "status_code,status", "access_token": tok()}, timeout=30).json()
        if s.get("status_code") == "FINISHED": break
        if s.get("status_code") == "ERROR": raise RuntimeError(f"container error: {s}")
        time.sleep(3)
    r = requests.post(f"{BASE}/{C.IG_USER_ID}/media_publish", data={"creation_id": container, "access_token": tok()}, timeout=60)
    r.raise_for_status(); return r.json()["id"]

def publish_image(image_url, caption):
    return _publish({"image_url": image_url, "caption": caption})

def publish_story(image_url):
    return _publish({"image_url": image_url, "media_type": "STORIES"})

def reply_comment(comment_id, text):
    r = requests.post(f"{BASE}/{comment_id}/replies", data={"message": text, "access_token": tok()}, timeout=30)
    r.raise_for_status(); return r.json()

def send_dm(recipient_id, text):
    r = requests.post(f"{BASE}/{C.IG_USER_ID}/messages", json={"recipient": {"id": recipient_id}, "message": {"text": text}},
                      params={"access_token": tok()}, timeout=30)
    r.raise_for_status(); return r.json()

def notify(text):
    if C.NOTIFY_WEBHOOK_URL:
        try: requests.post(C.NOTIFY_WEBHOOK_URL, json={"text": text}, timeout=10)
        except Exception: pass
