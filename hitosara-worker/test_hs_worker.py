# -*- coding: utf-8 -*-
"""ワーカーを、調査仕様書どおりに作った模型サイト（mock_hitosara.py）に対して実際のブラウザで動かすテスト。
本物のヒトサラには接続しない。  実行: cd hitosara-worker && python -m pytest -q
"""
import threading

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import hs_worker as W  # noqa: E402
from mock_hitosara import LOGIN_ID, LOGIN_PW, make_app  # noqa: E402

NAME_OLD = "【鹿児島の旬の味覚】地鶏の炭火たたきなど鍋無8品"
NAME_NEW = "【鹿児島の旬の味覚】地鶏のたたき・炭火焼きなど鍋無8品"
TOP = "【1番人気】鍋無10品/3H飲み放題＆個室確約"
SEAT = "【全席完全個室】お席のみ予約◆個室をご用意！"


def T(i, kind, ref="", text=""):
    return {"id": i, "type": kind, "store_key": "umidori", "store": "うみどり", "hs_id": LOGIN_ID, "ref": ref, "current": ref,
            "text": text, "hs_catch": "旧キャッチ", "hs_intro": "旧紹介文"}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=W.LAUNCH_ARGS)   # 本番と同じ省メモリ設定で動かす
        yield b
        b.close()


@pytest.fixture()
def site(monkeypatch):
    app = make_app()
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setattr(W, "OWNER", base); monkeypatch.setattr(W, "RESERVE", base)
    yield app.state
    srv.shutdown()


def run(browser, tasks, pw=LOGIN_PW):
    pages, log = [], []
    res = W.process_store(browser, LOGIN_ID, pw, tasks, pages, log)
    return {t["id"]: (ok, msg) for t, ok, msg in res}, pages, log


def test_run_mode_writes_everything_and_publishes_only_intro(browser, site, monkeypatch):
    monkeypatch.setattr(W, "MODE", "run")
    res, pages, _ = run(browser, [T(1, "intro_catch", text="予約殺到！薩摩地鶏×漁港直送旬魚の居酒屋"),
                                  T(2, "plan_name", NAME_OLD, NAME_NEW),
                                  T(3, "plan_price", TOP, "4500"),
                                  T(4, "plan_off", SEAT)])
    assert all(ok for ok, _ in res.values()), res
    assert site["intro"]["catch"] == "予約殺到！薩摩地鶏×漁港直送旬魚の居酒屋" and site["intro"]["text"] == "旧紹介文"
    assert site["reflect_calls"] == ["OwnerIntroduction"]          # 公開は紹介文だけ。全体公開は使っていない
    assert site["seat_unreflect"] is True                           # 座席情報の未反映には触れていない
    assert site["plans"]["103"]["name"] == NAME_NEW
    assert site["plans"]["101"]["sale"] == 4500 and site["plans"]["101"]["price"] == 5980   # 定価は高いのでそのまま
    assert site["plans"]["201"]["sale"] == 4500 and site["plans"]["201"]["pub"] == 0        # 同名の非掲載プランは別物
    assert site["plans"]["102"]["pub"] == 0 and site["plan_updates"] == 3


def test_dry_mode_changes_nothing_but_records_screens(browser, site, monkeypatch):
    monkeypatch.setattr(W, "MODE", "dry")
    before = {k: dict(v) for k, v in site["plans"].items()}
    res, pages, log = run(browser, [T(1, "intro_catch", text="新キャッチ"), T(2, "plan_name", NAME_OLD, NAME_NEW)])
    assert all(ok for ok, _ in res.values())
    assert site["intro"]["catch"] == "旧キャッチ" and site["intro_draft"] is None and site["reflect_calls"] == []
    assert site["plans"] == before and site["plan_updates"] == 0
    names = [p["name"] for p in pages]
    assert any("intro_edit" in n for n in names) and any("plan_confirm" in n for n in names)
    line = next(l for l in log if "確認画面のボタン" in l)                       # dry でも、どのボタンを押す予定かを記録する
    assert "★「この内容で登録する」name=update_plan" in line and "「検索」name=search（別のフォーム）" in line
    assert "★「内容を修正する」" not in line
    assert all(p["png"] for p in pages)


def test_price_with_same_as_list_price_checked(browser, site, monkeypatch):
    monkeypatch.setattr(W, "MODE", "run")
    res, _, _ = run(browser, [T(1, "plan_price", NAME_OLD, "4500")])   # 「定価と同じ」にチェックがあるプラン
    assert res[1][0], res
    assert site["plans"]["103"]["price"] == 4500 and site["plans"]["103"]["sale"] == 4500


