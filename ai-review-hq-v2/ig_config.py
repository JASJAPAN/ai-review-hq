"""Instagram / OneDrive 設定（環境変数）。未設定でもimportは通る（ルート側でチェック）"""
import os
E = os.environ.get
MS_TENANT_ID = E("MS_TENANT_ID", ""); MS_CLIENT_ID = E("MS_CLIENT_ID", ""); MS_CLIENT_SECRET = E("MS_CLIENT_SECRET", "")
ONEDRIVE_DRIVE_ID = E("ONEDRIVE_DRIVE_ID", "b!YAZ0dzqGpEK5uErMsjvFWxs_a1tLuE9BoBGNNFPFr3k0R-1hLEe3RZlGxI7cKXLh")
ONEDRIVE_FOLDER_ID = E("ONEDRIVE_FOLDER_ID", "01VJWSBSFT2KEFGMN5EJA3BII5AHU62RYH")   # HP掲載中
IG_USER_ID = E("IG_USER_ID", ""); IG_ACCESS_TOKEN = E("IG_ACCESS_TOKEN", "")
IG_VERIFY_TOKEN = E("IG_VERIFY_TOKEN", "kuchikomi-hq-verify")
PUBLIC_BASE_URL = E("PUBLIC_BASE_URL", "https://kuchikomi-hq.onrender.com").rstrip("/")
DRY_RUN = E("IG_DRY_RUN", "true").lower() == "true"
NOTIFY_WEBHOOK_URL = E("NOTIFY_WEBHOOK_URL", "")   # Teams Incoming Webhook 等。DM/コメント通知先
MODEL = E("ANTHROPIC_MODEL", "claude-sonnet-4-6")

def configured():
    return all([MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, IG_USER_ID, IG_ACCESS_TOKEN])

STORE = {
    "name": "南薩農場 鹿児島天文館店", "short": "南薩農場",
    "tagline": "全席完全個室居酒屋 鹿児島を食べる！",
    "address": "鹿児島市東千石町1-26 フォリス観光ビル天文館3F",
    "access": "天文館通電停 徒歩2分", "hours": "16:00〜24:00（L.O.23:00）無休",
    "features": ["全席掘りごたつ完全個室", "漁港直送の鮮魚を毎日仕入れ", "枕崎産鰹の藁焼き", "六白黒豚・黒毛和牛・薩摩地鶏など鹿児島食材"],
    "hashtags": ["#南薩農場", "#鹿児島グルメ", "#天文館グルメ", "#鹿児島居酒屋", "#天文館居酒屋",
                 "#鹿児島郷土料理", "#完全個室居酒屋", "#鹿児島観光", "#kagoshima", "#鹿児島飲み"],
}
