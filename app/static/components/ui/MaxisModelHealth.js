// Service API probes prove only the synthetic path, not model identity or every
// generation branch. Keep both settings entry points on the same presentation.
export default {
  name: "MaxisModelHealth",
  props: {
    workflows: { type: Array, required: true },
    probe: { type: Object, default: null },
  },
  computed: {
    checkedAt() {
      const at = this.probe && this.probe.at;
      return typeof at === "string" && Number.isFinite(Date.parse(at)) ? at : "검사 기록 없음";
    },
    rows() {
      const results = (this.probe && this.probe.workflows) || {};
      return this.workflows.map((workflow) => {
        const health = results[workflow.key] && results[workflow.key].modelHealth;
        const hasQuery = health && health.scope === "synthetic_control_and_query";
        const knownScope = hasQuery || (health && health.scope === "synthetic_control_path");
        const status = knownScope ? health.status : "not_checked";
        const label = status === "passed" ? (hasQuery ? "제어·실제 Query 경로 통과" : "합성 모델 응답 통과") :
          status === "failed" ? "합성 모델 응답 실패" : "모델 응답 미검사";
        return { key: workflow.key, label: workflow.label, status: label,
          nextAction: status !== "passed" && health && health.nextAction
            ? health.nextAction : status === "passed" ? "" : "연결·모델 검사를 실행하세요." };
      });
    },
  },
  template: `
  <section class="mx-runtime-status" aria-label="선택한 프로필의 모델 응답 검사">
    <p class="mx-help" style="margin:0">마지막 연결·모델 검사: {{ checkedAt }}</p>
    <div v-for="row in rows" :key="row.key" class="mx-runtime-row">
      <b>{{ row.label }}</b><span>{{ row.status }}</span>
      <small v-if="row.nextAction">{{ row.nextAction }}</small>
    </div>
    <p class="mx-help" style="margin:0">합성 입력으로 Core의 제어·실제 Query 경로를 검사합니다. 조회 계획만 만들며 Jira 조회·쓰기는 실행하지 않습니다. 모델 변경 후에는 재검사하세요. 전체 LLM 노드·본문·댓글 생성 품질을 검증한 결과는 아닙니다. Service API가 실제 모델 이름을 제공하지 않습니다.</p>
  </section>`,
};
