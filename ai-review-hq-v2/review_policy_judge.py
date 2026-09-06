# -*- coding: utf-8 -*-
"""
ホットペッパーグルメ 口コミ掲載基準による違反判定 + 報告文ドラフト生成

掲載が認められない類型（ホットペッパーグルメ 口コミ掲載基準より）
  A 虚偽の情報
  B 必要以上に感情的な表現
  C 独断的・断定的な意見
  D 品位を欠いた表現 / 誹謗中傷
  E 公序良俗に反する内容
  F 実際に利用していない投稿・なりすまし

方針:
  - 低評価であることは違反ではない（現行方針は「満足した体験もそうでない体験もすべて掲載」）
  - 条項に該当する根拠が一つでもあれば report 候補に上げる
  - 「事実の主張」は店側が証憑で否定できて初めて虚偽報告として成立するため、
    必要な証憑を evidence_needed に列挙する
"""

import re
import json

# ---------- A 虚偽の事実主張（客観的に検証できる主張） ----------
FACT_CLAIMS = [
    (r"(豚|ぶた|ブタ).{0,12}(出|来|提供|入って|混ざ)", "提供された肉の種別についての事実主張", ["該当日の仕入伝票・納品書", "該当日のPOS/注文履歴"]),
    (r"(偽装|産地偽装|表示と違|メニューと違)", "食材・表示の偽装という事実主張", ["仕入伝票", "メニュー表示物", "該当日の提供記録"]),
    (r"(髪の毛|毛が入|虫|異物|ゴキブリ|カビ)", "異物混入という事実主張", ["該当日の衛生記録", "クレーム対応記録", "該当日の予約・来店記録"]),
    (r"(食中毒|腹痛|下痢|吐い|嘔吐)", "健康被害という事実主張", ["保健所への届出有無", "同日他客からの申告有無", "衛生記録"]),
    (r"(賞味期限|消費期限|腐って|傷んで)", "食材の劣化という事実主張", ["仕入・在庫記録", "廃棄記録"]),
    (r"(ぼったく|不当な請求|勝手に(請求|加算)|会計が違|金額が違|多く取られ)", "会計・請求についての事実主張", ["該当日の伝票・レシート控え", "POS会計記録"]),
    (r"(予約(した|して).{0,15}(入れ|取れ|無かった|なかった|されて))", "予約の取扱いについての事実主張", ["予約台帳・レストランボード記録"]),
    (r"(店長|店員|スタッフ|従業員).{0,20}(と言われ|と言った|言い放|と返され)", "従業員の発言内容についての事実主張", ["該当日のシフト表", "当該従業員への聞き取り記録"]),
]

# ---------- D 品位を欠いた表現 / 誹謗中傷 ----------
INDECENT = [
    r"(死ね|しね|殺す|クズ|くず野郎|バカ|馬鹿|アホ|ブス|デブ|きもい|キモ)",
    r"(詐欺|サギ|犯罪|違法)(店|だ|です)",
    r"(ゴミ|くそ|クソ|最悪|論外|話にならな)",
    r"(態度が悪|接客がなって|教育がなって|人としてどう)",
]

# ---------- B 必要以上に感情的な表現 ----------
EMOTIONAL = [
    r"[！!]{2,}",
    r"[？?]{2,}",
    r"(二度と|絶対に行かな|金返せ|返金しろ|潰れ)",
    r"(怒り|腹立|ムカつ|むかつ|許せな)",
]

# ---------- C 独断的・断定的な意見 ----------
ASSERTIVE = [
    r"(絶対に|間違いなく|100%|確実に).{0,10}(まずい|悪い|ひどい|だめ|ダメ)",
    r"(誰も|皆|みんな|全員).{0,10}(思う|言って|感じ)",
    r"(全て|すべて|何もかも|全部).{0,8}(ダメ|だめ|最悪|ひどい)",
    r"(この店は|ここは).{0,10}(やめた方|行くべきでな|おすすめしな)",
]

# ---------- F 未利用・なりすましの疑い ----------
NOT_VISITED = [
    r"(電話(で|の)(だけ|対応|問い合わ))",
    r"(行って(いな|ませ)|入店(できな|せず)|入れなかった)",
    r"(予約(の|を)(電話|段階|時点))",
]

