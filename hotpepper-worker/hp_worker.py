"""ホットペッパーグルメ 店舗管理画面 無人ワーカー（Render Cron / Docker）
HP_MODE: recon（画面構造を送る）/ run（取り込み → 確定済み返信の投稿）
店舗ごとにログイン: HP_1_NAME / HP_1_ID / HP_1_PW, HP_2_NAME / HP_2_ID / HP_2_PW ...（無ければ HP_STORE_NAME + HP_LOGIN_ID / HP_LOGIN_PASSWORD）
"""
import os, re, json, base64, datetime, sys, traceback, hashlib
import requests
from playwright.sync_api import sync_playwright

BASE = os.environ.get("KUCHIKOMI_BASE", "https://kuchikomi-hq.onrender.com").rstrip("/") + "/admin/replies"
TOKEN = os.environ["REPLY_RUN_TOKEN"]
MODE = os.environ.get("HP_MODE", "run")
H = {"X-Run-Token": TOKEN, "Content-Type": "application/json"}
LOGIN_URL = "https://www.cms.hotpepper.jp/CLN/login/"

def accounts():
    """旧形式（HP_LOGIN_ID/HP_LOGIN_PASSWORD + HP_STORE_NAME）と番号付き（HP_n_NAME/ID/PW）を両方読む。ID重複は除外"""
    out = []
    if os.environ.get("HP_LOGIN_ID") and os.environ.get("HP_LOGIN_PASSWORD"):
        out.append({"name": os.environ.get("HP_STORE_NAME", "川畜天文館店"), "id": os.environ["HP_LOGIN_ID"], "pw": os.environ["HP_LOGIN_PASSWORD"]})
    for i in range(1, 31):  # 最大30店舗まで
        n, u, p = os.environ.get(f"HP_{i}_NAME"), os.environ.get(f"HP_{i}_ID"), os.environ.get(f"HP_{i}_PW")
        if n and u and p and u not in [a["id"] for a in out]: out.append({"name": n, "id": u, "pw": p})
    print("対象店舗:", [a["name"] for a in out])
    return out

def api(path, method="GET", body=None):
    r = requests.request(method, BASE + path, headers=H, json=body, timeout=120); r.raise_for_status()
    return r.json() if r.text.startswith(("{", "[")) else r.text

def notify(msg):
    try: api("/notify", "POST", {"text": msg})
    except Exception: pass

def outline(page, limit=500):
    return page.evaluate("""(limit) => { const lines=[]; const walk=(el,d)=>{ if(d>9||lines.length>limit) return;
      const tag=el.tagName.toLowerCase(); if(['script','style','svg','noscript','link','meta'].includes(tag)) return;
      const cls=(typeof el.className==='string'&&el.className.trim())?'.'+el.className.trim().split(/\\s+/).slice(0,3).join('.'):'';
      const id=el.id?'#'+el.id:''; const name=el.getAttribute('name')?`[name=${el.getAttribute('name')}]`:'';
      const own=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).filter(Boolean).join(' ').slice(0,50);
      lines.push('  '.repeat(d)+tag+id+cls+name+(own?`  「${own}」`:'')); [...el.children].forEach(c=>walk(c,d+1)); };
      walk(document.body,0); return lines.join('\\n'); }""", limit)

# ---------- ログイン → トップメニュー ----------
def login(page, acct):
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    page.fill("#jscInputUserId", acct["id"]); page.fill("input[name='password']", acct["pw"])
    page.click("form[name='loginActionForm'] input[type='submit']")
    page.wait_for_url(re.compile(r"/CLN/topMenu/"), timeout=120000)   # 「認証中...」を通過してトップメニューまで待つ
    page.wait_for_load_state("networkidle", timeout=60000)

def open_review_list(page, unreplied_only=True):
    page.get_by_role("link", name=re.compile("口コミ一覧")).first.click()
    page.wait_for_load_state("networkidle", timeout=60000)
    if unreplied_only and page.locator("a[href*='showReportListNoComment']").count():
        page.locator("a[href*='showReportListNoComment']").first.click(); page.wait_for_load_state("networkidle", timeout=60000)

