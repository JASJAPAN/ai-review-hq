"""媒体管制室：ホットペッパーとヒトサラのズレを機械的に洗い出す（AIは使わない）。
入力先と文字数制限は、2026年10月にヒトサラ管理画面（うみどり・ライトプラン）で確認した値。
"""
import re, unicodedata
from difflib import SequenceMatcher

# field -> (ヒトサラの入力先, 文字数制限)
TARGETS = {
    "catch":       ("オーナー管理 ＞ アピール ＞ お店の紹介文 ＞ キャッチコピー", 40),
    "intro":       ("オーナー管理 ＞ アピール ＞ お店の紹介文 ＞ 紹介文", 300),
    "plan_name":   ("即時予約管理 ＞ プラン管理 ＞ プラン名称", 50),
    "plan_price":  ("即時予約管理 ＞ プラン管理 ＞ 価格（定価・販売価格）", None),
    "plan_desc":   ("即時予約管理 ＞ プラン管理 ＞ 説明文", 500),
    "course_menu": ("即時予約管理 ＞ コース管理 ＞ コースメニュー（内容）", 500),
    "drink_menu":  ("即時予約管理 ＞ プラン管理 ＞ 飲み放題メニュー", 500),
    "plan_off":    ("即時予約管理 ＞ プラン管理 ＞ 掲載状態", None),
    "coupon":      ("オーナー管理 ＞ クーポンの登録 ＞ クーポン内容", 50),
    "budget":      ("オーナー管理 ＞ 平均予算", None),
    "hours":       ("オーナー管理 ＞ 定休日・営業時間", None),
    "name":        ("サポートデスクへ依頼（管理画面では変更できない）", None),
    "hp_side":     ("ホットペッパー側の修正（ヒトサラではない）", None),
}
# 即時予約を使っていない店は、コースをオーナー管理側で登録する
OWNER_COURSE = {
    "plan_name":   ("オーナー管理 ＞ メニュー ＞ コースメニュー ＞ コース名", 50),
    "plan_price":  ("オーナー管理 ＞ メニュー ＞ コースメニュー ＞ 価格", None),
    "plan_desc":   ("オーナー管理 ＞ メニュー ＞ コースメニュー ＞ コースのキャッチ", 50),
    "course_menu": ("オーナー管理 ＞ メニュー ＞ コースメニュー ＞ コース内容", 500),
    "drink_menu":  ("オーナー管理 ＞ メニュー ＞ コースメニュー ＞ 飲み放題メニュー", 500),
    "plan_off":    ("オーナー管理 ＞ メニュー ＞ コースメニュー ＞ 表示期間", None),
}
LEVEL_LABEL = {"high": "要対応", "mid": "確認", "low": "文言の差", "info": "参考"}
LEVEL_ORDER = {"high": 0, "mid": 1, "low": 2, "info": 3}


def side_of(field, use_reserve=True):
    """その欄がどちらの管理画面にあるか（文字数の数え方が違う）"""
    return "reserve" if target(field, use_reserve)[0].startswith("即時予約管理") else "owner"


def hs_len(text, side="owner"):
    """ヒトサラ管理画面と同じ数え方：全角1・半角0.5。
    半角カナだけはオーナー管理が1文字、即時予約管理が0.5文字（2026年10月の画面JSで確認）。改行は1文字として数える"""
    n = 0.0
    for ch in text or "":
        if ch in "\r":
            continue
        w = unicodedata.east_asian_width(ch)
        if ch == "\n":
            n += 1
        elif w == "H":
            n += 0.5 if side == "reserve" else 1
        elif w in ("Na", "N"):
            n += 0.5
        else:
            n += 1
    return n


def target(field, use_reserve=True):
    if not use_reserve and field in OWNER_COURSE:
        return OWNER_COURSE[field]
    return TARGETS[field]


def norm(s):
    """見た目だけの違い（全角半角・空白・記号ゆれ）を無視するための正規化"""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    return s.replace("〜", "~")   # 波ダッシュ（NFKCで変換されない方）も ~ に揃える


def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def nabe(name):
    n = norm(name)
    if re.search(r"鍋(有|あり|付)", n):
        return "有"
    if re.search(r"鍋(無|なし)", n):
        return "無"
    return ""


def count_in_name(name):
    m = re.search(r"(\d+)品", unicodedata.normalize("NFKC", name or ""))
    return int(m.group(1)) if m else None


def yen(n):
    return "価格なし" if n is None else f"{int(n):,}円"


def _item(level, kind, title, hp="", hs="", field="", note="", ref=""):
    """ref はヒトサラ側のプラン名。自動書き換えのとき、どのプランを直すかの目印に使う"""
    return {"level": level, "kind": kind, "title": title, "hp": hp or "", "hs": hs or "",
            "field": field, "note": note, "ref": ref or ""}


# ---------------------------------------------------------------- コース

def match_courses(hp, hs):
    """コース同士を、名前の近さ＋価格＋鍋あり/なしで1対1に結び付ける"""
    cand = []
    for i, a in enumerate(hp):
        for j, b in enumerate(hs):
            s = sim(a.get("name"), b.get("name"))
            same_price = a.get("price") is not None and a.get("price") == b.get("price")
            same_nabe = nabe(a.get("name")) == nabe(b.get("name"))
            if s >= 0.6 or (same_price and same_nabe and s >= 0.4):
                cand.append((s + (0.25 if same_price else 0) + (0.1 if same_nabe else -0.2), i, j))
    pairs, used_i, used_j = [], set(), set()
    for _, i, j in sorted(cand, reverse=True):
        if i not in used_i and j not in used_j:
            pairs.append((i, j)); used_i.add(i); used_j.add(j)
    return (sorted(pairs),
            [i for i in range(len(hp)) if i not in used_i],
            [j for j in range(len(hs)) if j not in used_j])


