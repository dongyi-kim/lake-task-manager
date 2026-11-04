import { taskFindRows, findTaskCards, nextTaskMatch } from "./taskFind.js";

const visible = (element) => element?.getClientRects().length && getComputedStyle(element).visibility !== "hidden";

export default {
  data: () => ({ findOpen: false, findQuery: "", findKey: "" }),
  computed: {
    findResults() { return this.findOpen ? findTaskCards(taskFindRows(this.sections, (key) => this.epicTitle(key)), this.findQuery) : []; },
    findIndex() { return this.findResults.findIndex((row) => row.key === this.findKey); },
    findActive() { return this.findResults[this.findIndex] || null; },
    findLoading() { return this.loading || Object.values(this.streamAxes).some((axis) => axis.state === "loading"); },
  },
  watch: {
    findQuery() { this.findKey = this.findResults[0]?.key || ""; this.revealFind(); },
    findResults(rows) {
      if (!rows.some((row) => row.key === this.findKey)) {
        this.findKey = rows[0]?.key || "";
        this.revealFind();
      }
    },
  },
  mounted() {
    window.addEventListener("keydown", this._taskFindKey = (event) => {
      if (!visible(this.$el) || !/^#\/mytasks(?:$|[?\/])/.test(location.hash)) return;
      if ([...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')].some(visible)) return;
      const editing = event.target?.closest('input, textarea, select, [contenteditable="true"]');
      if (editing && !editing.closest(".mt-find")) return;
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "f") {
        event.preventDefault();
        if (!this.findOpen) this._findPreviousFocus = document.activeElement;
        this.findOpen = true;
        this.$nextTick(() => this.$refs.taskFind?.focus());
      } else if (event.key === "Escape" && this.findOpen) {
        event.preventDefault(); this.closeFind();
      }
    });
  },
  updated() { this.paintFind(); },
  unmounted() { window.removeEventListener("keydown", this._taskFindKey); },
  methods: {
    nextFind(delta) { this.findKey = nextTaskMatch(this.findResults, this.findKey, delta); this.revealFind(); },
    closeFind() {
      this.findOpen = false; this.findQuery = ""; this.findKey = "";
      if (this._findPreviousFocus?.isConnected) this._findPreviousFocus.focus({ preventScroll: true });
    },
    findOpens(panel) { return !!this.findActive && this.findActive.parentKey === panel?.key; },
    paintFind() {
      const matches = new Set(this.findResults.map((row) => row.key));
      for (const card of this.$el.querySelectorAll(".mt-card[data-key]")) {
        card.classList.toggle("mt-find-match", matches.has(card.dataset.key));
        card.classList.toggle("mt-find-current", this.findOpen && card.dataset.key === this.findKey);
      }
    },
    revealFind() {
      this.$nextTick(() => {
        this.paintFind();
        const card = [...this.$el.querySelectorAll(".mt-find-current")].find(visible);
        card?.scrollIntoView({ block: "center", inline: "nearest" });
      });
    },
  },
};
