// BulkCreateDialog.js — JSON 으로 여러 티켓을 한 번에 만든다.
//
// 3단계: [입력] → [미리보기] → [결과]
//   입력   : JSON 붙여넣기 + 예제/필드설명 + LLM 프롬프트 복사. 1차(스키마)+2차(서버 실값) 검증을
//            통과해야 다음으로 넘어간다. 오류는 **항목 인덱스·필드·사유**로 접히는 목록.
//   미리보기: 무엇이 만들어질지 상위별로 묶어 보여 준다(내 Task 화면과 같은 카드 UI).
//   결과   : 성공/실패 요약. 실패는 계속 진행 후 모아 보여 주고 **실패분만 JSON 으로 복사**할 수 있다
//            (Jira 는 롤백이 없다 — 고쳐서 다시 돌리는 게 유일한 복구다).
//
// 규칙·예제·프롬프트는 lib/bulkSchema.js 단일 소스. 서버 검증기는 app/domain/bulk.py.
import { api } from "../../lib/api.js";
import { copyText } from "../../lib/ticketlink.js";
import { pushToast } from "../../lib/toast.js";
import { fromBackdrop } from "../../lib/backdrop.js";
import { validateBulk, exampleJson, fieldDocs, buildLlmPrompt } from "../../lib/bulkSchema.js";
import { categoryColor } from "../../lib/colors.js";
import TaskCard from "./TaskCard.js";

