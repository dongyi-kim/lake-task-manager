import { eventText } from './trackingModel.js';

// bodyHtml is produced by the server's shared Jira renderer and sanitizer.
let nextCommentId = 0;

export function commentLineLayout(rects, top, lineHeight) {
  const lines = [];
  for (const rect of [...rects].sort((a, b) => a.top - b.top)) {
    const previous = lines.at(-1);
    if (previous && Math.abs(previous.top - rect.top) < lineHeight * .45) {
      previous.bottom = Math.max(previous.bottom, rect.bottom);
    } else lines.push({top: rect.top, bottom: rect.bottom});
  }
  return {foldable: lines.length >= 3,
    previewHeight: Math.max(lineHeight * 2, (lines[1]?.bottom ?? top) - top)};
}

export default {
  name: 'TrackingComment',
  props: {event: {type: Object, required: true}},
  data() {
    return {expanded: false, foldable: false, previewHeight: 42,
      bodyId: `tr-comment-${++nextCommentId}`};
  },
  computed: {
    bodyHtml() { return this.event.bodyHtml || ''; },
    bodyText() { return eventText(this.event); },
  },
  watch: {
    expanded() { this.$nextTick(() => this.updateFocusability()); },
    event: {handler() {
      this.expanded = false;
      this.foldable = false;
      this.$nextTick(() => this.measure());
    }},
  },
  mounted() {
    this._resizeObserver = new ResizeObserver(() => this.measure());
    this._resizeObserver.observe(this.$refs.body);
    this.$refs.body.addEventListener('load', this.measure, true);
    this.measure();
  },
  beforeUnmount() {
    this._resizeObserver?.disconnect();
    this.$refs.body?.removeEventListener('load', this.measure, true);
  },
  methods: {
    measure() {
      const body = this.$refs.body;
      if (!body || !body.getBoundingClientRect().width) return;
      const lineHeight = parseFloat(getComputedStyle(body).lineHeight) || 21;
      const rects = [];
      const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      const range = document.createRange();
      while (walker.nextNode()) {
        if (!walker.currentNode.textContent.trim()) continue;
        range.selectNodeContents(walker.currentNode);
        rects.push(...[...range.getClientRects()].filter(r => r.width && r.height));
      }
      // Preserve blank lines and large non-text content such as attached images.
      for (const node of body.querySelectorAll('br, img')) {
        const rect = node.getBoundingClientRect();
        rects.push({top: rect.top, bottom: rect.bottom});
        if (node.tagName === 'IMG' && rect.height >= lineHeight * 3) {
          rects.push({top: rect.top + lineHeight, bottom: rect.top + lineHeight * 2},
            {top: rect.top + lineHeight * 2, bottom: rect.top + lineHeight * 3});
        }
      }
      const layout = commentLineLayout(rects, body.getBoundingClientRect().top, lineHeight);
      this.foldable = layout.foldable;
      this.previewHeight = Math.ceil(layout.previewHeight);
      this.$nextTick(() => this.updateFocusability());
    },
    updateFocusability() {
      const body = this.$refs.body;
      if (!body) return;
      if (!this._originalTabindex) this._originalTabindex = new WeakMap();
      const boundary = body.parentElement.getBoundingClientRect().bottom;
      for (const target of body.querySelectorAll('a[href], button, input, [tabindex]')) {
        if (!this._originalTabindex.has(target)) this._originalTabindex.set(target, target.getAttribute('tabindex'));
        if (this.foldable && !this.expanded && target.getBoundingClientRect().bottom > boundary + 1) {
          target.setAttribute('tabindex', '-1');
        } else {
          const original = this._originalTabindex.get(target);
          if (original === null) target.removeAttribute('tabindex');
          else target.setAttribute('tabindex', original);
        }
        // Tracking is a saved observation; checkboxes do not mutate Jira.
        if (target.matches('input')) target.disabled = true;
      }
    },
  },
  template: `
    <div class="tr-comment" :class="{'is-collapsed': foldable && !expanded}">
      <div :id="bodyId" class="tr-comment-clip"
        :style="foldable && !expanded ? {maxHeight: previewHeight + 'px'} : null">
        <div v-if="bodyHtml" ref="body" class="tr-comment-body tkt-desc" v-html="bodyHtml"></div>
        <div v-else ref="body" class="tr-comment-body tr-comment-plain">{{ bodyText }}</div>
      </div>
      <button v-if="foldable" type="button" class="tr-comment-toggle"
        :aria-expanded="expanded" :aria-controls="bodyId" @click="expanded = !expanded">
        {{ expanded ? '댓글 접기' : '댓글 펼치기' }}
        <span aria-hidden="true">{{ expanded ? '−' : '+' }}</span>
      </button>
    </div>`,
};
