// BulkCreateDialog.js — JSON 으로 여러 티켓을 한 번에 만든다.
//
// 3단계: [입력] → [미리보기] → [결과]
//   입력   : JSON 붙여넣기 + 예제/필드설명 + LLM 프롬프트 복사. 1차(스키마)+2차(서버 실값) 검증을
//            통과해야 다음으로 넘어간다. 오류는 **항목 인덱스·필드·사유**로 접히는 목록.
//   미리보기: 무엇이 만들어질지 **상위(Epic/부모 Task)별로 묶어** 보여 준다 — Task 화면의
//            'Task with SubTask' 와 같은 모양(머리=실제 상위 티켓, 안=새로 만들 티켓).
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
import { errorLines } from "../../lib/jsonlines.js";
import TypeBadge from "./TypeBadge.js";
import JsonEditor from "./JsonEditor.js";
import PriIcon from "./PriIcon.js";
import Avatar from "./Avatar.js";
import DueText from "./DueText.js";

/** 오늘부터 며칠 남았나 — Task 화면의 D-day 와 같은 값이어야 한다(카드·머리 공용). */
function dayDiff(ymd) {
  if (!ymd) return null;
  const d = new Date(ymd + "T00:00:00");
  if (isNaN(d)) return null;
  const t = new Date(); t.setHours(0, 0, 0, 0);
  return Math.round((d - t) / 86400000);
}

