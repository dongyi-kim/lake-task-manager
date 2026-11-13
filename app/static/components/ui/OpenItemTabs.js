import { tabFocusTarget } from "../../lib/openItemTabs.js";

// Manual-activation tabs. Parents own guard completion, store mutations and active-only reads.
export default {
  name: "OpenItemTabs",
  props: {
    items: { type: Array, default: () => [] },
    activeId: { type: String, default: null },
    kind: { type: String, default: "task" },
    label: { type: String, default: "최근 열린 항목" },
    panelId: { type: String, default: "" },
    disabled: { type: Boolean, default: false },
  },
  emits: ["activate", "close"],
  data() { return { focusId: null, knownIds: [], closingFocusId: null, canLeft: false, canRight: false }; },
  computed: {
    itemKeys() { return JSON.stringify(this.items.map(item => String(item.id))); },
    enabledItems() { return this.items.filter(item => !item.disabled); },
    rovingId() {
      const ids = this.enabledItems.map(item => String(item.id));
      return ids.includes(this.focusId) ? this.focusId : ids.includes(this.activeId) ? this.activeId : ids[0] || null;
    },
  },
  watch: {
    itemKeys: { immediate: true, handler() {
      const ids = this.items.map(item => String(item.id));
      const removed = this.closingFocusId && !ids.includes(this.closingFocusId) ? this.closingFocusId : null;
      const previous = this.knownIds;
      this.knownIds = ids;
      if (removed) {
        const index = previous.indexOf(removed);
        const candidates = [...this.items.slice(Math.max(index, 0)), ...this.items.slice(0, Math.max(index, 0)).reverse()];
        this.focusId = candidates.find(item => !item.disabled)?.id || null;
        this.closingFocusId = null;
        this.$nextTick(() => this.focusTab(this.rovingId));
      }
      this.$nextTick(() => { this.updateOverflow(); this.reveal(this.activeId); });
    } },
    activeId(id) { this.focusId = id; this.$nextTick(() => this.reveal(id)); },
  },
  mounted() {
    this.updateOverflow();
    if (typeof ResizeObserver !== "undefined") {
      this._tabsResize = new ResizeObserver(() => this.updateOverflow());
      this._tabsResize.observe(this.$refs.list);
    }
  },
  beforeUnmount() { this._tabsResize?.disconnect(); },
  methods: {
    tabNode(id) { return Array.from(this.$el?.querySelectorAll("[data-open-tab]") || []).find(node => node.dataset.openTab === id); },
    reveal(id) {
      const node = this.tabNode(id), list = this.$refs.list;
      if (!node || !list) return;
      // offsetLeft and scrolling use layout pixels; client rects include CSS/browser scaling.
      const left = node.offsetLeft;
      if (left < list.scrollLeft) list.scrollLeft = left;
      else if (left + node.offsetWidth > list.scrollLeft + list.clientWidth) list.scrollLeft = left + node.offsetWidth - list.clientWidth;
      this.updateOverflow();
    },
    focusTab(id) {
      if (!id) { this.$refs.list?.focus(); return; }
      this.tabNode(id)?.focus({ preventScroll: true }); this.reveal(id);
    },
    updateOverflow() {
      const list = this.$refs.list;
      this.canLeft = !!list && list.scrollLeft > 1;
      this.canRight = !!list && list.scrollWidth - list.clientWidth - list.scrollLeft > 1;
    },
    scroll(direction) {
      const list = this.$refs.list;
      if (!list) return;
      list.scrollLeft += direction * Math.max(160, list.clientWidth * .75);
      this.updateOverflow();
    },
    activate(item) {
      if (this.disabled || item.disabled) return;
      this.closingFocusId = null;
      this.focusId = String(item.id);
      this.$emit("activate", String(item.id));
    },
    close(item) {
      if (this.disabled || item.disabled) return;
      this.closingFocusId = String(item.id);
      this.$emit("close", String(item.id));
    },
    onKey(event, item) {
      if (this.disabled || item.disabled || event.altKey || event.ctrlKey || event.metaKey || event.isComposing) return;
      const id = tabFocusTarget(this.items, String(item.id), event.key);
      if (id) {
        event.preventDefault(); event.stopPropagation(); this.focusId = id; this.focusTab(id);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault(); event.stopPropagation(); this.activate(item);
      } else if (event.key === "Delete") {
        event.preventDefault(); event.stopPropagation(); this.close(item);
      }
    },
  },
  template: `
    <div class="open-item-tabs">
      <button class="oit-scroll" type="button" :disabled="disabled || !canLeft" @click="scroll(-1)" aria-label="앞쪽 탭 보기" title="앞쪽 탭 보기">‹</button>
      <div class="oit-list" ref="list" role="tablist" :aria-label="label" aria-orientation="horizontal"
           :aria-disabled="disabled ? 'true' : null" :tabindex="!enabledItems.length ? 0 : -1" @scroll.passive="updateOverflow">
        <div v-for="item in items" :key="item.id" class="oit-item" role="presentation" :class="{ active: String(item.id) === activeId }">
          <button class="oit-tab" type="button" role="tab" :data-open-tab="String(item.id)" :data-item-id="String(item.id)"
                  :aria-selected="String(item.id) === activeId ? 'true' : 'false'" :aria-controls="panelId || null"
                  :aria-label="kind === 'task' ? item.id + (item.title ? ' ' + item.title : '') : (item.title || item.id)"
                  :tabindex="String(item.id) === rovingId ? 0 : -1" :disabled="disabled || item.disabled"
                  :title="kind === 'task' ? item.id + (item.title ? ' · ' + item.title : '') : (item.title || item.id)"
                  @focus="focusId = String(item.id)" @click="activate(item)" @keydown="onKey($event, item)">
            <svg class="oit-icon" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.4"><path v-if="kind === 'confluence'" d="M4 2.5h6l2 2V14H4zM9.5 2.5V5H12M6 8h4M6 10.5h4"/><path v-else d="M3 3h10v10H3zM5.5 8l1.6 1.6 3.4-3.4"/></svg>
            <span v-if="kind === 'task'" class="oit-key">{{ item.id }}</span><span v-if="item.title || kind !== 'task'" class="oit-title">{{ item.title || item.id }}</span>
          </button>
          <button class="oit-close" type="button" tabindex="-1" :disabled="disabled || item.disabled"
                  :aria-label="(item.title || item.id) + ' 탭 닫기'" :title="(item.title || item.id) + ' 탭 닫기 (Delete)'" @click="close(item)">×</button>
        </div>
      </div>
      <button class="oit-scroll" type="button" :disabled="disabled || !canRight" @click="scroll(1)" aria-label="뒤쪽 탭 보기" title="뒤쪽 탭 보기">›</button>
    </div>`,
};
