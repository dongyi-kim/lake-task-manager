const KEY = "lake.confluence.layout.v1";
const limits = { sidebar: [224, 420], toc: [180, 340] };
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export default {
  data() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(KEY)) || {}; } catch (_) { /* storage is optional */ }
    return { sidebarOpen: saved.sidebarOpen !== false, tocOpen: saved.tocOpen !== false,
      sidebarWidth: clamp(Number(saved.sidebarWidth) || 272, ...limits.sidebar),
      tocWidth: clamp(Number(saved.tocWidth) || 224, ...limits.toc),
      compact: window.innerWidth <= 1100, drawer: "", layoutWidth: window.innerWidth, draggingPanel: "" };
  },
  computed: {
    showSidebar() { return this.compact ? this.drawer === "sidebar" : this.sidebarOpen; },
    showToc() { return this.compact ? this.drawer === "toc" : this.tocOpen; },
    layoutStyle() { return { "--conf-sidebar-width": this.sidebarWidth + "px", "--conf-toc-width": this.tocWidth + "px" }; },
  },
  mounted() {
    this._layoutObserver = new ResizeObserver(([entry]) => {
      this.layoutWidth = entry.contentRect.width;
      const compact = this.layoutWidth <= 1100;
      if (this.compact !== compact) { this.stopPanelResize(); this.drawer = ""; this.compact = compact; }
      // Leave a readable body even when a window is narrowed after resizing both rails.
      if (!compact) this.fitPanels();
    });
    this._layoutObserver.observe(this.$el);
  },
  deactivated() { this.drawer = ""; this.stopPanelResize(); },
  beforeUnmount() { this._layoutObserver?.disconnect(); this.stopPanelResize(); },
  methods: {
    saveLayout() {
      try { localStorage.setItem(KEY, JSON.stringify({ sidebarOpen: this.sidebarOpen, tocOpen: this.tocOpen,
        sidebarWidth: this.sidebarWidth, tocWidth: this.tocWidth })); } catch (_) { /* session layout still works */ }
    },
    toggleSidebar() { this.togglePanel("sidebar"); },
    toggleToc() { this.togglePanel("toc"); },
    togglePanel(side) {
      if (this.compact) this.drawer = this.drawer === side ? "" : side;
      else { this[side + "Open"] = !this[side + "Open"]; this.fitPanels(); this.saveLayout(); }
    },
    closeDrawer() { this.drawer = ""; },
    panelMax(side) {
      const other = side === "sidebar" ? "toc" : "sidebar";
      const otherWidth = this[other + "Open"] && (other !== "toc" || this.headings.length) ? this[other + "Width"] + 8 : 0;
      return Math.max(limits[side][0], Math.min(limits[side][1], this.layoutWidth - otherWidth - 488));
    },
    fitPanels() {
      this.sidebarWidth = clamp(this.sidebarWidth, limits.sidebar[0], this.panelMax("sidebar"));
      this.tocWidth = clamp(this.tocWidth, limits.toc[0], this.panelMax("toc"));
    },
    resizePanelKey(side, event) {
      const delta = side === "sidebar" ? 16 : -16;
      const current = this[side + "Width"];
      const value = { ArrowLeft: current - delta, ArrowRight: current + delta,
        Home: limits[side][0], End: this.panelMax(side) }[event.key];
      if (value === undefined) return;
      event.preventDefault(); this[side + "Width"] = clamp(value, limits[side][0], this.panelMax(side)); this.saveLayout();
    },
    startPanelResize(side, event) {
      if (this.compact || event.button !== 0) return;
      event.preventDefault(); this.stopPanelResize();
      const target = event.currentTarget, start = event.clientX, width = this[side + "Width"];
      this.draggingPanel = side;
      target.setPointerCapture(event.pointerId);
      const move = e => { this[side + "Width"] = clamp(width + (e.clientX - start) * (side === "sidebar" ? 1 : -1), limits[side][0], this.panelMax(side)); };
      const stop = () => this.stopPanelResize();
      target.addEventListener("pointermove", move);
      target.addEventListener("pointerup", stop); target.addEventListener("pointercancel", stop);
      target.addEventListener("lostpointercapture", stop);
      this._endPanelResize = () => {
        target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", stop);
        target.removeEventListener("pointercancel", stop); target.removeEventListener("lostpointercapture", stop);
        if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId);
        this.saveLayout();
      };
    },
    stopPanelResize() { const stop = this._endPanelResize; this._endPanelResize = null; stop?.(); this.draggingPanel = ""; },
  },
};