export default {
  name: "BulkCreateDialog",
  components: { TaskCard },
  props: { mode: { type: String, required: true } },     // 'task' | 'subtask'
  emits: ["close", "done"],
  data() {
    return {
      step: "input",          // input | preview | result
      src: "",
      errors: [], warnings: [], errOpen: true,
      checking: false, busy: false,
      items: [],              // 검증 통과한 항목
      opts: { types: [], priorities: [], components: [] },   // 프롬프트에 박을 실제 선택지
      result: null,           // { created:[], failed:[] }
      progress: 0,
      // 미리보기에서 실제로 불러온 상위 티켓(Epic/부모 Task). key → badge | null(=없는 티켓).
      // 뱃지를 채우려고 받는 김에 **존재 여부 검수**까지 된다.
      refs: {}, refLoading: false,
    };
  },
  computed: {
    isSub() { return this.mode === "subtask"; },
    title() { return this.isSub ? "Bulk Sub Task 추가하기" : "Bulk Task 추가하기"; },
    docs() { return fieldDocs(this.mode); },
    /** 이 JSON 이 참조하는 상위 티켓 키들(Epic 또는 부모 Task) — 중복 제거. */
    refKeys() {
      const s = new Set();
      for (const it of this.items) {
        const k = this.isSub ? it.parent : it.epic;
        if (k) s.add(k);
      }
      return [...s];
    },
    /** 불러와 봤더니 **없는** 티켓 — 미리보기 단계에서 눈으로 걸러낸다. */
    refMissing() { return this.refKeys.filter((k) => this.refs[k] === null); },
    /**
     * 미리보기 카드 — **Task 화면과 같은 TaskCard 를 같은 모양으로** 쓴다. 미리보기 전용
     * 마크업을 따로 두면 "만들면 이렇게 보인다" 를 보여 주는 화면이 정작 실제와 달라진다.
     * 그룹화는 하지 않는다 — 아직 없는 티켓을 상위별로 묶어 봐야 실제 화면과 모양만 달라진다.
     *
     * Task 모드는 소속 Epic 뱃지가, Sub-Task 모드는 상위 Task 뱃지가 각각 채워진다.
     * 그 이름은 **실제로 불러온 상위 티켓**에서 온다 — 뱃지가 비면 그 키가 없다는 뜻이다.
     */
    cards() {
      const today = new Date(); today.setHours(0, 0, 0, 0);
      return this.items.map((it, i) => {
        const refKey = (this.isSub ? it.parent : it.epic) || null;
        const ref = refKey ? this.refs[refKey] : null;
        const pri = it.priority || "";
        const rank = pri ? this.opts.priorities.indexOf(pri) : -1;
        let dueDays = null;
        if (it.duedate) {
          const d = new Date(it.duedate + "T00:00:00");
          if (!isNaN(d)) dueDays = Math.round((d - today) / 86400000);
        }
        const voc = !this.isSub && !it.epic && /^\s*\[/.test(it.summary || "");
        const card = {
          key: "신규",                              // 아직 Jira 에 없다 — 번호 자리에 그렇게 적는다
          type: it.type,
          title: it.summary || "",
          pri, priRank: rank >= 0 ? rank : undefined,
          assignee: it.assignee || "", assigneeId: it.assignee || "",
          mine: false, statusCategory: "new",
          due: it.duedate || null, dueDays, resolved: null,
          epicKey: this.isSub ? null : (it.epic || null),
          voc,
          isSub: this.isSub,
          parent: this.isSub && refKey ? { key: refKey, title: (ref && ref.summary) || "" } : null,
          _i: i,
        };
        // 색 신호(--sig)는 Task 화면과 같은 규칙 — 소속이 없으면 색을 지어내지 않는다.
        const sig = card.epicKey ? categoryColor(card.epicKey) : (card.voc ? "var(--ty-story)" : null);
        card._sig = sig ? { "--sig": sig } : {};
        card._epicTitle = ref ? (ref.epicName || ref.summary || refKey) : (refKey || "");
        return card;
      });
    },
  },
  mounted() {
    this.src = exampleJson(this.mode);
    // 프롬프트에 박을 실제 선택지 — 실패해도 프롬프트는 만들 수 있다(그 자리에 안내가 들어간다).
    const tp = this.isSub ? Promise.resolve(["Sub-Task"]) : api.taskTypes().catch(() => []);
    Promise.all([tp, api.options("priorities").catch(() => []), api.options("components").catch(() => [])])
      .then(([types, pri, comp]) => {
        this.opts = {
          types: types || [],
          priorities: (pri || []).map((p) => p.name || p),
          components: (comp || []).map((c) => c.name || c),
        };
      });
    // Esc 는 이 창만 닫는다(뒤의 다이얼로그까지 닫히면 쓰던 JSON 이 날아간다).
    document.addEventListener("keydown", this._onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      this.$emit("close");
    }, true);
  },
  unmounted() { document.removeEventListener("keydown", this._onKey, true); },
  methods: {
    async copyPrompt() {
      const ok = await copyText(buildLlmPrompt(this.mode, this.opts));
      pushToast(ok
        ? { kind: "success", icon: "📋", title: "LLM 프롬프트를 복사했습니다",
            message: "쓰던 LLM 에 붙여넣고 만들 티켓을 설명하세요.", timeout: 5000 }
        : { kind: "error", icon: "⚠", title: "복사 실패", timeout: 5000 });
    },

    /** 1차(스키마) → 2차(서버 실값). 둘 다 통과해야 미리보기로 넘어간다. */
    async check() {
      if (this.checking) return;
      this.errors = []; this.warnings = [];
      const r = validateBulk(this.src, this.mode);
      if (!r.ok) { this.errors = r.errors; this.warnings = r.warnings; this.errOpen = true; return; }
      this.warnings = r.warnings;
      this.checking = true;
      try {
        const sv = await api.bulkValidate({ mode: this.mode, items: r.data.items });
        this.warnings = (this.warnings || []).concat(sv.warnings || []);
        if (!sv.ok) { this.errors = sv.errors || []; this.errOpen = true; return; }
        this.items = r.data.items;
        this.step = "preview";
        this.loadRefs();                 // 상위 티켓을 실제로 받아 뱃지를 채운다(= 존재 검수)
      } catch (e) {
        this.errors = [{ index: null, field: null, message: (e && e.message) || "검증에 실패했습니다." }];
        this.errOpen = true;
      } finally { this.checking = false; }
    },

    /** 미리보기가 참조하는 상위 티켓을 실제로 받아 온다.
     *  못 받은 것은 null 로 남겨 '없는 티켓' 으로 드러낸다 — 만들기 전에 눈으로 걸러낸다. */
    async loadRefs() {
      const keys = this.refKeys.filter((k) => !(k in this.refs));
      if (!keys.length) return;
      this.refLoading = true;
      try {
        await Promise.all(keys.map((k) =>
          api.ticketBadge(k).then((b) => { this.refs = { ...this.refs, [k]: b || null }; })
            .catch(() => { this.refs = { ...this.refs, [k]: null }; })));
      } finally { this.refLoading = false; }
    },

    async create() {
      if (this.busy) return;
      this.busy = true; this.progress = 0;
      try {
        const r = await api.bulkCreate({ mode: this.mode, items: this.items });
        if (r && r.ok === false && r.errors) {          // 서버가 다시 검증해 막은 경우
          this.errors = r.errors; this.step = "input"; this.errOpen = true; return;
        }
        this.result = r;
        this.step = "result";
        // ★ 건마다 ticket-changed 를 쏘면 안 된다 — 목록 뷰가 항목마다 '필터에서 제외' 토스트를
        //   띄워 10건이면 토스트가 10개 쌓인다(실제로 3건에 3개가 떴다). 갱신은 부모가 **한 번**
        //   force-refresh 로 한다(onBulkDone).
        this.$emit("done", r);
      } catch (e) {
        this.errors = [{ index: null, field: null, message: (e && e.message) || "생성에 실패했습니다." }];
        this.step = "input"; this.errOpen = true;
      } finally { this.busy = false; }
    },

    /** 실패분만 다시 JSON 으로 — 고쳐서 다시 돌리라고. */
    async copyFailed() {
      const idx = new Set((this.result.failed || []).map((f) => f.index));
      const items = this.items.filter((_, i) => idx.has(i));
      const ok = await copyText(JSON.stringify({ mode: this.mode, items }, null, 2));
      pushToast(ok
        ? { kind: "success", icon: "📋", title: `실패한 ${items.length}건을 JSON 으로 복사했습니다`, timeout: 5000 }
        : { kind: "error", icon: "⚠", title: "복사 실패", timeout: 5000 });
    },
    retryFailed() {
      const idx = new Set((this.result.failed || []).map((f) => f.index));
      this.src = JSON.stringify({ mode: this.mode, items: this.items.filter((_, i) => idx.has(i)) }, null, 2);
      this.result = null; this.items = []; this.errors = []; this.step = "input";
    },
  },
  template: `
  <div class="blk-ov" @click.self="fromBackdrop($event) && $emit('close')">
    <div class="blk">
      <div class="blk-h">
        <b>{{ step === 'preview' ? 'Bulk 생성 미리보기' : (step === 'result' ? 'Bulk 생성 결과' : title) }}</b>
        <span class="blk-h-s" v-if="step === 'input'">JSON 기반</span>
        <span class="blk-h-s" v-else-if="step === 'preview'">{{ items.length }}건</span>
        <button class="lp-x" @click="$emit('close')" aria-label="닫기">✕</button>
      </div>

      <!-- ── 1) 입력 ─────────────────────────────────────────────── -->
      <div v-if="step === 'input'" class="blk-body">
        <textarea class="blk-src" v-model="src" spellcheck="false"
                  placeholder="여기에 JSON 을 붙여넣으세요"></textarea>
        <div class="blk-side">
          <div class="blk-side-h">필드</div>
          <div v-for="d in docs" :key="d.f" class="blk-fd">
            <code>{{ d.f }}</code><em :class="{ req: d.req.indexOf('필수') === 0 }">{{ d.req }}</em>
            <span>{{ d.d }}</span>
          </div>
          <div class="blk-side-h">규칙</div>
          <ul class="blk-rules">
            <li v-if="isSub">상위 Task 는 <b>이미 존재</b>해야 합니다(이 JSON 안의 티켓 불가).</li>
            <li v-else>Epic 이 없으면 <code>"epic": null</code> 을 <b>명시</b>합니다.</li>
            <li>Task 와 Sub-Task 를 한 번에 섞을 수 없습니다.</li>
            <li>본문은 Markdown — 체크박스 <code>- [ ]</code>·표·불릿 지원.</li>
            <li>이미지·파일 첨부 불가. 링크는 <b>웹(http/https)</b>만.</li>
          </ul>
        </div>
      </div>

      <!-- ── 2) 미리보기 — Task 화면의 카드 그대로, 그룹화 없이 평평하게 ─────────── -->
      <div v-else-if="step === 'preview'" class="blk-prev">
        <div v-if="refLoading" class="blk-refload">상위 티켓을 확인하는 중…</div>
        <div v-if="refMissing.length" class="blk-refbad">
          <b>없는 티켓</b> {{ refMissing.join(', ') }} — 생성 시 이 항목들은 실패합니다.
        </div>
        <div class="mt-gbody plain blk-flat">
          <TaskCard v-for="c in cards" :key="c._i" :card="c" :style="c._sig"
                    :epic-title="c._epicTitle" />
        </div>
      </div>

      <!-- ── 3) 결과 ─────────────────────────────────────────────── -->
      <div v-else class="blk-res">
        <div class="blk-sum">
          <span class="ok"><b>{{ (result.created || []).length }}</b> 생성</span>
          <span v-if="(result.failed || []).length" class="ng"><b>{{ result.failed.length }}</b> 실패</span>
        </div>
        <div v-if="(result.created || []).length" class="blk-list">
          <div v-for="c in result.created" :key="'c' + c.index" class="blk-row ok tkt" :data-key="c.key">
            <b class="blk-key">{{ c.key }}</b><span class="blk-t">{{ c.summary }}</span>
          </div>
        </div>
        <template v-if="(result.failed || []).length">
          <div class="blk-side-h">실패</div>
          <div class="blk-list">
            <div v-for="f in result.failed" :key="'f' + f.index" class="blk-row ng">
              <b class="blk-key">#{{ f.index + 1 }}</b>
              <span class="blk-t">{{ f.summary }}</span>
              <span class="blk-why">{{ f.error }}</span>
            </div>
          </div>
        </template>
      </div>

      <!-- 오류/경고 — 접히는 목록 -->
      <div v-if="errors.length || warnings.length" class="blk-errs" :class="{ folded: !errOpen }">
        <button class="blk-errs-h" @click="errOpen = !errOpen">
          <span class="chev" :class="{ open: errOpen }">▸</span>
          <b v-if="errors.length" class="ng">오류 {{ errors.length }}</b>
          <b v-if="warnings.length" class="warn">경고 {{ warnings.length }}</b>
        </button>
        <div v-if="errOpen" class="blk-errs-b">
          <div v-for="(e, i) in errors" :key="'e' + i" class="blk-err">
            <span class="blk-at" v-if="e.index !== null && e.index !== undefined">#{{ e.index + 1 }}</span>
            <code v-if="e.field">{{ e.field }}</code><span>{{ e.message }}</span>
          </div>
          <div v-for="(w, i) in warnings" :key="'w' + i" class="blk-err warn">
            <span class="blk-at" v-if="w.index !== null && w.index !== undefined">#{{ w.index + 1 }}</span>
            <code v-if="w.field">{{ w.field }}</code><span>{{ w.message }}</span>
          </div>
        </div>
      </div>

      <div class="blk-f">
        <template v-if="step === 'input'">
          <button class="cmt-ed-btn ghost" @click="copyPrompt">LLM 프롬프트 복사하기</button>
          <span class="blk-hint">JSON 을 만들 때 쓰세요</span>
          <button class="cmt-ed-btn primary" :disabled="checking" @click="check">
            {{ checking ? '확인 중…' : '다음 (미리보기)' }}</button>
        </template>
        <template v-else-if="step === 'preview'">
          <button class="cmt-ed-btn ghost" @click="step = 'input'">뒤로가기</button>
          <span class="blk-hint">{{ items.length }}건을 차례로 만듭니다</span>
          <button class="cmt-ed-btn primary" :disabled="busy" @click="create">
            {{ busy ? '만드는 중…' : '생성하기' }}</button>
        </template>
        <template v-else>
          <button v-if="(result.failed || []).length" class="cmt-ed-btn ghost" @click="copyFailed">실패분 JSON 복사</button>
          <button v-if="(result.failed || []).length" class="cmt-ed-btn ghost" @click="retryFailed">실패분만 다시</button>
          <span class="blk-hint"></span>
          <button class="cmt-ed-btn primary" @click="$emit('close')">닫기</button>
        </template>
      </div>
    </div>
  </div>`,
  setup() { return { fromBackdrop }; },
};
