// FormulaCallout.js — 각 화면 "ⓘ 이 화면의 산식" 정보 callout(callout.js 대체). route prop 으로 내용 선택.
// 접힘 시 핵심 1줄, 펼침 시 상세(보고 중 참고용). updated: 2026-07-09
const PAGES = {
  wbs: {
    core: "Epic = Σ(완료 SP)/Σ(전체 SP) · WBS = Epic들의 가중평균",
    pre: "① Epic 진척률   = Σ(자식 SP, 상태=Done) / Σ(자식 SP, 전체)\n"
       + "② WBS Task(모듈) = Σ(Epic 진척률 × weight) / Σ(weight)\n"
       + "③ 모듈 / PMO 전체 = 하위(WBS·Epic) 진척률의 상위 집계",
    notes: [
      "<b>완료 판정</b> = statusCategory=Done(Resolved/Closed). 상태 이름이 아닌 카테고리 기준.",
      "<b>부분 크레딧 없음</b> — In Progress는 미완료. Done이냐 아니냐 이진.",
      "<b>weight</b> 는 상대 정수(합이 1이 아니어도 자동 정규화). 예) 6·4 → 60%:40%.",
      "<b>Mock(추정 SP)</b> 는 분모에만 반영. SP 빈칸 기본값 Bug→0 / 그 외→1.",
    ],
  },
  vit: {
    core: "현안 진척 = 자손 완료 개수 / 자손 전체 개수  (개수 기반)",
    pre: "현안 진척률 = (자손 티켓 중 상태=Done 개수) / (자손 티켓 전체 개수)",
    notes: [
      "<b>개수(count) 기반</b> — WBS/Epic의 SP 기반과 목적이 다른 데일리 지표. 섞지 말 것.",
      "자손 = 그 현안(<code>PMO_VIT</code> 라벨) 아래 모든 자손(Epic→티켓→하위티켓).",
      "<b>중복 방지</b> — 조상에 이미 PMO_VIT면 그 자손 현안은 자동 스킵.",
    ],
  },
  workload: {
    core: "진행 중 = 티켓 수(건) · 완료 실적 = Task 수 또는 소요시간(Time Tracking) 선택",
    pre: "진행 중   = 담당 & 진행중 티켓 수 (Task / Sub-Task / VoC)\n"
       + "완료(7일) = 담당 & 최근 7일 내 완료(Done)\n"
       + "  · Task 수  = 완료 티켓 개수\n"
       + "  · 소요시간 = 완료 티켓의 Time Tracking(timespent) 합",
    notes: [
      "<b>완료 실적 계산식</b>(우측하단 플로팅) — Task 수 / 소요시간 전환. <b>진행 중은 항상 티켓 수</b>(timespent 없음).",
      "<b>소요시간</b> = 표준 Time Tracking 필드(<code>timespent</code>, 예 2d 3h → 시간 환산). 커스텀필드 아님.",
      "막대 색 구분 — <b>Task</b> / <b>Sub-Task</b> / <b>VoC</b>(Component 사용자 VoC). 담당(Assignee) 기준. Sub-Task 도 조회·표시.",
      "막대 최대값 = 전체 인력 최대(진행중·완료 각각). <b>세로선 = 해당 모듈 평균</b>. 0은 생략.",
    ],
  },
};

export default {
  name: "FormulaCallout",
  props: { route: { type: String, default: "" } },
  data() { return { open: false }; },
  computed: { d() { return PAGES[this.route] || null; } },
  template: `
    <div v-if="d" class="fcallout">
      <div class="fhead" @click="open = !open">
        <span class="fico">&#9432;</span><span class="fcore">{{ d.core }}</span>
        <span class="ftog">{{ open ? '접기 ▲' : '산식 자세히 ▼' }}</span>
      </div>
      <div v-if="open" class="fbody">
        <pre>{{ d.pre }}</pre>
        <ul><li v-for="(n, i) in d.notes" :key="i" v-html="n"></li></ul>
      </div>
    </div>`,
};
