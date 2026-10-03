"""媒体管制室：AIの仕事は2つだけ。
1) ページの文字から、決まった項目を「そのまま」抜き出す（言い換えない）
2) ヒトサラの文字数制限を超えた文言だけを、意味を変えずに縮める
比較（どこがズレているか）は media_diff.py が機械的に行う。
"""
import json, os

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
SIDE = {"hp": "ホットペッパーグルメ", "hs": "ヒトサラ"}

RULE = """ページに書かれている文言を一字一句そのまま抜き出してください。言い換え・要約・補完は禁止です。
ページに無い項目は空文字 "" か null にしてください。推測で埋めないでください。
出力は指定のJSONだけ（前後の説明やコードフェンスは不要）。"""


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _ask(prompt, max_tokens=4000):
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text.strip()


def _json(prompt, max_tokens=4000):
    text = _ask(prompt, max_tokens).replace("```json", "").replace("```", "").strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [0])
    return json.loads(text[start:])


def extract_basics(side, text):
    return _json(f"""次は{SIDE[side]}の店舗トップページの文字です。{RULE}

{{"name": "店名（正式な掲載名）",
 "catch": "店名のすぐ近くにある一行のキャッチコピー",
 "intro": "お店の紹介文・PR文（複数あれば改行でつなぐ）",
 "budget": "平均予算の表記",
 "hours": "営業時間の表記",
 "holiday": "定休日の表記",
 "seats": "総席数・キャパシティの表記",
 "access": "アクセスの表記"}}

--- ページ ---
{text}""", 2500)


def extract_courses(side, text):
    data = _json(f"""次は{SIDE[side]}のコース一覧ページの文字です。掲載されているコース（席のみ予約を含む）を上から順に全部抜き出してください。{RULE}
「⟦…⟧」はそのコースの詳細ページへのリンクです。コースごとに対応するものを link に入れてください。
クーポンはコースではないので含めないでください。

[{{"name": "コース名（タイトル全文）",
  "price": 税込の販売価格を整数で（割引後の価格。価格が無ければ null）,
  "items_count": 「コース品数：◯品」のように名前とは別に書かれた品数を整数で（無ければ null）,
  "link": "⟦⟧の中身（無ければ空文字）"}}]

--- ページ ---
{text}""", 6000)
    return data if isinstance(data, list) else data.get("courses", [])


def extract_coupons(side, text):
    data = _json(f"""次は{SIDE[side]}のクーポン掲載ページの文字です。掲載されているクーポンを重複なく全部抜き出してください。{RULE}

[{{"title": "クーポンの内容（タイトル全文）", "condition": "利用条件・提示条件・有効期限の表記"}}]

--- ページ ---
{text}""", 3000)
    return data if isinstance(data, list) else data.get("coupons", [])


def extract_course_detail(text):
    return _json(f"""次はホットペッパーグルメのコース詳細ページの文字です。{RULE}

{{"name": "コース名",
 "price": 税込価格を整数で,
 "description": "コースの説明文",
 "menu": "コース内容（料理の品目。ページの並び・改行のまま）",
 "drink": "飲み放題メニュー・飲み放題の説明（無ければ空文字）",
 "people": "利用人数の表記",
 "stay": "滞在可能時間の表記",
 "days": "利用できる曜日・時間帯の表記",
 "deadline": "予約締切の表記"}}

--- ページ ---
{text}""", 4000)


def shorten(text, limit, label):
    """制限を超えた文言だけを縮める。事実（料理名・価格・品数・時間）は変えない"""
    return _ask(f"""飲食店の掲載文「{label}」を、別のグルメサイトの入力欄に入れるため全角{limit}文字以内に縮めてください。

- 料理名・食材名・価格・品数・時間・曜日などの事実は一切変えない。新しい情報を足さない
- 削るのは飾りの言葉や重複から。打ち出し（売りの順番・言い回し）はできるだけ元のまま残す
- 改行は元の区切りを保つ
- 出力は縮めた文言だけ（説明・かぎ括弧は不要）

--- 元の文言（{len(text)}文字）---
{text}""", 1500)
