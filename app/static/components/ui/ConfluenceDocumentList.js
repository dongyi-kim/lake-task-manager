import ConfluenceTreeRow from './ConfluenceTreeRow.js';

export default {
  components: { ConfluenceTreeRow },
  props: { title: String, rows: Array, expanded: Boolean, selectedId: String, emptyText: String,
    nodes: Map, favorites: Set, recents: Set, pending: Object, errors: Object },
  emits: ['toggle', 'open', 'expand', 'menu'],
  data: () => ({ folds: {} }),
  computed: {
    visibleRows() {
      const children = new Map();
      for (const node of this.nodes?.values() || []) {
        const siblings = children.get(node.parent) || [];
        siblings.push(node); children.set(node.parent, siblings);
      }
      for (const siblings of children.values()) siblings.sort((a,b) => a.title.localeCompare(b.title, undefined, {numeric:true}));
      const result = [];
      const visit = (node, root, level, seen) => {
        const id = String(node.id), key = root + ':' + id;
        if (seen.has(id)) return;
        const nested = children.get(id) || [], hasChildren = !!nested.length || node.hasChildren === true;
        const row = {...node, id, key, level, hasChildren, unknownChildren: !nested.length && node.hasChildren == null, open: !!this.folds[key]};
        result.push(row);
        if (row.open) for (const child of nested) visit(child, root, level+1, new Set([...seen,id]));
      };
      for (const row of this.rows) visit(this.nodes?.get(String(row.id)) || row, String(row.id), 0, new Set());
      return result;
    },
  },
  methods: {
    toggleRow(row) { this.folds[row.key] = !row.open; if (!row.open) this.$emit('expand',row); },
  },
  template: `<section class="conf-local-list" :aria-label="title">
    <h2><button class="conf-list-toggle" :aria-label="title" :aria-expanded="expanded" @click="$emit('toggle')">
      <span class="conf-list-chevron" aria-hidden="true">{{ expanded ? '⌄' : '›' }}</span>{{ title }}<span class="conf-list-count">{{ rows.length }}</span>
    </button></h2>
    <div v-show="expanded">
      <p v-if="!rows.length" class="conf-muted">{{ emptyText }}</p>
      <template v-for="row in visibleRows" :key="row.key">
        <ConfluenceTreeRow :row="row" :selected-id="selectedId" :favorite="favorites?.has(row.id)" :recent="recents?.has(row.id)" :pending="pending?.[row.id]"
          @toggle="toggleRow" @open="$emit('open',$event)" @menu="(row,event)=>$emit('menu',row,event)" />
        <p v-if="row.open && errors?.[row.id]" class="conf-saved-error" role="alert">하위 문서를 불러오지 못했습니다.
          <button class="conf-more" @click="$emit('expand',row)">다시 시도</button></p>
      </template>
    </div>
  </section>`,
};
