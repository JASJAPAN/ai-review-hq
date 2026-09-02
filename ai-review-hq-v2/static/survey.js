(() => {
  const app = document.getElementById("app");
  const slug = app.dataset.slug;
  const $ = (id) => document.getElementById(id);

  const state = { rating: 0, tags: new Set(), responseId: null, genCount: 0 };
  const CAPTIONS = { 1: "申し訳ございません…", 2: "ご不便をおかけしました",
    3: "ありがとうございます", 4: "ありがとうございます！", 5: "最高の評価をありがとうございます！" };

  // ---- Step1: 星 ----
  const stars = [...document.querySelectorAll(".star")];
  stars.forEach((btn) => btn.addEventListener("click", () => {
    state.rating = Number(btn.dataset.value);
    stars.forEach((b) => b.classList.toggle("on", Number(b.dataset.value) <= state.rating));
    $("star-caption").textContent = CAPTIONS[state.rating];
    unlock("step2"); unlock("step3");
  }));

  function unlock(id) { $(id).classList.remove("is-locked"); }

  // ---- Step2: チップ ----
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    const t = c.dataset.tag;
    if (state.tags.has(t)) { state.tags.delete(t); c.classList.remove("on"); }
    else { state.tags.add(t); c.classList.add("on"); }
  }));

  // ---- API helper ----
  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "通信に失敗しました。電波状況をご確認ください。");
    return data;
  }

  function showError(msg) { const e = $("error"); e.textContent = msg; e.hidden = !msg; }

  // ---- Step3: 生成 ----
  async function generate() {
    showError("");
    if (!state.rating) return showError("先に星の数を選択してください。");
    if (state.genCount >= 5) return showError("書き直しの上限に達しました。文章を直接編集してください。");
    const btn = $("btn-generate");
    btn.disabled = true;
    btn.querySelector(".spinner").hidden = false;
    btn.querySelector(".label").textContent = "作成中…";
    try {
      const data = await post("/api/generate", {
        slug, rating: state.rating,
        tags: [...state.tags], free_text: $("free-text").value,
      });
      state.genCount++;
      $("review").value = data.review;
      $("review-area").hidden = false;
      btn.hidden = true;
      $("review").scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (err) { showError(err.message); }
    btn.disabled = false;
    btn.querySelector(".spinner").hidden = true;
    btn.querySelector(".label").textContent = "AIで簡単に自動作成";
  }
  $("btn-generate").addEventListener("click", generate);
  $("btn-regen").addEventListener("click", () => { $("btn-generate").hidden = false; generate(); });

  // ---- 登録 → 分岐 ----
  $("btn-submit").addEventListener("click", async () => {
    showError("");
    const review = $("review").value.trim();
    if (!review) return showError("口コミ文が空です。");
    const btn = $("btn-submit");
    btn.disabled = true; btn.textContent = "登録中…";
    try {
      const data = await post("/api/submit", {
        slug, rating: state.rating, tags: [...state.tags],
        free_text: $("free-text").value, review,
      });
      state.responseId = data.response_id;
      $("view-survey").hidden = true;
      window.scrollTo({ top: 0 });
      if (data.routed === "google") {
        $("final-review").textContent = review;
        $("btn-google").href = data.google_url;
        $("view-google").hidden = false;
        copyReview(review);
      } else {
        $("view-internal").hidden = false;
      }
    } catch (err) {
      showError(err.message);
      btn.disabled = false; btn.textContent = "この内容で登録する";
    }
  });

  // ---- Google誘導画面 ----
  function copyReview(text) {
    if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
  }
  $("btn-copy").addEventListener("click", (e) => {
    copyReview($("final-review").textContent);
    e.target.textContent = "コピーしました";
    setTimeout(() => (e.target.textContent = "もう一度コピー"), 1600);
  });
  $("btn-google").addEventListener("click", () => {
    copyReview($("final-review").textContent);
    if (state.responseId)
      post("/api/google-click", { response_id: state.responseId }).catch(() => {});
  });

  // ---- 低評価画面 ----
  function finish() {
    $("view-internal").hidden = true;
    $("view-end").hidden = false;
    window.scrollTo({ top: 0 });
  }
  $("btn-improve").addEventListener("click", async () => {
    const text = $("improve-text").value.trim();
    if (text && state.responseId) {
      try { await post("/api/improve", { response_id: state.responseId, text }); } catch (_) {}
    }
    finish();
  });
  $("btn-skip").addEventListener("click", finish);
})();