# ---------- 口コミ抽出 ----------
SEL_ITEM = "table.style01:has(span[id^='contributionDate'])"
def scrape(page, store):
    items = []
    for el in page.locator(SEL_ITEM).all():
        cell = el.locator("tr:first-child > td:nth-child(4)")
        celltxt = cell.inner_text() if cell.count() else el.inner_text()
        m = re.search(r"★\s*([1-5])", celltxt); rating = int(m.group(1)) if m else 3
        namel = el.locator("tr:first-child > td:nth-child(4) a"); reviewer = namel.first.inner_text().strip() if namel.count() else ""
        full = el.locator("input[name='reportText']"); comment = (full.first.get_attribute("value") or "").strip() if full.count() else celltxt
        datel = el.locator("span[id^='contributionDate']"); date = datel.first.inner_text().strip() if datel.count() else ""
        hidden = {h.get_attribute("name"): h.get_attribute("value") for h in el.locator("input[type='hidden']").all()}
        key = next((v for k, v in hidden.items() if k and re.search(r"(report|contribution|review).*(id|no|seq)", k, re.I) and v), None)
        ext = key or hashlib.md5(f"{store}|{date}|{reviewer}|{comment[:60]}".encode()).hexdigest()[:16]
        items.append({"store": store, "reviewer": reviewer, "rating": rating, "comment": comment[:2000],
                      "posted_at": re.sub(r"[年月]", "/", date).replace("日", ""), "review_url": page.url, "external_id": ext,
                      "_hidden": hidden})
    return [i for i in items if len(i["comment"]) > 5]

def find_item(page, ext, task):
    for el in page.locator(SEL_ITEM).all():
        hidden = {h.get_attribute("name"): h.get_attribute("value") for h in el.locator("input[type='hidden']").all()}
        if ext in hidden.values(): return el
        # ハッシュID: 投稿者名＋本文先頭で照合
        full = el.locator("input[name='reportText']"); comment = (full.first.get_attribute("value") or "") if full.count() else ""
        if task and task["reviewer"] and task["reviewer"] in el.inner_text() and comment[:40] == task["comment"][:40]: return el
    return None

def post_reply(page, task):
    ext = task["review_id"].replace("hp:", "", 1)
    el = find_item(page, ext, task)
    if el is None: raise RuntimeError("一覧に該当口コミが見つからない（返信済み or ページ送りが必要）")
    el.locator("tr:first-child > td:nth-child(4) a").first.click(); page.wait_for_load_state("networkidle", timeout=60000)
    page.fill("textarea[name='commentText']", task["reply"])
    page.click("input[type='button'][value='同意して投稿する']"); page.wait_for_load_state("networkidle", timeout=60000)
    confirm = page.locator("input[type='button'][onclick*='doRegist']")
    if confirm.count(): confirm.first.click(); page.wait_for_load_state("networkidle", timeout=60000)

def recon(page, acct):
    shots = []
    def snap(name):
        shots.append({"name": name, "url": page.url, "outline": outline(page, 300),
                      "png": base64.b64encode(page.screenshot(type="jpeg", quality=45, full_page=False)).decode()})
    snap("top_menu"); open_review_list(page); snap("review_list")
    items = scrape(page, acct["name"])
    shots.append({"name": f"scraped_{len(items)}件", "url": page.url, "outline": json.dumps(items[:5], ensure_ascii=False, indent=1), "png": ""})
    api("/recon", "POST", {"at": datetime.datetime.now().isoformat(), "pages": shots})

