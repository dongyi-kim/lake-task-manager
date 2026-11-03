import { api } from "../../lib/api.js";

const clone = (value) => JSON.parse(JSON.stringify(value));

export default {
  name: "WorkspaceAdminDialog",
  emits: ["close"],
  data() {
    return { tab: "wbs", revision: "", projectKey: "", tasks: [], people: [],
      baseline: { wbs: "", people: "" }, loaded: false, busy: "", error: "", notice: "" };
  },
  computed: {
    modules() { return [...new Set([...this.people.map((row) => row.module), ...this.tasks.map((task) => task.module)])].filter(Boolean); },
    wbsDirty() { return this.loaded && JSON.stringify(this.tasks) !== this.baseline.wbs; },
    peopleDirty() { return this.loaded && JSON.stringify(this.people) !== this.baseline.people; },
    dirty() { return this.tab === "wbs" ? this.wbsDirty : this.peopleDirty; },
  },
  mounted() {
    this._alive = true;
    this._request = 0;
    this._previousFocus = document.activeElement;
    document.addEventListener("keydown", this._keyHandler = (event) => this.keydown(event), true);
    this.$nextTick(() => this.$refs.closeButton?.focus());
    this.load();
  },
  unmounted() {
    this._alive = false;
    this._request++;
    document.removeEventListener("keydown", this._keyHandler, true);
    if (this._previousFocus?.isConnected) this._previousFocus.focus();
  },
  methods: {
    accept(state, kind) {
      this.revision = state.revision;
      this.projectKey = state.projectKey;
      if (!kind || kind === "wbs") {
        this.tasks = clone(state.tasks || []);
        this.baseline.wbs = JSON.stringify(this.tasks);
      }
      if (!kind || kind === "people") {
        this.people = Object.entries(state.people || {}).map(([module, ids]) => ({ module, ids: ids.join("\n") }));
        this.baseline.people = JSON.stringify(this.people);
      }
      this.loaded = true;
    },
    async load() {
      if (this.busy) return;
      const request = ++this._request;
      this.busy = "load"; this.error = ""; this.notice = "";
      try {
        const state = await api.raw("/api/workspace/config");
        if (this._alive && request === this._request) this.accept(state);
      } catch (error) {
        if (this._alive && request === this._request) this.error = error.message || "설정을 불러오지 못했습니다.";
      } finally {
        if (this._alive && request === this._request) this.busy = "";
      }
    },
    setTab(tab) {
      this.tab = tab;
      this.error = ""; this.notice = "";
    },
    addTask() {
      this.tasks.push({ id: "wbs-" + crypto.randomUUID(), module: this.modules[0] || "",
        name: "", start: "", end: "", epics: [{ key: "", weight: 1 }] });
      this.$nextTick(() => [...this.$refs.dialog.querySelectorAll(".wa-task input[name='task-name']")].at(-1)?.focus());
    },
    addModule() {
      this.people.push({ module: "", ids: "" });
      this.$nextTick(() => [...this.$refs.dialog.querySelectorAll(".wa-people-row input")].at(-1)?.focus());
    },
    peoplePayload() {
      const people = {};
      for (const row of this.people) {
        const module = row.module.trim();
        if (!module) throw new Error("모듈 이름을 입력하세요.");
        if (Object.hasOwn(people, module)) throw new Error("같은 모듈을 중복 등록할 수 없습니다: " + module);
        people[module] = row.ids.split(/\r?\n/).map((id) => id.trim()).filter(Boolean);
      }
      return people;
    },
    tasksPayload() {
      for (const task of this.tasks) {
        if (task.start >= task.end) throw new Error("시작일은 종료일보다 앞서야 합니다: " + task.name);
        if (!task.epics.length) throw new Error("각 WBS Task에 Epic을 하나 이상 연결하세요.");
        if (new Set(task.epics.map((epic) => epic.key.toUpperCase())).size !== task.epics.length) {
          throw new Error("같은 Task에 Epic을 중복 연결할 수 없습니다: " + task.name);
        }
        if (task.epics.reduce((sum, epic) => sum + Number(epic.weight), 0) <= 0) {
          throw new Error("Epic 가중치 합은 0보다 커야 합니다: " + task.name);
        }
      }
      return clone(this.tasks);
    },
    async save(kind) {
      if (this.busy || !this.loaded) return;
      const request = ++this._request;
      this.error = ""; this.notice = "";
      try {
        const body = { revision: this.revision,
          ...(kind === "wbs" ? { tasks: this.tasksPayload() } : { people: this.peoplePayload() }) };
        this.busy = kind;
        const state = await api.raw("/api/workspace/config/" + kind, {
          method: "PUT", headers: { "Content-Type": "application/json", "X-LTM-Local": "1" },
          body: JSON.stringify(body),
        });
        // Config was saved even if the dialog closed while waiting; notify the visible views.
        api.workspaceConfigUpdated(state.revision, kind);
        if (this._alive && request === this._request) {
          this.accept(state, kind);
          this.notice = kind === "wbs" ? "WBS Task를 저장했습니다." : "인력 구성을 저장했습니다.";
        }
      } catch (error) {
        if (this._alive && request === this._request) this.error = error.message || "저장하지 못했습니다. 입력은 유지됩니다.";
      } finally {
        if (this._alive && request === this._request) this.busy = "";
      }
    },
    keydown(event) {
      if (event.key === "Escape") { event.stopPropagation(); this.$emit("close"); }
      if (event.key !== "Tab") return;
      const controls = [...this.$refs.dialog.querySelectorAll("button, input, textarea, select")]
        .filter((element) => !element.matches(":disabled") && element.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !this.$refs.dialog.contains(document.activeElement))) {
        event.preventDefault(); first?.focus();
      }
    },
  },
  template: `
  <Teleport to="body">
    <div class="wa-back" @click.self="$emit('close')">
      <section class="wa-dialog" ref="dialog" role="dialog" aria-modal="true" aria-labelledby="workspace-admin-title">
        <header class="wa-head">
          <div><h2 id="workspace-admin-title">인력 · WBS 관리</h2><p>LTM의 모듈별 인력과 WBS 계획을 관리합니다.</p></div>
          <button ref="closeButton" class="wa-close" aria-label="인력 · WBS 관리 닫기" @click="$emit('close')">✕</button>
        </header>
        <nav class="wa-tabs" aria-label="관리 항목">
          <button :aria-pressed="tab === 'wbs'" :disabled="!!busy" @click="setTab('wbs')">WBS Tasks <span v-if="wbsDirty">· 미저장</span></button>
          <button :aria-pressed="tab === 'people'" :disabled="!!busy" @click="setTab('people')">인력 <span v-if="peopleDirty">· 미저장</span></button>
        </nav>
        <div class="wa-body">
          <p v-if="!loaded && busy" role="status" class="wa-hint">설정을 불러오는 중…</p>
          <form v-if="loaded" :id="'workspace-form-' + tab" @submit.prevent="save(tab)">
            <fieldset :disabled="!!busy">
              <template v-if="tab === 'wbs'">
                <div class="wa-section-head"><div><b>{{ projectKey }} · WBS Task {{ tasks.length }}개</b><p class="wa-hint">Epic 가중치는 상대값입니다. 이름·일정·연결 Epic을 수정할 수 있습니다.</p></div>
                  <button type="button" @click="addTask">+ WBS Task 추가</button></div>
                <p v-if="!tasks.length" class="wa-empty">등록된 WBS Task가 없습니다.</p>
                <article v-for="(task, i) in tasks" :key="i" class="wa-task" :aria-label="'WBS Task ' + (i + 1)">
                  <div class="wa-row-head"><b>{{ task.name || '새 WBS Task' }}</b><button type="button" class="wa-danger" :aria-label="'Task ' + (i + 1) + ' 삭제'" @click="tasks.splice(i, 1)">Task 삭제</button></div>
                  <div class="wa-grid">
                    <label class="wa-wide">이름<input name="task-name" :aria-label="'Task ' + (i + 1) + ' 이름'" v-model="task.name" required maxlength="500"></label>
                    <label>모듈<input :aria-label="'Task ' + (i + 1) + ' 모듈'" v-model="task.module" list="workspace-modules" required maxlength="200"></label>
                    <label>Task ID<input :aria-label="'Task ' + (i + 1) + ' ID'" v-model="task.id" required maxlength="200" spellcheck="false"></label>
                    <label>시작일<input :aria-label="'Task ' + (i + 1) + ' 시작일'" type="date" v-model="task.start" required></label>
                    <label>종료일<input :aria-label="'Task ' + (i + 1) + ' 종료일'" type="date" v-model="task.end" :min="task.start || undefined" required></label>
                  </div>
                  <div class="wa-epics">
                    <div class="wa-row-head"><b>연결 Epic</b><button type="button" :aria-label="'Task ' + (i + 1) + ' Epic 추가'" @click="task.epics.push({key:'', weight:1})">+ Epic 추가</button></div>
                    <div v-for="(epic, n) in task.epics" :key="n" class="wa-epic-row">
                      <label>티켓 키<input :aria-label="'Task ' + (i + 1) + ' Epic ' + (n + 1) + ' 키'" v-model="epic.key" placeholder="DL-123" required pattern="[A-Za-z][A-Za-z0-9_]*-[0-9]+" spellcheck="false"></label>
                      <label>가중치<input :aria-label="'Task ' + (i + 1) + ' Epic ' + (n + 1) + ' 가중치'" type="number" v-model.number="epic.weight" required min="0" step="any"></label>
                      <button type="button" class="wa-danger" :aria-label="'Task ' + (i + 1) + ' Epic ' + (n + 1) + ' 삭제'" @click="task.epics.splice(n, 1)">삭제</button>
                    </div>
                  </div>
                </article>
                <datalist id="workspace-modules"><option v-for="module in modules" :key="module" :value="module"></option></datalist>
              </template>
              <template v-else>
                <div class="wa-section-head"><div><b>모듈 {{ people.length }}개</b><p class="wa-hint">Jira 사용자 ID를 한 줄에 한 명씩 입력하세요. 표시 이름은 Jira 정보를 사용합니다.</p></div>
                  <button type="button" @click="addModule">+ 모듈 추가</button></div>
                <p v-if="!people.length" class="wa-empty">등록된 모듈이 없습니다.</p>
                <article v-for="(row, i) in people" :key="i" class="wa-people-row" :aria-label="'인력 모듈 ' + (i + 1)">
                  <div class="wa-row-head"><b>{{ row.module || '새 모듈' }}</b><button type="button" class="wa-danger" :aria-label="'모듈 ' + (i + 1) + ' 삭제'" @click="people.splice(i, 1)">모듈 삭제</button></div>
                  <label>모듈 이름<input :aria-label="'모듈 ' + (i + 1) + ' 이름'" v-model="row.module" required maxlength="200"></label>
                  <label>Jira 사용자 ID<textarea :aria-label="'모듈 ' + (i + 1) + ' 사용자 ID'" v-model="row.ids" rows="4" spellcheck="false" placeholder="skcc.x1234&#10;skcc.x5678"></textarea></label>
                </article>
              </template>
            </fieldset>
          </form>
        </div>
        <div class="wa-feedback">
          <p v-if="error" role="alert" class="wa-error">{{ error }}</p>
          <p v-if="notice" role="status" class="wa-success">{{ notice }}</p>
          <span v-if="loaded && dirty" class="wa-hint">현재 탭에 저장하지 않은 변경이 있습니다.</span>
        </div>
        <footer class="wa-foot">
          <button :disabled="!!busy" @click="load">{{ wbsDirty || peopleDirty ? '입력 버리고 다시 불러오기' : '다시 불러오기' }}</button>
          <div><button @click="$emit('close')">닫기</button>
            <button v-if="loaded" class="wa-primary" type="submit" :form="'workspace-form-' + tab" :disabled="!!busy || !dirty">{{ busy && busy !== 'load' ? '저장 중…' : tab === 'wbs' ? 'WBS 저장' : '인력 저장' }}</button></div>
        </footer>
      </section>
    </div>
  </Teleport>`,
};
