// SettingsMenu.js — 헤더 우상단 고정 설정 버튼. 클릭 시 드롭다운:
//   · SSO 상태(Jira/Confluence/Bitbucket) — **서비스별로 개별·실시간** 표시(병렬 프로브 + 폴링)
//   · 테마(다크/라이트) 토글 · Dev Tools · rev
// 백엔드: /api/health · /api/dev/sso/{service}(서비스별) · /api/dev/tools · /api/login
import { api } from "../../lib/api.js";
import { TYPEAHEAD_PRESETS, typeaheadDelay, setTypeaheadDelay } from "../../lib/typeahead.js";

const SERVICES = ["Jira", "Confluence", "Bitbucket"];
// 빠른 열기 전역 단축키 선택지 — run.py 가 이 spec 을 등록한다(데스크톱 앱). 기본 ctrl+alt+space.
const HOTKEYS = [
  { spec: "alt+space", label: "Alt + Space" },
  { spec: "ctrl+alt+space", label: "Ctrl + Alt + Space" },
  { spec: "ctrl+alt+j", label: "Ctrl + Alt + J" },
];

export default {
  name: "SettingsMenu",
  props: { theme: { type: String, default: "light" } },
  emits: ["toggle-theme"],
  data() {
    return { me: null,   // 세션 사용자 — config 의 manager 목록에 무엇을 적어야 하는지 보이려고
      manager: false,   // 매니저 아니면 Dev Tools 섹션을 감춘다(판정 전에도 감춤)
     
      open: false, rev: "", tools: null, loggingIn: false,
      // 서비스별 독립 상태 — loading|ok|no|off|err. 각자 도착하는 대로 렌더된다.
      services: SERVICES.map((name) => ({ name, status: "loading", detail: "", configured: null })),
      taMs: typeaheadDelay(),          // 자동완성 대기(ms) — 검색·문서/티켓 링크·@멘션 공통
      bbEnabled: false, bbConfigured: false, bbBusy: false,   // Bitbucket 연동(저장됨, 기본 꺼짐)
      hotkey: "ctrl+alt+space", hkBusy: false,   // 빠른 열기 단축키(저장됨)
    };
  },
  computed: {
    taPresets() { return TYPEAHEAD_PRESETS; },
    hotkeys() { return HOTKEYS; },
    needsAuth() { return this.services.some((s) => s.status === "no" || s.status === "err"); },
  },
  mounted() {
    api.me().then((me) => { this.me = me || null; this.manager = !!(me && me.manager); })
      .catch((e) => { this.me = { error: (e && e.message) || "확인 실패" }; });
    api.prefs().then((p) => { this.bbEnabled = !!p.bitbucketEnabled; this.bbConfigured = !!p.bitbucketConfigured;
      if (p.quickOpenHotkey) this.hotkey = p.quickOpenHotkey; })
      .catch(() => {});
    this._onDoc = (e) => { if (this.open && this.$el && !this.$el.contains(e.target)) this.close(); };
    document.addEventListener("click", this._onDoc, true);
    document.addEventListener("keydown", this._onEsc = (e) => { if (e.key === "Escape" && this.open) this.close(); });
    // 로그인이 방금 성공하면(서버가 auth-ok 를 쏜다) 4초 폴링을 기다리지 말고 **즉시** 다시 확인한다.
    window.addEventListener("auth-ok", this._onAuthOk = () => { if (this.open) this.probeAll(); });
  },
  unmounted() {
    document.removeEventListener("click", this._onDoc, true);
    document.removeEventListener("keydown", this._onEsc);
    window.removeEventListener("auth-ok", this._onAuthOk);
    this._stopPoll();
  },
  methods: {
    toggle() { this.open ? this.close() : this.openMenu(); },
    async setBitbucket(on) {
      if (this.bbBusy) return;
      this.bbBusy = true;
      try { const p = await api.setPrefs({ bitbucketEnabled: on }); this.bbEnabled = !!p.bitbucketEnabled; }
      catch (e) { /* 실패하면 원래 값 유지 */ }
      finally { this.bbBusy = false; this.probeAll(); }
    },
    setTa(ms) { this.taMs = ms; setTypeaheadDelay(ms); },
    async setHotkey(spec) {
      if (this.hkBusy || this.hotkey === spec) return;
      const prev = this.hotkey;
      this.hkBusy = true; this.hotkey = spec;      // 낙관적 반영
      try { const p = await api.setPrefs({ quickOpenHotkey: spec }); this.hotkey = p.quickOpenHotkey || spec; }
      catch (e) { this.hotkey = prev; }            // 실패하면 되돌린다
      finally { this.hkBusy = false; }
    },
    openMenu() {
      this.open = true;
      api.health().then((h) => { this.rev = (h && h.rev) || ""; }).catch(() => {});
      if (!this.tools) api.raw("/api/dev/tools").then((t) => { this.tools = t; }).catch(() => {});
      this.probeAll();
      this._startPoll();               // 열려 있는 동안 실시간 갱신(로그인 완료 반영)
    },
    close() { this.open = false; this._stopPoll(); },
    // 서비스별 **병렬** 프로브 — 각 응답이 오는 대로 그 행만 갱신(하나 느려도 나머지는 즉시).
    probeAll() {
      for (const svc of this.services) {
        api.raw("/api/dev/sso/" + encodeURIComponent(svc.name))
          .then((r) => {
            svc.configured = r.configured;
            svc.detail = r.detail || "";
            svc.status = r.configured === false ? "off" : (r.authenticated ? "ok" : "no");
          })
          // 사유를 그대로 남긴다 — '오류' 만 뜨면 다음에 또 무엇이 문제인지 못 짚는다.
          .catch((e) => { svc.status = "err"; svc.detail = "확인 실패: " + ((e && e.message) || e); });
      }
    },
    _startPoll() { this._stopPoll(); this._poll = setInterval(() => { if (this.open) this.probeAll(); }, 4000); },
    _stopPoll() { if (this._poll) { clearInterval(this._poll); this._poll = null; } },
    stateLabel(s) {
      return { loading: "확인 중…", ok: "인증됨", no: "미인증", off: "미설정", err: "오류" }[s.status] || "";
    },
    async authenticate() {
      // SSO 로그인 창을 띄운다. 이후 폴링이 서비스별로 실시간 갱신하므로 refresh 대기 불필요.
      this.loggingIn = true;
      try { await api.login(); } catch (e) { /* 취소 등 */ }
      setTimeout(() => { this.loggingIn = false; this.probeAll(); }, 2500);
    },
  },
  template: `
  <div class="setmenu" :class="{ open }">
    <button class="theme-btn setmenu-trig" @click.stop="toggle"
            :title="open ? '설정 닫기' : '설정'" :aria-expanded="open">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      <span>설정</span>
    </button>

    <div v-if="open" class="setmenu-panel" @click.stop>
      <!-- SSO 상태 (서비스별 개별·실시간) -->
      <div class="sm-sec">
        <div class="sm-h">SSO 인증</div>
        <!-- 지금 앱이 나를 누구로 보고 있는지. config 의 manager 목록에 적을 값이 곧 이 id 다
             — 이게 안 보이면 "나는 매니저인데 왜 안 되지" 를 추측으로 풀어야 한다. -->
        <div v-if="me" class="sm-who">
          <template v-if="me.error">세션 확인 실패 — {{ me.error }}</template>
          <!-- '세션 미확인' 과 '워커' 를 구분해 보인다. 둘을 같은 말로 쓰면
               로그인이 안 된 건지 권한이 없는 건지 알 수 없다. -->
          <template v-else-if="!me.known">
            <b>세션 미확인</b><span>아래 [인증하기] 로 SSO 로그인하세요</span>
          </template>
          <template v-else>
            <b>{{ me.id }}</b><span v-if="me.name"> · {{ me.name }}</span>
            <em :class="{ mgr: manager }">{{ manager ? '매니저' : '일반' }}</em>
            <span v-if="!manager" class="sm-who-h">config 의 manager 목록에 위 id 를 넣으면 매니저</span>
          </template>
        </div>
        <div v-for="s in services" :key="s.name" class="sm-row" :title="s.detail">
          <span class="sm-dot" :class="s.status"></span>
          <span class="sm-svc">{{ s.name }}</span>
          <span class="sm-state" :class="s.status">{{ stateLabel(s) }}</span>
        </div>
        <button v-if="needsAuth" class="sm-btn primary" :disabled="loggingIn" @click="authenticate">
          {{ loggingIn ? '인증 창 진행 중…' : '인증하기 (SSO 로그인)' }}
        </button>
        <!-- Bitbucket 연동 — 켰을 때만 인증 순회·검색에 낀다(기본 꺼짐, 저장됨) -->
        <div class="sm-toggle-row bb-row">
          <span class="sm-tg-label">Bitbucket 연동
            <em v-if="!bbConfigured" class="sm-who-h">config 에 bitbucket.base 필요</em>
            <em v-else class="sm-who-h">켜면 인증·검색에 포함됩니다</em>
          </span>
          <button class="sm-switch" :class="{ on: bbEnabled }" role="switch" :aria-checked="bbEnabled"
                  :disabled="bbBusy || !bbConfigured" @click="setBitbucket(!bbEnabled)"
                  title="Bitbucket 연동 사용 여부"><span class="sm-knob"></span></button>
        </div>
      </div>

      <!-- 테마 (토글 스위치) -->
      <div class="sm-sec">
        <div class="sm-h">화면</div>
        <div class="sm-toggle-row">
          <span class="sm-tg-label">🌙 다크 모드</span>
          <button class="sm-switch" :class="{ on: theme === 'dark' }" role="switch"
                  :aria-checked="theme === 'dark'" @click="$emit('toggle-theme')" title="다크/라이트">
            <span class="sm-knob"></span>
          </button>
        </div>
        <!-- 자동완성 대기 — 통합검색·티켓/문서 링크·@멘션 공통. 얼마나 멈춰야 갱신할지. -->
        <div class="sm-ta">
          <div class="sm-ta-l">⌨ 자동완성 반응 속도<span class="sm-ta-ms">{{ taMs }}ms</span></div>
          <div class="sm-ta-seg">
            <button v-for="o in taPresets" :key="o.ms" :class="{ on: taMs === o.ms }"
                    @click="setTa(o.ms)" :title="o.hint">{{ o.label }}</button>
          </div>
          <div class="sm-ta-h">타이핑을 이만큼 멈추면 검색어를 갱신합니다. 느린 망에선 길게.</div>
        </div>
      </div>

      <!-- 단축키 — 빠른 열기 전역 단축키(데스크톱 앱) -->
      <div class="sm-sec">
        <div class="sm-h">단축키</div>
        <div class="sm-ta">
          <div class="sm-ta-l">⚡ 빠른 열기</div>
          <div class="sm-ta-seg">
            <button v-for="o in hotkeys" :key="o.spec" :class="{ on: hotkey === o.spec }"
                    :disabled="hkBusy" @click="setHotkey(o.spec)">{{ o.label }}</button>
          </div>
          <div class="sm-ta-h">이 조합을 누르면 앱 창이 지금 보고 있는 화면(가상 데스크톱)으로 옵니다. — 데스크톱 앱</div>
        </div>
      </div>

      <!-- Dev Tools — 매니저 전용 -->
      <div v-if="manager" class="sm-sec">
        <div class="sm-h">Dev Tools</div>
        <a class="sm-btn sm-devgo" href="#/devtools" @click="close()">개발용 API 열기 →</a>
      </div>

      <div class="sm-foot">
        <span class="sm-rev" title="빌드 커밋">rev {{ rev || '…' }}</span>
        <button class="sm-refresh" @click="probeAll" title="상태 새로고침">↻</button>
      </div>
    </div>
  </div>`,
};
