# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ["ADMIN_PASSWORD"] = "test-pass"

import app as appmod  # noqa: E402

appmod.init_db()


@pytest.fixture()
def client():
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


def login(c):
    return c.post("/admin/login", data={"password": "test-pass"})


def test_survey_page(client):
    r = client.get("/s/nansatsu")
    assert r.status_code == 200
    assert "南薩農場" in r.get_data(as_text=True)
    assert "AIで簡単に自動作成" in r.get_data(as_text=True)


def test_unknown_store_404(client):
    assert client.get("/s/nai-mise").status_code == 404


def test_generate_local_fallback(client):
    r = client.post("/api/generate", json={
        "slug": "nansatsu", "rating": 5,
        "tags": ["料理がおいしい", "接客が良い"], "free_text": "黒豚が最高でした"})
    assert r.status_code == 200
    body = r.get_json()["review"]
    assert "南薩農場" in body and "黒豚が最高でした" in body


def test_generate_requires_input(client):
    r = client.post("/api/generate", json={"slug": "nansatsu", "rating": 4})
    assert r.status_code == 400


def test_generate_filters_unknown_tags(client):
    r = client.post("/api/generate", json={
        "slug": "nansatsu", "rating": 4,
        "tags": ["存在しないタグ"], "free_text": "良かった"})
    assert r.status_code == 200
    assert "存在しないタグ" not in r.get_json()["review"]


def test_submit_routes_high_to_google(client):
    r = client.post("/api/submit", json={
        "slug": "nansatsu", "rating": 5, "tags": [],
        "free_text": "", "review": "とても良いお店でした。"})
    data = r.get_json()
    assert data["routed"] == "google"
    assert data["google_url"].startswith("https://")


def test_submit_routes_low_internal(client):
    r = client.post("/api/submit", json={
        "slug": "nansatsu", "rating": 2, "tags": [],
        "free_text": "", "review": "改善に期待します。"})
    data = r.get_json()
    assert data["routed"] == "internal"
    assert data["google_url"] is None


def test_improve_saved(client):
    rid = client.post("/api/submit", json={
        "slug": "nansatsu", "rating": 1, "tags": [],
        "free_text": "", "review": "残念でした。"}).get_json()["response_id"]
    client.post("/api/improve", json={"response_id": rid, "text": "提供が遅かった"})
    with appmod.db() as conn:
        row = conn.execute("SELECT improve_text FROM responses WHERE id=?", (rid,)).fetchone()
    assert row["improve_text"] == "提供が遅かった"


def test_google_click_counts(client):
    rid = client.post("/api/submit", json={
        "slug": "nansatsu", "rating": 5, "tags": [],
        "free_text": "", "review": "最高。"}).get_json()["response_id"]
    client.post("/api/google-click", json={"response_id": rid})
    with appmod.db() as conn:
        row = conn.execute("SELECT google_clicks FROM responses WHERE id=?", (rid,)).fetchone()
    assert row["google_clicks"] == 1


def test_admin_requires_login(client):
    r = client.get("/admin")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def test_admin_dashboard_after_login(client):
    login(client)
    r = client.get("/admin")
    assert r.status_code == 200
    assert "全店ダッシュボード" in r.get_data(as_text=True)


def test_store_settings_and_threshold(client):
    login(client)
    with appmod.db() as conn:
        sid = conn.execute("SELECT id FROM stores WHERE slug='nansatsu'").fetchone()["id"]
    client.post(f"/admin/stores/{sid}/settings", data={
        "name": "南薩農場", "google_url": "https://maps.app.goo.gl/x",
        "welcome": "", "guide_mode": "high_only", "threshold": "5",
        "tags": "料理がおいしい\n接客が良い"})
    r = client.post("/api/submit", json={
        "slug": "nansatsu", "rating": 4, "tags": [],
        "free_text": "", "review": "良かったです。"})
    assert r.get_json()["routed"] == "internal"  # しきい値5なので★4は自社完結
    # 戻す
    client.post(f"/admin/stores/{sid}/settings", data={
        "name": "南薩農場", "google_url": "https://maps.app.goo.gl/mYnfJBxwJBEwPb3Y7?g_st=ic",
        "welcome": "", "guide_mode": "high_only", "threshold": "4",
        "tags": "\n".join(appmod.DEFAULT_TAGS)})


def test_everyone_mode(client):
    login(client)
    with appmod.db() as conn:
        sid = conn.execute("SELECT id FROM stores WHERE slug='nansatsu'").fetchone()["id"]
    client.post(f"/admin/stores/{sid}/settings", data={
        "name": "南薩農場", "google_url": "https://maps.app.goo.gl/x",
        "welcome": "", "guide_mode": "everyone", "threshold": "4",
        "tags": "料理がおいしい"})
    r = client.post("/api/submit", json={
        "slug": "nansatsu", "rating": 1, "tags": [],
        "free_text": "", "review": "うーん。"})
    assert r.get_json()["routed"] == "google"
    client.post(f"/admin/stores/{sid}/settings", data={
        "name": "南薩農場", "google_url": "https://maps.app.goo.gl/mYnfJBxwJBEwPb3Y7?g_st=ic",
        "welcome": "", "guide_mode": "high_only", "threshold": "4",
        "tags": "\n".join(appmod.DEFAULT_TAGS)})


def test_add_store_and_qr_and_csv(client):
    login(client)
    client.post("/admin/stores/new", data={
        "name": "テスト店", "slug": "test-ten", "google_url": "https://maps.app.goo.gl/y"})
    assert client.get("/s/test-ten").status_code == 200
    qr = client.get("/admin/qr/test-ten.png")
    assert qr.status_code == 200 and qr.mimetype == "image/png"
    with appmod.db() as conn:
        sid = conn.execute("SELECT id FROM stores WHERE slug='test-ten'").fetchone()["id"]
    csv_r = client.get(f"/admin/stores/{sid}/export.csv")
    assert csv_r.status_code == 200 and "text/csv" in csv_r.mimetype


def test_bad_slug_rejected(client):
    login(client)
    r = client.post("/admin/stores/new", data={
        "name": "X", "slug": "日本語", "google_url": "https://x"})
    assert "正しく入力" in r.get_data(as_text=True) or r.status_code == 200
