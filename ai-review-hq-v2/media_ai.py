"""媒体管制室：AIの仕事は2つだけ。
1) ページの文字から、決まった項目を「そのまま」抜き出す（言い換えない）
2) ヒトサラの文字数制限を超えた文言だけを、意味を変えずに縮める
比較（どこがズレているか）は media_diff.py が機械的に行う。

抜き出しは「決まった形のデータで返させる」方式（tool use）を使う。
文章の中にJSONを書かせる方式だと、紹介文の改行などでJSONが壊れることがあるため（2026/10/3 の JSONDecodeError 対策）。
"""
import json, os
import media_fetch as F

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
SIDE = {"hp": "ホットペッパーグルメ", "hs": "ヒトサラ"}

RULE = """ページに書かれている文言を一字一句そのまま抜き出してください。言い換え・要約・補完は禁止です。
ページに無い項目は空文字 "" にしてください（数値の項目は null）。推測で埋めないでください。"""

S = {"type": "string"}
N = {"type": ["integer", "null"]}


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props)}


BASICS = _obj({"name": S, "catch": S, "intro": S, "budget": S, "hours": S, "holiday": S, "seats": S, "access": S})
COURSES = _obj({"courses": {"type": "array", "items": _obj({"name": S, "price": N, "items_count": N, "link": S})}})
COUPONS = _obj({"coupons": {"type": "array", "items": _obj({"title": S, "condition": S})}})
DETAIL = _obj({"name": S, "price": N, "description": S, "menu": S, "drink": S,
               "people": S, "stay": S, "days": S, "deadline": S})


class ExtractError(RuntimeError):
    """AIの読み取りに失敗（どの段階かを管理画面に出すための例外）"""


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client():
    import anthropic
    return anthropic.Anthropic()


