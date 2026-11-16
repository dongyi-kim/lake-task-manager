// Named outcomes and scope-wide totals from the already collected report.
export default {
  name:'TrackingOverview',
  props:{overview:Object,section:{type:String,default:'highlights'},scopeLabel:{type:String,default:'전체 업무'}},
  emits:['select'],
  methods:{
    label(kind) { return ({completed:'완료 이력 · 현재 완료',progress:'기간 내 확인된 변화',review:'다시 확인할 업무'})[kind] || '확인된 활동'; },
    symbol(kind) { return ({completed:'✓',progress:'↑',review:'!'})[kind] || '·'; },
  },
  template:`
    <section v-if="section === 'highlights' && overview.highlights.length" class="tr-highlights" :aria-label="scopeLabel+' 주요 성과와 확인 사항'">
      <button v-for="item in overview.highlights" :key="item.key" :class="['tr-highlight','tr-highlight-'+item.kind]" :title="item.title+' · '+item.headline" @click="$emit('select',item.key)">
        <span class="tr-highlight-icon" aria-hidden="true">{{ symbol(item.kind) }}</span><div><span class="tr-highlight-label">{{ label(item.kind) }}</span><strong>{{ item.title }}</strong><p>{{ item.headline }}</p></div>
      </button>
    </section>
    <aside v-else-if="section === 'rail'" class="tr-overview-rail" :aria-label="scopeLabel+' 집계와 확인할 사항'">
      <section class="tr-overview-card tr-overview-metrics">
        <header class="tr-overview-card-head"><h2>요약 업무 완료 이력</h2><small>범위 내 {{ overview.totals.roots }}개 기준</small></header>
        <div class="tr-overview-total"><strong>{{ overview.totals.rootCompletionHistory }}</strong><span>건<span v-if="overview.totals.provisional"> 확인 · 잠정</span></span></div>
        <div class="tr-overview-split"><span>그중 현재 완료 <b>{{ overview.totals.currentDoneAfterCompletion }}</b></span><span>현재 미완료 <b>{{ overview.totals.currentNotDoneAfterCompletion }}</b></span><span v-if="overview.totals.currentUnknownAfterCompletion">현재 상태 미확인 <b>{{ overview.totals.currentUnknownAfterCompletion }}</b></span></div>
        <p class="tr-overview-note">완료 이력과 현재 상태는 다를 수 있음</p>
        <dl class="tr-overview-secondary"><div><dt>하위 완료 이력</dt><dd>{{ overview.totals.childCompletionHistory }}건</dd></div><div><dt :title="'상위 업무 '+overview.totals.newRoots+'건 포함'">신규 티켓</dt><dd>{{ overview.totals.newTickets }}건</dd></div><div><dt>보고·논의</dt><dd>{{ overview.totals.comments }}댓글</dd></div><div><dt>확인된 변경</dt><dd>{{ overview.totals.fieldChanges }}건</dd></div></dl>
        <p v-if="overview.totals.provisional" class="tr-overview-note">일부 근거 미확인 · 확인된 이력만 집계</p>
      </section>
      <section class="tr-overview-card tr-overview-followups"><header class="tr-overview-card-head"><h2>우선 확인할 업무</h2><small>{{ overview.totals.review }}개<span v-if="overview.totals.provisional"> · 잠정</span></small></header>
        <button v-for="item in overview.followups" :key="item.key" class="tr-overview-followup" @click="$emit('select',item.key)"><strong>{{ item.title }}</strong><span>{{ item.headline }}</span></button>
        <p v-if="!overview.followups.length" class="tr-overview-note">수집된 근거에서 재확인 대상으로 분류된 업무 없음</p>
        <p v-else-if="overview.totals.review > overview.followups.length" class="tr-overview-note">외 {{ overview.totals.review - overview.followups.length }}개 · 목록의 재확인 분류에서 확인</p>
      </section>
      <slot></slot>
      <p class="tr-overview-note">보고 범위: {{ scopeLabel }}<br>검색·강조는 목록에 적용되며 위 집계는 선택 범위 기준입니다.</p>
    </aside>`,
};
