// Render-only shell for AgentView.
const AGENT_VIEW_TEMPLATE = `
  <div class="agentview">
    <!-- 못 쓰는 상태를 숨기지 않는다 — 왜 안 되는지 알아야 고친다 -->
    <div v-if="ready === false" class="agent-off">
      <h2>AI 에이전트가 켜져 있지 않습니다</h2>
      <p>{{ reason || '설정을 확인하세요.' }}</p>
      <p class="hint">앱을 다시 시작하면 설치가 이어집니다. 그래도 같으면 <code>run.bat setup</code>.<br>
         설치가 끝나면 우상단 <b>설정 → AI 에이전트</b> 에서 키를 넣으세요.</p>
    </div>

    <template v-else>
      <!-- 정통 에이전트 레이아웃(사용자 요청): 좌측 사이드바(새 대화·최근 대화·설정) +
           본문. 빈 화면은 중앙 히어로(제목·추천 칩·입력창)로. -->
      <!-- 접힌 대화 목록을 다시 펴는 손잡이(얇은 레일) — 티켓 다이얼로그의 stub 과 같은 꼴 -->
      <button v-if="navHidden" class="ag-show stub nav" title="대화 목록 펼치기"
              @click="setNavHidden(false)">
        <span class="st-ic">›</span>
        <span class="st-label">대화 목록</span>
        <span class="st-dots" aria-hidden="true"><i></i><i></i><i></i></span>
      </button>
      <!-- ★ 폭은 **인라인 CSS 변수**로 내려간다 — agent.css 의 [style*=--nav-w] 규칙이
           그때만 flex-basis 를 잡는다. 이 바인딩이 빠져 있어서 끌어도 아무 일이 없었다
           (navW 는 계산·저장까지 다 되고 있었는데 화면에 닿는 줄이 없었다 — 사용자 지적).
           0 이면 아무것도 안 붙여 기본 폭(CSS)이 그대로 산다. -->
      <aside class="agent-nav" ref="nav" v-show="!navHidden"
             :style="navW ? { '--nav-w': navW + 'px' } : null">
        <button class="ag-hide nav" title="대화 목록 접기" @click="setNavHidden(true)">‹</button>
        <!-- 오른쪽 가장자리를 끌어 폭 조절 · 더블클릭하면 기본 폭 -->
        <div class="ag-grip nav" title="너비 조절 — 드래그 (더블클릭: 기본 폭)"
             @mousedown.prevent="startNavDrag" @dblclick="resetNavW"></div>
        <!-- 모델·설정은 좌상단 — 지금 무엇으로 도는지가 먼저 보인다(사용자 요청) -->
        <div class="an-top">
          <span v-if="status && status.runtimeConfigSource === 'named'" class="agent-prov"
                :title="'chat=' + status.chatModel + ' / embed=' + status.embedModel">
            {{ status.activeConfig.name }} · {{ status.provider }}<template v-if="status.chatModel"> · {{ status.chatModel }}</template>
          </span>
          <span v-else-if="status && status.runtimeConfigSource === 'environment'" class="agent-prov"
                :title="'chat=' + status.chatModel + ' / embed=' + status.embedModel">
            환경 설정 · {{ status.provider }}<template v-if="status.chatModel"> · {{ status.chatModel }}</template>
          </span>
          <span v-else class="agent-prov is-empty">
            연결 설정 없음
          </span>
          <button class="agent-reset" @click="settingsOpen = true" title="AI 에이전트 설정">⚙ 설정</button>
        </div>
        <button class="an-new" @click="reset">＋ 새 대화</button>
        <div class="an-h" v-if="convos.length">최근 대화</div>
        <div class="an-list">
          <div v-for="c in convos" :key="c.id" class="an-item"
               :class="{ on: c.id === threadId, live: !!live[c.id] }">
            <button class="an-open" @click="openConvo(c)"
                    :title="live[c.id] ? c.title + ' — 응답 중' : c.title">
              <span v-if="live[c.id]" class="an-live" title="응답 중"></span>{{ c.title }}</button>
            <button class="an-del" @click.stop="removeConvo(c)" title="삭제">✕</button>
          </div>
        </div>
      </aside>

      <!-- 이분할: 티켓 패널이 열리면 대화가 좁아지며 나란히 선다 -->
      <div class="agent-main" :class="{ 'is-empty': empty && !busy }">

      <!-- 홈에서 넘어올 때 인증이 안 됐으면 그 사실을 대화 위에 계속 보여 준다 -->
      <div v-if="authNote" class="agent-authnote">
        <span>⚠ {{ authNote }}</span>
        <button class="an-x" @click="authNote = ''" title="닫기">✕</button>
      </div>

      <!-- 대화 헤더 — 제목(첫 질문) + 우상단 액션(내보내기). 빈 화면에는 없다 -->
      <div v-if="turns.length" class="agent-chat-h">
        <b class="agent-chat-title" :title="convoTitle()">{{ convoTitle() }}</b>
        <div class="agent-chat-acts">
          <button @click="exportChat" title="대화 전체를 마크다운으로 클립보드에 복사">📋 대화 복사</button>
        </div>
      </div>

      <div class="agent-scroll" ref="scroller" @click="mdClick"
           @mouseover="refOver" @mouseout="refOut" @scroll="refTip = null">
        <!-- 빈 화면: 중앙 히어로 — 제목 + 추천 칩(입력창이 바로 아래 온다) -->
        <div v-if="empty && !busy" class="agent-empty">
          <h1 class="agent-hero">LTM Agent</h1>
          <p class="agent-hero-sub">과거 이력을 찾고, 대화로 구체화해, 승인받아 티켓까지 만듭니다.</p>
          <div class="agent-ex-wrap">
            <button v-for="ex in examples" :key="ex" class="agent-ex" @click="use(ex)">{{ ex }}</button>
          </div>
        </div>

        <div v-for="(t, ti) in turns" :key="ti" class="agent-turn" :class="t.who">
          <!-- 사용자 말풍선 — 에디터로 쓴 턴은 그 HTML 그대로(멘션·티켓 뱃지가 티켓 화면과
               같은 모양으로 보인다). 예시 버튼 등 텍스트 턴은 기존대로. -->
          <div v-if="t.who === 'user' && t.html" class="agent-bubble user rich" v-html="t.html"></div>
          <div v-else-if="t.who === 'user'" class="agent-bubble user">{{ t.text }}</div>

          <div v-else class="agent-bubble agent">
            <!-- tkt-desc 를 함께 단다 — 헤딩·인용·콜아웃·표·뱃지·멘션을 티켓 본문/댓글과
                 **같은 CSS** 로 그린다(사용자 지시: 렌더 체계는 하나여야 한다). -->
            <div v-if="t.text" class="agent-md tkt-desc" v-html="md(t)"></div>
            <div v-else-if="busy && ti === turns.length - 1" class="agent-thinking">
              <span class="dot"></span><span class="dot"></span><span class="dot"></span>
            </div>
            <!-- 응답 중이 아닌데 본문이 비었다 = 중단됐거나 새로고침으로 끊겼다.
                 '…'만 계속 떠 있으면 멈춘 건지 모른다(사용자 지적). -->
            <div v-else class="agent-stalled">
              ⏹ 응답이 이어지지 않았습니다 (중단 또는 연결 끊김) — 다시 물어보시면 이어서 진행합니다.
            </div>

            <!-- 비용 — 질문 하나로 보이지만 안에서 LLM 을 예닐곱 번 부른다.
                 숫자를 봐야 "이건 비싼 질문이었다"를 알고 다음에 다르게 묻는다. -->
            <div v-if="t.usage && t.usage.totalTokens" class="agent-usage"
                 :title="t.usage.model + ' · 입력 ' + t.usage.promptTokens + ' / 출력 ' + t.usage.completionTokens">
              <!-- ★ 이 컴포넌트의 template 은 JS 백틱 문자열이다 — "$" + "{{" 를 붙여 쓰면
                   \`\${\` 로 읽혀 JS 보간이 시작돼 버린다(실제로 파일 전체가 SyntaxError 로 죽었다).
                   달러 기호는 머스태시 안에서 문자열로 만든다. -->
              LLM {{ t.usage.calls }}회 · {{ t.usage.totalTokens.toLocaleString() }} 토큰<template
                v-if="t.usage.costUsd"> · {{ '$' + t.usage.costUsd.toFixed(4) }}</template>
            </div>

            <!-- 실행 결과: 실패를 눈에 띄게. 조용히 넘어가면 다 만들어진 줄 안다 -->
            <div v-if="t.result && (t.result.created || []).length" class="agent-made">
              <div class="agent-ev-h">생성됨</div>
              <button v-for="c in t.result.created" :key="c.key" class="agent-ev-row"
                      @click="openTicket(c.key)"><b>{{ c.key }}</b><span>{{ c.summary }}</span></button>
            </div>
            <div v-if="t.result && (t.result.failed || []).length" class="agent-failed">
              <div class="agent-ev-h">실패</div>
              <div v-for="(f, i) in t.result.failed" :key="i">{{ f.summary }} — {{ f.error }}</div>
            </div>

            <!-- 되묻기 폼 — 질문을 타이핑 대신 버튼·자동완성으로 답한다.
                 마지막 턴에만 활성(지난 질문에 답해 봤자 대화는 이미 지나갔다). -->
            <div v-if="t.questions && t.questions.length && ti === turns.length - 1 && !busy"
                 class="agent-qform">
              <!-- 클로드식 순차 폼: 질문은 한 번에 하나씩, 답한 질문은 접혀 선택만 보인다
                   (세로 카드형 보기가 스크롤을 먹는 것의 절충 — 사용자 요청). -->
              <div v-for="(q, qi) in t.questions" :key="q.question_id || qi" class="aq"
                   v-show="qDone[qi] || qi === qActive(t)">

                <!-- 접힌 질문 — 질문 한 줄 + 선택한 답. 누르면 다시 편다 -->
                <button v-if="qDone[qi] && qActive(t) !== qi" class="aq-folded"
                        @click="qDone[qi] = false; answers[qKey(qi)] = answers[qKey(qi)] || ''">
                  <span class="aq-fq">{{ q.question || q }}</span>
                  <b>{{ answers[qKey(qi)] }}</b><em>수정</em>
                </button>

                <template v-else>
                  <div class="aq-q">{{ q.question || q }}
                    <span class="aq-step">{{ qi + 1 }}/{{ t.questions.length }}</span></div>

                  <!-- 세로 카드형 보기 (추천 맨 위) + '직접 입력' 카드(인라인 즉시 입력).
                       kind=multi 는 토글 다중선택 + [선택 완료] -->
                  <div v-if="optionsFor(q).length" class="aq-opts">
                    <button v-for="(opt, oi) in optionsFor(q)" :key="opt" class="aq-card"
                            :class="{ on: isPicked(qi, q, opt), rec: oi === 0, multi: q.kind === 'multi' }"
                            @click="customOn[qi] = false;
                                    q.kind === 'multi' ? toggleMulti(qi, opt)
                                                       : (pickOpt(qi, opt), qDone[qi] = true)">
                      <i v-if="q.kind === 'multi'" class="aq-chk">{{ isPicked(qi, q, opt) ? '☑' : '☐' }}</i>
                      <span>{{ opt }}</span><em v-if="oi === 0">추천</em></button>
                    <button v-if="q.kind === 'multi'" class="aq-card aq-multi-done"
                            :disabled="!(answers[qKey(qi)] || '').trim()"
                            @click="qDone[qi] = true">
                      선택 완료 ({{ (answers[qKey(qi)] || '').split(' | ').filter(Boolean).length }}개)</button>
                    <!-- ★ **담당자·일정은 지금 안 정해도 된다**(사용자 요청). 이 둘은
                         승인 카드와 티켓 화면에서 언제든 바꿀 수 있는 값이라, 여기서
                         멈춰 세우면 초안까지 가는 길이 길어지기만 한다. -->
                    <button v-if="deferrable(q)" class="aq-card aq-defer"
                            @click="customOn[qi] = false; pickOpt(qi, '나중에 직접 선택 (기본값으로)');
                                    qDone[qi] = true">
                      나중에 직접 선택 <em>기본값으로</em></button>
                    <div v-if="q.field !== 'priority'" class="aq-card aq-custom"
                         :class="{ on: customOn[qi] }" @click="customOn[qi] = true">
                      <span v-if="!customOn[qi]">직접 입력…</span>
                      <template v-else>
                        <FieldEdit v-if="fieldOf(q)" class="aq-fe" ticket="__agent__"
                                   :field="fieldOf(q)" local :value="answers[qKey(qi)] || ''"
                                   @pick="(v, x) => { setAns(qi, v, x); qDone[qi] = true; }">
                          {{ answers[qKey(qi)] || feHint(q) }}</FieldEdit>
                        <input v-else class="aq-in" :value="answers[qKey(qi)] || ''"
                               placeholder="답을 입력하고 Enter" autofocus
                               @input="setAns(qi, $event.target.value)"
                               @keydown.enter.stop.prevent="answers[qKey(qi)] && (qDone[qi] = true)"
                               @click.stop>
                      </template>
                    </div>
                  </div>

                  <!-- 보기 없는 질문: 날짜·담당자·Epic 은 FieldEdit, 그 외 자유 서술 -->
                  <div v-else-if="fieldOf(q)">
                    <FieldEdit class="aq-fe" ticket="__agent__" :field="fieldOf(q)" local
                               :value="answers[qKey(qi)] || ''"
                               @pick="(v, x) => { setAns(qi, v, x); qDone[qi] = true; }">
                      {{ answers[qKey(qi)] || feHint(q) }}</FieldEdit>
                  </div>
                  <input v-else class="aq-in" :value="answers[qKey(qi)] || ''"
                         placeholder="답을 입력하고 Enter" @input="setAns(qi, $event.target.value)"
                         @keydown.enter.stop.prevent="answers[qKey(qi)] && (qDone[qi] = true)">
                </template>
              </div>
              <div class="aq-act">
                <button class="ag-ok" :disabled="!formReady(t)" @click="submitAnswers(t)">답변 보내기</button>
                <button class="ag-cancel" @click="skipAnswers(t)">알아서 진행해줘</button>
              </div>
            </div>

            <!-- ★ HITL 승인 카드 — 여기서 승인을 눌러야만 쓰기가 시작된다.
                 create(티켓 생성)와 update(기존 티켓 변경) 두 모양이 있다. -->
            <div v-if="t.pending && ti === turns.length - 1" class="agent-card">
              <!-- 변경 카드 -->
              <template v-if="['update_ticket', 'update_tickets', 'add_ticket_comment', 'add_ticket_comments'].includes(t.pending.action)">
                <div class="agent-card-h">
                  <b v-if="t.pending.keys">{{ t.pending.action === 'add_ticket_comments' ? '댓글 게시' : '일괄 변경' }} {{ t.pending.keys.length }}건</b>
                  <b v-else><a href="#" class="tkt" :data-key="t.pending.key">{{ t.pending.key }}</a>
                    {{ t.pending.action === 'add_ticket_comment' ? '댓글 게시' : '변경' }}</b>
                  <em>{{ t.pending.action.startsWith('add_ticket_comment') ? '아직 게시되지 않았습니다' : '아직 바뀌지 않았습니다' }} — 확인 후 승인하세요</em>
                </div>
                <!-- 일괄 대상 — 전부 보여야 승인이 의미 있다(각 키 클릭 검증 가능) -->
                <div v-if="t.pending.keys" class="agent-chg-keys">
                  <a v-for="k in t.pending.keys" :key="k" href="#" class="tkt" :data-key="k">{{ k }}</a>
                </div>
                <div v-if="t.pending.rationale" class="agent-card-why">{{ t.pending.rationale }}</div>
                <div class="agent-chg">
                  <div v-for="(v, k) in t.pending.changes" :key="k" class="agent-chg-row">
                    <span class="chg-k">{{ ({assignee:'담당자', duedate:'마감일', priority:'우선순위',
                                            summary:'제목', labels:'라벨', status:'상태 전이', link:'링크'})[k] || k }}</span>
                    <span class="chg-v">{{ Array.isArray(v) ? v.join(', ') : (v || '(비움)') }}</span>
                  </div>
                  <div v-if="t.pending.comment && !t.pending.comments" class="agent-chg-row">
                    <span class="chg-k">코멘트</span><span class="chg-v">{{ t.pending.comment }}</span>
                  </div>
                </div>
                <!-- ★ 티켓별 코멘트 미리보기 — 일괄 코멘트는 **티켓마다 문구가 다르다**
                     (멘션 대상이 그 티켓의 담당자다). 무엇이 어디에 달리는지 보여야 승인이
                     의미를 갖는다. 건수가 많으면 화면을 덮으므로 **기본 접힘**(사용자 요청). -->
                <details v-if="t.pending.comments" class="agent-cmt-pv"
                         :open="t.pending.comments.length <= 5">
                  <summary>티켓별 코멘트 미리보기 {{ t.pending.comments.length }}건
                    <em v-if="t.pending.comments.some(c => !c.assignee)">
                      · 담당 없는 티켓은 멘션 없이 남습니다</em></summary>
                  <div v-for="c in t.pending.comments" :key="c.key" class="agent-cmt-row">
                    <div class="cmt-h">
                      <a href="#" class="tkt" :data-key="c.key">{{ c.key }}</a>
                      <span class="cmt-t">{{ c.title }}</span>
                    </div>
                    <div class="cmt-b">{{ c.body }}</div>
                  </div>
                </details>
                <div class="agent-card-act">
                  <button class="ag-ok" :disabled="approving" @click="approve">
                    {{ approving ? (t.pending.action.startsWith('add_ticket_comment') ? '게시 중…' : '변경 중…')
                                  : (t.pending.action.startsWith('add_ticket_comment') ? '댓글 게시' : '이대로 변경') }}</button>
                  <button class="ag-cancel" :disabled="approving" @click="cancelPending">취소</button>
                </div>
              </template>

              <!-- 생성 카드 -->
              <template v-else>
              <div class="agent-card-h">
                <b>만들 티켓 {{ t.pending.items.length }}건</b>
                <em>아직 만들어지지 않았습니다 — 확인 후 승인하세요</em>
              </div>
              <div v-if="t.pending.rationale" class="agent-card-why">{{ t.pending.rationale }}</div>

              <ol class="agent-items">
                <li v-for="(it, i) in t.pending.items" :key="i">
                  <div class="ai-top">
                    <span class="ai-type">{{ it.type }}</span>
                    <span v-if="!cardEdit[i]" class="ai-sum">{{ liveVal(i, 'summary', it) }}</span>
                    <input v-else class="ai-edit-sum" v-model="editBuf[i].summary"
                           placeholder="제목" />
                    <button class="ai-edit-btn" :class="{ on: cardEdit[i] }"
                            @click="toggleEdit(i, it)" title="이 항목을 카드에서 직접 수정">
                      {{ cardEdit[i] ? '수정 중' : '✎ 수정' }}</button>
                  </div>
                  <!-- 인라인 편집 — 승인 전에 제목·본문·라벨·마감·우선순위·Epic 을 카드에서
                       직접 고친다(사용자 요청: 수정 루프). 서버가 같은 규칙으로 재검증한다. -->
                  <div v-if="cardEdit[i]" class="ai-edit">
                    <label>라벨 <input v-model="editBuf[i].labels" placeholder="쉼표로 구분" /></label>
                    <label>마감 <input v-model="editBuf[i].duedate" type="date" /></label>
                    <label>우선순위
                      <select v-model="editBuf[i].priority">
                        <option value="">(없음)</option>
                        <option v-for="p in priorities" :key="p" :value="p">{{ p }}</option>
                      </select></label>
                    <label>Epic <input v-model="editBuf[i].epic" placeholder="DL-123 (비우면 최상위)" /></label>
                    <div class="ai-edit-desc">
                      <div class="ai-edit-desc-h">본문 (저장은 [이대로 생성] 때 함께)</div>
                      <CommentEditor :ref="'ded' + i" ticket-key="" kind="description"
                                     :initial="it.description || ''" :hide-footer="true"
                                     :submit-fn="noopSubmit" />
                    </div>
                  </div>
                  <div class="ai-fields">
                    <span v-if="it.epic">상위 {{ it.epic }}</span>
                    <span v-if="it.parent">부모 {{ it.parent }}</span>
                    <span v-if="it.components">모듈 {{ it.components.join(', ') }}</span>
                    <span v-if="it.labels">라벨 {{ it.labels.join(', ') }}</span>
                    <span v-if="it.duedate">마감 {{ it.duedate }}</span>
                    <span v-if="it.priority">{{ it.priority }}</span>
                    <span v-if="it.assignee" class="ai-who">담당
                      <Avatar :user="it.assignee" :name="personName(t, it.assignee)" :size="15" />
                      {{ personName(t, it.assignee) || it.assignee }}</span>
                  </div>
                  <!-- 본문 요약(구조 텍스트) + 우측 패널 미리보기 열기 — 실물 렌더는
                       우측 채널이 담당한다(사용자 정정: 우측 = 초안 미리보기 공간) -->
                  <div v-if="it.description" class="ai-desc-wrap">
                    <button class="ai-pv-btn" :class="{ on: sideDraft === i }" @click="sideDraft = i">
                      ▸ 우측에 미리보기</button>
                    <div class="ai-desc">{{ descText(it.description) }}</div>
                  </div>
                  <!-- 함께 만들어질 Sub-Task — 안 보이면 부모 하나만 승인한 줄 안다 -->
                  <div v-if="childrenFor(t, i).length" class="ai-kids">
                    <div class="ai-kids-h">함께 만들 Sub-Task {{ childrenFor(t, i).length }}건</div>
                    <div v-for="(c, j) in childrenFor(t, i)" :key="j" class="ai-kid">
                      └ <template v-if="!cardEdit[i]"><b>{{ childVal(i, j, 'summary', c) }}</b>
                        <span v-if="childVal(i, j, 'assignee', c)" class="ai-who">
                          <Avatar :user="childVal(i, j, 'assignee', c)"
                                  :name="personName(t, childVal(i, j, 'assignee', c))" :size="14" />
                          {{ personName(t, childVal(i, j, 'assignee', c)) || childVal(i, j, 'assignee', c) }}
                        </span></template>
                      <template v-else>
                        <input class="ai-kid-sum" v-model="childBuf[i + '-' + j].summary" />
                        <FieldEdit class="aq-fe" ticket="__agent__" field="assignee" local
                                   :value="childBuf[i + '-' + j].assignee || ''"
                                   @pick="(v) => { childBuf[i + '-' + j].assignee = v; }">
                          {{ childBuf[i + '-' + j].assignee || '담당…' }}</FieldEdit>
                      </template>
                    </div>
                  </div>
                  <!-- 계보 — 이 초안이 어느 Epic 의 어떤 형제들 옆에 붙는지 -->
                  <div v-if="treeFor(t.pending, it) && treeFor(t.pending, it).length" class="ai-tree">
                    <div class="ai-tree-h">{{ it.epic }} 아래에 붙습니다</div>
                    <div v-for="c in treeFor(t.pending, it)" :key="c.key" class="ai-tree-row"
                         :class="{ done: c.done }">
                      ├ <a href="#" class="tkt" :data-key="c.key">{{ c.key }}</a>
                      <span>{{ c.summary }}</span><em v-if="c.done">완료</em>
                    </div>
                    <div class="ai-tree-row new">└ <b>+ {{ it.summary }}</b> <em>(이번에 생성)</em></div>
                  </div>

                  <!-- 담당자 — 추천을 그대로 받는 게 아니라 **후보 중 고른다**(근거 병기).
                       직접 입력을 고르면 사람 검색 자동완성이 붙는다. -->
                  <div v-if="reasonsFor(t, i)" class="ai-assign">
                    <div class="ai-assign-h">담당자 선택</div>
                    <label class="ai-cand" :class="{ on: !cardCustom[i] && pickFor(t, i, it) === reasonsFor(t, i).user }"
                           @click="setPick(i, reasonsFor(t, i).user)">
                      <span class="ai-cand-who">
                        <Avatar :user="reasonsFor(t, i).user" :name="personName(t, reasonsFor(t, i).user)" :size="22" />
                        <b>{{ personName(t, reasonsFor(t, i).user) || reasonsFor(t, i).user }}</b>
                        <small v-if="personName(t, reasonsFor(t, i).user)">{{ reasonsFor(t, i).user }}</small>
                        <em class="rec">추천</em>
                      </span>
                      <div class="ai-cand-why">
                        <div v-for="(r, ri) in reasonsFor(t, i).reasons" :key="ri">· {{ r }}</div>
                      </div>
                    </label>
                    <label v-for="(alt, ai) in (reasonsFor(t, i).alternates || [])" :key="'a'+ai"
                           class="ai-cand" :class="{ on: !cardCustom[i] && pickFor(t, i, it) === alt.user }"
                           @click="setPick(i, alt.user)">
                      <span class="ai-cand-who">
                        <Avatar :user="alt.user" :name="personName(t, alt.user)" :size="22" />
                        <b>{{ personName(t, alt.user) || alt.user }}</b>
                        <small v-if="personName(t, alt.user)">{{ alt.user }}</small>
                      </span>
                      <div class="ai-cand-why">{{ alt.why }}</div>
                    </label>
                    <label class="ai-cand" :class="{ on: cardCustom[i] }" @click="pickCustom(i)">
                      <b>직접 입력…</b>
                      <!-- 사람 검색은 티켓 화면과 같은 FieldEdit 팝업(규칙·디자인 재사용) -->
                      <div v-if="cardCustom[i]" @click.stop>
                        <FieldEdit class="aq-fe" ticket="__agent__" field="assignee" local
                                   :value="pickedAssignee[i] || ''" :user-id="pickedAssignee[i] || ''"
                                   @pick="(v) => { pickedAssignee[i] = v; }">
                          {{ pickedAssignee[i] || '사람 검색…' }}</FieldEdit>
                      </div>
                    </label>
                  </div>
                </li>
              </ol>

              <div v-if="(t.review.warnings || []).length" class="agent-warn">
                <div v-for="(w, i) in t.review.warnings" :key="i">주의 — {{ w.message }}</div>
              </div>
              <div v-if="(t.review.problems || []).length" class="agent-warn">
                <div v-for="(p, i) in t.review.problems" :key="i">검토 의견 — {{ p.message }}</div>
              </div>

              <div class="agent-card-act">
                <button class="ag-ok" :disabled="approving" @click="approve">
                  {{ approving ? '만드는 중…' : (hasCardEdits ? '수정한 내용으로 생성' : '이대로 생성') }}
                </button>
                <button class="ag-cancel" :disabled="approving" @click="cancelPending">취소하고 수정 요청</button>
                <em class="agent-card-hint">✎ 수정으로 카드에서 직접 고치거나, 채팅에 수정 요청을
                  적으면 초안을 고쳐 다시 보여 드립니다.</em>
              </div>
              </template>
            </div>
          </div>
        </div>

        <!-- 진행 상황: 최상위는 플랜 단계 체크리스트([✓]/[▸]/[ ]), 세부 행위(도구 호출)는
             각 단계 밑에 중첩. 기본은 진행 중 단계의 마지막 행위 한 줄만 — 펼치면 전부. -->
        <div v-if="busy && plan.length" class="agent-steps">
          <button class="agent-steps-h" @click="stepsOpen = !stepsOpen">
            {{ stepsOpen ? '▾' : '▸' }} 진행 — {{ planHead }}</button>
          <template v-for="s in plan" :key="s.id">
            <div class="agent-step"
                 :class="{ now: s.status === 'run', ok: s.status === 'done', skip: s.status === 'skip' }">
              <span class="smark">{{ s.status === 'done' ? '✓' : s.status === 'run' ? '▸'
                                     : s.status === 'skip' ? '–' : '○' }}</span>
              <b>{{ s.label }}</b>
              <em v-if="s.note && (stepsOpen || s.status !== 'pending')">{{ s.note }}</em>
              <span class="sdur">{{ s.status === 'done' && s.dur ? s.dur + 's'
                                    : s.status === 'run' ? '…' : '' }}</span>
            </div>
            <div v-for="(d, j) in visibleDetails(s)" :key="s.id + '-' + j"
                 class="agent-substep" :class="{ run: !d.done }">ㄴ {{ d.text }}</div>
          </template>
        </div>
      </div>

      <!-- 입력 — 클로드식 미니멀 채팅 박스. 밑은 코멘트 에디터지만(멘션·/jira·/confluence
           팝업과 뱃지 렌더 재사용) 툴바 등 크롬은 CSS 로 걷어냈다 — 채팅에 서식 메뉴는
           과하다(사용자 지적). 하단 아이콘 줄이 세 기능의 입구다. -->
      <!-- LLM 연결값이 없으면 입력창 대신 안내+[설정] — 눌러 보고 나서야 에러로 아는 것보다
           먼저 말해 주는 것이 낫다. 설정을 닫으면 상태를 다시 확인해 입력창이 살아난다. -->
      <div v-if="status && status.llmReady === false" class="agent-input agent-llmoff">
        <span class="agent-llmoff-msg">⚠ AI 를 쓸 수 없습니다 — {{ status.llmReason || 'LLM 연결이 설정되지 않았습니다.' }}
          <b>연결 확인된 LLM API 가 하나 이상 필요합니다.</b></span>
        <button class="agent-llmoff-btn" @click="settingsOpen = true">설정</button>
      </div>
      <div v-else class="agent-input agent-input-rich" @keydown.capture="onRichKey">
        <div class="agent-chatbox">
          <CommentEditor ref="richEd" ticketKey="" kind="agentchat" :hideFooter="true"
                         placeholder="하려는 업무를 적어 주세요 — @ 멘션 · 티켓·문서 넣기"
                         :submitFn="sendRich" />
          <div class="agent-chatbox-bar">
            <button @click="edMention" title="사람 멘션 (@)">@</button>
            <button class="agent-ref-add" @click="edPick('jira')" title="티켓 넣기 (/jira)">
              <span aria-hidden="true">🎫</span> 티켓 넣기
            </button>
            <button class="agent-ref-add" @click="edPick('confluence')" title="문서 넣기 (/confluence)">
              <span aria-hidden="true">📄</span> 문서 넣기
            </button>
            <span class="agent-chatbox-space"></span>
            <!-- 응답 중에는 같은 자리가 ■ 중단이다 — 멈출 방법이 없으면 기다리는 수밖에 없다 -->
            <button v-if="busy" class="agent-send-round is-stop" @click="stopStream"
                    title="응답 중단">■</button>
            <button v-else class="agent-send-round" :disabled="ready === null"
                    @click="submitRich" title="보내기 (Ctrl+Enter)">↑</button>
          </div>
        </div>
      </div>
      <div class="agent-foot">
        Ctrl+Enter 전송 — <b>승인하기 전에는 아무것도 만들거나 바꾸지 않습니다.</b>
        <a href="#/guide">서비스 안내</a>
      </div>
      </div>
      <!-- 참조 마커 호버 상자 — **본문 최상위에 fixed**. 표(가로 스크롤) 안의 마커에서도
           잘리지 않게 하려면 마커의 자식이 아니라 여기 있어야 한다(refOver 주석 참조). -->
      <div v-if="refTip" class="ref-tip" :style="refTip.style">{{ refTip.text }}</div>

      <AgentSettingsDialog v-if="settingsOpen"
        @saved="refreshStatus"
        @close="settingsOpen = false; refreshStatus()" />

      <!-- 우측 채널 — **생성하려는 초안**의 미리보기 공간(사용자 정정). 만들 실물을 티켓
           모양으로 옆에 두고 카드에서 담당자를 고르며 승인한다. 실존 티켓 클릭은 기존
           전역 모달(TicketDialog)이 그대로 뜬다. -->
      <!-- 접힌 미리보기를 다시 펴는 손잡이 — 접기는 초안을 **버리지 않는다**(✕ 는 버린다) -->
      <button v-if="draftTurn() && sideDraft >= 0 && sideHidden" class="ag-show stub side"
              title="초안 미리보기 펼치기" @click="setSideHidden(false)">
        <span class="st-ic">‹</span>
        <span class="st-label">초안 미리보기</span>
        <span class="st-dots" aria-hidden="true"><i></i><i></i><i></i></span>
      </button>
      <div v-if="draftTurn() && sideDraft >= 0 && !sideHidden" class="agent-side" ref="side"
           :style="sideW ? { '--side-w': sideW + 'px' } : null">
        <button class="ag-hide side" title="미리보기 접기(초안은 그대로 둔다)"
                @click="setSideHidden(true)">›</button>
        <!-- 왼쪽 가장자리를 끌어 폭 조절 — 오른쪽 패널이라 왼쪽으로 끌면 넓어진다 -->
        <div class="ag-grip side" title="너비 조절 — 드래그 (더블클릭: 기본 폭)"
             @mousedown.prevent="startSideDrag" @dblclick="resetSideW"></div>
        <div class="agent-side-h">
          <b>{{ sidePendingReady() ? '만들 티켓 미리보기' : '티켓 초안 (작성 중)' }}</b>
          <span v-if="sideItems().length > 1" class="agent-side-nav">
            <button v-for="(x, xi) in sideItems()" :key="xi"
                    :class="{ on: sideDraft === xi }" @click="sideDraft = xi">{{ xi + 1 }}</button>
          </span>
          <button class="agent-reset" @click="sideDraft = -1" title="닫기">✕</button>
        </div>
        <div class="agent-side-body" v-if="sideItem().summary">
          <div class="ai-ticketview side">
            <div class="tv-head">
              <span class="ai-type">{{ sideItem().type }}</span>
              <b>{{ sideItem().summary }}</b>
            </div>
            <div class="tv-meta">
              <!-- ★ 상위 티켓은 **키만 달랑 쓰지 않는다**(사용자 지적) — DL-102 만 보고는
                   어느 Epic 인지 모른다. 이 패널은 .agent-md 밖이라 기존 뱃지 augment 가
                   안 닿아서, 제목을 데이터로 직접 받아 건다(epicTitles).
                   ※ 이 주석에 백틱을 쓰지 마라 — 위 1067 줄의 경고와 같은 이유로 template
                     문자열이 그 자리에서 끊긴다(실제로 앱이 통째로 안 떴다). -->
              <span v-if="sideItem().epic">상위
                <a href="#" class="tkt" :data-key="sideItem().epic"
                   :title="sideItem().epic + ' ' + (epicTitles[sideItem().epic] || '')">
                  {{ sideItem().epic }}<template v-if="epicTitles[sideItem().epic]">
                  "{{ epicTitles[sideItem().epic] }}"</template></a></span>
              <span v-if="(sideItem().components || []).length">
                모듈 {{ sideItem().components.join(', ') }}</span>
              <span v-for="lb in (sideItem().labels || [])" :key="lb" class="tv-label">{{ lb }}</span>
              <span v-if="sideItem().priority">{{ sideItem().priority }}</span>
              <span v-if="sideItem().duedate">마감 {{ sideItem().duedate }}</span>
              <span v-if="pickFor(draftTurn(), sideDraft, sideItem())">담당
                <Avatar :user="pickFor(draftTurn(), sideDraft, sideItem())"
                        :name="personName(draftTurn(), pickFor(draftTurn(), sideDraft, sideItem()))"
                        :size="14" />
                {{ personName(draftTurn(), pickFor(draftTurn(), sideDraft, sideItem()))
                   || pickFor(draftTurn(), sideDraft, sideItem()) }}</span>
            </div>
            <div class="ai-desc-html" v-html="descPreview(sideItem().description)"></div>
            <!-- ★ Sub-Task 목록 — 승인하면 **함께 만들어지는 것들**이다(사용자 지적).
                 부모만 보여 주면 무엇이 생기는지 절반만 보고 승인하게 된다.
                 담당이 갈려 있으면 그것도 여기서 보여야 재배분 판단이 된다. -->
            <div v-if="(sideItem().children || []).length" class="tv-kids">
              <div class="tv-kids-h">함께 만들 Sub-Task {{ sideItem().children.length }}건</div>
              <div v-for="(c, ci) in sideItem().children" :key="ci" class="tv-kid">
                <span class="tv-kid-n">{{ ci + 1 }}</span>
                <span class="tv-kid-s">{{ c.summary }}</span>
                <span v-if="c.assignee" class="tv-kid-a">
                  <Avatar :user="c.assignee" :name="personName(draftTurn(), c.assignee)" :size="13" />
                  {{ personName(draftTurn(), c.assignee) || c.assignee }}</span>
              </div>
            </div>
            <div class="tv-hint">{{ sidePendingReady()
              ? '담당자 변경·승인은 왼쪽 카드에서 합니다 — 선택하면 여기 즉시 반영됩니다.'
              : '아직 작성 중인 초안입니다 — 질문에 답하거나 피드백을 주면 이 내용이 바뀝니다.' }}</div>
          </div>
        </div>
      </div>
    </template>
  </div>`;

export default AGENT_VIEW_TEMPLATE;
