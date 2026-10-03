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
import base64, datetime, faulthandler, os, re, sys, traceback, unicodedata
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


STEP_LIMIT = int(os.environ.get("HS_STEP_LIMIT", "150"))   # 1つの操作がこの秒数を超えたら、止まった場所を書き出して強制終了
WATCHDOG = False   # 本番実行（python hs_worker.py）のときだけ有効にする


def watchdog():
    """止まったまま動き続けるのを防ぐ（2026/10/3：ログイン画面を開いたところで無反応になった対策）。
    呼ばれるたびに残り時間を巻き戻す。時間切れになると、止まっている場所をログに出して終了する"""
    if WATCHDOG:
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(STEP_LIMIT, exit=True)


def say(*a):
    print(*a, flush=True)   # 途中で止まっても、どこまで進んだかがログに残るように
    watchdog()


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


OUTLINE_JS = """(limit) => { const lines=[]; const walk=(el,d)=>{ if(d>16||lines.length>limit) return;
  const tag=el.tagName.toLowerCase(); if(['script','style','svg','noscript','link','meta','br','option'].includes(tag)) return;
  const cls=(typeof el.className==='string'&&el.className.trim())?'.'+el.className.trim().split(/\\s+/).slice(0,3).join('.'):'';
  const id=el.id?'#'+el.id:''; const name=el.getAttribute('name')?`[name=${el.getAttribute('name')}]`:'';
  const own=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).filter(Boolean).join(' ').slice(0,50);
  lines.push('  '.repeat(d)+tag+id+cls+name+(own?`  「${own}」`:'')); [...el.children].forEach(c=>walk(c,d+1)); };
  walk(document.body,0); return lines.join('\\n'); }"""


CHECKS = {
    "owner_publish": ["a[rel='OwnerIntroduction']", "tr:has(a[rel='OwnerIntroduction']) td.status", "a[rel='OwnerSeat']", "a[rel='all']"],
    "owner_intro_list": ["a.edit", "div.introduce_field a.edit"],
    "owner_intro_edit": ["form#introduction", "#intro40", "#intro300", "form#introduction input[name=tenpo_seq]",
                         "form#introduction input[name=update_date]", "#edit", "#cancel"],
    "reserve_plan_list": ["#list-plan-publish", "#list-plan-publish tr.list-item",
                          "#list-plan-publish tr.list-item a[href*='/admin/plan/register/']"],
    "reserve_plan_edit": ["form.validate", "#place_name", "#plan_price", "#discounted_price", "#no-discount",
                          "#plan_available", "#plan_unavailable", "form.validate input[name=confirm]"],
    "intro_edit": ["form#introduction", "#intro40", "#intro300", "form#introduction input[name=tenpo_seq]",
                   "form#introduction input[name=update_date]", "#edit"],
    "plan_confirm": ["input[type=submit]", "button[type=submit]", "input[name=back]", ".notification", "div.formError"],
}
CHECK_JS = """(el) => { const t=(el.type||'').toLowerCase(); const secret=['password','hidden'].includes(t);
  return {tag: el.tagName.toLowerCase(), type: t, name: el.getAttribute('name')||'', cls: (typeof el.className==='string'?el.className:''),
          href: el.getAttribute('href')||'', checked: !!el.checked, disabled: !!el.disabled, readonly: !!el.readOnly,
          text: secret ? (el.value ? '（値あり）' : '（値なし）') : (el.innerText||'').trim().slice(0,40),
          value: secret?'':String(el.value||'').slice(0,40),
          value_len: secret?0:String(el.value||'').length}; }"""


BTN_JS = """() => [...document.querySelectorAll('input[type=submit], button[type=submit]')].map(e => ({
  name: e.getAttribute('name')||'', label: String(e.value||e.innerText||'').trim().slice(0,30),
  in_form: !!(e.form && e.form.querySelector('input[name=back]')),
  action: e.form ? (e.form.getAttribute('action')||'') : '', shown: !!(e.offsetWidth||e.offsetHeight) }))"""