export default {
  name: "BulkCreateDialog",
  components: { JsonEditor, TypeBadge, PriIcon, Avatar, DueText },
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
      epicNames: {},          // 상위 티켓이 속한 Epic 의 이름(키만으론 뱃지에 적을 수 없다)
    };
  },
  computed: {
    isSub() { return this.mode === "subtask"; },
    title() { return this.isSub ? "Bulk Sub Task 추가하기" : "Bulk Task 추가하기"; },
    docs() { return fieldDocs(this.mode); },
    /** 오류가 가리키는 **원문의 줄** — 편집기가 그 줄을 붉게 칠한다.
     *  "3번 항목의 duedate" 는 정확하지만 어디를 고칠지는 안 알려 준다. 줄로 바꿔 준다. */
    badLines() { return errorLines(this.src, this.errors); },
    /** 이 JSON 이 참조하는 상위 티켓 키들(Epic 또는 부모 Task) — 중복 제거. */
    refKeys() {
      const s = new Set();
      for (const it of this.items) {
        const k = this.isSub ? it.parent : it.epic;
        if (k) s.add(k);
      }
      return [...s];
    },
    /**
     * 새로 만들 티켓 하나하나의 재료 — Task 화면에서 **부모 밑 하위 카드**가 쓰는 것과 같은
     * 필드다(우선순위·번호·제목·담당자·기한). 미리보기 전용 필드를 지어내지 않는다:
     * "만들면 이렇게 보인다" 를 보여 주는 화면이 실제와 달라지면 미리보기가 아니다.
     * 묶기는 previewGroups 가 한다.
     */
    cards() {
      return this.items.map((it, i) => {
        const refKey = (this.isSub ? it.parent : it.epic) || null;
        const ref = refKey ? this.refs[refKey] : null;
        const pri = it.priority || "";
        const rank = pri ? this.opts.priorities.indexOf(pri) : -1;
        const dueDays = dayDiff(it.duedate);
        const voc = !this.isSub && !it.epic && /^\s*\[/.test(it.summary || "");
        const card = {
          key: "신규",                              // 아직 Jira 에 없다 — 번호 자리에 그렇게 적는다
          type: it.type,
          title: it.summary || "",
          pri, priRank: rank >= 0 ? rank : undefined,
          assignee: it.assignee || "", assigneeId: it.assignee || "",
          mine: false, statusCategory: "new",
          due: it.duedate || null, dueDays, resolved: null,
          // 상위 묶음 안에 들어가는 카드는 소속을 또 달지 않는다 — 머리에 이미 적혀 있다.
          epicKey: null,
          voc,
          isSub: false,
          parent: null,
          _i: i, _ref: refKey,
        };
        const sig = refKey ? categoryColor(refKey) : (card.voc ? "var(--ty-story)" : null);
        card._sig = sig ? { "--sig": sig } : {};
        return card;
      });
    },
    /**
     * 미리보기 묶음 — 상위(Epic 또는 부모 Task)가 있으면 **Task 화면의 'Task with SubTask'**
     * 처럼 그 상위를 머리로 세우고 그 안에 새로 만들 티켓을 하위 카드로 넣는다.
     * 어떤 티켓 밑에 무엇이 생기는지는 위치로 보는 게 가장 빠르다 — 카드마다 소속 뱃지를
     * 반복해 다는 것보다 낫다. 상위가 없는 것들(Epic 없는 Task)은 머리 없이 한 묶음으로 모은다.
     */
    previewGroups() {
      const by = new Map(), loose = [];
      for (const c of this.cards) {
        if (!c._ref) { loose.push(c); continue; }
        if (!by.has(c._ref)) by.set(c._ref, { key: c._ref, cards: [] });
        by.get(c._ref).cards.push(c);
      }
      const out = [...by.values()].map((g) => {
        const t0 = this.refs[g.key] || null;
        // 머리는 **실제 티켓**이다 — Task 화면의 부모 카드와 같은 재료를 그대로 넘긴다.
        const head = t0 && {
          key: g.key, title: t0.summary || "", type: t0.type,
          pri: t0.priority || "", priRank: t0.priRank,
          assignee: t0.assignee || "", assigneeId: t0.assigneeId || "",
          statusCategory: t0.statusCategory, due: t0.due || null, resolved: t0.resolved || null,
          dueDays: dayDiff(t0.due), mine: false,
          epicKey: t0.epicKey || null,
          epicTitle: t0.epicKey ? (this.epicNames[t0.epicKey] || t0.epicKey) : "",
        };
        return {
          key: g.key, cards: g.cards, head,
          missing: this.refs[g.key] === null,          // 불러왔는데 없더라 — 생성 시 실패한다
          sig: categoryColor(g.key),
        };
      });
      if (loose.length) out.push({ key: "__none__", cards: loose, head: null, missing: false, sig: null });
      return out;
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
    /** 오류가 가리키는 줄로 편집기를 스크롤한다(미리보기/결과 단계에선 편집기가 없다). */
    goToError(e) {
      const ln = errorLines(this.src, [e])[0];
      if (ln && this.$refs.ed) this.$refs.ed.revealLine(ln);
    },

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

    /**
     * 미리보기가 참조하는 상위 티켓을 실제로 받아 온다 — **Task 화면과 똑같이 그리려면**
     * 뱃지(요약)로는 부족하다(담당자·기한·우선순위·소속 Epic 이 다 필요하다). 그래서 티켓
     * 전체를 받고, 소속 Epic 의 **이름**은 한 번 더 조회한다(Epic 키만으론 못 적는다).
     * 못 받은 것은 null 로 남겨 '없는 티켓' 으로 드러낸다 — 만들기 전에 눈으로 걸러낸다.
     */
    async loadRefs() {
      const keys = this.refKeys.filter((k) => !(k in this.refs));
      if (!keys.length) return;
      this.refLoading = true;
      try {
        await Promise.all(keys.map((k) =>
          api.ticket(k)
            .then(async (t) => {
              if (t && t.epicKey && !(t.epicKey in this.epicNames)) {
                const b = await api.ticketBadge(t.epicKey).catch(() => null);
                this.epicNames = { ...this.epicNames, [t.epicKey]: (b && (b.epicName || b.summary)) || t.epicKey };
              }
              this.refs = { ...this.refs, [k]: t || null };
            })
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
        <!-- 편집기는 칸을 꽉 채운다(여백 없음) — JSON 은 줄이 길어 한 글자라도 더 보이는 쪽이 낫다.
             오류가 가리키는 줄은 붉게 표시된다(errorLines 가 항목 번호를 줄번호로 바꾼다). -->
        <JsonEditor ref="ed" v-model="src" :bad-lines="badLines"
                    placeholder="여기에 JSON 을 붙여넣으세요" />
        <div class="blk-side">
          <div class="blk-side-h">필드</div>
          <table class="blk-ftab">
            <thead><tr><th>필드</th><th>필수</th><th>설명</th></tr></thead>
            <tbody>
              <tr v-for="d in docs" :key="d.f" :class="{ req: d.req.indexOf('필수') === 0 }">
                <td><code>{{ d.f }}</code></td>
                <td class="bf-req">{{ d.req }}</td>
                <td class="bf-d">{{ d.d }}</td>
              </tr>
            </tbody>
          </table>
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

      <!-- ── 2) 미리보기 — Task 화면의 'Task with SubTask' 처럼 상위 밑에 새 티켓을 넣어 보인다.
           상위(Epic/부모 Task)는 **실제로 불러와** 머리에 세운다(= 존재 검수도 된다). ────── -->
      <div v-else-if="step === 'preview'" class="blk-prev">
        <div v-if="refLoading" class="blk-refload">상위 티켓을 확인하는 중…</div>
        <div v-for="g in previewGroups" :key="g.key" class="blk-g"
             :class="{ 'mt-gcard2 k-task': g.key !== '__none__' }"
             :style="g.sig ? { '--sig': g.sig } : {}">
          <!-- 상위 머리 — **실제 티켓이므로 Task 화면의 부모 카드를 그대로 그린다**(우선순위·타입·
               번호·제목·소속 Epic·담당자·기한). 여기서 필드를 줄이면 '만들면 이렇게 보인다' 가
               거짓이 된다. 다만 눌러 여는 기능은 없다(tkt 를 달지 않는다 — 미리보기 창이다). -->
          <div v-if="g.key !== '__none__'" class="mt-gh">
            <div v-if="g.head" class="mt-card parent">
              <PriIcon :rank="g.head.priRank" :name="g.head.pri" />
              <TypeBadge :type="g.head.type" />
              <span class="mt-key">{{ g.head.key }}</span>
              <span class="mt-title">{{ g.head.title }}</span>
              <span v-if="g.head.epicKey" class="mt-epic" :title="'Epic: ' + g.head.epicTitle">{{ g.head.epicTitle }}</span>
              <span v-else class="mt-epic none">Epic 없음</span>
              <span class="mt-sep" aria-hidden="true"></span>
              <span class="mt-owner" :title="(g.head.assignee || '미할당') + ' 담당'">
                <Avatar :user="g.head.assigneeId" :name="g.head.assignee" :size="16" />{{ g.head.assignee || '미할당' }}</span>
              <DueText :card="g.head" />
              <span class="blk-gn">+{{ g.cards.length }}건 생성</span>
            </div>
            <div v-else class="mt-card parent miss">
              <span class="mt-key">{{ g.key }}</span>
              <span class="mt-title">{{ g.missing ? '없는 티켓입니다 — 이 항목들은 실패합니다' : '불러오는 중…' }}</span>
            </div>
          </div>
          <div v-else class="blk-none-h">{{ isSub ? '상위 없음' : 'Epic 없이 만드는 티켓' }} · {{ g.cards.length }}건</div>
          <!-- 새로 만들 티켓 — Task 화면에서 **부모 밑의 하위 카드와 같은 한 줄 배치**다
               (우선순위 · 번호 · 제목 · 담당자 · 기한). 소속은 머리에 이미 적혀 있어 다시 안 단다. -->
          <div class="mt-gbody one">
            <div v-for="c in g.cards" :key="c._i" class="mt-card" :style="c._sig">
              <PriIcon :rank="c.priRank" :name="c.pri" />
              <span class="mt-key">{{ c.key }}</span>
              <span class="mt-title">{{ c.title }}</span>
              <span class="mt-owner" :title="(c.assignee || '미할당') + ' 담당'">
                <Avatar :user="c.assigneeId" :name="c.assignee" :size="15" />{{ c.assignee || '미할당' }}</span>
              <DueText :card="c" />
            </div>
          </div>
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
          <!-- 오류를 누르면 편집기가 그 줄로 간다 — 표시만 하고 찾아가게 두면 헛수고다. -->
          <div v-for="(e, i) in errors" :key="'e' + i" class="blk-err jump" @click="goToError(e)">
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
          <!-- 이 창에서 **가장 먼저 눌러야 하는** 버튼이다(JSON 을 손으로 쓰는 사람은 드물다).
               다른 보조 버튼과 같은 회색이면 그게 안 보인다 → 제 색을 준다. -->
          <button class="cmt-ed-btn blk-prompt" @click="copyPrompt">
            <span class="bp-ic">📋</span>JSON 생성 LLM 프롬프트 복사</button>
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
