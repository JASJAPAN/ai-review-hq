"""ホットペッパーグルメ 店舗管理画面 無人ワーカー（Render Cron / Docker）
HP_MODE: recon（画面構造を送る）/ run（取り込み → 確定済み返信の投稿）
店舗ごとにログイン: HP_1_NAME / HP_1_ID / HP_1_PW, HP_2_NAME / HP_2_ID / HP_2_PW ...（無ければ HP_STORE_NAME + HP_LOGIN_ID / HP_LOGIN_PASSWORD）
"""
import os, re, json, base64, datetime, sys, traceback, hashlib
import requests
from playwright.sync_api import sync_playwright

BASE = os.environ.get("KUCHIKOMI_BASE", "https://kuchikomi-hq.onrender.com").rstrip("/") + "/admin/replies"
TOKEN = os.environ.get("REPLY_RUN_TOKEN", "")
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
    try: api("/notify", "POST", {"text": str(msg)[:3000]})
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

REVIEW_LIST_URL = "https://www.cms.hotpepper.jp/CLP/ccm010/showReportListAllForAuth"

def open_review_list(page, unreplied_only=True):
    # 指示書対応: メニューのリンクを探さず、口コミ一覧を直接GETで開く
    page.goto(REVIEW_LIST_URL, wait_until="networkidle", timeout=60000)
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
    return items  # 本文なし（星のみ）の口コミも対象にして返信する（AIは「本文なし・星のみ」として返信文を生成する）

def find_item(page, ext, task):
    for el in page.locator(SEL_ITEM).all():
        hidden = {h.get_attribute("name"): h.get_attribute("value") for h in el.locator("input[type='hidden']").all()}
        if ext in hidden.values(): return el
        # ハッシュID: 投稿者名＋本文先頭で照合
        full = el.locator("input[name='reportText']"); comment = (full.first.get_attribute("value") or "") if full.count() else ""
        if task and task.get("reviewer") and task["reviewer"] in el.inner_text() and comment[:40] == (task.get("comment") or "")[:40]: return el
        # 最終手段: scrape()と同一の材料でこの行のハッシュIDを再計算して突き合わせる
        # （投稿者名リンクが無い行や本文の表記ゆれで上の照合が失敗するケースの救済）
        if task and task.get("store"):
            try:
                cell = el.locator("tr:first-child > td:nth-child(4)")
                celltxt = cell.inner_text() if cell.count() else el.inner_text()
                namel = el.locator("tr:first-child > td:nth-child(4) a")
                reviewer = namel.first.inner_text().strip() if namel.count() else ""
                c = (full.first.get_attribute("value") or "").strip() if full.count() else celltxt
                datel = el.locator("span[id^='contributionDate']")
                date = datel.first.inner_text().strip() if datel.count() else ""
                if hashlib.md5(f"{task['store']}|{date}|{reviewer}|{c[:60]}".encode()).hexdigest()[:16] == ext:
                    return el
            except Exception:
                pass
    return None

def post_reply(page, task):
    ext = task["review_id"].replace("hp:", "", 1)
    el = find_item(page, ext, task)
    if el is None: raise RuntimeError("一覧に該当口コミが見つからない（返信済み or ページ送りが必要）")
    el.locator("tr:first-child > td:nth-child(4) a").first.click(); page.wait_for_load_state("networkidle", timeout=60000)
    page.fill("textarea[name='commentText']", task["reply"])
    page.click("input[type='button'][value='同意して投稿する']"); page.wait_for_load_state("networkidle", timeout=60000)
    # 確認画面の最終確定ボタンは形が複数あるため広めに探す
    for sel in ("input[type='button'][onclick*='doRegist']", "input[type='submit'][value*='投稿']",
                "input[type='button'][value*='投稿する']", "input[type='submit'][value*='登録']"):
        confirm = page.locator(sel)
        if confirm.count():
            confirm.first.click(); page.wait_for_load_state("networkidle", timeout=60000); break
    diag_url = page.url
    try: diag_txt = re.sub(r"\s+", " ", page.inner_text("body"))[:180]
    except Exception: diag_txt = ""
    try:
        diag_btns = " | ".join(filter(None, [((b.get_attribute("value") or b.inner_text() or "").strip())[:20]
                                             for b in page.locator("input[type='button'],input[type='submit'],button").all()[:10]]))
    except Exception: diag_btns = ""
    # 投稿検証: 未返信一覧から消えたことを確認（消えていなければ投稿は反映されていない）
    open_review_list(page)
    if find_item(page, ext, task) is not None:
        raise RuntimeError(f"投稿後も未返信一覧に残っている（投稿未反映 / 直後URL:{diag_url[:80]} / ボタン:{diag_btns} / 文言:{diag_txt[:100]}）")

