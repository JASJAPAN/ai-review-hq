"""ヒトサラ管理画面 無人ワーカー（Render Cron / Docker）
媒体管制室（/admin/media）で人が承認した作業だけを、ヒトサラの管理画面に反映する。

HS_MODE:
  recon … ログインして主要画面を記録するだけ（何も入力しない）
  dry   … 承認済みの作業を「保存・登録の直前」まで進めて画面を記録する（保存・登録・公開はしない）★初期値
  run   … 承認済みの作業を実際に反映する
店舗ごとのログイン: HS_1_ID / HS_1_PW, HS_2_ID / HS_2_PW ...（IDは加盟店コード＝ヒトサラの店舗番号10桁）

対応している作業（2026年10月の画面調査にもとづく）:
  intro_catch / intro_text … オーナー管理「お店の紹介文」を保存 → その画面だけ公開
  plan_name / plan_price / plan_off … 即時予約管理「プラン管理」の掲載中プランを編集 → 確認 → 登録

安全のための決まり:
  ・「全ての情報を公開」は使わない。公開は自分が保存した画面（manager指定）だけ
  ・他の人の編集途中（未反映）が残っている画面には手を付けない
  ・削除は一切しない。確認ダイアログは決めた文言のものだけOKする
  ・確認画面に新しい値が出ていなければ登録しない。想定と違う画面になったら止めて画面を記録する
"""
import base64, datetime, os, re, sys, traceback, unicodedata
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = os.environ.get("KUCHIKOMI_BASE", "https://kuchikomi-hq.onrender.com").rstrip("/") + "/admin/media"
TOKEN = os.environ.get("REPLY_RUN_TOKEN", "").strip()   # 貼り付け時に入った前後の空白・改行は無視する
MODE = os.environ.get("HS_MODE", "dry")
OWNER = os.environ.get("HS_OWNER_BASE", "https://owner.hitosara.com").rstrip("/")
RESERVE = os.environ.get("HS_RESERVE_BASE", "https://reserve.hitosara.com").rstrip("/")
MANAGER_INTRO = "OwnerIntroduction"
ALLOW_CONFIRM = ("反映してもよろしいですか", "この内容で登録します")   # これ以外の確認ダイアログはキャンセルする
NAV = 60000
# メモリ512MBの枠で動かすための設定（2026/10/3 に Out of memory で落ちた対策）
LAUNCH_ARGS = ["--disable-dev-shm-usage", "--disable-gpu", "--no-zygote", "--disable-extensions",
               "--disable-background-networking", "--disable-sync", "--mute-audio", "--renderer-process-limit=1",
               "--disable-features=IsolateOrigins,site-per-process,Translate,BackForwardCache",
               "--enable-features=NetworkServiceInProcess", "--js-flags=--max-old-space-size=160"]
BLOCK_TYPES = {"image", "media", "font"}   # 画像・動画・フォントは読み込まない（入力と保存には不要）


def say(*a):
    print(*a, flush=True)   # 途中で止まっても、どこまで進んだかがログに残るように


class Abort(Exception):
    """作業を安全側に中止する（理由を管理画面に返す）"""


def accounts():
    out = {}
    for i in range(1, 41):
        u, p = os.environ.get(f"HS_{i}_ID"), os.environ.get(f"HS_{i}_PW")
        if u and p:
            out[u.strip()] = p
    return out


def api(path, method="GET", body=None):
    r = requests.request(method, BASE + path, headers={"X-Run-Token": TOKEN, "Content-Type": "application/json"},
                         json=body, timeout=120)
    if r.status_code == 403:
        raise SystemExit(f"403: 合言葉（REPLY_RUN_TOKEN）が kuchikomi-hq 側と一致しません。"
                         f"このCronに入っている値は {len(TOKEN)} 文字です。kuchikomi-hq の値と同じか確認してください。")
    r.raise_for_status()
    return r.json() if r.text.startswith(("{", "[")) else r.text


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[\s\u3000]+", "", s).replace("〜", "~")


OUTLINE_JS = """(limit) => { const lines=[]; const walk=(el,d)=>{ if(d>9||lines.length>limit) return;
  const tag=el.tagName.toLowerCase(); if(['script','style','svg','noscript','link','meta'].includes(tag)) return;
  const cls=(typeof el.className==='string'&&el.className.trim())?'.'+el.className.trim().split(/\\s+/).slice(0,3).join('.'):'';
  const id=el.id?'#'+el.id:''; const name=el.getAttribute('name')?`[name=${el.getAttribute('name')}]`:'';
  const own=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).filter(Boolean).join(' ').slice(0,50);
  lines.push('  '.repeat(d)+tag+id+cls+name+(own?`  「${own}」`:'')); [...el.children].forEach(c=>walk(c,d+1)); };
  walk(document.body,0); return lines.join('\\n'); }"""


