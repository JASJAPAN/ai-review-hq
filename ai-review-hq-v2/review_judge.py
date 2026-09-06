"""口コミ1件を判定：返信文の生成 + 違反報告該当チェック（Google / ホットペッパー共通）"""
import os, json
import anthropic

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

STORE_PROFILE = {
    "南薩農場": "鹿児島郷土料理の居酒屋。黒豚、さつま揚げ、鳥刺し、飲み放題付き宴会コースが看板。全席完全個室。",
    "川畜天文館店": "全席完全個室の焼肉食べ放題・飲み放題の店。家族連れ・宴会利用が多い。",
}

POLICY = {
    "google": """Googleビジネスプロフィールのクチコミ投稿ポリシーで削除対象になる主なもの：
- 無関係な内容（店舗での体験に基づかない、別店舗の話、政治・社会的主張）
- スパム・虚偽（同一内容の連投、来店していないと明確に分かる、事実と異なる主張）
- 利益相反（競合店・元従業員・現従業員・関係者による投稿、報酬目的）
- 制限されたコンテンツ（アルコール等の宣伝は別として、危険・違法行為の助長）
- 違法コンテンツ、テロ、性的コンテンツ、不適切なコンテンツ（わいせつ・冒涜的表現）
- 危険で中傷的なコンテンツ（脅迫、嫌がらせ、個人への攻撃・人格否定、差別）
- 個人情報の記載（従業員のフルネーム・電話番号・住所など）
- なりすまし""",
    "hotpepper": """ホットペッパーグルメの口コミ投稿ガイドラインで掲載不可・削除対象になる主なもの：
- 実際に利用していない内容、伝聞・推測に基づく内容
- 特定の個人（従業員・他客）を特定できる記載、誹謗中傷、名誉毀損
- 差別的表現、卑猥・暴力的な表現、公序良俗に反する内容
- 店舗と無関係な内容、他店との比較を主目的とした内容、宣伝・営業目的
- 事実と異なる内容、店舗への脅迫・威圧的な要求
- 個人情報（電話番号・メールアドレス・URL等）の記載
- 同一人物による複数投稿、関係者による投稿""",
}


def judge(platform, store, rating, comment, reviewer=""):
    """返り値: dict(action, reply, report, report_reason, policy_clause, confidence)"""
    profile = STORE_PROFILE.get(store, "")
    if rating >= 4:
        tone = "感謝を中心に。本文で触れられた料理や体験を具体的に拾い、再来店を自然に促す。"
    elif rating == 3:
        tone = "感謝しつつ、至らなかった点があれば真摯に受け止める姿勢。言い訳はしない。"
    else:
        tone = "まず謝罪。言い訳をせず、指摘を具体的に受け止め、改善を約束する。店舗へ直接連絡できる旨を添える。"
    platform_note = ("ホットペッパー経由の予約客が多いので、締めは『またのご予約をお待ちしております』系の自然な一言。"
                     if platform == "hotpepper" else "")

    prompt = f"""あなたは飲食店「{store}」の店長兼、口コミ運用の責任者です。
以下の口コミについて2つの作業をしてください。

【店舗情報】{profile}
【媒体】{platform}
【投稿者】{reviewer or "不明"}
【星評価】{rating}
【本文】
{comment or "（本文なし・星のみ）"}

作業1: 返信文を書く
- {tone}
- {platform_note}
- 80〜180字。丁寧だが定型文っぽくない、人が書いた温度感。
- 「AI」「自動」という言葉、絵文字・記号の乱用は禁止。本文にない事実を作らない。

作業2: 違反報告に該当するか判定する
以下のポリシーに「明確に」該当する場合だけ report=true にしてください。
低評価・厳しい意見・主観的な不満は違反ではありません。該当条項を具体的に指摘できない場合は必ず report=false。
{POLICY[platform]}

report=true の場合は、運営に提出する報告理由（100〜200字、事実ベース、感情を入れない、該当条項名を明記）を書いてください。

出力は次のJSONのみ（前後に説明やコードフェンス不要）:
{{"reply": "返信文", "report": true/false, "policy_clause": "該当条項名 or 空", "report_reason": "報告理由 or 空", "confidence": 0.0〜1.0}}"""

    client = anthropic.Anthropic()
    msg = client.messages.create(model=MODEL, max_tokens=900,
                                 messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    try:
        d = json.loads(text)
    except Exception:
        d = {"reply": text, "report": False, "policy_clause": "", "report_reason": "", "confidence": 0}
    report = bool(d.get("report")) and float(d.get("confidence", 0)) >= 0.7
    d["report"] = report
    d["action"] = "reply+report" if report else "reply"
    return d
