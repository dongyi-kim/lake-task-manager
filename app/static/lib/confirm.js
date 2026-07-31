// confirm.js — 앱 안에서 쓰는 확인 창.
//
// **window.confirm 을 쓰면 안 된다.** 앱 창은 Playwright 가 띄운 Chromium 이고, Playwright 는
// 브라우저 대화상자를 기본으로 **자동 거절**한다 — confirm() 이 사람에게 뜨지도 않고 곧장
// false 로 돌아온다. 그래서 앱 창에서는 '삭제' 를 눌러도 아무 일이 없었다(크롬에선 멀쩡했다).
// 자동 수락으로 바꾸는 방법도 있지만, 그러면 우리가 모르는 대화상자까지 전부 수락하게 된다.
//
// 반환은 Promise<boolean> — 쓰는 쪽 코드 모양은 window.confirm 과 거의 같다.
import { fromBackdrop } from "./backdrop.js";
import { typeLabel, TYPE_BG } from "./colors.js";

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
    // 대상 티켓을 카드 행으로 — [ 번호 | 제목 (타입뱃지) | 담당자 ]. 값은 전부 textContent(위와 같은 이유).
    // 폭이 부족해 제목이 말줄임되면 **타입뱃지 → 담당자** 순으로 생략한다.
    // 여러 개(o.tickets)면 세로로 쌓고, 3개 초과면 3개까지만 보이고 [+N개 더]로 접는다.
    const tickets = (o.tickets || (o.ticket ? [o.ticket] : [])).filter((t) => t && t.key);
    if (tickets.length) {
      const mk = (cls, text) => { const s = document.createElement("span"); s.className = cls; s.textContent = text; return s; };
      const box = document.createElement("div");
      box.className = "cfm-tkts";
      const FOLD_AT = 3;
      const titles = [];
      tickets.forEach((t, i) => {
        const row = document.createElement("div");
        row.className = "cfm-tkt";
        if (tickets.length > FOLD_AT && i >= FOLD_AT) row.classList.add("folded");
        row.appendChild(mk("cfm-tk", t.key));
        const tt = mk("cfm-tt", t.summary || "");
        row.appendChild(tt);
        let badge = null, asg = null;
        if (t.type) {
          badge = mk("tbadge v-solid", typeLabel(t.type));
          badge.style.setProperty("--tc", TYPE_BG[t.type] || "var(--ty-task)");
          row.appendChild(badge);
        }
        if (t.assignee) { asg = mk("cfm-ta", t.assignee); row.appendChild(asg); }
        titles.push({ tt, badge, asg });
        box.appendChild(row);
      });
      if (tickets.length > FOLD_AT) {
        const more = document.createElement("button");
        more.type = "button"; more.className = "cfm-more";
        more.textContent = "+" + (tickets.length - FOLD_AT) + "개 더 보기";
        more.addEventListener("click", () => {
          box.querySelectorAll(".cfm-tkt.folded").forEach((r) => r.classList.remove("folded"));
          more.remove();
        });
        box.appendChild(more);
      }
      ov.querySelector(".cfm-m").after(box);
      requestAnimationFrame(() => {           // 렌더 후 실측 — 말줄임이 생기면 뱃지 → 담당자 순으로 접는다
        for (const { tt, badge, asg } of titles) {
          const tight = () => tt.scrollWidth > tt.clientWidth + 1;
          if (tight() && badge) badge.style.display = "none";
          if (tight() && asg) asg.style.display = "none";
        }
      });
    }
    // 하단 안내문(작게, 버튼 위) — 예: '하위 Task 는 개별 완료처리가 필요합니다'
    if (o.note) {
      const nt = document.createElement("div");
      nt.className = "cfm-note";
      nt.textContent = o.note;
      ov.querySelector(".cfm-f").before(nt);
    }
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
    // 배경 클릭으로 닫되, **누른 곳과 뗀 곳이 모두 배경일 때만** — 창 안에서 글자를 끌다가
    // 손이 밖에서 떨어졌을 뿐인데 닫히면 그건 취소가 아니라 사고다.
    ov.addEventListener("click", (e) => { if (fromBackdrop(e)) done(false); });
    window.addEventListener("keydown", onKey, true);
    document.body.appendChild(ov);
    // 되돌릴 수 없는 일이면 **취소에** 손이 먼저 가야 한다 — 기본 포커스를 취소에 둔다.
    (o.danger ? cancel : ok).focus();
  });
}
