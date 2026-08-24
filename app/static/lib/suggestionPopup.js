// TipTap Suggestion popup renderer shared by @mention and slash commands.
// TipTap v3 owns mounting, Floating UI positioning, outside-click dismissal and async cancellation;
// this module only paints rows and handles list selection.

export function createManagedSuggestionRenderer(options) {
  const settings = options || {};
  return () => {
    let element = null;
    let items = [];
    let selected = 0;
    let command = null;
    let loading = false;
    let query = "";
    let unmount = null;

    const cleanup = () => {
      if (unmount) unmount();
      else if (element) element.remove();
      unmount = null;
      element = null;
      items = [];
      command = null;
    };

    const pick = (index) => {
      const item = items[index];
      if (item == null || !command) return false;
      settings.select(item, command);
      return true;
    };

    const paint = () => {
      if (!element) return;
      if (!items.length) {
        const label = loading ? settings.loadingLabel : settings.emptyLabel;
        element.innerHTML = `<div class="mn-empty">${label || ""}</div>`;
        return;
      }
      element.innerHTML = settings.renderItems(items, selected);
      element.querySelectorAll(settings.itemSelector || "[data-suggestion-index]").forEach((row) => {
        row.addEventListener("pointerdown", (event) => {
          event.preventDefault();
          pick(Number(row.dataset.suggestionIndex));
        });
      });
      if (settings.afterPaint) settings.afterPaint(element);
      const current = element.querySelector(settings.selectedSelector || ".sel");
      if (current && current.scrollIntoView) current.scrollIntoView({ block: "nearest" });
    };

    const receive = (props, resetSelection) => {
      const nextQuery = props.query || "";
      if (resetSelection || nextQuery !== query) selected = 0;
      query = nextQuery;
      items = Array.isArray(props.items) ? props.items : [];
      loading = Boolean(props.loading);
      command = props.command;
      if (selected >= items.length) selected = 0;
      paint();
    };

    return {
      onStart(props) {
        cleanup();
        element = document.createElement("div");
        element.className = settings.className || "mention-popup";
        element.setAttribute("role", "listbox");
        receive(props, true);
        unmount = props.mount(element);
      },
      onUpdate(props) { receive(props, false); },
      onKeyDown({ event }) {
        const key = event.key;
        const count = items.length;
        // TipTap handles Escape by dispatching suggestion exit after this callback.
        if (key === "Escape") return false;
        if (key === "ArrowDown") {
          selected = count ? (selected + 1) % count : 0;
          paint();
          return true;
        }
        if (key === "ArrowUp") {
          selected = count ? (selected - 1 + count) % count : 0;
          paint();
          return true;
        }
        if (key === "Enter" || (settings.selectOnTab && key === "Tab")) return pick(selected);
        return false;
      },
      onExit: cleanup,
    };
  };
}
