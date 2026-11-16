// Local display preference; resizing never changes the selected scope or query.
const DEFAULT_WIDTH = 300, MIN_WIDTH = 240, MAX_WIDTH = 600;

export default {
  name:'TrackingSplitter',
  props:{
    width:{type:Number,default:DEFAULT_WIDTH},
    defaultWidth:{type:Number,default:DEFAULT_WIDTH},
    minimumWidth:{type:Number,default:MIN_WIDTH},
    maximumWidth:{type:Number,default:MAX_WIDTH},
    controls:{type:String,default:'tracking-hierarchy'},
    storageKey:{type:String,default:'ltm.tracking.hierarchyWidth'},
  },
  emits:['resize'],
  data() { return {preferredWidth:this.width,containerWidth:1032,dragging:false}; },
  computed:{
    minWidth() { return this.minimumWidth ?? MIN_WIDTH; },
    maxWidth() { return Math.max(this.minWidth,Math.min(this.maximumWidth ?? MAX_WIDTH,this.containerWidth-420-12)); },
    renderedWidth() { return Math.round(Math.max(this.minWidth,Math.min(this.maxWidth,this.preferredWidth))); },
  },
  watch:{
    width(value) {
      // Parent echoes our bounded width. Keep the wider preference for when the
      // window expands; explicit changes from the header remain persistent.
      if (value !== this._publishedWidth) this.setWidth(value);
    },
  },
  mounted() {
    try {
      const saved = window.localStorage.getItem(this.storageKey);
      if (saved !== null && saved.trim() && Number.isFinite(Number(saved))) {
        this.preferredWidth = Math.max(this.minimumWidth ?? MIN_WIDTH,Math.min(this.maximumWidth ?? MAX_WIDTH,Number(saved)));
      }
    } catch (_) { /* Storage may be disabled; resizing still works. */ }
    this.observeLayout();
  },
  activated() { this.observeLayout(); },
  deactivated() { this.stopObserving(); },
  beforeUnmount() { this.stopObserving(); },
  methods:{
    publishWidth() {
      this._publishedWidth = this.renderedWidth;
      if (this.width !== this.renderedWidth) this.$emit('resize',this.renderedWidth);
    },
    saveWidth() {
      try { window.localStorage.setItem(this.storageKey,String(this.preferredWidth)); } catch (_) { /* Optional preference. */ }
    },
    setWidth(width,persist=true) {
      if (!Number.isFinite(width)) return;
      this.preferredWidth = Math.round(Math.max(this.minimumWidth ?? MIN_WIDTH,Math.min(this.maximumWidth ?? MAX_WIDTH,width)));
      this.publishWidth();
      if (persist) this.saveWidth();
    },
    updateBounds() {
      const width = this.$el.parentElement?.clientWidth;
      if (width > 0) this.containerWidth = width;
      this.publishWidth();
    },
    observeLayout() {
      if (this._layoutListener) return;
      this._layoutListener = () => this.updateBounds();
      window.addEventListener('resize',this._layoutListener);
      if (typeof ResizeObserver !== 'undefined' && this.$el.parentElement) {
        this._layoutObserver = new ResizeObserver(this._layoutListener);
        this._layoutObserver.observe(this.$el.parentElement);
      }
      this.updateBounds();
    },
    stopObserving() {
      this._stopResize?.();
      if (this._layoutListener) window.removeEventListener('resize',this._layoutListener);
      this._layoutObserver?.disconnect();
      this._layoutObserver = null;
      this._layoutListener = null;
    },
    resetWidth() { this.setWidth(this.defaultWidth ?? DEFAULT_WIDTH); },
    resizeKey(event) {
      const width = {ArrowLeft:this.renderedWidth-16,ArrowRight:this.renderedWidth+16,
        Home:this.defaultWidth ?? DEFAULT_WIDTH,End:this.maxWidth}[event.key];
      if (width === undefined) return;
      event.preventDefault();
      this.setWidth(Math.max(this.minWidth,Math.min(this.maxWidth,width)));
    },
    startResize(event) {
      if (event.button !== 0 || event.isPrimary === false) return;
      event.preventDefault();
      this._stopResize?.();
      const grip = event.currentTarget, pointer = event.pointerId;
      const startX = event.clientX, startWidth = this.renderedWidth;
      const previousCursor = document.body.style.cursor, previousSelection = document.body.style.userSelect;
      const move = e => {
        if (e.pointerId === pointer) this.setWidth(Math.max(this.minWidth,Math.min(this.maxWidth,startWidth+e.clientX-startX)),false);
      };
      const finish = e => {
        if (e?.pointerId !== undefined && e.pointerId !== pointer) return;
        this._stopResize = null;
        this.dragging = false;
        window.removeEventListener('pointermove',move);
        window.removeEventListener('pointerup',finish);
        window.removeEventListener('pointercancel',finish);
        window.removeEventListener('blur',finish);
        if (grip.hasPointerCapture?.(pointer)) grip.releasePointerCapture(pointer);
        document.body.style.cursor = previousCursor;
        document.body.style.userSelect = previousSelection;
        this.saveWidth();
      };
      this._stopResize = finish;
      this.dragging = true;
      // Window listeners also cover environments that cannot capture pointers.
      try { grip.setPointerCapture(pointer); } catch (_) { /* Window fallback. */ }
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      window.addEventListener('pointermove',move);
      window.addEventListener('pointerup',finish);
      window.addEventListener('pointercancel',finish);
      window.addEventListener('blur',finish);
    },
    lostCapture(event) { this._stopResize?.(event); },
  },
  template:`<div class="tr-splitter" :class="{'is-dragging':dragging}" role="separator" tabindex="0"
    aria-orientation="vertical" aria-label="계층 패널 너비 조절" :aria-controls="controls"
    :aria-valuemin="minWidth" :aria-valuemax="maxWidth" :aria-valuenow="renderedWidth"
    title="드래그 또는 방향키로 너비 조절 · 두 번 클릭하면 기본 너비"
    @pointerdown="startResize" @lostpointercapture="lostCapture" @keydown="resizeKey" @dblclick="resetWidth"></div>`,
};
