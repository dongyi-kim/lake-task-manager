import { localMcpApi } from "../../lib/localMcpApi.js";
import { copyText } from "../../lib/ticketlink.js";

export default {
  name: "LocalMcpDialog",
  emits: ["close"],
  data() {
    return { status: null, profile: "all", busy: "", error: "", notice: "", secret: "", manualCopy: "" };
  },
  computed: {
    profileUrl() { return this.status?.profiles?.find(item => item.id === this.profile)?.url || this.status?.url; },
    jiraLabel() {
      return { ready: "Jira 인증됨", login_required: "Jira 로그인 필요",
        degraded: "Jira 연결 확인 필요", unknown: "Jira 상태 미확인" }[this.status?.jiraState]
        || "Jira 상태 미확인";
    },
  },
  mounted() {
    this._alive = true;
    this._epoch = 0;
    this._previousFocus = document.activeElement;
    this._authChanged = () => { if (!this.busy) this.load(); };
    this._keyHandler = (event) => this.keydown(event);
    window.addEventListener("auth-ok", this._authChanged);
    document.addEventListener("keydown", this._keyHandler, true);
    this.$nextTick(() => this.$refs.closeButton?.focus());
    this.load();
  },
  unmounted() {
    this._alive = false;
    this.clearSecrets();
    window.removeEventListener("auth-ok", this._authChanged);
    document.removeEventListener("keydown", this._keyHandler, true);
    if (this._previousFocus?.isConnected) this._previousFocus.focus();
  },
  methods: {
    clearSecrets() { this._epoch++; this.secret = ""; this.manualCopy = ""; this.notice = ""; },
    async perform(name, action) {
      if (this.busy) return;
      this.busy = name; this.error = "";
      try { await action(); }
      catch (e) { if (this._alive) this.error = e?.message || "연결 설정을 처리하지 못했습니다."; }
      finally { if (this._alive) this.busy = ""; }
    },
    load() {
      return this.perform("load", async () => {
        this.clearSecrets();
        const status = await localMcpApi.settings();
        if (this._alive) this.status = status;
      });
    },
    configure() {
      return this.perform("configure", async () => {
        this.clearSecrets();
        const status = await localMcpApi.configure(!this.status.enabled);
        if (this._alive) {
          this.status = status;
          this.notice = status.enabled ? "로컬 MCP를 켰습니다." : "로컬 MCP 연결을 껐습니다.";
        }
      });
    },
    rotate() {
      return this.perform("rotate", async () => {
        this.clearSecrets();
        const status = await localMcpApi.rotate();
        if (this._alive) {
          this.status = status;
          this.notice = "토큰을 재발급했습니다. 에이전트의 연결 설정을 다시 복사하세요.";
        }
      });
    },
    reveal() {
      if (this.secret) { this.secret = ""; return; }
      return this.perform("reveal", async () => {
        const epoch = this._epoch;
        const result = await localMcpApi.credentials("token");
        if (this._alive && epoch === this._epoch) this.secret = result.text;
      });
    },
    copy(format) {
      return this.perform("copy", async () => {
        this.manualCopy = ""; this.notice = "";
        const epoch = this._epoch;
        const text = format === "address" ? this.profileUrl
          : (await localMcpApi.credentials(format, this.profile)).text;
        if (!this._alive || epoch !== this._epoch) return;
        if (!text) throw new Error("복사할 연결 정보가 없습니다.");
        const copied = await copyText(text);
        if (!this._alive || epoch !== this._epoch) return;
        if (copied) this.notice = format === "address" ? "서버 주소를 복사했습니다." : "인증 정보를 포함한 연결 설정을 복사했습니다.";
        else {
          this.manualCopy = text;
          this.notice = "클립보드에 접근할 수 없습니다. 아래 내용을 선택해 직접 복사하세요.";
          this.$nextTick(() => { this.$refs.manual?.focus(); this.$refs.manual?.select(); });
        }
      });
    },
    keydown(event) {
      if (event.key === "Escape") { event.stopPropagation(); this.$emit("close"); }
      if (event.key !== "Tab") return;
      const controls = [...this.$refs.dialog.querySelectorAll("button:not(:disabled), input, textarea, select:not(:disabled)")];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !this.$refs.dialog.contains(document.activeElement))) {
        event.preventDefault(); first?.focus();
      }
    },
  },
  template: `
  <Teleport to="body">
    <div class="mcp-back" @click.self="$emit('close')">
      <section class="mcp-dialog" ref="dialog" role="dialog" aria-modal="true" aria-labelledby="mcp-title">
        <header class="mcp-head">
          <h2 id="mcp-title">로컬 MCP 연결</h2>
          <button ref="closeButton" class="mcp-close" aria-label="로컬 MCP 설정 닫기" @click="$emit('close')">✕</button>
        </header>
        <div class="mcp-body">
          <p class="mcp-intro">이 PC의 에이전트가 LTM의 Jira 기능과 캐시를 함께 사용합니다.</p>
          <div v-if="!status && busy" class="mcp-note" role="status">연결 설정을 불러오는 중…</div>
          <template v-if="status">
            <div class="mcp-enable">
              <div><b>로컬 MCP 사용</b><span>{{ !status.enabled ? '꺼짐' : status.ready ? '연결 준비됨' : '연결 준비 중' }}</span></div>
              <button class="sm-switch" :class="{ on: status.enabled }" role="switch" aria-label="로컬 MCP 사용"
                      :aria-checked="status.enabled" :disabled="!!busy" @click="configure"><span class="sm-knob"></span></button>
            </div>
            <p class="mcp-note">{{ jiraLabel }} · LTM이 실행 중일 때 연결할 수 있습니다.</p>

            <label class="mcp-label" for="mcp-profile">사용할 기능</label>
            <select id="mcp-profile" v-model="profile" :disabled="!!busy" @change="clearSecrets">
              <option v-for="item in status.profiles || [{id:'all',label:'전체 기능'}]" :key="item.id" :value="item.id">{{ item.label }}</option>
            </select>
            <p class="mcp-note">필요한 도구만 연결해 에이전트의 입력량을 줄입니다. 인증과 캐시는 함께 사용합니다.</p>
            <label class="mcp-label" for="mcp-url">서버 주소</label>
            <div class="mcp-row">
              <input id="mcp-url" :value="profileUrl" readonly @focus="$event.target.select()">
              <button :disabled="!!busy" @click="copy('address')">주소 복사</button>
            </div>

            <label class="mcp-label" for="mcp-token">연결 토큰</label>
            <div class="mcp-row">
              <input id="mcp-token" :value="secret || status.tokenMasked || '••••••••'" readonly spellcheck="false"
                     autocomplete="off" :aria-label="secret ? '연결 토큰 원문' : '가려진 연결 토큰'">
              <button :disabled="!!busy" @click="reveal">{{ secret ? '토큰 가리기' : '토큰 보기' }}</button>
            </div>

            <section class="mcp-config">
              <h3>에이전트 연결 설정</h3>
              <p class="mcp-note">사용 중인 에이전트의 MCP 설정에 붙여넣으세요. HTTP를 지원하지 않는 클라이언트에서는 stdio를 선택하세요.</p>
              <div class="mcp-actions">
                <button :disabled="!!busy" @click="copy('http')">HTTP 설정 복사 · 인증 포함</button>
                <button :disabled="!!busy" @click="copy('stdio')">stdio 설정 복사 · 인증 포함</button>
              </div>
            </section>
            <div class="mcp-rotate">
              <span class="mcp-note">재발급하면 기존 토큰으로 연결할 수 없습니다.</span>
              <button class="mcp-danger" :disabled="!!busy" @click="rotate">토큰 재발급</button>
            </div>
          </template>
          <p v-if="error" class="mcp-error" role="alert">{{ error }}</p>
          <p v-if="notice" class="mcp-note" role="status">{{ notice }}</p>
          <label v-if="manualCopy" class="mcp-manual">직접 복사할 내용
            <textarea ref="manual" :value="manualCopy" readonly spellcheck="false" @focus="$event.target.select()"></textarea>
          </label>
        </div>
        <footer class="mcp-foot">
          <button :disabled="!!busy" @click="load">{{ busy === 'load' ? '확인 중…' : '상태 새로고침' }}</button>
          <button @click="$emit('close')">닫기</button>
        </footer>
      </section>
    </div>
  </Teleport>`,
};
