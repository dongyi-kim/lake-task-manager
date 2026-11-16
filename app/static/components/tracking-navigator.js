// Branches select a report scope; terminal tickets open their evidence directly.
import { isTerminalTrackingRow } from './trackingModel.js';
export default {
  name:'TrackingNavigator',
  props:{rows:Array,activeId:String,evidenceId:String,closed:Object,total:Number,mode:String,revealMatches:Boolean},
  emits:['scope','evidence','fold','inspect'],
  methods:{
    opensEvidence:isTerminalTrackingRow,
    activate(row) { this.opensEvidence(row) ? this.$emit('evidence',row) : this.$emit('scope',row.id); },
    kind(row) { return ({module:'모듈',wbs:row.virtual?'가상 WBS':'WBS',epic:row.virtual?'분류 Epic':'Epic',task:'Task',story:'Task',subtask:'Subtask'})[row.kind] || row.kind; },
  },
  template:`
    <nav class="tr-scope-nav" aria-label="업무 계층과 보고 범위">
      <header><h2>업무 범위</h2><span>상위는 요약 · 말단은 상세</span></header>
      <button class="tr-scope-all" :class="{'is-active':!activeId}" :aria-current="!activeId ? 'true' : undefined" @click="$emit('scope','')">전체 업무 <span>{{ total }}</span></button>
      <ul class="tr-scope-nodes"><li v-for="row in rows" :key="row.id" class="tr-scope-node" :class="{'is-active':activeId === row.id,'is-evidence':evidenceId === row.id,'is-match':row.matched}" :style="{'--scope-depth':Math.min(row.depth,3)}">
        <div class="tr-scope-node-main"><button v-if="row.children.length" class="tr-scope-fold" :aria-expanded="revealMatches || !closed[row.id]" :disabled="revealMatches" :title="revealMatches ? '일치 항목의 경로를 펼쳐 표시합니다' : undefined" :aria-label="row.title+(!revealMatches && closed[row.id] ? ' 펼치기' : ' 접기')" @click="$emit('fold',row)">{{ !revealMatches && closed[row.id] ? '+' : '−' }}</button><span v-else class="tr-scope-fold-space"></span>
          <button class="tr-scope-node-label" :aria-current="activeId === row.id ? 'true' : undefined" :aria-label="row.title+(opensEvidence(row) ? ' 상세 근거 보기' : ' 업무 요약 보기')" :title="row.title+(row.key ? ' ('+row.key+')' : '')" @click="activate(row)"><strong>{{ row.title }}</strong><small>{{ kind(row) }}<span v-if="opensEvidence(row)"> · 상세 근거</span><span v-else-if="row.counts.uniqueIssues"> · 조회 티켓 {{ row.counts.uniqueIssues }}</span></small></button>
        </div><button v-if="!opensEvidence(row)" class="tr-scope-node-evidence" :aria-label="row.title+' 활동 근거 보기'" @click="$emit('evidence',row)">근거</button>
      </li></ul>
      <footer class="tr-scope-nav-footer"><button @click="$emit('inspect')" :aria-pressed="mode === 'tree'">계층별 집계</button><span>생성·완료·댓글·변경 건수</span></footer>
    </nav>`,
};
