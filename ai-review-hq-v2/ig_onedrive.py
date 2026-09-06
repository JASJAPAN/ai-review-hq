"""OneDrive「HP掲載中」フォルダの写真取得（Microsoft Graph, client credentials）"""
import requests, ig_config as C
GRAPH = "https://graph.microsoft.com/v1.0"
IMAGE_EXT = (".jpg", ".jpeg", ".png")

def _token():
    r = requests.post(f"https://login.microsoftonline.com/{C.MS_TENANT_ID}/oauth2/v2.0/token", data={
        "client_id": C.MS_CLIENT_ID, "client_secret": C.MS_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status(); return r.json()["access_token"]

def list_photos():
    h = {"Authorization": f"Bearer {_token()}"}
    url = f"{GRAPH}/drives/{C.ONEDRIVE_DRIVE_ID}/items/{C.ONEDRIVE_FOLDER_ID}/children?$select=id,name,file&$top=200"
    items = []
    while url:
        d = requests.get(url, headers=h, timeout=60).json()
        items += [{"id": i["id"], "name": i["name"]} for i in d.get("value", [])
                  if "file" in i and i["name"].lower().endswith(IMAGE_EXT)]
        url = d.get("@odata.nextLink")
    return sorted(items, key=lambda x: x["name"])

def download(item_id):
    r = requests.get(f"{GRAPH}/drives/{C.ONEDRIVE_DRIVE_ID}/items/{item_id}/content",
                     headers={"Authorization": f"Bearer {_token()}"}, allow_redirects=True, timeout=120)
    r.raise_for_status(); return r.content
