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

def drain_auto(con):
    """autoキューに溜まったGoogle返信を実際に投稿する（従来はキューに入れるだけで投稿処理が無かった）。
    口コミが既に存在しない（404）ものは skipped で整理し、他のエラーは次サイクルに持ち越す。"""
    rows = con.execute("""SELECT review_id, location, reply FROM reviews
                          WHERE platform='google' AND reply_status='auto' AND reply!='' LIMIT 25""").fetchall()
    done = 0
    for r in rows:
        rid = r["review_id"]
        try:
            post_reply(r["location"], rid, r["reply"])
            mark_posted(con, rid, r["reply"]); done += 1
        except Exception as e:
            if "404" in str(e):
                con.execute("UPDATE reviews SET reply_status='skipped' WHERE review_id=?", (rid,)); con.commit()
                print("google auto投稿: 口コミが存在しないため整理:", rid)
            else:
                print("google auto投稿失敗:", rid, repr(e))
    if rows:
        print(f"google auto消化: {len(rows)}件中 投稿{done}件")

def _when(rv):
    """口コミが投稿された日時（Google の createTime）。無ければ今"""
    return (rv.get("createTime") or "")[:19] or datetime.datetime.now().isoformat()

def backfill_dates(con):
    """返信済みも含めた全口コミを見て、created_at を実際の投稿日に直す（以前は取得日時を入れていた）"""
    from google_reviews import fetch_all_reviews
    n = 0
    for store, loc, acct in stores():
        try:
            for rv in fetch_all_reviews(loc, acct):
                ct = (rv.get("createTime") or "")[:19]
                if ct:
                    n += con.execute("UPDATE reviews SET created_at=? WHERE review_id=? AND created_at<>?",
                                     (ct, rv["reviewId"], ct)).rowcount
        except Exception as e:
            print("日付の補正に失敗:", store, repr(e))
    con.commit()
    if n: print(f"google 口コミの日付を補正: {n}件")

def main():
    con = db()
    drain_auto(con)
    backfill_dates(con)
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
                   created_at=_when(rv))
            if auto:
                try:
                    post_reply(loc, rid, j["reply"], acct)
                    mark_posted(con, rid, j["reply"])
                except Exception as e:
                    # 投稿に失敗しても 'auto' のまま残す → 次サイクルの drain_auto が再試行（404なら整理）
                    print("google 新規返信の投稿失敗（次サイクルで再試行）:", rid, repr(e))

if __name__ == "__main__":
    main()
