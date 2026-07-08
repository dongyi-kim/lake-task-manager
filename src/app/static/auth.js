/*
 * auth.js — prod SSO 로그인 가드 (mock/local 에선 무해).
 *  - 로드 시 /api/health 로 needLogin 확인 → 오버레이.
 *  - window.fetch 를 감싸 어떤 API 든 401 {needLogin:true} 면 오버레이(세션 만료 포함).
 *  - "SSO 로그인" 버튼 → POST /api/login (서버가 설치된 Chrome 을 띄워 로그인 감지 후 세션 저장).
 *    완료되면 자동 새로고침.
 */
(function () {
  var shown = false;

  function el(tag, css, txt) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (txt != null) e.textContent = txt;
    return e;
  }

  function overlay() {
    if (shown) return document.getElementById("__authov");
    shown = true;
    var ov = el("div", "position:fixed;inset:0;z-index:99999;display:flex;align-items:center;" +
      "justify-content:center;background:rgba(17,20,28,.72);backdrop-filter:blur(3px);" +
      "font-family:system-ui,-apple-system,'Segoe UI',sans-serif");
    ov.id = "__authov";
    var card = el("div", "background:#fff;color:#1b2330;border-radius:14px;padding:30px 34px;" +
      "max-width:420px;box-shadow:0 18px 60px rgba(0,0,0,.4);text-align:center");
    card.appendChild(el("div", "font-size:34px;margin-bottom:6px", "🔒"));
    card.appendChild(el("div", "font-size:18px;font-weight:700;margin-bottom:8px", "사내 Jira SSO 로그인 필요"));
    var msg = el("div", "font-size:13.5px;line-height:1.6;color:#4a5568;margin-bottom:20px",
      "세션이 없거나 만료되었습니다. 아래 버튼을 누르면 브라우저 창이 열립니다. " +
      "사내 SSO/인증서 로그인을 완료하면 자동으로 이어집니다.");
    msg.id = "__authmsg";
    card.appendChild(msg);
    var btn = el("button", "background:#2f6df5;color:#fff;border:0;border-radius:9px;" +
      "padding:11px 26px;font-size:14px;font-weight:600;cursor:pointer", "SSO 로그인");
    btn.id = "__authbtn";
    btn.onclick = doLogin;
    card.appendChild(btn);
    ov.appendChild(card);
    document.body.appendChild(ov);
    return ov;
  }

  function setBusy(text) {
    var b = document.getElementById("__authbtn");
    var m = document.getElementById("__authmsg");
    if (b) { b.disabled = true; b.style.opacity = ".6"; b.style.cursor = "default"; b.textContent = "로그인 진행 중…"; }
    if (m && text) m.textContent = text;
  }

  function doLogin() {
    setBusy("브라우저 창에서 사내 SSO 로그인을 끝까지 완료하세요. 완료를 감지하면 자동으로 새로고침합니다…");
    // 서버가 Chrome 을 띄우고 로그인 완료를 폴링 감지할 때까지 응답을 보류(최대 수 분).
    _fetch("/api/login", { method: "POST" })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (b) { return { s: r.status, b: b }; }); })
      .then(function (o) {
        if (o.s === 200 && o.b && o.b.ok) { location.reload(); return; }
        var m = document.getElementById("__authmsg");
        var b = document.getElementById("__authbtn");
        if (m) m.textContent = "로그인이 완료되지 않았습니다(시간 초과 또는 취소). 다시 시도하세요.";
        if (b) { b.disabled = false; b.style.opacity = "1"; b.style.cursor = "pointer"; b.textContent = "다시 로그인"; }
      })
      .catch(function () {
        var b = document.getElementById("__authbtn");
        if (b) { b.disabled = false; b.style.opacity = "1"; b.style.cursor = "pointer"; b.textContent = "다시 로그인"; }
      });
  }

  // 원본 fetch 보존 후 래핑 — 어떤 API 호출이든 401 needLogin 이면 오버레이.
  var _fetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    return _fetch(input, init).then(function (resp) {
      if (resp.status === 401) {
        resp.clone().json().then(function (b) { if (b && b.needLogin) overlay(); }).catch(function () {});
      }
      return resp;
    });
  };

  // 로드 시 사전 점검 — 첫 API 호출 전에 미리 안내.
  document.addEventListener("DOMContentLoaded", function () {
    _fetch("/api/health").then(function (r) { return r.json(); })
      .then(function (h) { if (h && h.needLogin) overlay(); })
      .catch(function () {});
  });
})();
