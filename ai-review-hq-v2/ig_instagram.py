"""Instagram Graph API: フィード投稿・ストーリーズ投稿・コメント返信・DM送信・通知"""
import time, requests, ig_config as C
BASE = "https://graph.facebook.com/v21.0"

def _publish(params):
    r = requests.post(f"{BASE}/{C.IG_USER_ID}/media", data={**params, "access_token": C.IG_ACCESS_TOKEN}, timeout=60)
    r.raise_for_status(); container = r.json()["id"]
    for _ in range(20):
        s = requests.get(f"{BASE}/{container}", params={"fields": "status_code,status", "access_token": C.IG_ACCESS_TOKEN}, timeout=30).json()
        if s.get("status_code") == "FINISHED": break
        if s.get("status_code") == "ERROR": raise RuntimeError(f"container error: {s}")
        time.sleep(3)
    r = requests.post(f"{BASE}/{C.IG_USER_ID}/media_publish", data={"creation_id": container, "access_token": C.IG_ACCESS_TOKEN}, timeout=60)
    r.raise_for_status(); return r.json()["id"]

def publish_image(image_url, caption):
    return _publish({"image_url": image_url, "caption": caption})

def publish_story(image_url):
    return _publish({"image_url": image_url, "media_type": "STORIES"})

def reply_comment(comment_id, text):
    r = requests.post(f"{BASE}/{comment_id}/replies", data={"message": text, "access_token": C.IG_ACCESS_TOKEN}, timeout=30)
    r.raise_for_status(); return r.json()

def send_dm(recipient_id, text):
    r = requests.post(f"{BASE}/{C.IG_USER_ID}/messages", json={"recipient": {"id": recipient_id}, "message": {"text": text}},
                      params={"access_token": C.IG_ACCESS_TOKEN}, timeout=30)
    r.raise_for_status(); return r.json()

def notify(text):
    if C.NOTIFY_WEBHOOK_URL:
        try: requests.post(C.NOTIFY_WEBHOOK_URL, json={"text": text}, timeout=10)
        except Exception: pass
