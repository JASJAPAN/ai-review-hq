"""媒体管制室：公開ページの取得とテキスト化。
ログイン不要の公開ページだけを読む（ホットペッパー / ヒトサラ）。管理画面には触れない。
"""
import os, re, time
from html.parser import HTMLParser
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
WAIT = float(os.environ.get("MEDIA_FETCH_WAIT", "1.5"))   # 相手サーバーに負荷をかけない間隔（秒）
MAX_CHARS = int(os.environ.get("MEDIA_PAGE_MAX_CHARS", "24000"))

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


_SKIP = {"script", "style", "noscript", "svg", "iframe", "template", "select", "footer"}
_BLOCK = {"p", "div", "li", "ul", "ol", "tr", "table", "section", "article", "header", "main", "dl", "dt", "dd",
          "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "td", "th", "form", "nav", "aside"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param", "track"}


class _Lin(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.href = [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in _BLOCK:
            self.out.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self.href.append(href if KEEP_LINK.search(href.split("?")[0]) else "")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK and not self.skip:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in _VOID:
            return
        if tag == "a" and self.href:
            href = self.href.pop()
            if href:
                self.out.append(f" ⟦{href.split('?')[0]}⟧ ")
        if tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def to_text(html, limit=None):
    """HTMLを「読める文字だけ」に直す。コース詳細へのリンクは ⟦URL⟧ の形で残す"""
    p = _Lin()
    p.feed(html)
    lines, prev = [], None
    for raw in "".join(p.out).splitlines():
        line = re.sub(r"[ \t\u3000\xa0]+", " ", raw).strip()
        if line and line != prev:          # 空行と直前の重複行（スマホ用・PC用の二重表示）を落とす
            lines.append(line)
            prev = line
    return "\n".join(lines)[: (limit or MAX_CHARS)]


def page_text(url, limit=None):
    return to_text(get(url), limit)


def absolute(side, link):
    """⟦…⟧ の中のリンクを絶対URLにする"""
    if link.startswith("http"):
        return link
    host = "https://www.hotpepper.jp" if side == "hp" else "https://hitosara.com"
    return host + (link if link.startswith("/") else "/" + link)
