"""キャプション／ストーリーズ文／DM・コメント返信文の生成"""
import json, random
from datetime import date
import anthropic, ig_config as C

SYSTEM = """あなたは鹿児島・天文館の居酒屋「南薩農場」のInstagram担当です。
目的はブランディングです。「安い」「お得」ではなく、「鹿児島の食材と職人の手仕事を、落ち着いた個室で味わう店」という世界観を積み上げます。

ルール:
- 価格・円・クーポン・割引・飲み放題の金額など、金銭に関する表現は一切書かない
- 語りは静かで品よく。「！」の連発、煽り、押し売りはしない。絵文字は使わないか1個まで
- 誇張や虚偽は書かない。「本日限定」「今だけ」など事実でない限定表現は禁止
- ハッシュタグは書かない（別途付与する）
- 出力は本文のみ。前置きや説明は不要"""

def _ask(user, max_tokens=600):
    m = anthropic.Anthropic().messages.create(model=C.MODEL, max_tokens=max_tokens, system=SYSTEM,
                                              messages=[{"role": "user", "content": user}])
    return m.content[0].text.strip()

def feed(meta, today: date):
    angle = random.choice(["産地と生産者への敬意", "調理の手仕事と火入れ", "鹿児島の夜と個室の静けさ", "季節と旬の移ろい", "焼酎や酒との寄り添い方"])
    wd = "月火水木金土日"[today.weekday()]
    body = _ask(f"""店舗情報: {json.dumps(C.STORE, ensure_ascii=False)}
料理: {json.dumps(meta, ensure_ascii=False)}
今日は{today.month}月{today.day}日({wd})。切り口: {angle}
フィード投稿のキャプション。冒頭1行は料理名を含む短い一言、本文3〜5行で五感に訴える具体描写、最後の1行は店の世界観で締める。""")
    return f"{body}\n\n📍{C.STORE['address']}\n{C.STORE['access']}\n\n{' '.join(C.STORE['hashtags'])}"

def story(meta, today: date):
    """ストーリーズはテキストを画像に載せないので、投稿ログ用の短文（実際の画像は写真のみ）"""
    return _ask(f"料理: {json.dumps(meta, ensure_ascii=False)}\n今日({today.month}/{today.day})のストーリーズ用に、1行20字以内の静かな一言を1つ。", 100)

def reply(kind, text, sender=""):
    """kind: 'dm' | 'comment'"""
    return _ask(f"""{'DM' if kind=='dm' else 'コメント'}が届きました。店舗として返信文を書いてください。
送信者: {sender or '不明'}
内容: {text}

- {'DM: 予約・営業時間・席・アレルギー等の問い合わせには店舗情報に基づいて具体的に答える。分からないことは店舗に電話で確認いただくよう案内（電話番号は書かない）' if kind=='dm' else 'コメント: 40字以内で短く感謝や一言。'}
- 人が書いた温度感。定型文っぽくしない。
店舗情報: {json.dumps(C.STORE, ensure_ascii=False)}""", 400)
