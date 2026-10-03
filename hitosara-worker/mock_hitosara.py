"""テスト用：ヒトサラ管理画面の模型（2026年10月の調査仕様書どおりの要素名で作った偽サイト）。
本物には一切アクセスしない。調査で「未確認」だった部分（プランの確認・完了画面、保存後の遷移先）は推定で作ってある。
"""
from flask import Flask, jsonify, redirect, request, session

LOGIN_ID, LOGIN_PW = "0020004633", "pw"


def make_app():
    app = Flask(__name__)
    app.secret_key = "mock"
    st = app.state = {
        "intro": {"catch": "旧キャッチ", "text": "旧紹介文"}, "intro_draft": None,
        "seat_unreflect": True,              # 調査前から残っている「座席情報」の未反映
        "reflect_calls": [], "plan_updates": 0, "bad_save": False, "stock_opened": False,
        "plans": {
            "101": {"name": "【1番人気】鍋無10品/3H飲み放題＆個室確約", "price": 5980, "sale": 4000, "same": False, "pub": 1},
            "102": {"name": "【全席完全個室】お席のみ予約◆個室をご用意！", "price": 1, "sale": 1, "same": True, "pub": 1},
            "103": {"name": "【鹿児島の旬の味覚】地鶏の炭火たたきなど鍋無8品", "price": 4000, "sale": 4000, "same": True, "pub": 1},
            "201": {"name": "【1番人気】鍋無10品/3H飲み放題＆個室確約", "price": 4500, "sale": 4500, "same": True, "pub": 0},  # 同名の非掲載
        },
        "pending": None,
    }

    def owner_guard():
        if not session.get("owner"):
            return """<title>ログイン | ヒトサラ お店管理</title><form action="/se/login.php" method="post">
              <input type="hidden" name="request" value="x"><input type="text" name="user" maxlength="15">
              <input type="password" name="password" maxlength="32"><input type="checkbox" name="save" id="save">
              <input type="submit" value="ログイン"></form>"""

    @app.route("/se/")
    def se_top():
        return owner_guard() or "<h1>お店管理トップ</h1>"

    @app.route("/se/login.php", methods=["POST"])
    def se_login():
        if request.form.get("user") == LOGIN_ID and request.form.get("password") == LOGIN_PW:
            session["owner"] = True
        return redirect("/se/")

    @app.route("/se/introduction/")
    def intro_list():
        # 本物と同じ作り：「編集」は href="#" で、スクリプトが form#_send（POST、kamei_cd と edit=true）を作って送信する
        cur = st["intro_draft"] or st["intro"]
        return owner_guard() or f"""<h1>お店の紹介文</h1><div class="introduce_field">
          <p class="copy">{cur['catch']}</p><p class="about">{cur['text']}</p><a class="edit" href="#">編集</a></div>
        <script>document.querySelector('a.edit').onclick=function(e){{e.preventDefault();
          var f=document.createElement('form'); f.id='_send'; f.method='POST'; f.action='/se/introduction/edit.php';
          [['kamei_cd','x'],['edit','true']].forEach(function(kv){{var i=document.createElement('input');i.type='hidden';i.name=kv[0];i.value=kv[1];f.appendChild(i)}});
          document.body.appendChild(f); f.submit();}};</script>"""

    @app.route("/se/introduction/edit.php", methods=["GET", "POST"])
    def intro_edit():
        g = owner_guard()
        if g: return g
        if request.method == "POST" and "intro40" in request.form:      # 保存
            if not request.form.get("tenpo_seq") or not request.form.get("update_date"):
                st["bad_save"] = True                                    # 空のフォームからの保存（本番では文言が消えるおそれ）
            new = {"catch": request.form["intro40"], "text": request.form["intro300"]}
            if new != st["intro"]:
                st["intro_draft"] = new
            return redirect("/se/introduction/")
        # 一覧の「編集」から来たとき（edit=true）だけ文言と管理番号が入る。URLを直接開くと空（本番で確認した動き）
        via_link = request.method == "POST" and request.form.get("edit") == "true" and not st.get("edit_link_broken")
        cur = (st["intro_draft"] or st["intro"]) if via_link else {"catch": "", "text": ""}
        hid = "x" if via_link else ""
        return f"""<form id="introduction" action="/se/introduction/edit.php" method="post" enctype="multipart/form-data">
          <input type="hidden" name="confirm" value="1"><input type="hidden" name="kamei_cd" value="x">
          <input type="hidden" name="tenpo_seq" value="{hid}"><input type="hidden" name="update_date" value="{hid}">
          <input type="text" name="intro40" id="intro40" maxlength="80" value="{cur['catch']}">
          <textarea name="intro300" id="intro300" maxlength="600">{cur['text']}</textarea>
          <input type="submit" id="cancel" class="result_back" value="戻る">
          <input type="submit" id="edit" class="result_submit" value="保存"></form>
        <script>
          document.getElementById('cancel').onclick=function(e){{e.preventDefault();location.href='/se/introduction/'}};
          document.getElementById('edit').onclick=function(e){{
            if(!document.getElementById('intro40').value){{e.preventDefault();
              alert('データに不備があるため保存できませんでした。\\n・お店のキャッチコピーが入力されていません');}}}};
        </script>"""

    @app.route("/se/publish.php")
    def publish():
        g = owner_guard()
        if g: return g
        def row(title, manager, unreflect):
            status = '<td class="status unreflect">変更が未反映</td>' if unreflect else '<td class="status">最終反映日2026/10/01</td>'
            return (f'<tr><td class="title">{title}</td>{status}<td class="edit"><a href="#">編集</a></td>'
                    f'<td class="open"><a id="publish" rel="{manager}" href="#">公開する</a></td></tr>')
        return f"""<div class="global-link-publish exist_alert"><a href="/se/publish.php">お店情報の更新<span class="alert">未反映の更新があります</span></a></div>
        <a id="reset" rel="all" href="#">全ての情報を戻す</a> <a id="publish" rel="all" href="#">全ての情報を公開</a>
        <div class="func_wrapper"><table>{row('お店の紹介文', 'OwnerIntroduction', st['intro_draft'] is not None)}
        {row('座席情報', 'OwnerSeat', st['seat_unreflect'])}</table></div>
        <script>document.querySelectorAll('a#publish').forEach(function(a){{a.onclick=function(e){{e.preventDefault();
          if(!confirm('反映してもよろしいですか？')) return;
          var m=a.getAttribute('rel');
          fetch('/?action_owner_reflection_reflect=true'+(m==='all'?'':'&manager='+m)).then(r=>r.json()).then(function(j){{
            if(j.status==='OK'){{alert('公開が完了しました');
              var td=a.closest('tr')&&a.closest('tr').querySelector('td.status'); if(td){{td.className='status';td.textContent='最終反映日2026/10/03'}}}}
            else alert('エラーが発生したため、処理を中断しました');}});}}}});</script>"""

    @app.route("/")
    def root():
        if request.args.get("action_owner_reflection_reflect"):
            if not session.get("owner"): return jsonify(status="NG")
            m = request.args.get("manager", "all")
            st["reflect_calls"].append(m)
            if m in ("all", "OwnerIntroduction") and st["intro_draft"]:
                st["intro"], st["intro_draft"] = st["intro_draft"], None
            if m in ("all", "OwnerSeat"):
                st["seat_unreflect"] = False
            return jsonify(status="OK")
        return "root"

    @app.route("/se/reserveBridge.php")
    def bridge():
        if session.get("owner"):
            session["reserve"] = True
        return redirect("/admin/top")

    @app.route("/admin/top")
    def admin_top():
        return "<h1>予約管理トップ</h1>" if session.get("reserve") else redirect("/admin/auth/")

    @app.route("/admin/auth/")
    def admin_auth():
        return """<title>ネット予約管理ログイン[ヒトサラ]</title><form action="login" method="post">
          <input type="hidden" name="csrf_token" value="t"><input type="text" name="login_id">
          <input type="password" name="password"><input type="submit" value="ログイン"></form>"""

    @app.route("/admin/plan/")
    def plan_list():
        if not session.get("reserve"): return redirect("/admin/auth/")
        def rows(pub):
            return "".join(f'<tr class="list-item"><td><a href="/admin/plan/register/{i}">{p["name"]}</a></td></tr>'
                           for i, p in st["plans"].items() if p["pub"] == pub)
        note = ('<div class="oc-alert is-success notification"><p class="oc-alert__message">プランを更新しました。</p></div>'
                if request.args.get("updated") else "")
        return note + f"""<a id="plan-register" href="/admin/plan/register/">プランを登録</a>
          <table id="list-plan-publish"><tbody id="sort-table">{rows(1)}</tbody></table>
          <h3>非掲載のプラン</h3><table>{rows(0)}</table>"""

    @app.route("/admin/plan/register/<pid>")
    def plan_edit(pid):
        if not session.get("reserve"): return redirect("/admin/auth/")
        p = st["plans"][pid]
        return f"""<form class="validate" action="/admin/plan/confirm/" method="post" enctype="multipart/form-data">
          <input type="hidden" name="plan[plan_id]" value="{pid}"><input type="hidden" name="csrf_token" value="t">
          <input type="radio" name="plan[plan_publish_flag]" id="plan_available" value="1" {'checked' if p['pub'] else ''}>
          <input type="radio" name="plan[plan_publish_flag]" id="plan_unavailable" value="0" {'' if p['pub'] else 'checked'}>
          <input type="text" name="plan[plan_name]" id="place_name" maxlength="100" value="{p['name']}">
          <input type="text" name="plan[plan_price]" id="plan_price" value="{p['price']}">
          <input type="checkbox" id="no-discount" {'checked' if p['same'] else ''}>
          <input type="text" name="plan[discounted_price]" id="discounted_price" value="{p['sale']}" {'readonly' if p['same'] else ''}>
          <input type="submit" name="confirm" value="この内容で確認する">
          <button type="button" class="js-plan-destroy-btn">このプランを削除する</button></form>
        <script>var pp=document.getElementById('plan_price'),dp=document.getElementById('discounted_price'),nd=document.getElementById('no-discount');
          pp.addEventListener('input',function(){{if(nd.checked)dp.value=pp.value}});
          document.querySelector('.js-plan-destroy-btn').onclick=function(){{if(confirm('このプランを削除してもよろしいですか？'))document.body.innerHTML='DELETED'}};</script>"""

    @app.route("/admin/plan/confirm/", methods=["POST"])
    def plan_confirm():
        f = request.form
        st["pending"] = {"id": f["plan[plan_id]"], "name": f["plan[plan_name]"], "price": int(f["plan[plan_price]"]),
                         "sale": int(f["plan[discounted_price]"]), "pub": int(f["plan[plan_publish_flag]"])}
        d = st["pending"]
        attr = "1" if st.get("stock_modal") else ""
        return f"""<h1>プランの更新（内容確認）</h1><p>以下の内容でプランを更新します。よろしいですか？</p>
          <p>掲載状態：{'掲載' if d['pub'] else '非掲載'}</p><p class="confirm-plan-name">{d['name']}</p>
          <p>定価 {d['price']:,}円 ／ 販売価格 {d['sale']:,}円</p>
          <form id="submit_form" action="/admin/plan/update/" method="post">
            <input type="hidden" name="update_flag" value="1"><input type="hidden" name="csrf_token" value="t">
            <input type="hidden" name="auto_update_stock_flag" value="">
            <input type="submit" name="back" value="内容を修正する" class="op-input-form__action-target oc-btn oc-btn--l">
            <input type="button" name="create_plan" value="この内容で登録する" data-is-show-update-stock-modal="{attr}"
                   class="op-input-form__action-target oc-btn oc-btn--l oc-btn--success js-stock-modal">
            <div id="daily_setting_attention_modal" class="confirm_modal" style="display:none"><div class="bg"></div><div class="content">
              <div class="close-btn"></div><p>登録したプランの在庫を開放しますか？</p>
              <input type="submit" name="register_only" value="プランの登録のみ" class="oc-btn submit_btn">
              <input type="submit" name="stock_open" value="在庫も開放する" class="oc-btn stock_button submit_btn">
              <input class="stock_open" type="hidden" name="stock_open" value=""></div></div>
          </form>
          <script>document.querySelector('.js-stock-modal').onclick=function(){{
            if(this.getAttribute('data-is-show-update-stock-modal')){{document.getElementById('daily_setting_attention_modal').style.display='block';return false}}
            document.querySelector(".submit_btn[name='register_only']").click();}};
            document.querySelector('.stock_button').addEventListener('click',function(){{document.querySelector('input.stock_open').value='在庫も開放する'}});</script>"""

    @app.route("/admin/plan/update/", methods=["POST"])
    def plan_update():
        if "back" in request.form or not st["pending"]:
            return redirect("/admin/plan/")
        if any(request.form.getlist("stock_open")):
            st["stock_opened"] = True            # 「在庫も開放する」が押された（ワーカーは決して押さないこと）
        if "register_only" not in request.form and not any(request.form.getlist("stock_open")):
            return "登録ボタン以外から送信されました", 400
        d, st["pending"] = st["pending"], None
        st["plans"][d["id"]].update(name=d["name"], price=d["price"], sale=d["sale"], pub=d["pub"])
        st["plan_updates"] += 1
        return redirect("/admin/plan/?updated=1")   # 本物と同じく、一覧へ戻って帯で知らせる

    return app