def test_does_not_touch_someone_elses_pending_edit(browser, site, monkeypatch):
    monkeypatch.setattr(W, "MODE", "run")
    site["intro_draft"] = {"catch": "誰かの編集途中", "text": "旧紹介文"}
    res, _, _ = run(browser, [T(1, "intro_catch", text="新キャッチ")])
    assert res[1][0] is False and "未反映の編集が残っています" in res[1][1]
    assert site["intro_draft"]["catch"] == "誰かの編集途中" and site["reflect_calls"] == []


def test_aborts_when_plan_not_found_or_login_fails(browser, site, monkeypatch):
    monkeypatch.setattr(W, "MODE", "run")
    res, _, _ = run(browser, [T(1, "plan_name", "存在しないプラン", "x"), T(2, "plan_off", SEAT)])
    assert res[1][0] is False and "0件" in res[1][1]
    assert res[2][0] is True                                         # 1件の失敗で他の作業は止めない
    res, pages, _ = run(browser, [T(3, "plan_off", TOP)], pw="wrong")
    assert res[3][0] is False and "ログインできません" in res[3][1]
    assert site["plans"]["101"]["pub"] == 1


def test_unexpected_confirm_dialog_is_dismissed(browser, site, monkeypatch):
    """削除の確認ダイアログが出ても、OKは押さない"""
    monkeypatch.setattr(W, "MODE", "run")
    s = W.Session(browser, LOGIN_ID, LOGIN_PW, [], [])
    try:
        s.login(); s.to_reserve(); s.goto(W.RESERVE + "/admin/plan/register/101")
        s.page.click(".js-plan-destroy-btn")
        assert s.page.locator("#place_name").count() == 1 and any("削除" in d for d in s.dialogs)   # 画面はそのまま残っている
    finally:
        s.close()


def test_opens_edit_screen_via_link_and_never_saves_an_empty_form(browser, site, monkeypatch):
    """編集画面は一覧の「編集」リンクから開く（URLを直接開くと空になる）。空のフォームは決して保存しない"""
    monkeypatch.setattr(W, "MODE", "run")
    res, _, _ = run(browser, [T(1, "intro_catch", text="新キャッチ")])
    assert res[1][0], res
    assert site["intro"] == {"catch": "新キャッチ", "text": "旧紹介文"}          # 触っていない紹介文は消えていない
    assert site["bad_save"] is False

    s = W.Session(browser, LOGIN_ID, LOGIN_PW, [], [])                          # URLを直接開いた場合は本物と同じく空
    try:
        s.login(); s.goto(W.OWNER + "/se/introduction/edit.php")
        assert s.page.input_value("#intro40") == "" and s.page.input_value("#intro300") == ""
    finally:
        s.close()

    site["edit_link_broken"] = True                                             # リンクから開いても空だった場合（想定外の画面）
    res, pages, _ = run(browser, [T(2, "intro_catch", text="さらに新しいキャッチ")])
    assert res[2][0] is False and "保存せずに中止" in res[2][1]
    assert site["intro"] == {"catch": "新キャッチ", "text": "旧紹介文"} and site["intro_draft"] is None and site["bad_save"] is False


def test_recon_reports_selector_checks(browser, site, monkeypatch):
    monkeypatch.setattr(W, "MODE", "recon")
    _, pages, log = run(browser, [])
    by = {p["name"].split(" ", 1)[1]: {c["selector"]: c for c in p["checks"]} for p in pages}
    assert by["owner_publish"]["a[rel='OwnerIntroduction']"]["count"] == 1
    assert by["owner_intro_list"]["div.introduce_field a.edit"]["count"] == 1
    assert by["owner_intro_edit"]["#intro40"]["first"]["value"] == "旧キャッチ"      # 「編集」リンクから開くので文言が入っている
    assert by["owner_intro_edit"]["form#introduction input[name=update_date]"]["first"]["text"] == "（値あり）"
    assert any("キャッチコピー 5文字 / 紹介文 4文字" in line for line in log)
    assert by["reserve_plan_list"]["#list-plan-publish tr.list-item a[href*='/admin/plan/register/']"]["count"] == 3
    assert by["reserve_plan_edit"]["form.validate input[name=confirm]"]["first"]["value"] == "この内容で確認する"
    assert by["reserve_plan_edit"]["#plan_available"]["first"]["checked"] is True


def test_register_button_is_not_guessed_when_ambiguous(browser, site, monkeypatch):
    """確認画面に種類の違う登録系ボタンが2つあったら、どちらも押さずに中止する"""
    monkeypatch.setattr(W, "MODE", "run")
    orig = W.BTN_JS
    monkeypatch.setattr(W, "BTN_JS", orig.replace("return", "return").replace(
        "}))", "})).concat([{name:'delete_plan', label:'登録を取り消す', in_form:true, action:'', shown:true}])"))
    res, _, log = run(browser, [T(1, "plan_name", NAME_OLD, NAME_NEW)])
    assert res[1][0] is False and "登録ボタンを特定できなかった" in res[1][1]
    assert site["plans"]["103"]["name"] == NAME_OLD and site["plan_updates"] == 0