CLAUSES = {
    "A": "虚偽の情報",
    "B": "必要以上に感情的な表現",
    "C": "独断的・断定的な意見",
    "D": "品位を欠いた表現・誹謗中傷",
    "F": "実際に利用していない投稿の疑い",
}


def judge(review):
    text = review.get("comment") or ""
    hits, evidence, facts = [], [], []

    for pat, label, ev in FACT_CLAIMS:
        if re.search(pat, text):
            hits.append("A")
            facts.append(label)
            evidence.extend(ev)

    for pat in INDECENT:
        if re.search(pat, text):
            hits.append("D")
            break
    for pat in EMOTIONAL:
        if re.search(pat, text):
            hits.append("B")
            break
    for pat in ASSERTIVE:
        if re.search(pat, text):
            hits.append("C")
            break
    for pat in NOT_VISITED:
        if re.search(pat, text):
            hits.append("F")
            evidence.append("該当期間の予約台帳・来店記録")
            break

    clauses = sorted(set(hits))
    evidence = sorted(set(evidence))

    return {
        "external_id": review.get("external_id"),
        "store": review.get("store"),
        "rating": review.get("rating"),
        "reviewer": review.get("reviewer"),
        "posted_at": review.get("posted_at"),
        "review_url": review.get("review_url"),
        "verdict": "report" if clauses else "reply",
        "clauses": clauses,
        "clause_names": [CLAUSES[c] for c in clauses],
        "fact_claims": sorted(set(facts)),
        "evidence_needed": evidence,
        "draft": draft_report(review, clauses, facts) if clauses else "",
    }


def draft_report(review, clauses, facts):
    """報告文の下書き。事実主張(A)がある場合は、店側が証憑で否定できることが前提。"""
    posted = review.get("posted_at", "")
    nick = review.get("reviewer", "")
    lines = [
        f"【対象】{posted} 投稿／投稿者名「{nick}」",
        "【報告内容】下記の点が口コミ掲載基準に該当すると考えられるため、確認をお願いいたします。",
        "",
    ]

    if "A" in clauses:
        lines.append("■ 虚偽の情報")
        for f in sorted(set(facts)):
            lines.append(f"　・投稿には{f}が含まれています。")
        lines.append("　　当店の記録では当該内容は確認できません。根拠として下記を提示できます。")
        lines.append("　　（※ここに証憑を具体的に記載してください。記載できない場合はこの項目を削除してください）")
        lines.append("")

    if "D" in clauses:
        lines.append("■ 品位を欠いた表現・誹謗中傷")
        lines.append("　・従業員個人の人格や能力を否定する表現が含まれています。")
        lines.append("　　サービス内容への評価を超えており、掲載基準に反すると考えます。")
        lines.append("")

    if "B" in clauses:
        lines.append("■ 必要以上に感情的な表現")
        lines.append("　・体験の記述ではなく、感情の表出が中心の記載が含まれています。")
        lines.append("")

    if "C" in clauses:
        lines.append("■ 独断的・断定的な意見")
        lines.append("　・個人の体験の範囲を超えた断定的な一般化が含まれています。")
        lines.append("")

    if "F" in clauses:
        lines.append("■ 実際のご利用が確認できない投稿")
        lines.append("　・当該期間の予約台帳および来店記録に該当が見当たりません。")
        lines.append("")

    lines.append("以上、ご確認のほどよろしくお願いいたします。")
    return "\n".join(lines)


def run(reviews, max_rating=2):
    targets = [r for r in reviews if (r.get("rating") or 9) <= max_rating]
    results = [judge(r) for r in targets]
    rep = [r for r in results if r["verdict"] == "report"]
    rep_a = [r for r in rep if "A" in r["clauses"]]
    return {
        "対象件数": len(targets),
        "報告候補": len(rep),
        "うち事実主張あり_要証憑": len(rep_a),
        "報告根拠なし_返信で対応": len(targets) - len(rep),
        "results": results,
    }


if __name__ == "__main__":
    import sys
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    out = run(data)
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, ensure_ascii=False, indent=1))
    json.dump(out["results"], open("judge_results.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