def recon(page, acct):
    shots = []
    def snap(name):
        shots.append({"name": name, "url": page.url, "outline": outline(page, 300),
                      "png": base64.b64encode(page.screenshot(type="jpeg", quality=45, full_page=False)).decode()})
    snap("top_menu"); open_review_list(page); snap("review_list")
    items = scrape(page, acct["name"])
    shots.append({"name": f"scraped_{len(items)}件", "url": page.url, "outline": json.dumps(items[:5], ensure_ascii=False, indent=1), "png": ""})
    api("/recon", "POST", {"at": datetime.datetime.now().isoformat(), "pages": shots})

def send_reports_for(page, acct, errors):
    """この店舗の承認済み違反報告を、同じログインセッション内で送信する（返信と同時並行）"""
    if MODE != "run":
        return 0
    KBN_MAP = [
        (("体験に基づ", "来店", "利用してい"), "01"), (("無関係", "関係がない", "関係のない"), "02"),
        (("事実ではない", "事実に反", "虚偽"), "03"), (("誹謗", "中傷", "差別", "人権"), "04"),
        (("法律", "条例", "犯罪"), "05"), (("権利", "著作", "肖像", "個人情報", "プライバシー"), "06"),
        (("商業", "宣伝", "営業目的"), "07"), (("衛生",), "08"),
    ]
    def pick_kbn(rp):
        basis = (rp.get("policy_clause", "") or "") + (rp.get("report_reason", "") or "")
        for keys, kbn in KBN_MAP:
            if any(k in basis for k in keys):
                return kbn
        return "99"
    try:
        t = api("/tasks")
    except Exception:
        return 0
    limit = int(os.environ.get("REPORT_DAILY_LIMIT", "3"))
    mine = [r for r in t.get("reports", []) if r.get("platform") == "hotpepper" and r["store"] == acct["name"]][:limit]
    sent = 0
    for rp in mine:
        pg = None
        try:
            pg = page.context.new_page()  # 口コミごとに新しいページ（メモリ肥大防止）
            pg.on("dialog", lambda d: d.accept())
            open_review_list(pg, unreplied_only=False)
            if not pg.locator(SEL_ITEM).count():
                # 一覧が空 = 掲載自体が終了している可能性（店舗閉店・HP契約終了など）。個別削除と混同しない
                errors.append(f"{acct['name']} 違反報告: 口コミ一覧が空のため判定保留（掲載終了の可能性）"); continue
            ext = rp["review_id"].replace("hp:", "", 1)
            el = None; exhausted = False
            for _ in range(60):  # 全ページ走査（古い口コミは深いページにある）
                el = find_item(pg, ext, rp)
                if el is not None: break
                nxt = pg.get_by_role("link", name=re.compile("次"))
                if not nxt.count(): exhausted = True; break
                nxt.first.click(); pg.wait_for_load_state("networkidle", timeout=60000)
            if el is None:
                if exhausted:
                    # 全ページ見ても掲載されていない = すでに非掲載（削除済み）として確定
                    api("/tasks/done", "POST", {"review_id": rp["review_id"], "kind": "removed"})
                    print(f"{acct['name']} 違反報告: 全ページに掲載なし→削除済みとして確定（{rp['review_id']}）")
                else:
                    errors.append(f"{acct['name']} 違反報告: 対象口コミが見つからない（{rp['review_id']}）")
                continue
            btn = el.locator("input.policyButton")
            if not btn.count():
                api("/tasks/done", "POST", {"review_id": rp["review_id"], "kind": "report"})
                print(f"{acct['name']} 違反報告: 既に再審査中/掲載不可のため完了扱い（{rp['review_id']}）"); continue
            btn.first.click(); pg.wait_for_load_state("networkidle", timeout=60000)
            if "/ccm030/" not in pg.url:
                errors.append(f"{acct['name']} 違反報告: フォームに遷移できず（{rp['review_id']}）"); continue
            pg.locator(f"input[name='reportClaimKbn'][value='{pick_kbn(rp)}']").first.check()
            pg.locator("textarea[name='reason']").fill((rp.get("report_reason", "") or "")[:1400])
            pg.locator("input[type='button'][value='報告する']").first.click()
            pg.wait_for_load_state("networkidle", timeout=60000)
            if "/doComplete" in pg.url or "報告を完了しました" in pg.inner_text("body"):
                api("/tasks/done", "POST", {"review_id": rp["review_id"], "kind": "report"}); sent += 1
            else:
                errors.append(f"{acct['name']} 違反報告: 完了画面を確認できず（{rp['review_id']}）")
        except Exception as e:
            errors.append(f"{acct['name']} 違反報告失敗（{rp['review_id']}）: {str(e)[:120]}")
        finally:
            if pg is not None:
                try: pg.close()
                except Exception: pass
    return sent


