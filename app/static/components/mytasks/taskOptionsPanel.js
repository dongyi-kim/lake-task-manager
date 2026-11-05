// Tasks uses the ticket dialog's edge grip and middle fold handle. Width is a
// display preference: resizing never changes query filters or reloads tickets.
export const OPTIONS_WIDTH = 220;
export const OPTIONS_MIN_WIDTH = 200;
export const OPTIONS_MAX_WIDTH = 460;

export const optionsPanelComputed = {
  optionsMaxWidth() { return Math.min(OPTIONS_MAX_WIDTH, Math.max(OPTIONS_MIN_WIDTH, this.optionsViewportWidth - 48)); },
  optionsRenderedWidth() { return Math.min(this.optionsWidth, this.optionsMaxWidth); },
};

export const optionsPanelMethods = {
  updateOptionsLayout() {
    this.optionsViewportWidth = window.innerWidth;
    // The application header can grow on a narrow window. The drawer starts at
    // its real boundary and fills the viewport, even when the board is short.
    const top = Math.max(0, document.querySelector("header.top")?.getBoundingClientRect().bottom || 46);
    this.$el.style.setProperty("--mt-options-top", top + "px");
  },
  setOptionsWidth(width) {
    this.optionsWidth = Math.round(Math.max(OPTIONS_MIN_WIDTH, Math.min(this.optionsMaxWidth, width)));
  },
  resetOptionsWidth() {
    this.optionsWidth = OPTIONS_WIDTH;
    this.savePrefs();
  },
  resizeOptionsKey(e) {
    const next = { ArrowLeft: this.optionsRenderedWidth - 16, ArrowRight: this.optionsRenderedWidth + 16,
      Home: OPTIONS_MIN_WIDTH, End: this.optionsMaxWidth }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    this.setOptionsWidth(next);
    this.savePrefs();
  },
  startOptionsResize(e) {
    if (e.button !== 0 || this.optionsClosed) return;
    e.preventDefault();
    this._stopOptionsResize?.();
    const grip = e.currentTarget, pointer = e.pointerId;
    const startX = e.clientX, width = this.optionsRenderedWidth;
    const previousSelection = document.body.style.userSelect;
    const previousCursor = document.body.style.cursor;
    const move = (event) => {
      if (event.pointerId === pointer) this.setOptionsWidth(width + event.clientX - startX);
    };
    const finish = (event) => {
      if (event?.pointerId !== undefined && event.pointerId !== pointer) return;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      window.removeEventListener("blur", finish);
      if (grip.hasPointerCapture(pointer)) grip.releasePointerCapture(pointer);
      document.body.style.userSelect = previousSelection;
      document.body.style.cursor = previousCursor;
      this._stopOptionsResize = null;
      this.savePrefs();
    };
    this._stopOptionsResize = finish;
    grip.setPointerCapture(pointer);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    window.addEventListener("blur", finish);
  },
};
