"""Instagram Graph API: フィード投稿・ストーリーズ投稿・コメント返信・DM送信・通知"""
import time, datetime, requests, ig_config as C
# Instagram API with Instagram Login（Facebookページ不要）
BASE = C.IG_API_BASE
_TOKEN = {"v": C.IG_ACCESS_TOKEN}

def tok(): return _TOKEN["v"]

def refresh_token(con, key, env_token):
    """長期トークン（60日で失効）を7日ごとに自動更新してDBに保存し、使うべきトークンを返す。
    key: アカウントごとの保存キー（既定アカウントは ""）。環境変数を貼り替えたらそちらを正とする"""
    pre = f"{key}:" if key else ""
    row = {k[len(pre):]: v for k, v in con.execute("SELECT key, value FROM ig_settings").fetchall()
           if k.startswith(pre) and (key or ":" not in k)}
    if row.get("env_token") != env_token:
        row = {"env_token": env_token, "token": env_token, "refreshed_at": ""}
    token = row["token"]
    last = row.get("refreshed_at") or ""
    if last and datetime.datetime.fromisoformat(last) > datetime.datetime.now() - datetime.timedelta(days=7):
        return token
    try:
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30)
        r.raise_for_status(); token = r.json()["access_token"]
    except Exception as e:
        notify(f"[Instagram{(' ' + key) if key else ''}] アクセストークンの自動更新に失敗しました（失効すると投稿が止まります）: {e}")
    for k, v in (("env_token", env_token), ("token", token), ("refreshed_at", datetime.datetime.now().isoformat())):
        con.execute("INSERT OR REPLACE INTO ig_settings(key,value) VALUES(?,?)", (pre + k, v))
    con.commit()
    return token

def ensure_token(con):
    _TOKEN["v"] = refresh_token(con, "", C.IG_ACCESS_TOKEN)

def _publish(params, user_id=None, token=None):
    user_id = user_id or C.IG_USER_ID; token = token or tok()
    r = requests.post(f"{BASE}/{user_id}/media", data={**params, "access_token": token}, timeout=60)
    if not r.ok: raise RuntimeError(f"media作成失敗 {r.status_code}: {r.text[:300]}")
    container = r.json()["id"]
    for _ in range(20):
        s = requests.get(f"{BASE}/{container}", params={"fields": "status_code,status", "access_token": token}, timeout=30).json()
        if s.get("status_code") == "FINISHED": break
        if s.get("status_code") == "ERROR": raise RuntimeError(f"container error: {s}")
        time.sleep(3)
    r = requests.post(f"{BASE}/{user_id}/media_publish", data={"creation_id": container, "access_token": token}, timeout=60)
    if not r.ok: raise RuntimeError(f"公開失敗 {r.status_code}: {r.text[:300]}")
    return r.json()["id"]

def publish_image(image_url, caption, user_id=None, token=None):
    return _publish({"image_url": image_url, "caption": caption}, user_id, token)

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