def check_removals(page, acct, errors):
    """送信済みの違反報告について、口コミが一覧から消えたか（=削除成功か）を全ページ照合する"""
    try:
        t = api("/tasks")
    except Exception:
        return 0
    mine = {r["review_id"]: r for r in t.get("reported", []) if r["store"] == acct["name"]}
    if not mine:
        return 0
    try:
        open_review_list(page, unreplied_only=False)
    except Exception as e:
        errors.append(f"{acct['name']} 削除照合: 一覧を開けず {str(e)[:80]}"); return 0
    if not page.locator(SEL_ITEM).count():
        # 一覧が0行 = 掲載自体が終了している可能性が高い。全件を「削除成功」と誤記録しない
        errors.append(f"{acct['name']} 削除照合: 口コミ一覧が空のため判定保留（掲載終了の可能性・対象{len(mine)}件）")
        return 0
    remaining = dict(mine)
    exhausted = False
    for _ in range(60):  # 全ページ走査
        for rid, rp in list(remaining.items()):
            ext = rid.replace("hp:", "", 1)
            try:
                if find_item(page, ext, rp) is not None:
                    remaining.pop(rid)
            except Exception:
                pass
        if not remaining:
            break
        nxt = page.get_by_role("link", name=re.compile("次"))
        if not nxt.count():
            exhausted = True; break
        nxt.first.click(); page.wait_for_load_state("networkidle", timeout=60000)
    if not exhausted and remaining:
        # 最終ページまで確認できていないのに「消えた」と断定しない
        errors.append(f"{acct['name']} 削除照合: 全ページを走査しきれず{len(remaining)}件は判定保留")
        return 0
    removed = 0
    for rid in remaining:  # 全ページ見ても存在しない = 非掲載（削除成功）
        try:
            api("/tasks/done", "POST", {"review_id": rid, "kind": "removed"}); removed += 1
        except Exception:
            pass
    return removed


