"""媒体管制室：公開ページの取得とテキスト化。
ログイン不要の公開ページだけを読む（ホットペッパー / ヒトサラ）。管理画面には触れない。
"""
import os, re, time
from html.parser import HTMLParser
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
WAIT = float(os.environ.get("MEDIA_FETCH_WAIT", "1.5"))   # 相手サーバーに負荷をかけない間隔（秒）
MAX_CHARS = int(os.environ.get("MEDIA_PAGE_MAX_CHARS", "60000"))

HP_URL_RE = re.compile(r"^https://www\.hotpepper\.jp/strJ\d{9}/$")
HS_ID_RE = re.compile(r"^\d{10}$")
# コース詳細ページへのリンクだけ本文に残す（AIがコースと詳細URLを結び付けるため）
KEEP_LINK = re.compile(r"(/strJ\d{9}/course_cnod\d+/?$)|(/\d{10}/course\d+\.html$)")
ALLOWED_HOSTS = ("https://www.hotpepper.jp/", "https://hitosara.com/")

_last = [0.0]


def normalize_hp_url(url):
    """入力されたホットペッパーURLを https://www.hotpepper.jp/strJxxxxxxxxx/ の形に揃える。不正なら空文字"""
    m = re.search(r"hotpepper\.jp/(strJ\d{9})", url or "")
    return f"https://www.hotpepper.jp/{m.group(1)}/" if m else ""


def hp_urls(base):
    return {"top": base, "course": base + "course/", "coupon": base + "map/"}


def hs_urls(hs_id):
    b = f"https://hitosara.com/{hs_id}/"
    return {"top": b, "course": b + "course.html", "coupon": b + "coupon.html"}


def get(url):
    """公開ページを1枚取得。決めた2サイト以外は読まない"""
    if not url.startswith(ALLOWED_HOSTS):
        raise ValueError(f"対象外のURLです: {url}")
    gap = WAIT - (time.time() - _last[0])
    if gap > 0:
        time.sleep(gap)
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.5"}, timeout=25)
    finally:
        _last[0] = time.time()
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


_SKIP = {"script", "style", "noscript", "svg", "template", "select", "footer"}
LINK_MARK = re.compile(r"⟦([^⟧]+)⟧")
LINK_PATH = re.compile(r"(/strJ\d{9}/course_cnod\d+/|/\d{10}/course\d+\.html)")
_BLOCK = {"p", "div", "li", "ul", "ol", "tr", "table", "section", "article", "header", "main", "dl", "dt", "dd",
          "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "td", "th", "form", "nav", "aside"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param", "track"}


class _Lin(HTMLParser):
    def __init__(self, skip=_SKIP):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.href, self.skip_tags = [], [], [], skip

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.stack.append(tag)
            return
        if self.stack:
            return
        if tag in _BLOCK:
            self.out.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self.href.append(href if KEEP_LINK.search(href.split("?")[0]) else "")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK and not self.stack:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skip_tags:
            if tag in self.stack:   # 内側に閉じ忘れのタグがあっても、ここでまとめて閉じる（以降の本文を落とさない）
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        if self.stack or tag in _VOID:
            return
        if tag == "a" and self.href:
            href = self.href.pop()
            if href:
                self.out.append(f" ⟦{href.split('?')[0]}⟧ ")
        if tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.stack:
            self.out.append(data)


def _linearize(html, skip):
    p = _Lin(skip)
    p.feed(html)
    lines, prev = [], None
    for raw in "".join(p.out).splitlines():
        line = re.sub(r"[ \t\u3000\xa0]+", " ", raw).strip()
        if line and line != prev:          # 空行と直前の重複行（スマホ用・PC用の二重表示）を落とす
            lines.append(line)
            prev = line
    return "\n".join(lines)


def to_text(html, limit=None):
    """HTMLを「読める文字だけ」に直す。コース詳細へのリンクは ⟦URL⟧ の形で残す"""
    text = _linearize(html, _SKIP)
    if len(text) < 1500 and len(html) > 20000:   # 極端に短いときは、飛ばす範囲を最小限にして読み直す
        text = _linearize(html, {"script", "style"})
    return text[: (limit or MAX_CHARS)]