def _ask(prompt, max_tokens=4000):
    msg = _client().messages.create(model=MODEL, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def parse_json(text):
    """文章に混ざったJSONを取り出す。前後に説明が付いていても、文字列の中に生の改行があっても読める"""
    text = (text or "").replace("```json", "").replace("```", "").strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ExtractError("AIの返答にデータが含まれていません: " + text[:80])
    try:
        value, _ = json.JSONDecoder(strict=False).raw_decode(text, min(starts))
    except json.JSONDecodeError as e:
        raise ExtractError(f"AIの返答を読み取れません（{e.msg}）: " + text[:80])
    return value


def _extract(prompt, schema, max_tokens):
    """決まった形（schema）のデータで受け取る。途中で切れたら上限を上げて1回やり直す。
    この方式が使えないときだけ、文章からJSONを取り出す方式に切り替える"""
    last = None
    for attempt in range(2):
        try:
            msg = _client().messages.create(
                model=MODEL, max_tokens=max_tokens,
                tools=[{"name": "save", "description": "ページから抜き出した内容を保存する", "input_schema": schema}],
                tool_choice={"type": "tool", "name": "save"},
                messages=[{"role": "user", "content": prompt}])
        except Exception as e:
            last = e
            break
        if getattr(msg, "stop_reason", "") == "max_tokens":
            last = ExtractError("AIの出力が長すぎて途中で切れました")
            max_tokens *= 2
            continue
        for b in msg.content:
            if getattr(b, "type", "") == "tool_use" and isinstance(b.input, dict):
                return b.input
        last = ExtractError("AIがデータを返しませんでした")
    try:   # 予備の方式
        return parse_json(_ask(prompt + "\n\n出力は次の形のJSONだけ（前後の説明やコードフェンスは不要）:\n"
                               + json.dumps(schema["properties"], ensure_ascii=False), max_tokens))
    except ExtractError:
        raise
    except Exception as e:
        raise ExtractError(f"AIの呼び出しに失敗しました（{type(last or e).__name__}: {str(last or e)[:120]}）")


def _list(data, key):
    if isinstance(data, list):
        return data
    v = data.get(key) if isinstance(data, dict) else None
    return v if isinstance(v, list) else []


def extract_basics(side, text):
    d = _extract(f"""次は{SIDE[side]}の店舗トップページの文字です。{RULE}

name: 店名（正式な掲載名）
catch: 店名のすぐ近くにある一行のキャッチコピー
intro: お店の紹介文・PR文（複数あれば改行でつなぐ）
budget: 平均予算の表記
hours: 営業時間の表記
holiday: 定休日の表記
seats: 総席数・キャパシティの表記
access: アクセスの表記

--- ページ ---
{text}""", BASICS, 3000)
    return d if isinstance(d, dict) else {}


COURSE_FIELDS = """name: コース名（タイトル全文）
price: 税込の販売価格（割引後の価格）を整数で。価格が無ければ null
items_count: 「コース品数：◯品」のように名前とは別に書かれた品数を整数で。無ければ null
link: 「⟦…⟧」はそのコースの詳細ページへのリンク。対応するものの中身を入れる。無ければ空文字"""


def extract_courses(side, text):
    """コース一覧を抜き出す。本文中の詳細リンクの数と突き合わせ、抜けがあればそのリンクを指定して読み直す
    （2026/10/3：先頭の「1杯100円」プランを読み落とした対策）"""
    rows = _list(_extract(f"""次は{SIDE[side]}のコース一覧ページの文字です。掲載されているコースを上から順に全部抜き出してください。{RULE}
詳細ページへのリンク「⟦…⟧」が付いている項目は、すべてコースです。価格が安いもの・ドリンクの企画・飲み放題だけのプラン・
二次会プラン・席のみ予約も、必ず含めてください。件数はリンクの種類の数と同じになるはずです。
リンクの付いていない「クーポン」の見出しだけは含めないでください。

{COURSE_FIELDS}

--- ページ ---
{text}""", COURSES, 8000), "courses")
    want = F.course_links(text)
    have = {F.link_path(str(r.get("link") or "")) for r in rows if isinstance(r, dict)}
    missing = [l for l in want if l not in have]
    if missing and len(rows) < len(want):
        more = _list(_extract(f"""次は{SIDE[side]}のコース一覧ページの文字です。次のリンクが付いている項目だけを抜き出してください。{RULE}
どれもコースとして掲載されているものです（ドリンクの企画や席のみ予約でも対象です）。

対象のリンク:
{chr(10).join(missing)}

{COURSE_FIELDS}

--- ページ ---
{text}""", COURSES, 4000), "courses")
        rows += [r for r in more if isinstance(r, dict) and F.link_path(str(r.get("link") or "")) in missing]
        order = {l: i for i, l in enumerate(want)}
        rows.sort(key=lambda r: order.get(F.link_path(str(r.get("link") or "")), len(order)) if isinstance(r, dict) else len(order))
    return rows


def extract_coupons(side, text):
    return _list(_extract(f"""次は{SIDE[side]}のページの文字です。このページに載っているクーポンを全部抜き出してください。{RULE}
クーポンは「【…】」で始まる見出しと、提示条件・利用条件・有効期限の説明が組になって並んでいます。
同じ見出しが何度も出てくる場合は1つにまとめてください。コースそのものはクーポンではありません。

title: クーポンの内容（タイトル全文）
condition: 利用条件・提示条件・有効期限の表記

--- ページ ---
{text}""", COUPONS, 4000), "coupons")


def extract_course_detail(text):
    d = _extract(f"""次はホットペッパーグルメのコース詳細ページの文字です。{RULE}

name: コース名
price: 税込価格を整数で
description: コースの説明文
menu: コース内容（料理の品目。ページの並び・改行のまま）
drink: 飲み放題メニュー・飲み放題の説明（無ければ空文字）
people: 利用人数の表記
stay: 滞在可能時間の表記
days: 利用できる曜日・時間帯の表記
deadline: 予約締切の表記

--- ページ ---
{text}""", DETAIL, 5000)
    return d if isinstance(d, dict) else {}


def shorten(text, limit, label):
    """制限を超えた文言だけを縮める。事実（料理名・価格・品数・時間）は変えない"""
    return _ask(f"""飲食店の掲載文「{label}」を、別のグルメサイトの入力欄に入れるため全角{limit}文字以内に縮めてください。

- 料理名・食材名・価格・品数・時間・曜日などの事実は一切変えない。新しい情報を足さない
- 削るのは飾りの言葉や重複から。打ち出し（売りの順番・言い回し）はできるだけ元のまま残す
- 改行は元の区切りを保つ
- 出力は縮めた文言だけ（説明・かぎ括弧は不要）

--- 元の文言（{len(text)}文字）---
{text}""", 1500)