def main():
    fix = os.environ.get("HP_FIX_UNREMOVE", "")  # "店名|sinceISO;店名|"（since空=その店の削除記録すべて）
    if fix:
        for ent in fix.split(";"):
            if not ent.strip(): continue
            store, _, since = ent.partition("|")
            r = api("/tasks/unremove", "POST", {"store": store.strip(), "since": since.strip()})
            print(f"unremove {store.strip()}: {r}")
        return
    accts = accounts()
    if not accts: print("no accounts configured"); return
    try:
        fl = api("/tasks/flush_pending", "POST", {}).get("flushed", 0)
        if fl: print(f"承認待ちの返信{fl}件を自動投稿キューへ移動")
    except Exception:
        pass
    with sync_playwright() as p:
        errors = []
        for acct in accts:
            # 店舗ごとにブラウザを起動し直してメモリ肥大（OOM）を防ぐ
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900}); page = ctx.new_page()
            page.on("dialog", lambda d: d.accept())  # confirm()ダイアログは常に承認（既定のdismissだと投稿がキャンセルされる）
            try:
                login(page, acct)
                if MODE == "recon": recon(page, acct); continue
                open_review_list(page)
                items = scrape(page, acct["name"])
                print(f"{acct['name']} 一覧{page.locator(SEL_ITEM).count()}行 / 抽出{len(items)}件")
                for i in items: i.pop("_hidden", None)
                imported = api("/import", "POST", items).get("imported", 0) if items else 0
                # 自己修復: 未返信一覧に残っているのにDB上「投稿済み/整理済み」の口コミを再キュー
                requeued = 0
                if items:
                    try:
                        requeued = api("/tasks/requeue", "POST", {"review_ids": ["hp:" + i["external_id"] for i in items],
                                                                  "force": bool(os.environ.get("HP_FORCE_REQUEUE"))}).get("requeued", 0)
                        if requeued: print(f"{acct['name']} 再投稿キュー{requeued}件（投稿済み扱いだが未返信一覧に残存）")
                    except Exception:
                        pass
                tasks = [t for t in api("/tasks").get("hotpepper_replies", []) if t["store"] == acct["name"]]
                done = 0; cleaned = 0; vfail = 0
                for tk in tasks:
                    try:
                        open_review_list(page); post_reply(page, tk)
                        api("/tasks/done", "POST", {"review_id": tk["review_id"], "kind": "reply"}); done += 1
                    except Exception as e:
                        if "一覧に該当口コミが見つからない" in str(e):
                            # 既に返信済みの旧ID重複など。キューから外して毎朝のエラーを止める
                            try:
                                api("/tasks/done", "POST", {"review_id": tk["review_id"], "kind": "reply_skipped"}); cleaned += 1
                            except Exception:
                                pass
                        elif "投稿未反映" in str(e):
                            vfail += 1; print("reply failed:", tk["review_id"], e)
                            if vfail == 1: errors.append(f"{acct['name']} 返信投稿が画面に反映されない: {str(e)[:150]}")
                            if vfail >= 3:
                                print(f"{acct['name']} 投稿未反映が{vfail}件連続のため、この店舗の残りをスキップ"); break
                        else:
                            print("reply failed:", tk["review_id"], e); errors.append(f"{acct['name']} 返信失敗: {str(e)[:120]}")
                rep_sent = send_reports_for(page, acct, errors)
                # 削除照合は全ページ走査で重いため、SKIP_REMOVALS=1 のジョブ（2時間おきループ等）では省略し毎朝の本番だけで行う
                removed = 0 if os.environ.get("SKIP_REMOVALS") else check_removals(page, acct, errors)
                print(f"[{acct['name']}] 取り込み{imported}件 / 返信投稿{done}件 / 違反報告{rep_sent}件 / 削除確認{removed}件 / 整理{cleaned}件")
                if removed:
                    notify(f"[削除成功] {acct['name']}: 違反報告した口コミ{removed}件がホットペッパーから非掲載になりました。")
            except Exception as e:
                traceback.print_exc(); errors.append(f"{acct['name']}: {str(e)[:200]}")
                try: api("/recon", "POST", {"at": datetime.datetime.now().isoformat(), "error": f"{acct['name']}: {str(e)[:500]}",
                         "pages": [{"name": "error", "url": page.url, "outline": outline(page, 300), "png": base64.b64encode(page.screenshot(type="jpeg", quality=45, full_page=False)).decode()}]})
                except Exception: pass
            finally:
                ctx.close()
                try: browser.close()
                except Exception: pass
        # --- 承認済みの違反報告を自動送信（ホットペッパー実画面仕様に準拠） ---
        KBN_MAP = [  # policy_clause/理由 → 違反ポリシー区分
            (("体験に基づ", "来店", "利用してい"), "01"), (("無関係", "関係がない", "関係のない"), "02"),
            (("事実ではない", "事実に反", "虚偽"), "03"), (("誹謗", "中傷", "差別", "人権"), "04"),
            (("法律", "条例", "犯罪"), "05"), (("権利", "著作", "肖像", "個人情報", "プライバシー"), "06"),
            (("商業", "宣伝", "営業目的"), "07"), (("衛生"), "08"),
        ]
        def pick_kbn(rp):
            basis = (rp.get("policy_clause", "") or "") + (rp.get("report_reason", "") or "")
            for keys, kbn in KBN_MAP:
                if any(k in basis for k in (keys if isinstance(keys, tuple) else (keys,))):
                    return kbn
            return "99"
        t2 = {}
        try: t2 = api("/tasks") if MODE == "run" else {}
        except Exception: pass
        pend = t2.get("reports_pending_count", 0)
        if pend:
            notify(f"[口コミ管制室] 承認待ちの違反報告候補が{pend}件あります。管理画面で承認/却下してください。")
        g_manual = [r for r in t2.get("reports", []) if r.get("platform") == "google"]
        if g_manual:
            notify(f"[Google] 承認済みの違反報告が{len(g_manual)}件あります。Googleは自動送信非対応のため、クチコミ管理ツールから手動報告してください。")
        if errors: notify("[ホットペッパー] " + " / ".join(errors)); sys.exit(1)