def link_path(link):
    """リンク表記のゆれ（絶対URL・括弧つき・?以降）を取り除いて、パスだけにする"""
    m = LINK_PATH.search(link or "")
    return m.group(1) if m else ""


def course_links(text):
    """本文に出てくるコース詳細リンクを、出てくる順に重複なく返す"""
    out = []
    for m in LINK_MARK.finditer(text or ""):
        path = link_path(m.group(1))
        if path and path not in out:
            out.append(path)
    return out


def debug_page(url, q=""):
    """読み取りの不具合調査用：元のHTMLと、AIに渡す文字のそれぞれで、語句 q の前後を返す"""
    html = get(url)
    text = to_text(html)
    out = {"url": url, "html_chars": len(html), "text_chars": len(text), "course_links": course_links(text),
           "text_head": text[:1500]}
    if q:
        def around(src, width):
            hits = [m.start() for m in re.finditer(re.escape(q), src)]
            return {"count": len(hits), "contexts": [src[max(0, i - width): i + width] for i in hits[:3]]}
        out["q"] = q
        out["in_html"] = around(html, 700)
        out["in_text"] = around(text, 400)
    return out


def page_text(url, limit=None):
    return to_text(get(url), limit)


def absolute(side, link):
    """⟦…⟧ の中のリンクを絶対URLにする"""
    if link.startswith("http"):
        return link
    host = "https://www.hotpepper.jp" if side == "hp" else "https://hitosara.com"
    return host + (link if link.startswith("/") else "/" + link)


# ---------------------------------------------------------------- ホットペッパーはページの形から直接読む（AIを使わない）
# 2026/10/3 に実際のページで確認した並び:
#   コース名 ⟦詳細リンク⟧ → 説明 → 「コース品数：10品／利用人数：…」 → 「4,500」「円（税込）」 → 「クーポン【…】」… → 「詳細・予約 ⟦同じリンク⟧」
#   クーポンページは  見出し → 【提示条件】… → 【利用条件】… → 【有効期限】… → 「このクーポンが使えるコース」

def hp_courses_from_text(text):
    lines, starts, seen = (text or "").split("\n"), [], set()
    for i, line in enumerate(lines):
        m = re.match(r"^(.*?)\s*⟦([^⟧]+)⟧\s*$", line)
        if not m:
            continue
        title, path = m.group(1).strip(), link_path(m.group(2))
        if title and title != "詳細・予約" and "/course_cnod" in path and path not in seen:
            seen.add(path)
            starts.append((i, path, title))
    out = []
    for n, (i, path, title) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        block = re.split(r"(?m)^詳細・予約", "\n".join(lines[i + 1:end]))[0]
        price = (re.search(r"(?m)^([\d,]+)\s*\n\s*円[（(]税込[）)]", block)
                 or re.search(r"(?m)^([\d,]+)\s*円[（(]税込[）)]\s*$", block))
        count = re.search(r"コース品数[：:]\s*(\d+)\s*品", block)
        out.append({"name": title, "link": path,
                    "price": int(price.group(1).replace(",", "")) if price else None,
                    "items_count": int(count.group(1)) if count else None,
                    "coupons": [l.strip()[4:].strip() for l in block.split("\n") if l.strip().startswith("クーポン【")]})
    return out


def hp_coupons_from_text(text):
    lines, out, seen = [l.strip() for l in (text or "").split("\n")], [], set()
    for i, line in enumerate(lines):
        if line != "【提示条件】" or i == 0:
            continue
        title, cond, j = lines[i - 1], [], i
        while j < len(lines) and lines[j] != "このクーポンが使えるコース" \
                and not (j > i and j + 1 < len(lines) and lines[j + 1] == "【提示条件】"):
            cond.append(lines[j]); j += 1
        key = re.sub(r"\s+", "", title)
        if title and key not in seen:
            seen.add(key)
            out.append({"title": title, "condition": " ".join(cond)})
    return out
