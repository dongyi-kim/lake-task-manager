/** The same panel is docked on desktop and a native modal drawer on small screens. */
export default {
  props: { visible: Boolean, compact: Boolean, label: String },
  emits: ["close"],
  watch: { visible() { this.sync(); }, compact() { this.sync(); } },
  mounted() { this.sync(); },
  activated() { this.sync(); },
  deactivated() { this.$refs.panel?.close(); },
  methods: {
    sync() {
      // Release modal inertness before the caller's nextTick moves focus into the editor.
      if (!this.visible) { this.$refs.panel?.close(); return; }
      this.$nextTick(() => {
        const panel = this.$refs.panel;
        if (!panel) return;
        if (!this.visible) { panel.close(); return; }
        if (this.compact) {
          if (!panel.matches(":modal")) { panel.close(); panel.showModal(); }
        } else {
          if (panel.matches(":modal")) panel.close();
          panel.setAttribute("open", "");
        }
      });
    },
    backdrop(event) {
      if (!this.compact || event.target !== this.$refs.panel) return;
      const box = event.target.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)
        this.$emit("close");
    },
    leaveForSearch(event) {
      if (!this.compact) return;
      const target = event.target;
      const typing = target.matches("input,textarea") || target.isContentEditable;
      if (((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") || (event.key === "/" && !typing)) {
        // Release the native top layer before app-root opens the global search overlay.
        this.$refs.panel.close(); this.$emit("close");
      }
    },
  },
  template: `<dialog ref="panel" :role="compact ? 'dialog' : 'complementary'"
    :aria-label="label" :aria-modal="compact ? 'true' : null"
    @cancel.prevent="$emit('close')" @click="backdrop" @keydown="leaveForSearch"><slot></slot></dialog>`,
};
