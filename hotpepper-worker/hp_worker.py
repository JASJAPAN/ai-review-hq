"""ホットペッパーグルメ 店舗管理画面 無人ワーカー（Render Cron / Docker）
モード（環境変数 HP_MODE）:
  recon : ログイン → 口コミ管理ページの構造とスクショを kuchikomi-hq に送る（セレクタ調整用）
  run   : ログイン → 口コミ取り込み → 確定済み返信の投稿 → 完了報告
セレクタ・URLは kuchikomi-hq の /admin/replies/hp_config から取得（管理画面で編集可能）
"""
import os, re, json, base64, datetime, sys, traceback
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = os.environ.get("KUCHIKOMI_BASE", "https://kuchikomi-hq.onrender.com").rstrip("/") + "/admin/replies"
TOKEN = os.environ["REPLY_RUN_TOKEN"]
HP_USER, HP_PASS = os.environ["HP_LOGIN_ID"], os.environ["HP_LOGIN_PASSWORD"]
MODE = os.environ.get("HP_MODE", "run")
H = {"X-Run-Token": TOKEN, "Content-Type": "application/json"}

def api(path, method="GET", body=None):
    r = requests.request(method, BASE + path, headers=H, json=body, timeout=120); r.raise_for_status()
    return r.json() if r.text.startswith(("{", "[")) else r.text

def notify(msg):
    try: api("/notify", "POST", {"text": msg})
    except Exception: pass

def outline(page, limit=500):
    return page.evaluate("""(limit) => {
      const lines=[]; const walk=(el,d)=>{ if(d>9||lines.length>limit) return;
        const tag=el.tagName.toLowerCase(); if(['script','style','svg','noscript','link','meta'].includes(tag)) return;
        const cls=(typeof el.className==='string'&&el.className.trim())?'.'+el.className.trim().split(/\\s+/).slice(0,3).join('.'):'';
        const id=el.id?'#'+el.id:''; const name=el.getAttribute('name')?`[name=${el.getAttribute('name')}]`:'';
        const own=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).filter(Boolean).join(' ').slice(0,50);
        const href=tag==='a'&&el.getAttribute('href')?` href=${el.getAttribute('href').slice(0,80)}`:'';
        lines.push('  '.repeat(d)+tag+id+cls+name+href+(own?`  「${own}」`:''));
        [...el.children].forEach(c=>walk(c,d+1)); };
      walk(document.body,0); return lines.join('\\n'); }""", limit)

def login(page, cfg):
    page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=60000)
    page.fill(cfg["sel_login_id"], HP_USER); page.fill(cfg["sel_login_pw"], HP_PASS)
    page.click(cfg["sel_login_submit"]); page.wait_for_load_state("networkidle", timeout=60000)
    if page.locator(cfg["sel_login_pw"]).count() and "ログアウト" not in page.content():
        raise RuntimeError("ログインに失敗（IDパスワード or 追加認証）")

def recon(page, cfg):
    shots = []
    for name, url in [("login_after", None), ("reviews", cfg.get("reviews_url", ""))]:
        if url: page.goto(url, wait_until="networkidle", timeout=60000)
        png = base64.b64encode(page.screenshot(full_page=True)).decode()
        shots.append({"name": name, "url": page.url, "outline": outline(page), "png": png})
    api("/recon", "POST", {"at": datetime.datetime.now().isoformat(), "pages": shots})
    print("recon uploaded:", [s["url"] for s in shots])

def scrape(page, cfg, store):
    page.goto(cfg["reviews_url"], wait_until="networkidle", timeout=60000)
    items = []
    for el in page.locator(cfg["sel_item"]).all():
        t = lambda sel: (el.locator(sel).first.inner_text().strip() if sel and el.locator(sel).count() else "")
        txt = el.inner_text()
        rating = 3
        m = re.search(cfg.get("rating_regex") or r"([1-5])", t(cfg.get("sel_rating")) or txt)
        if m: rating = int(m.group(1))
        href = el.locator(cfg["sel_link"]).first.get_attribute("href") if cfg.get("sel_link") and el.locator(cfg["sel_link"]).count() else ""
        url = page.urljoin(href) if hasattr(page, "urljoin") else (href if href.startswith("http") else requests.compat.urljoin(page.url, href))
        idm = re.search(r"([A-Z0-9]{8,}|\d{6,})", (href or "") + txt)
        date = (re.search(r"20\d{2}[/.年]\s?\d{1,2}[/.月]\s?\d{1,2}", txt) or [None])
        items.append({"store": store, "reviewer": t(cfg.get("sel_reviewer")), "rating": rating,
                      "comment": t(cfg.get("sel_comment")) or txt[:2000],
                      "posted_at": date.group(0).replace("年", "/").replace("月", "/") if date else "",
                      "review_url": url, "external_id": idm.group(1) if idm else ""})
    return [i for i in items if len(i["comment"]) > 10]

def post_reply(page, cfg, task):
    page.goto(task["review_url"], wait_until="networkidle", timeout=60000)
    page.fill(cfg["sel_reply_textarea"], task["reply"])
    page.click(cfg["sel_reply_submit"]); page.wait_for_load_state("networkidle", timeout=60000)
    if cfg.get("sel_reply_confirm") and page.locator(cfg["sel_reply_confirm"]).count():
        page.click(cfg["sel_reply_confirm"]); page.wait_for_load_state("networkidle", timeout=60000)

def main():
    cfg = api("/hp_config")
    stores = cfg.get("stores") or []   # [{"name":"南薩農場","reviews_url":"...","login_id_env":...}]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        try:
            login(page, cfg)
            if MODE == "recon":
                recon(page, cfg); return
            total = 0
            for s in stores or [{"name": cfg.get("store_name", "南薩農場"), "reviews_url": cfg.get("reviews_url")}]:
                c = {**cfg, "reviews_url": s["reviews_url"]}
                items = scrape(page, c, s["name"])
                if items:
                    r = api("/import", "POST", items); total += r.get("imported", 0)
            print(f"imported {total}")
            tasks = api("/tasks")
            done = 0
            for tk in tasks.get("hotpepper_replies", []):
                if not cfg.get("sel_reply_textarea"): break
                try:
                    post_reply(page, cfg, tk); api("/tasks/done", "POST", {"review_id": tk["review_id"], "kind": "reply"}); done += 1
                except Exception as e:
                    print("reply failed:", tk["review_id"], e)
            print(f"replied {done}")
            if tasks.get("reports"):
                notify(f"[ホットペッパー] 違反報告の実行待ちが{len(tasks['reports'])}件あります。管理画面で報告文を確認してください。")
        except Exception as e:
            traceback.print_exc()
            try: api("/recon", "POST", {"at": datetime.datetime.now().isoformat(), "error": str(e)[:500],
                     "pages": [{"name": "error", "url": page.url, "outline": outline(page, 300),
                                "png": base64.b64encode(page.screenshot(full_page=True)).decode()}]})
            except Exception: pass
            notify(f"[ホットペッパー] 自動処理でエラー: {str(e)[:200]}")
            sys.exit(1)
        finally:
            browser.close()

if __name__ == "__main__":
    main()
