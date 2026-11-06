export default {
  props: { row: Object, selectedId: String, favorite: Boolean, recent: Boolean, currentMatch: Boolean, pending: Boolean },
  emits: ['toggle', 'open', 'menu'],
  template: `<div class="conf-tree-row" :class="{'is-match': row.match, 'has-match': row.containsMatch, 'is-current-match': currentMatch, 'is-favorite': favorite, 'is-recent': recent, selected: selectedId === row.id}"
    :data-page-id="row.id" :style="{'--depth': row.level || 0}" @contextmenu.prevent.stop="$emit('menu',row,$event)" @keydown.shift.f10.prevent="$emit('menu',row,$event)">
    <button v-if="row.hasChildren || row.unknownChildren" class="conf-tree-toggle" @click="$emit('toggle',row)"
      :aria-expanded="row.hasChildren ? !!row.open : undefined" :aria-label="row.title + (row.unknownChildren ? ' 하위 문서 확인' : ' 하위 문서')">
      <span v-if="row.unknownChildren" aria-hidden="true">…</span><svg v-else viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg>
    </button>
    <span v-else class="conf-tree-leaf" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M4 2h5l3 3v9H4zM9 2v4h3"/></svg></span>
    <span v-if="favorite" class="conf-tree-star" role="img" aria-label="즐겨찾기">★</span>
    <button class="conf-tree-title" :class="{selected: selectedId === row.id}" :aria-current="selectedId === row.id ? 'page' : null"
      @click="$emit('open',row.id)" :title="row.title"><mark v-if="row.match">{{ row.title }}</mark><template v-else>{{ row.title }}</template></button>
    <span v-if="pending" class="conf-tree-pending" role="status" aria-label="하위 문서를 불러오는 중"></span>
  </div>`,
};
