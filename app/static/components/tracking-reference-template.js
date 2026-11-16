// Reference dashboard chrome. Collection remains in TrackingView; local exploration
// is delegated to the briefing, navigation, and evidence components.
export default `
<section class="tracking-reference" aria-labelledby="tracking-title" @keydown.esc="picker = ''">
  <header class="rf-header">
    <div class="rf-brand"><span class="rf-brand-icon"><TrackingReferenceIcon name="layers" :size="21" /></span><div><h1 id="tracking-title">Tracking</h1><p>기간의 성과, 지금의 진척</p></div></div>
    <div class="rf-header-actions">
      <button @click="showOverlay('history')" :disabled="!recent.length">이전 조회</button>
      <button @click="showOverlay('ai')" :disabled="!run">AI 요약 <span class="rf-beta">AI</span></button>
      <button class="rf-primary" @click="showOverlay('copy')" :disabled="!run">보고서 복사</button>
    </div>
  </header>
  <form class="rf-query" @submit.prevent="collect">
    <fieldset><legend class="rf-sr">조회 기간 · 한국 시간, 양끝 날짜 포함</legend><label for="tracking-start">기간</label>
      <input id="tracking-start" type="date" v-model="startDate" :max="endDate || today" aria-label="시작일" required><span class="rf-muted">—</span><input type="date" v-model="endDate" :min="startDate" :max="today" aria-label="종료일" required>
      <div class="rf-presets"><button type="button" @click="preset('today')">오늘</button><button type="button" @click="preset('week')">최근 7일</button><button type="button" @click="preset('lastWeek')">지난주</button><button type="button" @click="preset('month')">30일</button></div>
    </fieldset>
    <div class="rf-picker" v-for="kind in ['modules','people']" :key="kind"><span>{{ kind === 'modules' ? '모듈' : '사람' }}</span>
      <button type="button" class="rf-picker-button" @click="togglePicker(kind)" :aria-expanded="picker === kind" :aria-controls="'tracking-picker-'+kind" :disabled="!optionsLoaded" :title="kind === 'people' ? '기간 중 담당 업무와 본인 활동을 수집합니다' : '여러 모듈을 함께 선택할 수 있습니다'">{{ selectionLabel(kind) }}<span aria-hidden="true">⌄</span></button>
      <div v-if="picker === kind" class="rf-picker-menu" :id="'tracking-picker-'+kind"><input type="search" v-model="pickerQuery" :aria-label="kind === 'people' ? '사람 찾기' : '모듈 찾기'" placeholder="이름으로 찾기"><button type="button" class="rf-text-button" @click="selectAll(kind)">선택 해제 · 전체 조회</button>
        <div class="rf-picker-list"><label v-for="option in pickerOptions" :key="option.id"><input type="checkbox" :checked="isSelected(kind,option.id)" @change="toggleValue(kind,option.id)"><span>{{ option.name }}</span></label><p v-if="!pickerOptions.length">일치하는 대상이 없습니다.</p></div><button type="button" class="rf-primary" @click="picker = ''">선택 완료</button>
      </div>
    </div>
    <button type="submit" class="rf-primary" :disabled="!!validation || busy || !optionsLoaded">{{ busy ? '수집 중…' : '조회' }}</button>
  </form>
  <div v-if="picker" class="rf-picker-backdrop" @click="picker = ''"></div>
  <p v-if="validation" class="rf-alert" role="alert">{{ validation }}</p>
  <p v-if="optionsError" class="rf-alert" role="alert">{{ optionsError }} <button @click="initialize">다시 연결</button></p>
  <p v-if="error" class="rf-alert" role="alert">{{ error }} <button @click="initialize">최근 실행 확인</button></p>
  <template v-if="run">
    <div class="rf-run-status" :class="{'is-partial':provisional}" aria-live="polite"><span class="rf-status-dot"></span><strong>{{ runLabel }}</strong><span>{{ dateLabel(run.request?.startDate) }} — {{ dateLabel(run.request?.endDate) }}</span><span>{{ observationLabel }}</span>
      <span v-if="provisional">확인된 활동만 표시 · 잠정 집계</span><span v-if="changedRequest" class="rf-draft">조건 변경됨 · 조회 시 적용</span>
      <button v-if="canResume" @click="runAction('resume')" :disabled="pending">이어서 수집</button><button v-if="['queued','running'].includes(run.status)" @click="runAction('cancel')" :disabled="pending">수집 취소</button>
      <button class="rf-audit-button" @click="showOverlay('audit')">수집 범위와 비용</button>
    </div>
    <div v-if="pollError" class="rf-alert" role="alert">{{ pollError }} 저장된 결과는 유지됩니다. <button @click="openRun(run.id,false)">상태 다시 확인</button></div>
    <div class="rf-workspace" :class="{'has-detail':reportEvidenceOpen,'is-leaf':reportEvidenceOpen && summaryDisabled}" :style="{'--tracking-nav-width':hierarchyWidth+'px'}">
      <TrackingReferenceNav id="tracking-hierarchy" :rows="navigationRows" :all-rows="allRows" :active-id="reportEvidenceOpen && summaryDisabled ? selectedId : scopeId" :detail-id="reportEvidenceOpen ? selectedId : ''" :closed="scopeClosed" :pins="pins" :width="hierarchyWidth" :collapsed="treeCollapsed" :reveal-matches="matchedOnly && !!(query || highlight)" :total="rootWorkCount" :scope-label="scopeLabel" @scope="selectScope" @fold="foldScope" @collapse="setTreeCollapsed" @pin="togglePin" @inspect="inspectReference" />
      <TrackingSplitter v-if="!treeCollapsed" :width="hierarchyWidth" :default-width="referenceNavWidth" :minimum-width="220" :maximum-width="440" @resize="hierarchyWidth = $event" />
      <main class="rf-main" v-show="!(reportEvidenceOpen && summaryDisabled)">
        <TrackingBriefing ref="briefing" :key="run.id" :run="displayRun" :scope-id="scopeId" :query="query" :highlight="highlight" :matched-only="matchedOnly" :pins="pins" @scope="selectScope" @inspect="inspectReference" @evidence="focusEvidence" @ticket="openTicket" @query="query = $event" @highlight="setHighlight" @matched-only="matchedOnly = $event" @pin="togglePin" />
      </main>
      <TrackingReferenceDetail ref="referenceDetail" v-if="reportEvidenceOpen" :row="selected" :run="displayRun" :events="allSelectedEvents" :match-keys="[...matchKeys]" :has-match="!!(query || highlight)" :selected-event-id="selectedEventId" :leaf="summaryDisabled" @close="closeReferenceDetail" @scope="selectScope" @ticket="openTicket" @focus="focusEvidence" />
    </div>
  </template>
  <div v-else class="rf-start"><TrackingReferenceIcon name="layers" :size="30" /><h2>{{ pending ? '저장된 실행을 여는 중…' : '기간의 변화를 한눈에' }}</h2><p>기간과 대상을 선택하면 완료된 업무, 변경 사항과 댓글을 업무 계층으로 정리합니다.</p><p class="rf-muted">검색·강조·보기 전환은 저장된 결과에서 동작합니다.</p></div>
  <dialog ref="overlayDialog" class="rf-dialog" aria-labelledby="tracking-dialog-title" @close="overlayClosed" @click="dialogBackdrop">
    <header><div><small>{{ overlay === 'ai' ? '전체 조회 범위' : 'TRACKING' }}</small><h2 id="tracking-dialog-title">{{ overlayTitle }}</h2></div><button class="rf-dialog-close" @click="closeOverlay" aria-label="닫기">×</button></header>
    <div class="rf-dialog-body">
      <div v-if="overlay === 'history'" class="rf-history"><p class="rf-muted">저장된 결과를 엽니다. 다시 수집하지 않습니다.</p><button v-for="item in recent" :key="item.id" @click="openPrevious(item.id)" :disabled="pending"><span><strong>{{ dateLabel(item.request?.startDate) }} — {{ dateLabel(item.request?.endDate) }}</strong><small>{{ item.request?.modules?.length || '전체' }} 모듈 · {{ item.request?.people?.length || '전체' }} 사람</small></span><span>{{ statusLabel(item.status) }}</span></button></div>
      <div v-if="overlay === 'copy'"><p class="rf-muted">{{ reportEvidenceOpen && summaryDisabled ? "현재 티켓의 조회 기간 내 활동입니다." : "현재 보고 범위와 필터가 반영된 내용입니다." }}</p><textarea ref="reportText" class="rf-copy-text" v-model="reportCopyText" aria-label="보고서 내용"></textarea><div class="rf-dialog-actions"><span role="status">{{ copyState }}</span><button class="rf-primary" @click="copyReport">클립보드에 복사</button></div></div>
      <div v-if="overlay === 'ai'" class="rf-ai"><p class="rf-muted">{{ dateLabel(run?.request?.startDate) }} — {{ dateLabel(run?.request?.endDate) }} · 선택 계층과 관계없이 전체 조회 결과를 요약합니다.</p><p v-if="provisional" class="rf-warning">일부 수집 근거에 따른 잠정 요약</p><p v-if="!options.aiEnabled">{{ options.aiReason || 'AI 연결 설정이 필요합니다.' }}</p><button class="rf-primary" @click="summarize" :disabled="!options.aiEnabled || aiPending || busy">{{ aiPending ? '근거를 읽는 중…' : ai ? 'AI 요약 다시 생성' : 'AI 요약 생성' }}</button><p v-if="aiError" class="rf-alert" role="alert">{{ aiError }}</p><ul v-if="ai"><li v-for="(item,i) in ai.items || []" :key="i">{{ item.text }}<div><button v-for="id in item.evidenceIds || []" :key="id" class="rf-text-button" @click="openAiEvidence(id)">{{ evidenceLabel(id) }} · 근거</button></div></li></ul><p v-if="ai" class="rf-muted">추가 Jira 조회 없음 · 사용 토큰 {{ ai.usage?.totalTokens ?? '미확인' }}</p></div>
      <div v-if="overlay === 'audit'" class="rf-audit"><p>시작일·종료일 포함 · 한국 시간 · 기간 중 담당 업무와 본인 활동 · 조직과 WBS는 현재 설정 기준</p><p>생성·완료는 고유 티켓 수. 재오픈·재완료 사건은 상세 근거에 표시. 삭제된 댓글과 과거 편집본은 복원하지 않습니다.</p><dl><dt>대상 티켓 이력 확인</dt><dd>{{ historyConfirmed }} / {{ targetIssues.length }}</dd><dt>후보 검색</dt><dd>{{ coverage.queriesComplete ? '완료' : '미완료' }}</dd><dt>전체 확인</dt><dd>{{ coverage.issuesComplete ?? '미확인' }} / {{ coverage.issuesTotal ?? '미확인' }}</dd><dt>댓글 · 변경 이력</dt><dd>{{ coverage.commentsComplete ? '완료' : '일부 미확인' }} · {{ coverage.historyComplete ? '완료' : '일부 미확인' }}</dd><dt>Jira REST 호출</dt><dd>{{ metrics.restRequests ?? '미확인' }}회 · 인증 요청 제외</dd><dt>응답 · 소요 시간</dt><dd>{{ metrics.responseBytes == null ? '미확인' : (metrics.responseBytes / 1024).toFixed(1) + ' KB' }} · {{ metrics.elapsedMs == null ? '미확인' : (metrics.elapsedMs / 1000).toFixed(1) + '초' }}</dd><dt>재시도 · 캐시 재사용</dt><dd>{{ metrics.retries ?? '미확인' }}회 · {{ metrics.cacheHits ?? '미확인' }}회</dd><dt>MCP · LLM 토큰</dt><dd>{{ metrics.mcpCalls ?? 0 }}회 · {{ metrics.llmTokens ?? 0 }} 토큰</dd></dl><ul v-if="coverage.missing?.length"><li v-for="(item,i) in coverage.missing" :key="i">{{ item.issueKey ? ticketLabel(item.issueKey) : '검색' }} · {{ item.source }} · {{ item.reason }}</li></ul><ul v-if="run?.errors?.length"><li v-for="(item,i) in run.errors" :key="i">{{ typeof item === 'string' ? item : item.message || item.reason || item.error }}</li></ul></div>
    </div>
  </dialog>
</section>`;
