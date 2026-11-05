// Board-level state belongs outside Epic/WBS sections; cards keep their existing renderer.
export const BOARD_HEADER_TEMPLATE = `
  <div class="mt-board-head mt-headrow" aria-label="전체 상태별 티켓">
    <div v-for="st in states" :key="st.k" class="mt-colh"
         :class="['c-' + st.k, { closed: !bandOpen(st.k) }]">
      <span class="mt-status-heading"><i aria-hidden="true"></i><span>{{ st.label }}</span></span>
      <b>{{ bandCount(st.k) }}</b>
      <span v-if="bandOpen(st.k) && axisPending(st.k)" class="mt-axis-load"
            :class="{ degraded: axisDegraded(st.k) }">
        <i v-if="axisLoading(st.k)" aria-hidden="true"></i>{{ axisLoadText(st.k) }}
      </span>
      <small v-else class="mt-status-range">{{ bandOpen(st.k) ? bandRangeLabel(st.k) : '접힘' }}</small>
    </div>
  </div>`;

export const STATUS_CONTROLS_TEMPLATE = `
  <div class="mt-status-controls" role="group" aria-label="전체 상태 표시와 조회 기간">
    <span class="mt-status-controls-title">상태 · 조회 기간</span>
    <div v-for="st in states" :key="st.k" class="mt-status-control" :data-status-control="st.k">
      <label><input type="checkbox" :checked="bandOpen(st.k)" @change="toggleBand(st.k)"
                    :aria-label="st.label + ' 표시'" /><span>{{ st.label }}</span></label>
      <select class="mt-sel" :value="opt(st.k)" @change="setBandFilter(st.k, $event.target.value)"
              :aria-label="st.label + ' 조회 기간'">
        <option v-for="o in bandFilter(st.k).opts" :key="o.k" :value="o.k" :title="o.hint">{{ o.label }}</option>
      </select>
    </div>
  </div>`;


const QUICK_FILTER_TEMPLATE = `
      <div class="mt-qf" role="group" aria-label="퀵필터">
        <span class="mt-quick-title">퀵필터</span>
        <!-- 담당자 세그먼트: [담당자][picker: 나/이름] — 버튼이 스코프를 켜고 picker 가 대상을 정한다. -->
        <span class="mt-qf-seg">
          <button type="button" class="mt-qf-b seg-l" :class="{ on: scope === 'assignee' }"
                  @click="setScope('assignee')" title="담당자 기준으로 보기">담당자</button>
          <FieldEdit class="mt-qf-fe seg-r" :class="{ 'qf-on': scope === 'assignee' }" ticket="__filter__"
                     field="assignee" local :value="assigneeSel ? assigneeSel.id : ''"
                     :user-id="assigneeSel ? assigneeSel.id : ''" @pick="pickAssignee">
            <span class="qf-fe-v" :title="assigneeSel ? assigneeSel.name : '나'">{{ assigneeSel ? assigneeSel.name : '나' }}</span><span class="qf-fe-cav">▾</span>
          </FieldEdit>
        </span>
        <!-- 보고자 세그먼트 -->
        <span class="mt-qf-seg">
          <button type="button" class="mt-qf-b seg-l" :class="{ on: scope === 'reporter' }"
                  @click="setScope('reporter')" title="보고자 기준으로 보기">보고자</button>
          <FieldEdit class="mt-qf-fe seg-r" :class="{ 'qf-on': scope === 'reporter' }" ticket="__filter__"
                     field="reporter" local :value="reporterSel ? reporterSel.id : ''"
                     :user-id="reporterSel ? reporterSel.id : ''" @pick="pickReporter">
            <span class="qf-fe-v" :title="reporterSel ? reporterSel.name : '나'">{{ reporterSel ? reporterSel.name : '나' }}</span><span class="qf-fe-cav">▾</span>
          </FieldEdit>
        </span>
        <!-- 모듈 세그먼트: [모듈][콤보] — 콤보 선택은 포커스 떠나도 유지, 버튼이 모듈 스코프를 켠다. -->
        <span class="mt-qf-seg">
          <button type="button" class="mt-qf-b seg-l" :class="{ on: scope === 'module' }"
                  @click="setScope('module')" title="모듈 단위로 보기">모듈</button>
          <select class="mt-qf-sel seg-r" :class="{ on: scope === 'module' }"
                  :value="moduleSel || '__all__'"
                  @change="onModulePick($event.target.value)" title="모듈 선택(포커스 떠나도 유지)">
            <option value="__all__">{{ myModules.length ? '내 모듈 전체' : '모듈 전체' }}</option>
            <optgroup v-if="myModules.length" label="내 모듈">
              <option v-for="m in myModules" :key="'my-' + m" :value="m">{{ m }}</option>
            </optgroup>
            <optgroup v-if="otherModules.length" label="다른 모듈">
              <option v-for="m in otherModules" :key="'ot-' + m" :value="m">{{ m }}</option>
            </optgroup>
          </select>
        </span>

        <!-- 고급 검색: [고급 검색][JQL 입력][🔍] — 버튼은 빌더 다이얼로그, 우측 입력창은 JQL 직접 입력,
             돋보기(또는 Enter)로 그 JQL 을 Task 에 검색해 띄운다. -->
        <span class="mt-qf-seg mt-qf-adv" :class="{ 'qf-on': scope === 'jql' }">
          <button type="button" class="mt-qf-b seg-l" @click="advOpen = true"
                  title="조건을 조합해 JQL 만들기">고급 검색</button>
          <input class="mt-qf-jql" v-model="jqlText" @keydown.enter="runJql"
                 placeholder="JQL 직접 입력" aria-label="JQL 직접 입력" spellcheck="false" autocomplete="off" />
          <button type="button" class="mt-qf-jgo seg-r" @click="runJql" title="이 JQL 로 검색" aria-label="검색">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
          </button>
        </span>
      </div>`;

