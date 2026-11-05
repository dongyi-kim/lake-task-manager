export default {
  props: { title: String, rows: Array, expanded: Boolean, selectedId: String, emptyText: String },
  emits: ["toggle", "open"],
  template: `<section class="conf-local-list" :aria-label="title">
    <h2><button class="conf-list-toggle" :aria-label="title" :aria-expanded="expanded" @click="$emit('toggle')">
      <span class="conf-list-chevron" aria-hidden="true">{{ expanded ? '⌄' : '›' }}</span>{{ title }}<span class="conf-list-count">{{ rows.length }}</span>
    </button></h2>
    <div v-show="expanded">
      <p v-if="!rows.length" class="conf-muted">{{ emptyText }}</p>
      <button v-for="item in rows" :key="item.id" class="conf-tree-title" :class="{selected: selectedId === item.id}"
        @click="$emit('open', item.id)" :title="item.title">{{ title === '즐겨찾기' ? '★ ' : '' }}{{ item.title }}</button>
    </div>
  </section>`,
};
