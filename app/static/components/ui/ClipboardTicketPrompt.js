import { watchClipboardTickets } from "../../lib/clipboardTickets.js";

export default {
  name: "ClipboardTicketPrompt",
  props: {
    jiraBase: { type: String, default: "" },
    openKeys: { type: Array, default: () => [] },
  },
  data() { return { keys: [], selected: "" }; },
  mounted() { this.start(); },
  beforeUnmount() { if (this._reader) this._reader.stop(); },
  watch: {
    jiraBase() { this.start(); },
    openKeys() { if (this._reader) this._reader.refresh(); },
  },
  methods: {
    start() {
      if (this._reader) this._reader.stop();
      this._reader = null;
      if (!this.jiraBase) return;
      this._reader = watchClipboardTickets({
        jiraBase: this.jiraBase,
        isOpen: (key) => this.openKeys.some((open) => String(open || "").toUpperCase() === key),
        onKeys: (keys) => {
          this.keys = keys;
          if (!keys.includes(this.selected)) this.selected = keys[0] || "";
        },
      });
    },
    dismiss() { if (this._reader) this._reader.dismiss(); },
    open() {
      if (!this.keys.includes(this.selected)) return;
      const key = this.selected;
      this.dismiss();
      window.dispatchEvent(new CustomEvent("lake-open-ticket", { detail: { key } }));
    },
  },
  template: `
    <div v-if="keys.length" class="clipboard-ticket-prompt" role="region" aria-label="클립보드 Jira 링크">
      <span>클립보드의 Jira 링크를 여시겠습니까?</span>
      <select v-if="keys.length > 1" v-model="selected" aria-label="열 Jira 티켓">
        <option v-for="key in keys" :key="key" :value="key">{{ key }}</option>
      </select>
      <strong v-else>{{ selected }}</strong>
      <button type="button" class="btn sm primary" @click="open">열기</button>
      <button type="button" class="btn sm" @click="dismiss">닫기</button>
    </div>`,
};