class Session:
    """1店舗ぶんのブラウザ操作"""

    def __init__(self, browser, hs_id, pw, pages, log):
        self.hs_id, self.pw, self.pages, self.log = hs_id, pw, pages, log
        self.ctx = browser.new_context(locale="ja-JP", viewport={"width": 1100, "height": 800}, service_workers="block")
        self.ctx.route("**/*", lambda route: route.abort() if route.request.resource_type in BLOCK_TYPES else route.continue_())
        self.page = self.ctx.new_page()
        self.dialogs = []
        self.page.on("dialog", self._on_dialog)
        self.in_reserve = False

    def _on_dialog(self, d):
        self.dialogs.append(d.message)
        if d.type == "confirm" and not any(k in d.message for k in ALLOW_CONFIRM):
            d.dismiss()   # 想定外の確認（削除など）は必ずキャンセル
        else:
            d.accept()

    def close(self):
        self.ctx.close()

    def snap(self, name):
        if len(self.pages) >= 14:   # 記録が大きくなりすぎないように
            return
        try:
            png = base64.b64encode(self.page.screenshot(type="jpeg", quality=45, full_page=False)).decode()
            outline = self.page.evaluate(OUTLINE_JS, 250)
        except Exception as e:
            png, outline = "", f"(記録失敗: {e})"
        self.pages.append({"name": f"{self.hs_id} {name}", "url": self.page.url, "outline": outline, "png": png})
        say(f"  記録: {name} {self.page.url}")

    def goto(self, url):
        say(f"  開く: {url}")
        self.page.goto(url, wait_until="domcontentloaded", timeout=NAV)

    def click_nav(self, locator):
        """1回だけ押して、次の画面を待つ（二重送信を防ぐ）。画面が変わらなければ False"""
        try:
            with self.page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                locator.click()
            return True
        except PWTimeout:
            return False

    # ---------------------------------------------------------- ログイン
    def login(self):
        p = self.page
        self.goto(OWNER + "/se/")
        if p.locator("input[name=user]").count():
            p.fill("input[name=user]", self.hs_id)
            p.fill("input[name=password]", self.pw)
            form = p.locator("form", has=p.locator("input[name=user]"))
            self.click_nav(form.locator("input[type=submit]").first)
        if p.locator("input[name=user]").count() or p.locator("input[type=password]").count():
            self.snap("login_failed")
            raise Abort("オーナー管理にログインできませんでした（ID・パスワードの誤り、または追加の認証）")

    def to_reserve(self):
        if self.in_reserve:
            return
        p = self.page
        self.goto(OWNER + "/se/reserveBridge.php?type=11")     # オーナー管理から乗り入れ
        self.goto(RESERVE + "/admin/plan/")
        if "/admin/auth" in p.url or p.locator("input[name=login_id]").count():   # 乗り入れできなければ直接ログインを試す
            p.fill("input[name=login_id]", self.hs_id)
            p.fill("input[name=password]", self.pw)
            self.click_nav(p.locator("form", has=p.locator("input[name=login_id]")).locator("input[type=submit]").first)
            self.goto(RESERVE + "/admin/plan/")
        if "/admin/auth" in p.url or p.locator("input[name=login_id]").count():
            self.snap("reserve_login_failed")
            raise Abort("即時予約管理に入れませんでした")
        self.in_reserve = True

    # ---------------------------------------------------------- オーナー管理：お店の紹介文
    def _pub_row(self, manager):
        row = self.page.locator("tr", has=self.page.locator(f"a[rel='{manager}']"))
        if row.count() != 1:
            self.snap("publish_unexpected")
            raise Abort("公開画面で該当の行を特定できませんでした")
        return row

    def intro(self, catch=None, text=None):
        """キャッチコピー・紹介文を入力 → 保存 → お店の紹介文だけ公開"""
        p = self.page
        self.goto(OWNER + "/se/publish.php")
        if self._pub_row(MANAGER_INTRO).locator("td.status.unreflect").count():
            raise Abort("お店の紹介文に未反映の編集が残っています。誰かの編集途中の可能性があるため、手を付けていません")
        self.goto(OWNER + "/se/introduction/edit.php")
        if not p.locator("#intro40").count() or not p.locator("#intro300").count():
            self.snap("intro_unexpected")
            raise Abort("お店の紹介文の編集画面が想定と違います")
        if catch is not None:
            p.fill("#intro40", catch)
        if text is not None:
            p.fill("#intro300", text)
        self.snap("intro_edit")
        if MODE != "run":
            return "入力まで確認しました（保存・公開はしていません）"
        n = len(self.dialogs)
        if not self.click_nav(p.locator("#edit")):
            msg = " ".join(self.dialogs[n:]).replace("\n", " ") or "保存を押しても画面が切り替わりません"
            self.snap("intro_save_failed")
            raise Abort("保存できませんでした：" + msg[:200])
        self.goto(OWNER + "/se/publish.php")
        row = self._pub_row(MANAGER_INTRO)
        if not row.locator("td.status.unreflect").count():
            self.snap("intro_not_saved")
            raise Abort("保存後も「未反映」になっていません（内容が同じか、保存に失敗した可能性）")
        n = len(self.dialogs)
        row.locator(f"a[rel='{MANAGER_INTRO}']").filter(has_text="公開").first.click()   # この行だけ公開（全体公開は使わない）
        for _ in range(120):
            got = " ".join(self.dialogs[n:])
            if "公開が完了" in got or "エラー" in got or "中断" in got:
                break
            p.wait_for_timeout(250)
        got = " ".join(self.dialogs[n:])
        if "公開が完了" not in got:
            self.snap("intro_publish_failed")
            raise Abort("保存はできましたが、公開を確認できませんでした（未反映のまま残っています）：" + got[:150])
        return "保存して公開しました"

    # ---------------------------------------------------------- 即時予約管理：プラン
    def _find_plan(self, ref):
        """掲載中のプラン一覧から、名前が一致するプランを1件だけ特定する"""
        p = self.page
        self.goto(RESERVE + "/admin/plan/")
        links = p.locator("#list-plan-publish tr.list-item a[href*='/admin/plan/register/']")
        hits = []
        for i in range(links.count()):
            a = links.nth(i)
            if norm(a.inner_text()) == norm(ref):
                m = re.search(r"/admin/plan/register/(\d+)", a.get_attribute("href") or "")
                if m:
                    hits.append(m.group(1))
        hits = sorted(set(hits))
        if len(hits) != 1:
            self.snap("plan_list")
            raise Abort(f"掲載中のプラン一覧で「{ref[:30]}…」が{len(hits)}件でした（1件に特定できないため中止）")
        return hits[0]

    def plan(self, task):
        """掲載中プランの名称・販売価格・掲載状態のどれか1つを変える → 確認画面 → 登録"""
        p, kind, new = self.page, task["type"], task.get("text") or ""
        self.to_reserve()
        plan_id = self._find_plan(task["ref"])
        self.goto(f"{RESERVE}/admin/plan/register/{plan_id}")
        form = p.locator("form.validate")
        if form.count() != 1 or not p.locator("#place_name").count():
            self.snap("plan_edit_unexpected")
            raise Abort("プランの編集画面が想定と違います")
        if norm(p.input_value("#place_name")) != norm(task["ref"]):
            raise Abort("開いたプランの名称が、直す対象と一致しません")
        if kind == "plan_name":
            p.fill("#place_name", new)
            expect = norm(new)
        elif kind == "plan_price":
            price = int(new)
            if p.locator("#no-discount").is_checked():       # 「定価と同じ」なら定価を変える
                p.fill("#plan_price", str(price))
            else:
                if int(re.sub(r"\D", "", p.input_value("#plan_price")) or 0) < price:
                    p.fill("#plan_price", str(price))        # 販売価格は定価以下でないと登録できない
                p.fill("#discounted_price", str(price))
            p.locator("#place_name").focus()                 # 入力後の画面側の処理を走らせる
            if int(re.sub(r"\D", "", p.input_value("#discounted_price")) or 0) != price:
                self.snap("plan_price_unexpected")
                raise Abort("販売価格の欄に新しい価格が入りませんでした")
            expect = f"{price:,}"
        elif kind == "plan_off":
            p.check("#plan_unavailable")
            expect = "非掲載"
        else:
            raise Abort(f"未対応の作業です: {kind}")
        if not self.click_nav(form.locator("input[name=confirm]")) or "/admin/plan/confirm" not in p.url:
            errs = " ".join(p.locator("div.formError:visible").all_inner_texts())[:200]
            self.snap("plan_input_error")
            raise Abort("確認画面に進めませんでした：" + (errs or "入力エラー"))
        self.snap("plan_confirm")
        body = p.locator("body").inner_text()
        shown = norm(body) if kind == "plan_name" else body
        if expect not in shown and not (kind == "plan_price" and str(int(new)) in body):
            raise Abort("確認画面に新しい値が表示されていないため、登録していません")
        if MODE != "run":
            return "確認画面まで進めました（登録はしていません）"
        btn = p.locator("input[type=submit]:not([name=back]), button[type=submit]:not([name=back])")
        if btn.count() != 1:
            raise Abort("確認画面の登録ボタンを特定できませんでした（画面記録を確認してください）")
        self.click_nav(btn.first)
        if not p.locator(".notification").filter(has_text=re.compile("完了")).count():
            self.snap("plan_result_unexpected")
            raise Abort("登録ボタンは押しましたが、完了画面を確認できませんでした。ヒトサラ側で結果を確認してください")
        return "登録しました"

    # ---------------------------------------------------------- 記録だけ
    def recon(self):
        for name, url in (("owner_publish", OWNER + "/se/publish.php"),
                          ("owner_intro_edit", OWNER + "/se/introduction/edit.php")):
            self.goto(url); self.snap(name)
        self.to_reserve(); self.snap("reserve_plan_list")
        first = self.page.locator("#list-plan-publish tr.list-item a[href*='/admin/plan/register/']").first
        if first.count():
            self.goto(RESERVE + re.search(r"/admin/plan/register/\d+", first.get_attribute("href")).group(0))
            self.snap("reserve_plan_edit")


