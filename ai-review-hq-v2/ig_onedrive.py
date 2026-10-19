"""OneDrive「HP掲載中」フォルダの写真取得（Microsoft Graph, client credentials）"""
import requests, ig_config as C
GRAPH = "https://graph.microsoft.com/v1.0"
IMAGE_EXT = (".jpg", ".jpeg", ".png")

def _token():
    r = requests.post(f"https://login.microsoftonline.com/{C.MS_TENANT_ID}/oauth2/v2.0/token", data={
        "client_id": C.MS_CLIENT_ID, "client_secret": C.MS_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status(); return r.json()["access_token"]

def _list_folder(folder_id, h):
    url = f"{GRAPH}/drives/{C.ONEDRIVE_DRIVE_ID}/items/{folder_id}/children?$select=id,name,file,createdDateTime&$top=200"
    items = []
    while url:
        d = requests.get(url, headers=h, timeout=60).json()
        items += [{"id": i["id"], "name": i["name"], "created": i.get("createdDateTime", "")} for i in d.get("value", [])
                  if "file" in i and i["name"].lower().endswith(IMAGE_EXT)]
        url = d.get("@odata.nextLink")
    return items

def list_photos():
    """定番フォルダ（HP掲載中）＋ 任意の新着フォルダ。新着は入れた順（古い順）に優先投稿される"""
    h = {"Authorization": f"Bearer {_token()}"}
    photos = sorted(_list_folder(C.ONEDRIVE_FOLDER_ID, h), key=lambda x: x["name"])
    if C.ONEDRIVE_NEW_FOLDER_ID:
        photos += [{**p, "new": True} for p in sorted(_list_folder(C.ONEDRIVE_NEW_FOLDER_ID, h), key=lambda x: x["created"])]
    return photos

def list_folder(drive_id, folder_id):
    """任意のドライブ・フォルダの画像一覧（投稿カレンダーの素材選び用）"""
    h = {"Authorization": f"Bearer {_token()}"}
    url = f"{GRAPH}/drives/{drive_id}/items/{folder_id}/children?$select=id,name,file,folder&$top=200"
    items = []
    while url:
        d = requests.get(url, headers=h, timeout=60).json()
        if "error" in d: raise RuntimeError(f"OneDrive一覧の取得に失敗: {d['error']}")
        items += [{"id": i["id"], "name": i["name"], "folder": "folder" in i} for i in d.get("value", [])
                  if "folder" in i or i["name"].lower().endswith(IMAGE_EXT)]
        url = d.get("@odata.nextLink")
    return sorted(items, key=lambda x: (not x["folder"], x["name"]))

def download(item_id, drive_id=None):
    r = requests.get(f"{GRAPH}/drives/{drive_id or C.ONEDRIVE_DRIVE_ID}/items/{item_id}/content",
                     headers={"Authorization": f"Bearer {_token()}"}, allow_redirects=True, timeout=120)
    r.raise_for_status(); return r.content
