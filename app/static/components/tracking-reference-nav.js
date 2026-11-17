// Saved-run navigation. Scope selection and disclosure remain separate actions.
const ICONS = {
  layers:'<rect x="3" y="3" width="13" height="13" rx="2"/><path d="M8 20h11a2 2 0 0 0 2-2V8M6.5 9.5l2.2 2.2 4-4"/>',
  task:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m8 11 3 3 5-6"/>',
  epic:'<path d="M13 2 5 13h6l-1 9 9-12h-6Z"/>',
  bug:'<rect x="7" y="6" width="10" height="15" rx="5"/><path d="m9 6-2-3m8 3 2-3M3 10h4m10 0h4M3 16h4m10 0h4M12 7v13"/>',
  story:'<path d="M6 3h12v18l-6-4-6 4Z"/>',
  subtask:'<path d="M4 3v9h5M4 7h5"/><rect x="9" y="4" width="11" height="6" rx="2"/><rect x="9" y="14" width="11" height="6" rx="2"/><path d="M4 12v5h5"/>',
  chat:'<path d="M20 15a3 3 0 0 1-3 3H8l-5 3V6a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3Z"/><path d="M7 8h9M7 12h6"/>',
  outbound:'<path d="M7 17 17 7M7 7h10v10"/>',
  panel:'<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 4v16m7-11-3 3 3 3"/>',
  reopen:'<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 4v16m4-11 3 3-3 3"/>',
  check:'<circle cx="12" cy="12" r="8.3"/><path d="m8 12 2.7 2.7 5.5-6"/>',
  clock:'<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5h4"/>',
  again:'<path d="M5 8a8 8 0 1 1-1 7M5 3v5h5"/>',
  alert:'<circle cx="12" cy="12" r="8.5"/><path d="M12 7v6m0 3v.1"/>',
  progress:'<circle cx="12" cy="12" r="8.5"/><path d="M12 6v6l4 2"/>',
  help:'<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3v.1"/>',
};
export const TrackingReferenceIcon = {
  name:'TrackingReferenceIcon',
  props:{name:{type:String,default:'task'},size:{type:[Number,String],default:16}},
  computed:{paths(){return ICONS[['module','wbs','group'].includes(this.name)?'layers':this.name] || ICONS.task;}},
  template:'<svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" v-html="paths"></svg>',
};
export function trackingReferenceIconName(row) {
  const kind=String(row?.issue?.type || row?.kind || '').toLowerCase().replace(/[-_ ]/g,'');
  if (['module','wbs','group'].includes(kind)) return 'layers';
  return ({voc:'chat',subtask:'subtask',epic:'epic',bug:'bug',story:'story'})[kind] || 'task';
}
export default {
  name:'TrackingReferenceNav',components:{TrackingReferenceIcon},
  props:{rows:{type:Array,default:()=>[]},allRows:{type:Array,default:()=>[]},activeId:String,detailId:String,
    closed:{type:Object,default:()=>({})},pins:{type:Array,default:()=>[]},width:Number,collapsed:Boolean,
    revealMatches:Boolean,total:Number,scopeLabel:String},
  emits:['scope','fold','collapse','pin','inspect'],
  computed:{
    entries(){
      const entries=this.rows.map(row=>({row,last:true,guides:[]})),next=[];
      for(let i=entries.length-1;i>=0;i--){
        while(next.length&&entries[next.at(-1)].row.depth>entries[i].row.depth)next.pop();
        entries[i].last=!next.length||entries[next.at(-1)].row.depth<entries[i].row.depth;next.push(i);
      }
      const ancestors=[];
      for(const entry of entries){
        ancestors.length=entry.row.depth;
        entry.guides=ancestors.flatMap((ancestor,depth)=>depth>0&&ancestor&&!ancestor.last?[depth-1]:[]);
        ancestors[entry.row.depth]=entry;
      }
      return entries;
    },
    ancestorIds(){return new Set((this.allRows.find(row=>row.id===this.activeId)?.path||[]).map(node=>node.id));},
    pinnedRows(){const seen=new Set();return this.allRows.filter(row=>{const identity=row.key||row.id;return this.pins.includes(identity)&&!seen.has(identity)&&seen.add(identity);});},
  },
  methods:{
    icon:trackingReferenceIconName,
    expanded(row){return !this.closed[row.id]||(this.revealMatches&&row.containsMatch);},
    collapseAll(){
      for(const row of this.allRows)if(row.children?.length&&!this.closed[row.id])this.$emit('fold',row);
    },
    tooltip(row){return row.title+(row.key?' ('+row.key+')':'');},
    select(row){this.$emit('scope',row.id);},
    togglePanel(collapsed){
      this.$emit('collapse',collapsed);
      this.$nextTick(()=>this.$refs.panelToggle?.focus());
    },
  },
  template:`
    <nav v-if="collapsed" class="tp-nav-rail" aria-label="접힌 업무 탐색">
      <button type="button" ref="panelToggle" class="tp-panel-reopen" @click="togglePanel(false)" aria-controls="tracking-hierarchy-content" aria-label="업무 탐색 패널 펼치기" title="업무 탐색 패널 펼치기" aria-expanded="false"><span aria-hidden="true">+</span></button>
      <span class="tp-panel-rail-label" aria-hidden="true">업무 탐색</span>
    </nav>
    <div v-else class="tp-navigation-panel" :style="width ? {width:width+'px',flexBasis:width+'px'} : null">
      <button ref="panelToggle" type="button" class="tp-panel-toggle" @click="togglePanel(true)" aria-label="업무 탐색 패널 접기" title="업무 탐색 패널 접기" aria-expanded="true" aria-controls="tracking-hierarchy-content"><span aria-hidden="true">−</span></button>
      <nav id="tracking-hierarchy-content" class="tp-navigation" aria-label="업무 계층 탐색" tabindex="0">
      <header class="tp-side-head"><span>업무 탐색</span><div class="tp-tree-panel-actions"><button type="button" class="tp-tree-collapse-all" @click="collapseAll" :disabled="revealMatches" aria-label="트리의 모든 항목 접기" :title="revealMatches?'일치 경로를 표시하고 있습니다.':'모든 항목 접기'"><span class="tp-disclosure" aria-hidden="true">−</span>모두 접기</button></div></header>
      <button type="button" class="tp-all-node" :class="{'is-active':!activeId}" :aria-current="!activeId?'page':null" @click="$emit('scope','')"><span><TrackingReferenceIcon name="layers" :size="15"/>전체 업무</span><span>{{ total ?? 0 }}</span></button>
      <div class="tp-side-kicker">WBS · Epic · Task · Subtask</div>
      <ul class="tp-tree-roots" aria-label="업무 계층">
        <li v-for="entry in entries" :key="entry.row.id" class="tp-tree-entry" :style="{'--tp-depth':entry.row.depth}" :class="{'is-last':entry.last,'has-parent':entry.row.depth>0,'on-route':ancestorIds.has(entry.row.id),'is-expanded':entry.row.children?.length&&expanded(entry.row)}">
          <i v-for="guide in entry.guides" :key="guide" class="tp-tree-guide" :style="{left:(guide*18+12)+'px'}" aria-hidden="true"></i><i v-if="entry.row.depth" class="tp-tree-elbow" aria-hidden="true"></i>
          <div class="tp-tree-node" :class="{'is-selected':activeId===entry.row.id,'is-ancestor':ancestorIds.has(entry.row.id),'is-detail':detailId===entry.row.id&&activeId!==entry.row.id}">
            <button v-if="entry.row.children?.length" type="button" class="tp-tree-exp" :aria-expanded="expanded(entry.row)" :aria-label="entry.row.title+(expanded(entry.row)?' 접기':' 펼치기')" :title="entry.row.title+(expanded(entry.row)?' 접기':' 펼치기')" @click="$emit('fold',entry.row)"><span class="tp-disclosure" aria-hidden="true">{{ expanded(entry.row)?'−':'+' }}</span></button><span v-else class="tp-tree-exp-spacer" aria-hidden="true"></span>
            <span class="tp-tree-type-icon" :class="'tp-kind-'+icon(entry.row)"><TrackingReferenceIcon :name="icon(entry.row)" :size="13"/></span>
            <button type="button" class="tp-tree-label" :title="tooltip(entry.row)" :aria-label="tooltip(entry.row)+' 범위 선택'" :aria-current="activeId===entry.row.id?'page':null" @click="select(entry.row)">{{ entry.row.title }}</button>
            <span v-if="activeId===entry.row.id" class="tp-tree-selected-tag">선택</span><span v-else-if="detailId===entry.row.id" class="tp-tree-focus-tag">근거</span><span v-else-if="entry.row.children?.length" class="tp-node-count" :title="'직속 하위 '+entry.row.children.length+'개'">{{ entry.row.children.length }}</span>
          </div>
        </li>
      </ul>
      <div class="tp-side-kicker">고정한 주요 업무 · {{ pinnedRows.length }}</div>
      <div v-for="row in pinnedRows" :key="row.key||row.id" class="tp-pinned-row"><button class="tp-side-pin" type="button" :title="tooltip(row)" @click="select(row)"><span aria-hidden="true">☆</span>{{ row.title }}</button><button type="button" class="tp-unpin" :aria-label="row.title+' 고정 해제'" title="고정 해제" @click="$emit('pin',row.key||row.id)">×</button></div>
      <footer class="tp-side-foot">WBS → Epic → Task → Subtask<br>파란색: 현재 선택 범위<br>연결선: 부모와 직속 하위 항목</footer>
      </nav>
    </div>`,
};
