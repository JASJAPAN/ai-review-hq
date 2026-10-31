"""口コミ1件を判定：返信文の生成 + 違反報告該当チェック（Google / ホットペッパー共通）"""
import os, json
import anthropic
from review_policy_judge import judge as rule_judge

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


def judge_rereport(store, rating, comment, reviewer="", prev_reason="", used_angles=""):
    """審査落ちした違反報告を、前回と異なる条項・論点で組み直す（再報告用・★2以下のみ呼ばれる前提）。
    返り値: dict(report, policy_clause, report_reason, confidence)"""
    prompt = f"""あなたは飲食店「{store}」の口コミ運用責任者の代理人です。
以下の口コミについて、ホットペッパーグルメ運営へ違反報告を提出しましたが、審査の結果、非掲載にはなりませんでした。
再審査を求めるため、報告文を作り直します。

【投稿者】{reviewer or "不明"}
【星評価】{rating}
【本文】
{comment or "（本文なし・星のみ）"}

【これまでに主張した条項】{used_angles or "（記録なし）"}
【前回の報告文（冒頭）】{(prev_reason or "")[:300]}

{POLICY["hotpepper"]}

作業: 前回と【異なる条項】を主軸に、本文の記載を引用しながら新しい論点で報告理由（300〜500字）を組み立ててください。
- 構成: ①冒頭で該当条項名を明記 ②本文のどの記載がなぜ該当するかを短い引用で断定的に示す ③掲載継続による店舗の実害を一文 ④「掲載基準に照らした非掲載措置を強く要請いたします」で結ぶ
- 守る一線: 本文に存在しない事実や推測を根拠にしない。投稿者への人格攻撃・法的措置の示唆はしない。丁寧語は維持する
- 前回と同じ条項の言い換えしかできない場合、またはどの条項にも新しく結び付けられない場合は report=false にしてください（無理にひねり出さない）

出力は次のJSONのみ（前後に説明やコードフェンス不要）:
{{"report": true/false, "policy_clause": "該当条項名 or 空", "report_reason": "報告理由 or 空", "confidence": 0.0〜1.0}}"""
    client = anthropic.Anthropic()
    msg = client.messages.create(model=MODEL, max_tokens=800,
                                 messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    try:
        d = json.loads(text)
    except Exception:
        return {"report": False, "policy_clause": "", "report_reason": "", "confidence": 0}
    # 再報告は通常より基準を上げる（confidence 0.6 以上のみ）
    d["report"] = bool(d.get("report")) and float(d.get("confidence", 0)) >= 0.6 and bool(d.get("report_reason"))
    return d


def judge(platform, store, rating, comment, reviewer="", **extra):
    """返り値: dict(action, reply, report, report_reason, policy_clause, confidence)"""
    profile = STORE_PROFILE.get(store, "")
    if rating >= 4:
        tone = "感謝を中心に。本文で触れられた料理や体験を具体的に拾い、うれしい気持ちを素直に伝えて受け止める。来店を促す表現は入れない。"
    elif rating == 3:
        tone = "感謝しつつ、至らなかった点があれば真摯に受け止める姿勢。言い訳はしない。"
    else:
        tone = "まず謝罪。言い訳をせず、指摘を一つひとつ具体的に受け止める。締めは『いただいたご意見をもとに改善に努め、より良いお店づくりに励みます』という改善の姿勢で結ぶ。お客様に連絡・問い合わせ・再訪の負担を求める表現は使わない。"
    platform_note = ""  # 媒体を問わず、来店・予約・連絡を促す締めは使わない

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
- 感想を受け止める「感想型」で書く。「またのご来店をお待ちしております」「ぜひまたお越しください」「ご予約をお待ちしております」などの来店・予約を促す定型句、「店舗まで直接ご連絡ください」「お問い合わせください」などお客様に行動を求める表現はすべて禁止。締めは感謝の言葉、または「より良いお店づくりに励みます」のような自店の姿勢で結ぶ。
- 100〜200字。丁寧だが定型文っぽくない、人が書いた温度感。
- 「AI」「自動」という言葉、絵文字・記号の乱用は禁止。本文にない事実を作らない。

作業2: 違反報告に該当するか判定する
対象は星2以下の口コミのみ。星3以上はどんな内容でも report=false にしてください。
星2以下は「原則report=true」の前提で、店舗側の代理人として、適用できる条項を積極的に探してください。
虚偽・誹謗中傷のような明白な違反だけでなく、「必要以上に感情的な表現」「個人の体験を超えた断定的・独断的な記載」「具体的な利用事実の記述を欠き実際の利用が確認しづらい」など、解釈の幅がある条項も主張の根拠として使って構いません。
ただし一線: 口コミ本文に存在しない事実を根拠にしてはいけません。本文のどの記載を条項に当てはめるのか、引用で示せることが条件です。本文のどこをどう読んでも一つも条項に結び付けられない場合のみ report=false にしてください。
{POLICY[platform]}

report=true の場合は、運営に提出する報告理由（300〜500字）を書いてください。トーンは毅然とした強い要請: 「検討を依頼する」ではなく、該当条項への違反を明確に指摘した上で「掲載基準に照らした非掲載措置を強く要請いたします」という結論まで言い切る。構成の要素: ①該当するガイドライン条項名を冒頭近くで明記 ②口コミ本文のどの記載がなぜ該当するかを本文を短く引用しながら断定的に示す（「〜の可能性がある」ではなく「〜に該当します」） ③この記載が掲載され続けることによる店舗への実害（事実に基づかない印象の拡散、営業上の不利益など）を一文で指摘 ④非掲載措置の要請で結ぶ。ただし守るべき一線: 本文にない事実や推測を断定しない、投稿者への人格攻撃や法的措置の示唆はしない、丁寧語は維持する（強い=失礼ではない）。定型の構成・言い回しの使い回しは禁止: 書き出し・段落構成・結びの表現は口コミごとに変え、人間の担当者がその口コミを読んで個別に書いた文章に見えるようにする。感情表現ではなく事実と条項で押す。証拠資料の提出は行わない前提で、本文の記載内容だけで判断できる論理構成にする。

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
    report = bool(d.get("report")) and float(d.get("confidence", 0)) >= 0.5 and rating <= 2  # 違反報告は星2以下のみ（積極方針: 根拠が組めれば報告）

    # ホットペッパーは掲載基準ベースのルール判定を優先（条項・要証憑・報告文の型が確定する）
    if platform == "hotpepper" and rating <= 2:
        r = rule_judge({"comment": comment, "reviewer": reviewer, "rating": rating,
                        "external_id": extra.get("external_id"), "posted_at": extra.get("posted_at")})
        if r["verdict"] == "report":
            report = True
            d["policy_clause"] = "／".join(r["clause_names"])
            d["report_reason"] = r["draft"]
            d["evidence_needed"] = r["evidence_needed"]
            d["confidence"] = max(float(d.get("confidence", 0)), 0.8)
    d.setdefault("evidence_needed", [])
    d["report"] = report
    d["action"] = "reply+report" if report else "reply"
    return d
