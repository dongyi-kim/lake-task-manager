// Every hierarchy row selects the same summary/detail workspace.
import { isTerminalTrackingRow } from './trackingModel.js';
import { TYPE_BG, typeLabel } from '../lib/colors.js';
const ISSUE_TYPES = Object.fromEntries(Object.keys(TYPE_BG).map(type => [type.toLowerCase().replace(/[-_ ]/g,''),type]));
export default {
  name:'TrackingNavigator',
  props:{width:Number,rows:Array,activeId:String,closed:Object,total:Number,mode:String,revealMatches:Boolean},
  emits:['scope','fold','inspect','resize'],
  computed:{
    entries() {
      const entries = this.rows.map(row => ({row,last:true,guides:[]})), next = [];
      for (let i = entries.length-1; i >= 0; i--) {
        while (next.length && entries[next.at(-1)].row.depth > entries[i].row.depth) next.pop();
        entries[i].last = !next.length || entries[next.at(-1)].row.depth < entries[i].row.depth;
        next.push(i);
      }
      const ancestors = [];
      for (const entry of entries) {
        ancestors.length = entry.row.depth;
        entry.guides = ancestors.flatMap((ancestor,depth) => depth > 0 && ancestor && !ancestor.last ? [depth-1] : []);
        ancestors[entry.row.depth] = entry;
      }
      return entries;
    },
  },
  methods:{
    opensEvidence:isTerminalTrackingRow,
    activate(row) { this.$emit('scope',row.id); },
    typeGroup(row) { return ['module','wbs','epic'].includes(row.kind) ? row.kind : row.kind.startsWith('sub') ? 'subtask' : 'task'; },
    issueType(row) {
      const raw = row.issue?.type || row.type || row.kind;
      return ISSUE_TYPES[String(raw).toLowerCase().replace(/[-_ ]/g,'')] || raw;
    },
    kind(row) {
      if (row.epicUnassigned) return '미소속';
      return ({module:'모듈',wbs:'WBS Task'})[row.kind] || typeLabel(this.issueType(row));
    },
    typeColor(row) { return row.epicUnassigned || ['module','wbs'].includes(row.kind) ? undefined : TYPE_BG[this.issueType(row)]; },
  },
  template:`
    <nav class="tr-scope-nav" aria-label="업무 계층과 보고 범위">
      <header><h2>업무 범위</h2><label class="tr-nav-width"><span class="tr-sr">계층 패널 너비</span><select :value="[240,300,420,560].includes(width) ? width : ''" @change="$emit('resize',Number($event.target.value))" aria-label="계층 패널 너비"><option value="" disabled>사용자 폭</option><option :value="240">좁게</option><option :value="300">기본 폭</option><option :value="420">넓게</option><option :value="560">아주 넓게</option></select></label></header>
      <button class="tr-scope-all" :class="{'is-active':!activeId}" :aria-current="!activeId ? 'true' : undefined" @click="$emit('scope','')">전체 업무 <span>{{ total }}</span></button>
      <ul class="tr-scope-nodes" aria-label="업무 계층"><li v-for="{row,last,guides} in entries" :key="row.id" class="tr-scope-node" :class="['tr-scope-level-'+typeGroup(row),{'is-active':activeId === row.id,'is-match':row.matched,'is-unassigned':row.epicUnassigned}]" :style="{'--scope-depth':row.depth}">
        <span v-for="lane in guides" :key="lane" class="tr-tree-guide" :style="{'--scope-lane':lane}" aria-hidden="true"></span><span v-if="row.depth" class="tr-tree-branch" :class="{'is-last':last}" aria-hidden="true"></span>
        <div class="tr-scope-node-main"><button v-if="row.children.length" class="tr-scope-fold" :aria-expanded="revealMatches || !closed[row.id]" :disabled="revealMatches" :title="revealMatches ? '일치 항목의 경로를 펼쳐 표시합니다' : undefined" :aria-label="row.title+(!revealMatches && closed[row.id] ? ' 펼치기' : ' 접기')" @click="$emit('fold',row)"><span aria-hidden="true">{{ !revealMatches && closed[row.id] ? '+' : '−' }}</span></button><span v-else class="tr-scope-fold-space" aria-hidden="true"></span>
          <button class="tr-scope-node-label" :aria-current="activeId === row.id ? 'true' : undefined" :aria-label="kind(row)+' '+row.title+(opensEvidence(row) ? ' 상세 근거 보기' : ' 업무 요약 보기')" :title="row.title+(row.key ? ' ('+row.key+')' : '')" @click="activate(row)"><span class="tr-scope-type" :class="['tr-scope-type-'+typeGroup(row),{'tr-scope-type-unassigned':row.epicUnassigned}]" :style="{'--type-color':typeColor(row)}">{{ kind(row) }}</span><strong>{{ row.title }}</strong><small v-if="row.counts.uniqueIssues && !opensEvidence(row)" :title="'조회된 티켓 '+row.counts.uniqueIssues+'개'">{{ row.counts.uniqueIssues }}</small></button>
        </div>
      </li></ul>
      <footer class="tr-scope-nav-footer"><button @click="$emit('inspect')" :aria-pressed="mode === 'tree'">계층별 집계</button><span>생성·완료·댓글·변경 건수</span></footer>
    </nav>`,
};