def diff_courses(hp, hs):
    items = []
    pairs, only_hp, only_hs = match_courses(hp, hs)
    for i, j in pairs:
        a, b = hp[i], hs[j]
        if a.get("price") != b.get("price"):
            items.append(_item("high", "price", f"料金が違う：{a['name']}",
                               yen(a.get("price")), yen(b.get("price")), "plan_price", ref=b.get("name")))
        if norm(a.get("name")) != norm(b.get("name")):
            tag = yen(a.get("price")) + (f"・鍋{nabe(a.get('name'))}" if nabe(a.get("name")) else "")
            items.append(_item("low", "course_name", f"コース名の文言が違う（{tag}）",
                               a.get("name"), b.get("name"), "plan_name", ref=b.get("name")))
    for i in only_hp:
        a = hp[i]
        items.append(_item("high", "course_missing", f"ヒトサラに無いコース：{a['name']}",
                           f"{a['name']}／{yen(a.get('price'))}", "", "plan_name",
                           note=a.get("link") or ""))
    for j in only_hs:
        b = hs[j]
        items.append(_item("mid", "course_extra", f"ヒトサラにだけあるコース：{b['name']}",
                           "", f"{b['name']}／{yen(b.get('price'))}", "plan_off",
                           note="ホットペッパーに無いプランです。終了したコースなら非掲載にしてください。", ref=b.get("name")))
    for a in hp:   # ホットペッパーの中での表記ゆれ（タイトルの品数と品数欄）
        n1, n2 = count_in_name(a.get("name")), a.get("items_count")
        if n1 and n2 and n1 != n2:
            items.append(_item("info", "hp_count", f"ホットペッパー内の品数が食い違い：{a['name']}",
                               f"タイトルは{n1}品／品数欄は{n2}品", "", "hp_side"))
    return items


# ---------------------------------------------------------------- 基本情報

def _budget_range(s):
    nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", unicodedata.normalize("NFKC", s or ""))]
    nums = [n for n in nums if n >= 100]
    return (min(nums), max(nums)) if nums else None


def _hours(s):
    """最初の「開店～閉店」を (開店分, 閉店分) にする。翌0:00 と 24:00 と 00:00 は同じ扱い"""
    t = unicodedata.normalize("NFKC", s or "")
    m = re.search(r"(\d{1,2}):(\d{2})\s*[~\-ー―–]\s*(翌)?\s*(\d{1,2}):(\d{2})", t.replace("〜", "~"))
    if not m:
        return None
    o = int(m.group(1)) * 60 + int(m.group(2))
    c = int(m.group(4)) * 60 + int(m.group(5))
    if m.group(3) or c <= o:
        c += 24 * 60
    return o, c


def diff_basics(hp, hs):
    items = []
    if hp.get("name") and hs.get("name") and norm(hp["name"]) != norm(hs["name"]):
        items.append(_item("info", "name", "店名が違う", hp["name"], hs["name"], "name"))
    if hp.get("catch") and sim(hp.get("catch"), hs.get("catch")) < 0.8:
        items.append(_item("low", "catch", "キャッチコピーが違う", hp["catch"], hs.get("catch"), "catch"))
    if hp.get("intro") and sim(hp.get("intro"), hs.get("intro")) < 0.6:
        items.append(_item("low", "intro", "紹介文が違う", hp["intro"], hs.get("intro"), "intro"))
    a, b = _budget_range(hp.get("budget")), _budget_range(hs.get("budget"))
    if a and b and (b[1] < a[0] or b[0] > a[1]):
        items.append(_item("mid", "budget", "平均予算が合っていない", hp.get("budget"), hs.get("budget"), "budget"))
    a, b = _hours(hp.get("hours")), _hours(hs.get("hours"))
    if a and b and a != b:
        items.append(_item("high", "hours", "営業時間が違う", hp.get("hours"), hs.get("hours"), "hours"))
    return items


# ---------------------------------------------------------------- クーポン

def diff_coupons(hp, hs):
    items, used = [], set()
    for a in hp:
        best, bj = 0, None
        for j, b in enumerate(hs):
            s = sim(a.get("title"), b.get("title"))
            if j not in used and s > best:
                best, bj = s, j
        if bj is not None and best >= 0.6:
            used.add(bj)
            if norm(a.get("title")) != norm(hs[bj].get("title")):
                items.append(_item("low", "coupon_text", "クーポンの文言が違う",
                                   a.get("title"), hs[bj].get("title"), "coupon"))
        else:
            items.append(_item("mid", "coupon_missing", "ヒトサラに無いクーポン",
                               a.get("title"), "", "coupon", note=a.get("condition") or ""))
    for j, b in enumerate(hs):
        if j not in used:
            items.append(_item("low", "coupon_extra", "ヒトサラにだけあるクーポン", "", b.get("title"), "coupon",
                               note="ホットペッパーに無いクーポンです。終了分なら削除してください。"))
    return items


def diff_all(hp, hs):
    items = (diff_basics(hp.get("basics", {}), hs.get("basics", {}))
             + diff_courses(hp.get("courses", []), hs.get("courses", []))
             + diff_coupons(hp.get("coupons", []), hs.get("coupons", [])))
    return sorted(items, key=lambda x: LEVEL_ORDER[x["level"]])