def process_store(browser, hs_id, pw, tasks, pages, log):
    """1店舗の作業を実行し、[(task, ok, message)] を返す"""
    results, s = [], Session(browser, hs_id, pw, pages, log)
    try:
        say(f"{hs_id}: ログイン開始")
        s.login()
        say(f"{hs_id}: ログイン成功")
        if MODE == "recon":
            s.recon(); log.append(f"{hs_id}：ログイン成功・画面を記録"); return results
        intro_tasks = [t for t in tasks if t["type"] in ("intro_catch", "intro_text")]
        if intro_tasks:   # キャッチコピーと紹介文は同じ画面なので1回の保存にまとめる
            try:
                msg = s.intro(catch=next((t["text"] for t in intro_tasks if t["type"] == "intro_catch"), None),
                              text=next((t["text"] for t in intro_tasks if t["type"] == "intro_text"), None))
                results += [(t, True, msg) for t in intro_tasks]
            except Abort as e:
                results += [(t, False, str(e)) for t in intro_tasks]
        for t in [t for t in tasks if t["type"].startswith("plan_")]:
            try:
                results.append((t, True, s.plan(t)))
            except Abort as e:
                results.append((t, False, str(e)))
    except Abort as e:
        done = {id(r[0]) for r in results}
        results += [(t, False, str(e)) for t in tasks if id(t) not in done]
        log.append(f"{hs_id}：{e}")
    except Exception as e:   # 想定外の例外。画面を記録して、この店舗の残りを失敗扱いにする
        traceback.print_exc(); s.snap("error")
        done = {id(r[0]) for r in results}
        results += [(t, False, f"想定外のエラー: {str(e)[:150]}") for t in tasks if id(t) not in done]
        log.append(f"{hs_id}：想定外のエラー {str(e)[:150]}")
    finally:
        s.close()
    return results


