"""Google口コミ：取得 → 判定 → 自動投稿 or 承認待ち（/admin/replies/run から15分おきに呼ばれる）"""
import os, datetime
from google_reviews import fetch_reviews, post_reply, db, upsert, mark_posted, STAR
from review_judge import judge

AUTO_MIN = int(os.environ.get("AUTO_POST_MIN_RATING", "6"))  # 6=全件承認制

def stores():
    """管理画面（/admin/google）で選んだ店舗を優先。無ければ環境変数 REVIEW_STORES"""
    from google_reviews import _state, ACCOUNT_ID
    st = _state().get("stores", [])
    if st:
        for s in st: yield s["name"], s["location"], s["account"]
        return
    for item in os.environ.get("REVIEW_STORES", "").split(","):
        if ":" in item:
            name, loc = item.split(":", 1)
            yield name.strip(), loc.strip(), ACCOUNT_ID

def main():
    con = db()
    for store, loc, acct in stores():
        for rv in fetch_reviews(loc, acct):
            rid = rv["reviewId"]
            if con.execute("SELECT 1 FROM reviews WHERE review_id=?", (rid,)).fetchone():
                continue
            rating = STAR.get(rv.get("starRating"), 3)
            comment = rv.get("comment", "")
            reviewer = rv.get("reviewer", {}).get("displayName", "")
            j = judge("google", store, rating, comment, reviewer)
            auto = rating >= AUTO_MIN and not j["report"]   # 違反報告候補は必ず人が見る
            upsert(con, review_id=rid, platform="google", store=store, location=loc,
                   reviewer=reviewer, rating=rating, comment=comment,
                   reply=j["reply"], reply_status="auto" if auto else "pending",
                   report=int(j["report"]), policy_clause=j.get("policy_clause", ""),
                   report_reason=j.get("report_reason", ""),
                   created_at=datetime.datetime.now().isoformat())
            if auto:
                post_reply(loc, rid, j["reply"], acct)
                mark_posted(con, rid, j["reply"])

if __name__ == "__main__":
    main()
