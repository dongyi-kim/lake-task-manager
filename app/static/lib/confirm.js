// confirm.js — 앱 안에서 쓰는 확인 창.
//
// **window.confirm 을 쓰면 안 된다.** 앱 창은 Playwright 가 띄운 Chromium 이고, Playwright 는
// 브라우저 대화상자를 기본으로 **자동 거절**한다 — confirm() 이 사람에게 뜨지도 않고 곧장
// false 로 돌아온다. 그래서 앱 창에서는 '삭제' 를 눌러도 아무 일이 없었다(크롬에선 멀쩡했다).
// 자동 수락으로 바꾸는 방법도 있지만, 그러면 우리가 모르는 대화상자까지 전부 수락하게 된다.
//
// 반환은 Promise<boolean> — 쓰는 쪽 코드 모양은 window.confirm 과 거의 같다.
export function confirmBox(message, opts) {
  const o = opts || {};
  return new Promise((resolve) => {
    const ov = document.createElement("div");
    ov.className = "cfm-ov";
    ov.innerHTML =
      '<div class="cfm" role="alertdialog" aria-modal="true">'
      + '<div class="cfm-m"></div>'
      + '<div class="cfm-f">'
      + '<button type="button" class="cfm-b cancel"></button>'
      + '<button type="button" class="cfm-b ok"></button>'
      + "</div></div>";
    // 메시지는 **텍스트로** 넣는다 — 티켓 제목이 그대로 들어오는 자리라 HTML 로 두면
    // 남이 쓴 제목이 우리 화면의 마크업이 된다.
    ov.querySelector(".cfm-m").textContent = String(message == null ? "" : message);
    const ok = ov.querySelector(".cfm-b.ok");
    const cancel = ov.querySelector(".cfm-b.cancel");
    ok.textContent = o.okLabel || "확인";
    cancel.textContent = o.cancelLabel || "취소";
    if (o.danger) ok.classList.add("danger");

    const done = (v) => {
      window.removeEventListener("keydown", onKey, true);
      ov.remove();
      resolve(v);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); done(false); }
      else if (e.key === "Enter") { e.stopPropagation(); done(true); }
    };
    ok.addEventListener("click", () => done(true));
    cancel.addEventListener("click", () => done(false));
    ov.addEventListener("click", (e) => { if (e.target === ov) done(false); });
    window.addEventListener("keydown", onKey, true);
    document.body.appendChild(ov);
    // 되돌릴 수 없는 일이면 **취소에** 손이 먼저 가야 한다 — 기본 포커스를 취소에 둔다.
    (o.danger ? cancel : ok).focus();
  });
}