def main():
    accts = accounts()
    tasks = api("/tasks").get("tasks", [])   # 最初に合言葉を確かめる（違っていればブラウザを起動する前に止まる）
    if MODE == "recon":
        tasks = []
    by_store = {}
    for t in tasks:
        by_store.setdefault(t["hs_id"], []).append(t)
    targets = list(accts) if MODE == "recon" else list(by_store)
    say(f"mode={MODE} 対象店舗={targets} 作業={len(tasks)}件")
    if not targets:
        return
    pages, log, results = [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
        say("ブラウザ起動")
        for hs_id in targets:
            if hs_id not in accts:
                results += [(t, False, f"ログイン情報が未設定です（HS_n_ID / HS_n_PW に {hs_id} を登録してください）")
                            for t in by_store.get(hs_id, [])]
                continue
            results += process_store(browser, hs_id, accts[hs_id], by_store.get(hs_id, []), pages, log)
        browser.close()
    for line in log:
        say(line)
    for t, ok, msg in results:
        say(f"{t['store']} {t['type']} {'OK' if ok else '中止'} {msg}")
        log.append(f"{t['store']}｜{t['type']}｜{'OK' if ok else '中止'}｜{msg}")
        if MODE == "run":
            api("/tasks/done", "POST", {"id": t["id"], "ok": ok, "message": msg})
    api("/recon", "POST", {"at": datetime.datetime.now().isoformat(), "mode": MODE, "log": log, "pages": pages})
    if MODE == "run":
        if any(ok for _, ok, _ in results):
            try: api("/run", "POST")     # 反映できたら公開ページを読み直して、直ったかを確認する
            except Exception: pass
    try: api("/notify", "POST", {"text": f"[媒体管制室] ヒトサラ ワーカー（{MODE}）\n" + "\n".join(log)})
    except Exception: pass
    if any(not ok for _, ok, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
