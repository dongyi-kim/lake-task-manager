// One reactive notification source shared by cards, grouped rows and display filters.
import { ref } from "../vendor/vue.esm-browser.prod.js";
import { isTicketUnread, ticketUpdatesState } from "./ticketUpdates.js";

const revision = ref(ticketUpdatesState.revision);
window.addEventListener("ticket-updates-changed", () => { revision.value++; });

export const ticketUpdateView = {
  computed: { ticketUpdatesRevision() { return revision.value; } },
  methods: {
    isUnread(card) {
      void this.ticketUpdatesRevision;
      return isTicketUnread(card);
    },
  },
};
