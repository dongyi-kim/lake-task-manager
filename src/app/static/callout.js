/*
 * callout.js — 각 화면 상단에 "이 화면의 산식" 정보성 callout 을 주입.
 *  - 3개 페이지 공용. location.pathname 으로 해당 화면 산식을 고른다.
 *  - 접힌 상태에서도 핵심 1줄은 보이고, 클릭하면 상세가 펼쳐진다(보고 중 참고용).
 *  - 페이지 공용 CSS 변수(--panel/--border/--accent/--muted)를 그대로 사용 → 라이트/다크 자동.
 */
(function () {
  var CSS =
    ".fcallout{border:1px solid var(--border);border-left:3px solid var(--accent);background:var(--panel);" +
    "border-radius:10px;padding:9px 13px;margin:0 0 16px;font-size:12.5px;box-shadow:var(--shadow)}" +
    ".fcallout .fhead{display:flex;align-items:baseline;gap:8px;cursor:pointer;flex-wrap:wrap}" +
    ".fcallout .fico{color:var(--accent);font-weight:700;flex:none}" +
    ".fcallout .fcore{color:var(--muted);font-family:'Consolas','Menlo',monospace;font-size:12px}" +
    ".fcallout .ftog{margin-left:auto;color:var(--accent);font-size:12px;white-space:nowrap;flex:none;user-select:none}" +
    ".fcallout .fbody{margin-top:9px;padding-top:9px;border-top:1px dashed var(--border);color:var(--muted);line-height:1.7}" +
    ".fcallout .fbody pre{margin:0 0 8px;padding:9px 11px;background:var(--panel-2,rgba(127,127,127,.09));" +
    "border-radius:7px;overflow-x:auto;font-family:'Consolas','Menlo',monospace;font-size:12px;color:var(--text);white-space:pre}" +
    ".fcallout .fbody ul{margin:0;padding-left:17px}.fcallout .fbody li{margin:2px 0}" +
    ".fcallout .fbody b{color:var(--text)}";

  var PAGES = {
    "/": {
      core: "Epic = Σ(완료 SP)/Σ(전체 SP) · WBS = Epic들의 가중평균",
      pre:
        "① Epic 진척률   = Σ(자식 SP, 상태=Done) / Σ(자식 SP, 전체)\n" +
        "② WBS Task(모듈) = Σ(Epic 진척률 × weight) / Σ(weight)\n" +
        "③ 모듈 / PMO 전체 = 하위(WBS·Epic) 진척률의 상위 집계",
      notes: [
        "<b>완료 판정</b> = statusCategory=Done(Resolved/Closed). 상태 이름이 아닌 카테고리 기준.",
        "<b>부분 크레딧 없음</b> — In Progress는 미완료. Done이냐 아니냐 이진.",
        "<b>weight</b> 는 상대 정수(합이 1이 아니어도 자동 정규화). 예) 6·4 → 60%:40%.",
        "<b>Mock(추정 SP)</b> 는 분모에만 반영·별도 표기. SP 빈칸 기본값 Bug→0 / 그 외→1.",
      ],
    },
    "/vit.html": {
      core: "현안 진척 = 자손 완료 개수 / 자손 전체 개수  (개수 기반)",
      pre: "현안 진척률 = (자손 티켓 중 상태=Done 개수) / (자손 티켓 전체 개수)",
      notes: [
        "<b>개수(count) 기반</b> — WBS/Epic의 SP 기반과 목적이 다른 데일리 지표. 섞지 말 것.",
        "자손 = 그 현안(<code>PMO_VIT</code> 라벨) 아래 모든 자손(Epic→티켓→하위티켓).",
        "<b>중복 방지</b> — 조상에 이미 PMO_VIT면 그 자손 현안은 자동 스킵.",
      ],
    },
    "/workload.html": {
      core: "막대 = 진행중 / 7일완료 × Task성 / VoC성  (건수)",
      pre:
        "진행중 Task성 = 담당 & 진행중 & (Task 또는 Sub-Task) 개수\n" +
        "진행중 VoC성  = 담당 & 진행중 & Component=사용자 VoC 개수\n" +
        "7일완료 Task/VoC = 위와 같되 최근 7일 내 완료(Done)",
      notes: [
        "<b>Task성</b> = 이슈타입 Task+Sub-Task. <b>VoC성</b> = Component가 사용자 VoC.",
        "<b>완료 건은 반투명</b>으로 진행중과 구분. 담당(Assignee) 기준 카운트.",
      ],
    },
  };

  function esc(s) { return s; }

  document.addEventListener("DOMContentLoaded", function () {
    var path = location.pathname.replace(/\/index\.html$/, "/");
    var d = PAGES[path] || PAGES["/"];
    var nav = document.querySelector("nav");
    if (!d || !nav) return;

    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var box = document.createElement("div");
    box.className = "fcallout";
    var notesHtml = d.notes.map(function (n) { return "<li>" + n + "</li>"; }).join("");
    box.innerHTML =
      '<div class="fhead"><span class="fico">&#9432;</span>' +
      '<span class="fcore">' + esc(d.core) + "</span>" +
      '<span class="ftog">산식 자세히 &#9662;</span></div>' +
      '<div class="fbody" hidden><pre>' + esc(d.pre) + "</pre><ul>" + notesHtml + "</ul></div>";

    // nav 바로 아래에 삽입
    nav.parentNode.insertBefore(box, nav.nextSibling);

    var head = box.querySelector(".fhead");
    var body = box.querySelector(".fbody");
    var tog = box.querySelector(".ftog");
    head.addEventListener("click", function () {
      var open = body.hasAttribute("hidden");
      if (open) { body.removeAttribute("hidden"); tog.innerHTML = "접기 &#9652;"; }
      else { body.setAttribute("hidden", ""); tog.innerHTML = "산식 자세히 &#9662;"; }
    });
  });
})();