def probe():
    """調査モード: HP_PROBE_IDS="C000001|ラベル1;C000002|ラベル2" のIDに順にログインし、
    口コミ一覧に何件見えるかをログに出す（APIトークン不要・DBには何も書かない）"""
    pairs = [s.split("|", 1) for s in os.environ["HP_PROBE_IDS"].split(";") if s.strip()]
    pw = os.environ["HP_PROBE_PW"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for pid, label in pairs:
            ctx = browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900}); page = ctx.new_page()
            page.on("dialog", lambda d: d.accept())  # confirm()ダイアログは常に承認（既定のdismissだと投稿がキャンセルされる）
            try:
                login(page, {"id": pid.strip(), "pw": pw})
                page.goto(REVIEW_LIST_URL, wait_until="networkidle", timeout=60000)
                all_rows = page.locator(SEL_ITEM).count()
                body = page.inner_text("body")
                m = re.search(r"全\s*([0-9,]+)\s*件", body)
                total = m.group(1) if m else "?"
                unrep = -1
                if page.locator("a[href*='showReportListNoComment']").count():
                    page.locator("a[href*='showReportListNoComment']").first.click(); page.wait_for_load_state("networkidle", timeout=60000)
                    unrep = page.locator(SEL_ITEM).count()
                first = ""
                if page.locator(SEL_ITEM).count():
                    d = page.locator(SEL_ITEM).first.locator("span[id^='contributionDate']")
                    first = d.first.inner_text().strip() if d.count() else ""
                its = scrape(page, label)
                smp = " | ".join(f"★{i['rating']} {i['posted_at']} {i['reviewer'][:6]} 本文{len(i['comment'])}字 ext:{i['external_id'][:20]}" for i in its[:3])
                print(f"[PROBE {label} {pid.strip()}] 一覧{all_rows}行(全{total}件表記) / 未返信{unrep}行 / 抽出{len(its)}件 / 先頭日付:{first} / 例: {smp}")
            except Exception as e:
                print(f"[PROBE {label} {pid.strip()}] 失敗: {str(e)[:150]}")
            finally:
                ctx.close()
        browser.close()

if __name__ == "__main__":
    if os.environ.get("HP_PROBE_IDS"):
        probe()
    else:
        main()
