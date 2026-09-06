"""PCで1回だけ実行してリフレッシュトークンを取得する"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/business.manage"]

client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or input("Client ID: ").strip()
client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or input("Client Secret: ").strip()

flow = InstalledAppFlow.from_client_config(
    {"installed": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }},
    SCOPES,
)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\n=== Renderの環境変数に設定してください ===")
print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
