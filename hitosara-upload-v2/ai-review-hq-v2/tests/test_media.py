# -*- coding: utf-8 -*-
"""媒体管制室のテスト。
コース名・価格は 2026/10/3 時点の「うみどり 天文館店」の公開ページ（ホットペッパー／ヒトサラ）の実データ。
キャッチコピーなど一部はテスト用の仮の値（コメントで明記）。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ.setdefault("ADMIN_PASSWORD", "test-pass")

import app as appmod  # noqa: E402
import media_admin as M  # noqa: E402
import media_ai as AI  # noqa: E402
import media_diff as D  # noqa: E402
import media_fetch as F  # noqa: E402

TAIL = "/3H飲み放題＆個室確約"
HP_COURSES = [  # (コース名, 税込価格, 品数欄)
    ("【日～木＆期間限定】定番サワーやハイボール、翠ジンソーダなど1杯100円(税込110円)何杯でも◎", 110, None),
    ("【1番人気】地鶏たたき・刺身・黒豚と黒毛和牛メンチなど鍋無10品" + TAIL, 4500, 10),
    ("【1番人気】黒豚2種しゃぶ・地鶏たたき・真あじなど鍋有10品" + TAIL, 4500, 10),
    ("【鹿児島の旬の味覚】地鶏のたたき・炭火焼き・かつお炙りなど鍋無8品" + TAIL, 4000, 9),
    ("【鹿児島の旬の味覚】黒豚しゃぶ・地鶏炭火たたき・かつおなど鍋有9品" + TAIL, 4000, 9),
    ("【薩摩づくし】地鶏たたき・刺身・鰆・炭火地鶏・メンチなど鍋無11品" + TAIL, 5000, 11),
    ("【薩摩づくし】黒豚2種しゃぶ・地鶏たたき・刺身・メンチなど鍋有11品" + TAIL, 5000, 11),
    ("【日～木限定／コスパ抜群】地鶏の炭火焼き・かつお・きびなごなど鍋無8品" + TAIL, 3500, 8),
    ("【日～木限定／コスパ抜群】黒豚しゃぶ・枕崎かつお・きびなごなど鍋有8品" + TAIL, 3500, 9),
    ("【接待や会食に】地鶏と黒豚の炭火焼き・刺身4種・黒豚角煮など鍋無12品" + TAIL, 6000, 12),
    ("【接待や会食に】黒豚3種しゃぶ・刺身・地鶏たたき・西京焼きなど鍋有12品" + TAIL, 6000, 12),
    ("【豪華食材を堪能】熟成赤身ステーキ・三元豚ヒレカツ・刺身など鍋無13品" + TAIL, 8000, 13),
    ("【豪華食材を堪能】黒牛黒豚しゃぶ・刺身・地鶏&黒豚炭火焼きなど鍋有13品" + TAIL, 8000, 13),
    ("【単品飲み放題】生ビールやハイボール、サワーなど30種以上！2時間プレミアム飲み放題 2,000円", 2000, None),
    ("【21時以降の二次会プラン】150分飲み放題＋本日のおつまみ3品付、お1人様3,500円→2,500円♪", 2500, 3),
    ("お席のみのご予約", None, None),
]
HS_COURSES = [  # (プラン名, 税込価格)
    ("お席のみの予約", None),
    ("【日～木＆期間限定】定番サワーやハイボール、翠ジンソーダなどが1杯100円(税込110円)！何杯でもOK◎", 110),
    ("【1番人気】地鶏たたき・刺身・黒豚と黒毛和牛メンチなど鍋無10品" + TAIL, 4500),
    ("【1番人気】黒豚2種しゃぶ・地鶏たたき・真あじなど鍋有10品" + TAIL, 4500),
    ("【鹿児島の旬の味覚】地鶏の炭火たたき・炭火焼き・かつお炙りなど鍋無8品" + TAIL, 4000),
    ("【鹿児島の旬の味覚】黒豚しゃぶ・地鶏の炭火たたき・かつおなど鍋有9品" + TAIL, 4000),
    ("【薩摩づくし】地鶏たたき・刺身・鰆・炭火地鶏・メンチなど鍋無11品" + TAIL, 5000),
    ("【薩摩づくし】黒豚2種しゃぶ・地鶏たたき・刺身・メンチなど鍋有11品" + TAIL, 5000),
    ("【日～木限定／コスパ抜群】地鶏の炭火焼き・枕崎かつお・きびなごなど鍋無8品" + TAIL, 3500),
    ("【日～木限定／コスパ抜群】黒豚しゃぶ・枕崎かつお・きびなごなど鍋有8品" + TAIL, 3500),
    ("【接待や会食に】地鶏と黒豚の炭火焼き・刺身4種・黒豚角煮など鍋無12品" + TAIL, 6000),
    ("【接待や会食に】黒豚3種しゃぶ・刺身4種・地鶏たたき・西京焼きなど鍋有12品" + TAIL, 6000),
    ("【豪華食材を堪能】熟成赤身肉ステーキ・三元豚ヒレカツ・刺身など鍋無13品" + TAIL, 8000),
    ("【豪華食材を堪能】黒牛黒豚しゃぶ・刺身・地鶏と黒豚炭火焼きなど鍋有13品" + TAIL, 8000),
    ("【単品飲み放題】生ビールやハイボール、サワーなど30種以上！2時間プレミアム飲み放題 2,000円", 2000),
    ("【21時以降の二次会プラン】150分飲み放題＋本日のおつまみ3品が付いて、お1人様3,500円→2,500円♪", 2500),
    ("【全席完全個室】お席のみ予約◆少人数から大人数まで人数に合わせた個室をご用意！", None),
]


def hp_snap():
    return {
        "basics": {"name": "全席完全個室居酒屋　鮮魚と地鶏と炭火　うみどり　天文館店",
                   "catch": "予約殺到！薩摩地鶏×漁港直送旬魚の居酒屋", "intro": "",
                   "budget": "3001～4000円", "hours": "月～日、祝日、祝前日: 16:00～翌0:00"},
        "courses": [{"name": n, "price": p, "items_count": c, "link": f"/strJ003474523/course_cnod{i:02d}/"}
                    for i, (n, p, c) in enumerate(HP_COURSES, 1)],
        "coupons": [{"title": "【幹事無料】日～木限定！6名様以上のコース利用で1名様分無料！", "condition": ""}],
    }


def hs_snap():
    return {
        "basics": {"name": "鮮魚と地鶏と炭火 うみどり 天文館店",
                   "catch": "（テスト用の仮のキャッチ）個室で味わう鮮魚と地鶏", "intro": "",
                   "budget": "通常：3500円", "hours": "ディナー 16:00～00:00"},
        "courses": [{"name": n, "price": p, "items_count": None, "link": ""} for n, p in HS_COURSES],
        "coupons": [],
    }


# ------------------------------------------------------------ 比較（AIなし）

def test_real_umidori_courses_match_one_to_one():
    pairs, only_hp, only_hs = D.match_courses(hp_snap()["courses"], hs_snap()["courses"])
    assert len(pairs) == 16 and only_hp == []
    assert [HS_COURSES[j][0] for j in only_hs] == ["【全席完全個室】お席のみ予約◆少人数から大人数まで人数に合わせた個室をご用意！"]
    for i, j in pairs:   # 鍋あり/なし・価格を取り違えていないこと
        assert HP_COURSES[i][1] == HS_COURSES[j][1]
        assert D.nabe(HP_COURSES[i][0]) == D.nabe(HS_COURSES[j][0])


def test_real_umidori_diff_summary():
    items = D.diff_all(hp_snap(), hs_snap())
    kinds = [i["kind"] for i in items]
    assert kinds.count("price") == 0 and kinds.count("course_missing") == 0
    assert kinds.count("course_extra") == 1
    assert kinds.count("course_name") == 9          # 言い回しだけ違うコース
    assert kinds.count("hp_count") == 2             # タイトル8品なのに品数欄9品、の2コース
    assert kinds.count("name") == 1                 # 店名の「全席完全個室居酒屋」
    assert "hours" not in kinds and "budget" not in kinds   # 翌0:00=00:00、3500円は3001～4000円の範囲内
    assert kinds.count("coupon_missing") == 1
    assert [D.LEVEL_ORDER[i["level"]] for i in items] == sorted(D.LEVEL_ORDER[i["level"]] for i in items)


def test_price_change_and_missing_course_are_high():
    hp, hs = hp_snap(), hs_snap()
    hs["courses"][2]["price"] = 4000                                   # ヒトサラだけ旧価格
    hp["courses"].append({"name": "【冬季限定】黒豚しゃぶと釜飯の忘年会コース鍋有10品" + TAIL, "price": 5500,
                          "items_count": 10, "link": "/strJ003474523/course_cnod99/"})
    high = [i for i in D.diff_all(hp, hs) if i["level"] == "high"]
    assert {i["kind"] for i in high} == {"price", "course_missing"}
    assert any("4,500円" in i["hp"] and "4,000円" in i["hs"] for i in high)


def test_hours_and_budget_detection():
    assert D._hours("16:00～翌0:00") == D._hours("ディナー 16:00～00:00") == D._hours("16：00〜24：00")
    assert D.diff_basics({"hours": "17:00～翌0:00"}, {"hours": "16:00～00:00"})[0]["kind"] == "hours"
    assert D.diff_basics({"budget": "2001～3000円"}, {"budget": "ディナー：3500円"})[0]["kind"] == "budget"
    assert D.norm("３Ｈ飲み放題＆個室 確約") == D.norm("3H飲み放題&個室確約")


# ------------------------------------------------------------ 取得・文字化

def test_to_text_keeps_course_links_and_drops_noise():
    html = """<html><head><title>x</title><script>var a=1;</script><style>p{}</style></head><body>
    <div><a href="/strJ003474523/course_cnod01/">【1番人気】鍋無10品</a><p>4,500 円（税込）</p></div>
    <div><a href="/strJ003474523/course_cnod01/cpnh03/">クーポン</a></div>
    <p>同じ行</p><p>同じ行</p><a href="https://hitosara.com/0020004633/course3.html?x=1">コース詳細を見る</a>
    <footer>フッターは不要</footer></body></html>"""
    t = F.to_text(html)
    assert "【1番人気】鍋無10品 ⟦/strJ003474523/course_cnod01/⟧" in t
    assert "⟦https://hitosara.com/0020004633/course3.html⟧" in t
    assert "cpnh03" not in t and "var a" not in t and "フッター" not in t
    assert t.count("同じ行") == 1


def test_url_validation():
    assert F.normalize_hp_url("https://www.hotpepper.jp/strJ003474523/course/") == "https://www.hotpepper.jp/strJ003474523/"
    assert F.normalize_hp_url("https://example.com/strJ00/") == ""
    with pytest.raises(ValueError):
        F.get("https://example.com/")
    assert F.absolute("hp", "/strJ003474523/course_cnod01/") == "https://www.hotpepper.jp/strJ003474523/course_cnod01/"


# ------------------------------------------------------------ 文字数調整

def test_fit(monkeypatch):
    monkeypatch.setattr(AI, "available", lambda: False)
    assert M.fit("短い文言", 50, "x") == ("短い文言", "そのまま")
    body, how = M.fit("あ" * 60, 50, "x")
    assert len(body) == 50 and "要手直し" in how
    monkeypatch.setattr(AI, "available", lambda: True)
    monkeypatch.setattr(AI, "shorten", lambda t, limit, label: "い" * 45)
    assert M.fit("あ" * 60, 50, "x") == ("い" * 45, "AIで短縮")
    monkeypatch.setattr(AI, "shorten", lambda t, limit, label: "い" * 70)   # AIが制限を守らなかった場合
    body, how = M.fit("あ" * 60, 50, "x")
    assert len(body) == 50 and "要手直し" in how


# ------------------------------------------------------------ 画面と実行（取得・AIは差し替え）

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "DATA_DIR", tmp_path)
    monkeypatch.setattr(M, "DB_PATH", tmp_path / "media.db")
    monkeypatch.setattr(M, "RECON_PATH", tmp_path / "hs_recon.json")
    monkeypatch.setenv("MEDIA_SYNC_INLINE", "1")
    monkeypatch.setenv("REPLY_RUN_TOKEN", "tok")
    monkeypatch.setattr(F, "page_text", lambda url, limit=None: url)
    monkeypatch.setattr(AI, "available", lambda: True)
    monkeypatch.setattr(AI, "extract_basics", lambda side, text: (hp_snap() if side == "hp" else hs_snap())["basics"])
    monkeypatch.setattr(AI, "extract_courses", lambda side, text: (hp_snap() if side == "hp" else hs_snap())["courses"])
    monkeypatch.setattr(AI, "extract_coupons", lambda side, text: (hp_snap() if side == "hp" else hs_snap())["coupons"])
    monkeypatch.setattr(AI, "shorten", lambda t, limit, label: t[:limit])
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


def test_pages_require_login_and_run_requires_token(client):
    assert client.get("/admin/media/").status_code == 302
    assert client.post("/admin/media/run").status_code == 302
    assert client.post("/admin/media/run", headers={"X-Run-Token": "wrong"}).status_code == 403


def test_cron_run_then_store_page(client):
    r = client.post("/admin/media/run", headers={"X-Run-Token": "tok"})
    assert r.status_code == 202 and r.get_json()["started"] is True
    client.post("/admin/login", data={"password": os.environ["ADMIN_PASSWORD"]})
    top = client.get("/admin/media/").get_data(as_text=True)
    assert "うみどり 天文館店" in top and "ズレ 15件" in top     # 対象は試行店舗のうみどりだけ
    page = client.get("/admin/media/store/umidori").get_data(as_text=True)
    assert "ヒトサラにだけあるコース" in page and "即時予約管理 ＞ プラン管理 ＞ プラン名称" in page
    assert "サポートデスクへ依頼" in page and "コピー" in page
    snap = client.get("/admin/media/store/umidori/snapshot").get_json()
    assert len(snap["hp"]["courses"]) == 16 and len(snap["hs"]["courses"]) == 17


def test_ignore_survives_next_run_and_done_reopens(client):
    client.post("/admin/media/run", headers={"X-Run-Token": "tok"})
    client.post("/admin/login", data={"password": os.environ["ADMIN_PASSWORD"]})
    con = M.db()
    extra = con.execute("SELECT id FROM media_items WHERE kind='course_extra'").fetchone()["id"]
    name = con.execute("SELECT id FROM media_items WHERE kind='name'").fetchone()["id"]
    client.post(f"/admin/media/item/{extra}/ignore")
    client.post(f"/admin/media/item/{name}/done")
    client.post("/admin/media/run", headers={"X-Run-Token": "tok"})
    con = M.db()
    open_kinds = [r["kind"] for r in con.execute("SELECT kind FROM media_items WHERE status='open'")]
    assert "course_extra" not in open_kinds      # 対象外にしたものは出てこない
    assert "name" in open_kinds                  # 対応済みでも直っていなければ再び出る


def test_store_save_validates(client):
    client.post("/admin/login", data={"password": os.environ["ADMIN_PASSWORD"]})
    client.post("/admin/media/stores/save", data={"name": "テスト店", "hp_url": "https://evil.example/", "hs_id": "12", "enabled": "1"})
    row = M.db().execute("SELECT * FROM media_stores WHERE name='テスト店'").fetchone()
    assert row["hp_url"] == "" and row["hs_id"] == "" and row["enabled"] == 0


# ------------------------------------------------------------ 自動反映（承認 → ワーカー用API）

def test_hs_len_matches_hitosara_counting():
    assert D.hs_len("3H飲み放題＆個室確約") == 10          # 半角の「3H」は合わせて1文字
    assert D.hs_len("ｶﾅ", "owner") == 2 and D.hs_len("ｶﾅ", "reserve") == 1   # 半角カナは画面で扱いが違う
    name = HP_COURSES[3][0]                                 # 実際のコース名（半角を含む）は50文字に収まる
    assert len(name) > D.hs_len(name, "reserve") and D.hs_len(name, "reserve") <= 50
    assert D.side_of("plan_name") == "reserve" and D.side_of("catch") == "owner"


def _run_and_login(client):
    client.post("/admin/media/run", headers={"X-Run-Token": "tok"})
    client.post("/admin/login", data={"password": os.environ["ADMIN_PASSWORD"]})
    return M.db()


def test_approve_then_worker_tasks_and_done(client):
    con = _run_and_login(client)
    assert client.get("/admin/media/tasks").status_code == 403            # トークンなしは不可
    assert client.get("/admin/media/tasks", headers={"X-Run-Token": "tok"}).get_json()["tasks"] == []   # 承認前は空

    catch = con.execute("SELECT id FROM media_items WHERE kind='catch'").fetchone()["id"]
    name = con.execute("SELECT * FROM media_items WHERE kind='course_name' AND title LIKE '%4,000円・鍋無%'").fetchone()
    extra = con.execute("SELECT id FROM media_items WHERE kind='course_extra'").fetchone()["id"]
    client.post(f"/admin/media/item/{catch}/approve", data={"text": "予約殺到！薩摩地鶏×漁港直送旬魚の居酒屋"})
    client.post(f"/admin/media/item/{name['id']}/approve", data={"text": name["hp"]})
    client.post(f"/admin/media/item/{extra}/approve")

    tasks = client.get("/admin/media/tasks", headers={"X-Run-Token": "tok"}).get_json()["tasks"]
    by = {t["type"]: t for t in tasks}
    assert set(by) == {"intro_catch", "plan_name", "plan_off"}
    assert all(t["hs_id"] == "0020004633" for t in tasks)
    assert by["plan_name"]["ref"] == HS_COURSES[4][0] and by["plan_name"]["text"] == HP_COURSES[3][0]   # 直す先はヒトサラ側の今の名前で探す
    assert by["plan_off"]["ref"].startswith("【全席完全個室】お席のみ予約")

    client.post("/admin/media/tasks/done", headers={"X-Run-Token": "tok"}, json={"id": catch, "ok": True, "message": "公開まで完了"})
    client.post("/admin/media/tasks/done", headers={"X-Run-Token": "tok"}, json={"id": extra, "ok": False, "message": "プランが見つからない"})
    con = M.db()
    st = {r["id"]: r["status"] for r in con.execute("SELECT id, status FROM media_items")}
    assert st[catch] == "written" and st[extra] == "failed" and st[name["id"]] == "approved"
    page = client.get("/admin/media/store/umidori").get_data(as_text=True)
    assert "自動反映に失敗しました：プランが見つからない" in page and "自動反映を承認済みです" in page


def test_approve_rejects_over_limit_and_keeps_approved_on_rerun(client):
    con = _run_and_login(client)
    catch = con.execute("SELECT id FROM media_items WHERE kind='catch'").fetchone()["id"]
    client.post(f"/admin/media/item/{catch}/approve", data={"text": "あ" * 41})     # キャッチコピーは全角40文字まで
    assert M.db().execute("SELECT status FROM media_items WHERE id=?", (catch,)).fetchone()["status"] == "open"
    client.post(f"/admin/media/item/{catch}/approve", data={"text": "あ" * 40})
    client.post("/admin/media/run", headers={"X-Run-Token": "tok"})                  # 反映前に次のチェックが走っても
    rows = M.db().execute("SELECT status FROM media_items WHERE kind='catch'").fetchall()
    assert [r["status"] for r in rows] == ["approved"]                                # 承認は消えず、二重にもならない


def test_price_approval_uses_hotpepper_price(client, monkeypatch):
    snap = hs_snap(); snap["courses"][2]["price"] = 4000                              # ヒトサラだけ旧価格
    monkeypatch.setattr(AI, "extract_courses", lambda side, text: (hp_snap() if side == "hp" else snap)["courses"])
    con = _run_and_login(client)
    price = con.execute("SELECT id FROM media_items WHERE kind='price'").fetchone()["id"]
    client.post(f"/admin/media/item/{price}/approve")
    task = client.get("/admin/media/tasks", headers={"X-Run-Token": "tok"}).get_json()["tasks"][0]
    assert task["type"] == "plan_price" and task["text"] == "4500" and task["ref"] == HS_COURSES[2][0]


def test_recon_upload_and_view(client):
    r = client.post("/admin/media/recon", headers={"X-Run-Token": "tok"},
                    json={"at": "2026-10-03T10:00:00", "mode": "dry", "log": ["うみどり：ログイン成功"],
                          "pages": [{"name": "plan_confirm", "url": "https://reserve.hitosara.com/admin/plan/confirm/", "outline": "body", "png": ""}]})
    assert r.status_code == 200
    client.post("/admin/login", data={"password": os.environ["ADMIN_PASSWORD"]})
    page = client.get("/admin/media/recon").get_data(as_text=True)
    assert "plan_confirm" in page and "ログイン成功" in page