export const OPTIONS_PANEL_TEMPLATE = `
  <div v-if="!optionsClosed" class="mt-options-backdrop" @click="toggleOptions" aria-hidden="true"></div>
  <aside ref="optionsPanel" class="mt-options-panel" :class="{ collapsed: optionsClosed }" aria-label="Tasks 보기 옵션">
    <button type="button" class="mt-options-toggle" @click="toggleOptions"
            :aria-expanded="!optionsClosed" aria-controls="mt-options-content"
            :aria-label="optionsClosed ? '보기 옵션 펼치기' : '보기 옵션 접기'"
            :title="optionsClosed ? '보기 옵션 펼치기' : '보기 옵션 접기'">
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m12 5-5 5 5 5"/></svg>
      <span>보기 옵션</span>
    </button>
    <div id="mt-options-content" class="mt-options-content" v-show="!optionsClosed">
      ${QUICK_FILTER_TEMPLATE}
      ${STATUS_CONTROLS_TEMPLATE}
      <div class="mt-ticket-filters" role="group" aria-label="티켓 표시 조건">
        <label class="mt-opt">
          <span class="mt-opt-l">우선순위</span>
          <select class="mt-sel" :value="priorityFilter" @change="setDisplayFilter('priorityFilter', $event.target.value)"
                  aria-label="우선순위 필터">
            <option value="">전체</option>
            <option v-for="option in priorityOptions" :key="option.k" :value="option.k">{{ option.label }}</option>
          </select>
        </label>
        <label class="mt-opt">
          <span class="mt-opt-l">마감기한</span>
          <select class="mt-sel" :value="dueFilter" @change="setDisplayFilter('dueFilter', $event.target.value)"
                  aria-label="마감기한 필터" :title="dueFilterOptions.find(option => option.k === dueFilter)?.hint || '티켓에 표시되는 마감일 기준'">
            <option v-for="option in dueFilterOptions" :key="option.k" :value="option.k" :title="option.hint">{{ option.label }}</option>
          </select>
        </label>
      </div>
      <div class="mt-view-controls">
      <label v-for="o in options" :key="o.key" class="mt-opt">
        <span class="mt-opt-l" :class="o.cls">{{ o.label }}</span>
        <select class="mt-sel" :value="$data[o.key]" @change="setOpt(o, $event.target.value)"
                :title="hintOf(o)">
          <option v-for="v in o.opts" :key="v.k" :value="v.k" :title="v.hint">{{ v.label }}</option>
        </select>
      </label>

      <!-- Project 필터 — 다중선택 콤보. jira.yml search 등록 프로젝트는 기본 체크,
           그 외(다른 프로젝트에 걸린 내 티켓 등)는 기본 언체크로 감춘다(콤보에서 켤 수 있다). -->
      <div v-if="projectOptions.length > 1 || anyProjectHidden" class="mt-opt mt-epicf">
        <span class="mt-opt-l">Project</span>
        <div class="mt-ef">
          <button class="mt-ef-btn" :class="{ on: !allProjectsShown }" @click.stop="projOpen = !projOpen"
                  :aria-expanded="projOpen"
                  :title="'Project 필터 — ' + projFilterLabel">
            {{ projFilterLabel }}<i class="mt-ef-cav">▾</i>
          </button>
          <div v-if="projOpen" class="mt-ef-back" @click="projOpen = false"></div>
          <div v-if="projOpen" class="mt-ef-pop" @click.stop>
            <button type="button" class="mt-ef-i master" @click="toggleAllProjects">
              <span class="mt-ef-ck" :class="{ on: allProjectsShown, ind: anyProjectHidden && !allProjectsShown }"></span>
              모든 Project</button>
            <div class="mt-ef-sep"></div>
            <div class="mt-ef-list">
              <button v-for="p in projectOptions" :key="p.key" type="button" class="mt-ef-i"
                      @click="toggleProj(p.key)" :title="p.registered ? p.key : p.key + ' — 검색 미등록'">
                <span class="mt-ef-ck" :class="{ on: projShown(p.key) }"></span>
                <span class="mt-ef-t">{{ p.key }}</span>
                <span v-if="!p.registered" class="mt-projf-x" title="jira.yml search 에 미등록">미등록</span>
              </button>
            </div>
          </div>
        </div>
      </div>
      </div>

      <!-- Epic 목록은 항상 펼친다. 사용자 VoC와 소속 없음도 기존 필터 규칙을 공유한다. -->
      <div v-if="epicOptions.length || hasVocBucket || hasNoneBucket" class="mt-epic-checklist"
           role="group" aria-label="Epic 필터">
        <div class="mt-epic-checklist-title"><span>Epic</span><small>{{ epicFilterLabel }}</small></div>
        <label class="mt-epic-check master">
          <input type="checkbox" :checked="allEpicsShown"
                 :indeterminate="anyEpicHidden && allBuckets.some(k => !epicHidden[k])"
                 @change="toggleAllEpics" />
          <span>모든 Epic</span>
        </label>
        <label v-for="e in epicOptions" :key="e.key" class="mt-epic-check" :title="e.title">
          <input type="checkbox" :checked="!epicHidden[e.key]" @change="toggleEpic(e.key)" />
          <i class="mt-ef-dot" :style="{ background: e.color }" aria-hidden="true"></i>
          <span>{{ e.title }}</span>
        </label>
        <label v-if="hasVocBucket" class="mt-epic-check">
          <input type="checkbox" :checked="!epicHidden['__voc__']" @change="toggleEpic('__voc__')" />
          <i class="mt-ef-dot" style="background: var(--ty-story)" aria-hidden="true"></i>
          <span>사용자 VoC</span>
        </label>
        <label v-if="hasNoneBucket" class="mt-epic-check">
          <input type="checkbox" :checked="!epicHidden['__none__']" @change="toggleEpic('__none__')" />
          <i class="mt-ef-dot none" aria-hidden="true"></i><span>Epic 없음</span>
        </label>
      </div>

    </div>
  </aside>`;
