import { api } from "./api.js";
import { pageTree } from "./confluenceTreeModel.js";

export default {
  data: () => ({ query: "", treePages: [], treeNext: 0, treeLoading: false, treeError: "",
    expanded: {}, searchOverrides: {}, activeMatch: "" }),
  computed: {
    tree() { return pageTree(this.treePages, this.expanded, this.query, this.searchOverrides); },
    matchPosition() { return this.tree.matches.indexOf(this.activeMatch) + 1; },
  },
  watch: {
    query() { this.searchOverrides = {}; this.activeMatch = ""; this.selectTreeMatch(); },
    "tree.matches"() { this.selectTreeMatch(); },
  },
  mounted() { this._treeGeneration = 0; },
  activated() { if (this.space && this.treeNext !== null && !this.treeLoading) this.loadTree(); },
  deactivated() { this._treeGeneration++; this.treeLoading = false; },
  beforeUnmount() { this._treeGeneration++; },
  methods: {
    async loadTree(reset = false) {
      if (!this.space || this.treeLoading && !reset) return;
      const generation = ++this._treeGeneration, space = this.space;
      if (reset) { this.treePages = []; this.treeNext = 0; this.expanded = {}; this.searchOverrides = {}; }
      this.treeLoading = true; this.treeError = "";
      try {
        while (this.treeNext !== null) {
          const start = this.treeNext;
          const data = await api.confluenceTree(space, start);
          if (generation !== this._treeGeneration) return;
          const next = data.nextStart;
          if (!Array.isArray(data.results) || next !== null && (!Number.isInteger(next) || next <= start)) {
            throw new Error("다음 문서 목록을 확인하지 못했습니다.");
          }
          this.treePages = [...new Map([...this.treePages, ...data.results].map(row => [String(row.id), row])).values()];
          this.treeNext = next;
        }
      } catch (error) { if (generation === this._treeGeneration) this.treeError = error.message; }
      finally { if (generation === this._treeGeneration) this.treeLoading = false; }
    },
    toggle(row) {
      if (this.tree.searching) this.searchOverrides[row.id] = !row.open;
      else this.expanded[row.id] = !row.open;
    },
    clearTreeSearch() { this.query = ""; this.searchOverrides = {}; },
    selectTreeMatch() {
      if (this.tree.matches.includes(this.activeMatch)) return;
      this.activeMatch = this.tree.matches[0] || "";
      if (this.activeMatch) this.$nextTick(() => this.scrollTreeMatch());
    },
    moveTreeMatch(direction = 1) {
      const matches = this.tree.matches;
      if (!matches.length) return;
      this.activeMatch = matches[(matches.indexOf(this.activeMatch) + direction + matches.length) % matches.length];
      let parent = this.tree.nodes.get(this.activeMatch)?.parent;
      const visited = new Set();
      while (parent && !visited.has(parent)) {
        visited.add(parent); this.searchOverrides[parent] = true; parent = this.tree.nodes.get(parent)?.parent;
      }
      this.$nextTick(() => this.scrollTreeMatch());
    },
    scrollTreeMatch() {
      const row = [...(this.$el.querySelectorAll('.conf-tree-row[data-page-id]'))].find(el => el.dataset.pageId === this.activeMatch);
      row?.scrollIntoView({ block: "nearest" });
    },
  },
};