class Session:
    """1店舗ぶんのブラウザ操作"""

    def __init__(self, browser, hs_id, pw, pages, log):
        self.hs_id, self.pw, self.pages, self.log = hs_id, pw, pages, log
        self.ctx = browser.new_context(locale="ja-JP", viewport={"width": 1100, "height": 800}, service_workers="block")
        self.ctx.route("**/*", lambda route: route.abort() if route.request.resource_type in BLOCK_TYPES else route.continue_())
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(30000)
        self.page.set_default_navigation_timeout(NAV)
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
        watchdog()
        if len(self.pages) >= 14:   # 記録が大きくなりすぎないように
            return
        try:
            png = base64.b64encode(self.page.screenshot(type="jpeg", quality=45, full_page=False)).decode()
            outline = self.page.evaluate(OUTLINE_JS, 400)
        except Exception as e:
            png, outline = "", f"(記録失敗: {e})"
        checks = []
        for sel in CHECKS.get(name, []):   # 頼りにしている要素が実際の画面にあるか
            try:
                loc = self.page.locator(sel)
                n = loc.count()
                checks.append({"selector": sel, "count": n, "first": loc.first.evaluate(CHECK_JS) if n else {}})
            except Exception as e:
                checks.append({"selector": sel, "count": -1, "first": {"text": f"確認失敗: {str(e)[:60]}"}})
        self.pages.append({"name": f"{self.hs_id} {name}", "url": self.page.url, "outline": outline, "png": png, "checks": checks})
        say(f"  記録: {name} {self.page.url}")

    def settle(self):
        """画面の部品が出そろうのを少しだけ待つ（固定1.2秒）。
        読み込み完了の合図を待つ方式は、本番で無反応になったため使わない。入力欄の中身は使う側で個別に確かめる"""
        self.page.wait_for_timeout(1200)

    def goto(self, url):
        say(f"  開く: {url}")
        self.page.goto(url, wait_until="domcontentloaded", timeout=NAV)
        self.settle()

    def click_nav(self, locator):
        """1回だけ押して、次の画面を待つ（二重送信を防ぐ）。画面が変わらなければ False"""
        watchdog()
        try:
            with self.page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                locator.click()
        except PWTimeout:
            return False
        self.settle()
        return True

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

    def open_intro_edit(self):
        """お店の紹介文の編集画面を、一覧画面の「編集」リンクから開く。
        URLを直接開くと、文言も内部の管理番号も空のフォームになる（2026/10/3 に本番で確認）。
        空のフォームで保存すると既存の文言を消すおそれがあるので、必ずこの入口を使う。
        戻り値: (今のキャッチコピー, 今の紹介文)"""
        p = self.page
        self.goto(OWNER + "/se/introduction/")
        link = p.locator("div.introduce_field a.edit")
        if link.count() != 1:
            link = p.locator("a.edit")
        if link.count() != 1:
            self.snap("owner_intro_list")
            raise Abort(f"お店の紹介文の一覧画面で「編集」リンクを1つに特定できませんでした（{link.count()}件）")
        if not self.click_nav(link.first) or not p.locator("#intro40").count() or not p.locator("#intro300").count():
            self.snap("intro_unexpected")
            raise Abort("お店の紹介文の編集画面を開けませんでした")
        hidden = [p.locator(f"form#introduction input[name={n}]") for n in ("tenpo_seq", "update_date")]
        if any(h.count() != 1 or not h.first.evaluate("e => e.value") for h in hidden):
            self.snap("owner_intro_edit")
            raise Abort("編集画面に内部の管理番号（tenpo_seq・update_date）が入っていません。"
                        "正しい入口から開けていないため、保存せずに中止しました")
        return p.input_value("#intro40"), p.input_value("#intro300")

    def intro(self, catch=None, text=None, pub_catch="", pub_intro=""):
        """キャッチコピー・紹介文を入力 → 保存 → お店の紹介文だけ公開。
        pub_catch / pub_intro は公開ページに今出ている文言。編集画面が空のまま保存して既存の文言を消さないための照合に使う"""
        p = self.page
        self.goto(OWNER + "/se/publish.php")
        if self._pub_row(MANAGER_INTRO).locator("td.status.unreflect").count():
            raise Abort("お店の紹介文に未反映の編集が残っています。誰かの編集途中の可能性があるため、手を付けていません")
        cur40, cur300 = self.open_intro_edit()
        say(f"  現在の値: キャッチコピー {len(cur40)}文字 / 紹介文 {len(cur300)}文字")
        if (pub_catch and not cur40) or (pub_intro and not cur300):
            self.snap("owner_intro_edit")
            raise Abort("編集画面に現在の文言が入っていません（公開ページには文言があります）。"
                        "このまま保存すると既存の文言が消えるため中止しました")
        if catch is not None:
            p.fill("#intro40", catch)
        if text is not None:
            p.fill("#intro300", text)
        for sel in ("#intro40", "#intro300"):   # 画面の文字数表示は keyup / blur で更新される作り
            p.dispatch_event(sel, "keyup"); p.dispatch_event(sel, "blur")
        self.snap("intro_edit")
        if MODE != "run":
            was = f"変更前のキャッチコピー「{cur40[:30]}」（{len(cur40)}文字）、紹介文 {len(cur300)}文字"
            return "入力まで確認しました（保存・公開はしていません）。" + was
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

    def _register_button(self):
        """確認画面の登録ボタンを1つに特定する。「内容を修正する」（name=back）と同じフォームの中にあり、
        表示されていて、文字に「登録」か「更新」を含むものだけを対象にする。特定できなければ押さない"""
        p = self.page
        buttons = p.evaluate(BTN_JS)
        picks = [i for i, b in enumerate(buttons) if b["in_form"] and b["shown"] and b["name"] != "back"
                 and ("登録" in b["label"] or "更新" in b["label"])]
        if len({(buttons[i]["name"], buttons[i]["label"]) for i in picks}) != 1:
            picks = []   # 種類の違う候補が複数ある、または候補が無い
        desc = " ／ ".join(f"{'★' if picks and i == picks[0] else ''}「{b['label']}」name={b['name'] or '(なし)'}"
                           f"{'' if b['in_form'] else '（別のフォーム）'}{'' if b['shown'] else '（非表示）'}"
                           for i, b in enumerate(buttons))
        line = f"{self.hs_id}：確認画面のボタン {desc}（★が登録で押す対象）"
        say("  " + line); self.log.append(line)
        return p.locator("input[type=submit], button[type=submit]").nth(picks[0]) if picks else None

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
        btn = self._register_button()
        if MODE != "run":
            return "確認画面まで進めました（登録はしていません）" + ("" if btn else "。登録ボタンは特定できていません")
        if btn is None:
            raise Abort("確認画面の登録ボタンを特定できなかったため、登録していません（ログのボタン一覧を確認してください）")
        self.click_nav(btn)
        self.snap("plan_result")
        if not p.locator(".notification").filter(has_text=re.compile("完了")).count():
            self.snap("plan_result_unexpected")
            raise Abort("登録ボタンは押しましたが、完了画面を確認できませんでした。ヒトサラ側で結果を確認してください")
        return "登録しました"

    # ---------------------------------------------------------- 記録だけ
    def recon(self):
        self.goto(OWNER + "/se/publish.php"); self.snap("owner_publish")
        self.goto(OWNER + "/se/introduction/"); self.snap("owner_intro_list")
        try:
            cur40, cur300 = self.open_intro_edit()
            self.log.append(f"{self.hs_id}：紹介文の編集画面 キャッチコピー {len(cur40)}文字 / 紹介文 {len(cur300)}文字")
        except Abort as e:
            self.log.append(f"{self.hs_id}：紹介文の編集画面 {e}")
        self.snap("owner_intro_edit")
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
                              text=next((t["text"] for t in intro_tasks if t["type"] == "intro_text"), None),
                              pub_catch=intro_tasks[0].get("hs_catch", ""), pub_intro=intro_tasks[0].get("hs_intro", ""))
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
    WATCHDOG = True
    watchdog()
    main()
    faulthandler.cancel_dump_traceback_later()