def main():
    accts = accounts()
    if not accts: print("no accounts configured"); return
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        errors = []
        for acct in accts:
            ctx = browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900}); page = ctx.new_page()
            try:
                login(page, acct)
                if MODE == "recon": recon(page, acct); continue
                open_review_list(page)
                items = scrape(page, acct["name"])
                for i in items: i.pop("_hidden", None)
                imported = api("/import", "POST", items).get("imported", 0) if items else 0
                tasks = [t for t in api("/tasks").get("hotpepper_replies", []) if t["store"] == acct["name"]]
                done = 0
                for tk in tasks:
                    try:
                        open_review_list(page); post_reply(page, tk)
                        api("/tasks/done", "POST", {"review_id": tk["review_id"], "kind": "reply"}); done += 1
                    except Exception as e:
                        print("reply failed:", tk["review_id"], e); errors.append(f"{acct['name']} 返信失敗: {str(e)[:120]}")
                print(f"[{acct['name']}] 取り込み{imported}件 / 返信投稿{done}件")
            except Exception as e:
                traceback.print_exc(); errors.append(f"{acct['name']}: {str(e)[:200]}")
                try: api("/recon", "POST", {"at": datetime.datetime.now().isoformat(), "error": f"{acct['name']}: {str(e)[:500]}",
                         "pages": [{"name": "error", "url": page.url, "outline": outline(page, 300), "png": base64.b64encode(page.screenshot(type="jpeg", quality=45, full_page=False)).decode()}]})
                except Exception: pass
            finally:
                ctx.close()
        # --- 承認済みの違反報告を自動送信（ホットペッパーのみ。Googleは手動のまま） ---
        t = api("/tasks") if MODE == "run" else {}
        approved_hp = [r for r in t.get("reports", []) if r.get("platform") == "hotpepper"]
        sent = 0
        for acct in accts:
            mine = [r for r in approved_hp if r["store"] == acct["name"]]
            if not mine:
                continue
            ctx = browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            try:
                login(page, acct)
                for rp in mine:
                    try:
                        open_review_list(page, unreplied_only=False)
                        ext = rp["review_id"].replace("hp:", "", 1)
                        el = find_item(page, ext, rp)
                        if el is None:
                            errors.append(f"{acct['name']} 違反報告: 対象口コミが見つからない（{rp['review_id']}）")
                            continue
                        link = el.get_by_text("違反報告", exact=False)
                        if not link.count():
                            link = el.get_by_text("通報", exact=False)
                        if not link.count():
                            errors.append(f"{acct['name']} 違反報告: 報告リンクが見つからない（{rp['review_id']}）。画面仕様の確認が必要")
                            continue
                        link.first.click(); page.wait_for_load_state("networkidle", timeout=60000)
                        box = page.locator("textarea")
                        if box.count():
                            box.first.fill(rp.get("report_reason", "")[:800])
                        btn = page.get_by_role("button", name=re.compile("送信|報告する|確認"))
                        if not btn.count():
                            btn = page.locator("input[type='button'][value*='送信'], input[type='submit']")
                        btn.first.click(); page.wait_for_load_state("networkidle", timeout=60000)
                        confirm = page.get_by_role("button", name=re.compile("送信|報告する"))
                        if not confirm.count():
                            confirm = page.locator("input[type='button'][onclick*='doRegist']")
                        if confirm.count():
                            confirm.first.click(); page.wait_for_load_state("networkidle", timeout=60000)
                        api("/tasks/done", "POST", {"review_id": rp["review_id"], "kind": "report"})
                        sent += 1
                    except Exception as e:
                        errors.append(f"{acct['name']} 違反報告失敗（{rp['review_id']}）: {str(e)[:120]}")
                        try: api("/recon", "POST", {"at": datetime.datetime.now().isoformat(), "error": f"report {rp['review_id']}: {str(e)[:300]}",
                                 "pages": [{"name": "report_error", "url": page.url, "outline": outline(page, 300), "png": base64.b64encode(page.screenshot(type="jpeg", quality=45, full_page=False)).decode()}]})
                        except Exception: pass
            finally:
                ctx.close()
        browser.close()
        if sent:
            notify(f"[ホットペッパー] 承認済みの違反報告を{sent}件送信しました。")
        pend = t.get("reports_pending_count", 0)
        if pend:
            notify(f"[口コミ管制室] 承認待ちの違反報告候補が{pend}件あります。管理画面で承認/却下してください。")
        g_manual = [r for r in t.get("reports", []) if r.get("platform") == "google"]
        if g_manual:
            notify(f"[Google] 承認済みの違反報告が{len(g_manual)}件あります。Googleは自動送信非対応のため、クチコミ管理ツールから手動報告してください。")
        if errors: notify("[ホットペッパー] " + " / ".join(errors)); sys.exit(1)

if __name__ == "__main__":
    main()
