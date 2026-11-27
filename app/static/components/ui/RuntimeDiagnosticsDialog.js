import { api } from "../../lib/api.js";

const EVENTS = {
  component_failed: "오류 감지", runtime_stalled: "응답 지연", runtime_responsive: "응답 회복",
  worker_started: "앱 시작", worker_stopped: "앱 중지", worker_spawned: "앱 실행",
  worker_exit: "앱 종료", worker_recovery: "앱 복구", worker_cooldown: "복구 대기",
  worker_spawn_failed: "앱 실행 실패", fatal_error: "프로세스 오류",
  browser_recovery_started: "앱 창 복구", browser_recovery_completed: "앱 창 복구 완료",
  browser_recovery_failed: "앱 창 복구 실패", browser_recovery_cancelled: "앱 창 복구 중지",
  mcp_recovery_started: "MCP 복구", mcp_recovery_completed: "MCP 복구 완료",
  mcp_recovery_failed: "MCP 복구 실패", mcp_worker_started: "MCP 시작",
};

export default {
  name: "RuntimeDiagnosticsDialog",
  emits: ["close"],
  data: () => ({ files: [], records: [], truncated: false, loading: false, error: "" }),
  mounted() {
    this._alive = true;
    this._previousFocus = document.activeElement;
    document.addEventListener("keydown", this._keyHandler = event => this.keydown(event), true);
    this.$nextTick(() => this.$refs.closeButton?.focus());
    this.load();
  },
  unmounted() {
    this._alive = false;
    document.removeEventListener("keydown", this._keyHandler, true);
    if (this._previousFocus?.isConnected) this._previousFocus.focus();
  },
  methods: {
    async load() {
      if (this.loading) return;
      this.loading = true; this.error = "";
      try {
        const state = await api.raw("/api/app/diagnostics");
        if (!this._alive) return;
        this.files = state.files || []; this.records = state.records || [];
        this.truncated = !!state.truncated;
      } catch (error) {
        if (this._alive) this.error = "오류 기록을 불러오지 못했습니다. 새로고침해 주세요.";
      } finally { if (this._alive) this.loading = false; }
    },
    label(row) { return EVENTS[row.event] || row.event; },
    timestamp(value) {
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? "시각 미확인" : date.toLocaleString("ko-KR");
    },
    details(row) {
      const { at, event, file, ...fields } = row;
      return JSON.stringify(fields, null, 2);
    },
    downloadUrl(name) { return "/api/app/diagnostics/" + encodeURIComponent(name) + "/download"; },
    keydown(event) {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); this.$emit("close"); }
      if (event.key !== "Tab") return;
      const controls = [...this.$refs.dialog.querySelectorAll("button, a, summary")]
        .filter(element => !element.matches(":disabled") && element.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && (document.activeElement === first || !this.$refs.dialog.contains(document.activeElement))) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !this.$refs.dialog.contains(document.activeElement))) {
        event.preventDefault(); first?.focus();
      }
    },
  },
  template: `<Teleport to="body"><div class="ag-back" @click.self="$emit('close')">
    <section class="ag-dlg" ref="dialog" role="dialog" aria-modal="true" aria-labelledby="runtime-diagnostics-title"
             style="width:min(760px,94vw);max-height:90vh;overflow:auto;padding:24px;gap:16px;word-break:keep-all;overflow-wrap:anywhere">
      <header style="display:flex;align-items:start;justify-content:space-between;gap:16px">
        <div><h2 id="runtime-diagnostics-title" style="margin:0 0 8px">오류 기록</h2>
          <p style="margin:0">앱 종료·응답 지연·복구 기록을 이 컴퓨터에 보관합니다.</p></div>
        <button class="btn" ref="closeButton" aria-label="오류 기록 닫기" @click="$emit('close')">닫기</button>
      </header>
      <p style="margin:0;color:var(--muted)">오류 종류와 실행 위치를 표시합니다. 티켓 본문과 인증 정보는 기록하지 않습니다.</p>
      <div style="display:flex;align-items:center;gap:12px">
        <button class="btn" :disabled="loading" @click="load">{{ loading ? '불러오는 중…' : '새로고침' }}</button>
        <span v-if="truncated">최근 기록만 표시합니다. 다운로드에도 같은 범위를 적용합니다.</span>
      </div>
      <p v-if="error" role="alert" style="margin:0;color:var(--red,#c0392b)">{{ error }}</p>
      <p v-if="!loading && !error && !records.length" role="status">저장된 오류·복구 기록이 없습니다.</p>
      <div v-if="files.length" aria-label="오류 기록 다운로드" style="display:flex;flex-wrap:wrap;gap:8px 16px">
        <a v-for="file in files" :key="file.name" :href="downloadUrl(file.name)" download
           :aria-label="file.name + ' 다운로드'">{{ file.name }} 다운로드</a>
      </div>
      <ol v-if="records.length" aria-label="최근 오류와 복구" style="list-style:none;margin:0;padding:0">
        <li v-for="(row,index) in records" :key="row.file + ':' + index" style="padding:12px 0;border-top:1px solid var(--line)">
          <details><summary style="cursor:pointer;line-height:1.7">
            <time :datetime="row.at">{{ timestamp(row.at) }}</time> · {{ label(row) }}
            <span v-if="row.component"> · {{ row.component }}</span><span v-if="row.errorType"> · {{ row.errorType }}</span>
          </summary>
          <p style="color:var(--muted);margin:8px 0">{{ row.file }}</p>
          <pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.6;margin:0">{{ details(row) }}</pre>
          </details>
        </li>
      </ol>
    </section></div></Teleport>`,
};
