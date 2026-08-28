// Render-only shell for CommentEditor. Behavior remains in the component module.
const COMMENT_EDITOR_TEMPLATE = `
  <div class="cmt-editor" :class="{ maximized, 'drag-over': dragOver }"
       @dragenter="onDragEnter" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDropFiles">
    <div v-if="loadErr" class="cmt-ed-err">{{ loadErr }}
      <button class="cmt-ed-btn" @click="$emit('cancel')">닫기</button>
    </div>
    <template v-else>
      <div class="cmt-tb" v-show="ready">
        <button type="button" class="tb-b" :class="{on:active('bold')}" @click="tbBold" title="굵게"><b>B</b></button>
        <button type="button" class="tb-b" :class="{on:active('italic')}" @click="tbItalic" title="기울임"><i>I</i></button>
        <button type="button" class="tb-b" :class="{on:active('strike')}" @click="tbStrike" title="취소선"><s>S</s></button>
        <button type="button" class="tb-b" :class="{on:active('code')}" @click="tbCode" title="인라인 코드">&lt;/&gt;</button>
        <!-- 글자색 — 문자서식과 같은 묶음에 둔다(첫 줄에 보이게). -->
        <span class="tb-style">
          <button type="button" class="tb-b tb-color-b" :class="{on:colorOpen}"
                  @click.stop="colorOpen=!colorOpen; bgOpen=false" title="글자색"><b class="tb-ca">A</b><i class="tb-caret">▾</i></button>
          <span v-if="colorOpen" class="tb-style-pop tb-sw-pop" @click.stop>
            <button v-for="c in COLORS" :key="'fc'+c.k" type="button" class="tb-sw" :class="{none:!c.k}"
                    :style="c.k ? {background:c.k} : {}" :title="c.label" @click="setFontColor(c.k)"></button>
          </span>
          <span v-if="colorOpen" class="tb-style-back" @click.stop="colorOpen=false"></span>
        </span>
        <!-- 배경색(형광펜) -->
        <span class="tb-style">
          <button type="button" class="tb-b tb-bg-b" :class="{on:bgOpen}"
                  @click.stop="bgOpen=!bgOpen; colorOpen=false" title="배경색(형광펜)"><b class="tb-ba">A</b><i class="tb-caret">▾</i></button>
          <span v-if="bgOpen" class="tb-style-pop tb-sw-pop" @click.stop>
            <button v-for="c in BGCOLORS" :key="'bg'+c.k" type="button" class="tb-sw" :class="{none:!c.k}"
                    :style="c.k ? {background:c.k} : {}" :title="c.label" @click="setFontBg(c.k)"></button>
          </span>
          <span v-if="bgOpen" class="tb-style-back" @click.stop="bgOpen=false"></span>
        </span>
        <span class="tb-sep"></span>
        <!-- 스타일 콤보 — 문단/제목/코드블록. 헤딩 버튼 셋을 여기로 합쳤다(툴바가 짧아진다). -->
        <span class="tb-style" @keydown.esc="styleOpen = false">
          <button type="button" class="tb-b tb-style-b" :class="{on:styleOpen}" @click.stop="styleOpen = !styleOpen"
                  :title="'글 스타일 — ' + curStyle.label">{{ curStyle.short }}<i class="tb-caret">▾</i></button>
          <span v-if="styleOpen" class="tb-style-pop" @click.stop>
            <button v-for="o in STYLES" :key="o.k" type="button" class="tb-style-i"
                    :class="[o.k, { on: curStyle.k === o.k }]" @click="setStyle(o)">
              <span class="tb-style-t">{{ o.label }}</span><em>{{ o.hint }}</em>
            </button>
          </span>
          <span v-if="styleOpen" class="tb-style-back" @click.stop="styleOpen = false"></span>
        </span>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b" :class="{on:active('bulletList')}" @click="tbBullet" title="불릿">•</button>
        <button type="button" class="tb-b" :class="{on:active('orderedList')}" @click="tbOrdered" title="번호">1.</button>
        <button type="button" class="tb-b" :class="{on:active('taskList')}" @click="tbTask" title="체크박스(할 일)">☑</button>
        <button type="button" class="tb-b" :class="{on:active('blockquote')}" @click="tbQuote" title="인용">❝</button>
        <button type="button" class="tb-b" :class="{on:active('codeBlock')}" @click="tbCodeBlock" title="코드블록">{ }</button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b co-i" :class="{on:inCallout('info')}" @click="tbCallout('info')" title="정보 콜아웃 {info}">ℹ</button>
        <button type="button" class="tb-b co-n" :class="{on:inCallout('note')}" @click="tbCallout('note')" title="노트 콜아웃 {note}">📌</button>
        <button type="button" class="tb-b co-t" :class="{on:inCallout('tip')}" @click="tbCallout('tip')" title="팁 콜아웃 {tip}">💡</button>
        <button type="button" class="tb-b co-w" :class="{on:inCallout('warning')}" @click="tbCallout('warning')" title="경고 콜아웃 {warning}">⚠</button>
        <span class="tb-sep"></span>
        <!-- 정렬 — 문단·제목·표 셀에 적용. 표 셀에서도 쓰인다. -->
        <button type="button" class="tb-b" :class="{on:isAlign('left')}" @click="tbAlign('left')" title="왼쪽 정렬">⬅</button>
        <button type="button" class="tb-b" :class="{on:isAlign('center')}" @click="tbAlign('center')" title="가운데 정렬">⬌</button>
        <button type="button" class="tb-b" :class="{on:isAlign('right')}" @click="tbAlign('right')" title="오른쪽 정렬">➡</button>
        <span class="tb-sep"></span>
        <!-- 글꼴 — 기본 vs 코딩(고정폭). 선택 글자에 적용된다. -->
        <span class="tb-style">
          <button type="button" class="tb-b tb-style-b" :class="{on:fontOpen}" @click.stop="fontOpen = !fontOpen"
                  title="글꼴">{{ curFont.short }}<i class="tb-caret">▾</i></button>
          <span v-if="fontOpen" class="tb-style-pop" @click.stop>
            <button v-for="f in FONTS" :key="f.k" type="button" class="tb-style-i"
                    :class="{ on: curFont.k === f.k }" @click="setFont(f)">
              <span class="tb-style-t" :style="{ fontFamily: f.css || 'inherit' }">{{ f.label }}</span>
            </button>
          </span>
          <span v-if="fontOpen" class="tb-style-back" @click.stop="fontOpen = false"></span>
        </span>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b" :class="{on:active('linkBadge')}" @click="tbLink"
                title="링크 뱃지 (선택 텍스트가 제목이 됨 · 뱃지 더블클릭으로 수정)">🔗</button>
        <span class="tb-style tp-wrap">
          <button type="button" class="tb-b" :class="{on:tablePick}" @click.stop="openTablePicker" title="표 삽입 — 행·열 선택">▦</button>
          <span v-if="tablePick" class="tp-pop" @click.stop @mouseleave="tpR = 0; tpC = 0">
            <span class="tp-grid">
              <span v-for="i in 64" :key="i" class="tp-cell"
                    :class="{ on: tpRowOf(i) <= tpR && tpColOf(i) <= tpC }"
                    @mouseenter="tpR = tpRowOf(i); tpC = tpColOf(i)"
                    @click="insertTableSize(tpRowOf(i), tpColOf(i))"></span>
            </span>
            <span class="tp-label">{{ tpR && tpC ? (tpR + ' × ' + tpC) : '행 × 열 선택' }}</span>
          </span>
          <span v-if="tablePick" class="tb-style-back" @click.stop="tablePick = false"></span>
        </span>
        <button type="button" class="tb-b" @click="mdTable = true" title="마크다운 표 붙여넣기 → 변환">⊞</button>
        <button type="button" class="tb-b" @click="tbImage" title="이미지">🖼</button>
        <span class="tb-sep"></span>
        <!-- AI 자동완성 — 이 에디터가 무엇을(본문/코멘트) 어느 티켓에 쓰는 중인지 서버가 알고
             있으므로, 사용자는 "무엇을 써 달라"만 적으면 된다. 결과는 삽입될 뿐 저장은 사용자가. -->
        <span class="tb-style ai-wrap">
          <button type="button" ref="aiBtn" class="tb-b tb-ai" :class="{on:aiOpen}" @click.stop="openAi"
                  title="AI 자동완성 — 지금 쓰는 글을 이어 쓰거나 새로 초안을 만든다">AI<span class="tb-ai-spark" aria-hidden="true">✨</span></button>
          <span v-if="aiOpen" class="ai-pop" :style="aiPopStyle" @click.stop @keydown.esc="aiOpen=false">
            <template v-if="aiReady === false">
              <span class="ai-err">AI 를 쓸 수 없습니다 — {{ aiWhy || 'LLM 연결이 설정되지 않았습니다.' }}</span>
              <span class="ai-row">
                <span class="ai-hint">키를 등록하면 바로 쓸 수 있습니다</span>
                <button type="button" class="cmt-ed-btn ghost" @click="aiOpen=false">닫기</button>
                <button type="button" class="cmt-ed-btn primary" @click="aiSettings = true">설정</button>
              </span>
            </template>
            <template v-else>
            <textarea ref="aiInput" class="ai-in" v-model="aiPrompt" rows="4"
                      :placeholder="kind === 'description'
                        ? '예) 배경·범위·완료 조건까지 본문 초안 잡아줘'
                        : '예) 진행 상황 공유 코멘트 써줘'"
                      @keydown.enter.exact.prevent="runAi"></textarea>
            <label v-if="hasBody()" class="ai-ck">
              <input type="checkbox" v-model="aiSeed"> 지금 쓰던 글을 재료로 사용
            </label>
            <label class="ai-ck">
              <input type="checkbox" v-model="aiReplace"> 전체 교체 (끄면 커서 위치에 이어쓰기)
            </label>
            <span v-if="aiAsk" class="ai-ask">{{ aiAsk }}</span>
            <span v-if="aiErr" class="ai-err">{{ aiErr }}</span>
            <span class="ai-row">
              <span class="ai-hint">Enter 로 생성 · Ctrl+Z 로 되돌리기</span>
              <button type="button" class="cmt-ed-btn ghost" @click="aiOpen=false">취소</button>
              <button type="button" class="cmt-ed-btn primary" :disabled="aiBusy" @click="runAi">
                {{ aiBusy ? '작성 중…' : '생성' }}
              </button>
            </span>
            </template>
          </span>
          <span v-if="aiOpen" class="tb-style-back" @click.stop="aiOpen = false"></span>
        </span>
        <button type="button" class="tb-b" style="margin-left:auto" @click="toggleMax"
                :title="maximized ? '최대화 해제' : '에디터 최대화'">{{ maximized ? '🗗' : '🗖' }}</button>
        <input ref="file" type="file" multiple style="display:none" @change="onFile">
        <span v-if="aiBusy" class="ai-run" aria-live="polite"><i class="ai-spin"></i>AI가 작성 중… 잠시만요</span>
        <AgentSettingsDialog v-if="aiSettings" @close="aiSettings = false; openAi()" />
        <LinkPicker v-if="pick" :mode="pick" insert @close="pick = ''" @pick="onPick" />
        <MarkdownTableDialog v-if="mdTable" @close="mdTable = false" @insert="insertMdTable" />
      </div>
      <div class="cmt-tb cmt-tb-tbl" v-show="ready && inCodeBlock()">
        <span class="tb-lbl">코드 언어</span>
        <select class="cmt-langsel" :value="codeLang()" @change="setCodeLang">
          <option value="">(자동 감지)</option>
          <option v-for="l in codeLangs()" :key="l" :value="l">{{ l }}</option>
        </select>
      </div>
      <div class="cmt-tb cmt-tb-tbl" v-show="ready && inImage()">
        <span class="tb-lbl">이미지</span>
        <button type="button" class="tb-b" @click="imgWidth(0.25)" title="작게 (폭 25%)">25%</button>
        <button type="button" class="tb-b" @click="imgWidth(0.5)" title="보통 (폭 50%)">50%</button>
        <button type="button" class="tb-b" @click="imgWidth(0.75)" title="크게 (폭 75%)">75%</button>
        <button type="button" class="tb-b" @click="imgWidth(1)" title="가득 (폭 100%)">100%</button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b" @click="imgWidth(null)" title="원본 크기로">원본</button>
        <span class="tb-lbl" style="margin-left:auto">모서리 드래그로도 조절</span>
      </div>
      <div class="cmt-tb cmt-tb-tbl" v-show="ready && inTable()">
        <span class="tb-lbl">표</span>
        <button type="button" class="tb-b tb-ic" @click="tColBefore" title="왼쪽에 열 추가">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="7.5" y="2.5" width="6" height="11" rx="1"/><path d="M10.5 2.5v11"/><path d="M3.2 6.2v3.6M1.4 8h3.6"/></svg></button>
        <button type="button" class="tb-b tb-ic" @click="tColAfter" title="오른쪽에 열 추가">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="6" height="11" rx="1"/><path d="M5.5 2.5v11"/><path d="M12.8 6.2v3.6M11 8h3.6"/></svg></button>
        <button type="button" class="tb-b tb-ic tb-del" @click="tColDel" title="열 삭제">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M8 2.5v11"/><path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8"/></svg></button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b tb-ic" @click="tRowBefore" title="위에 행 추가">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="7.5" width="11" height="6" rx="1"/><path d="M2.5 10.5h11"/><path d="M6.2 3.2h3.6M8 1.4v3.6"/></svg></button>
        <button type="button" class="tb-b tb-ic" @click="tRowAfter" title="아래에 행 추가">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="6" rx="1"/><path d="M2.5 5.5h11"/><path d="M6.2 12.8h3.6M8 11v3.6"/></svg></button>
        <button type="button" class="tb-b tb-ic tb-del" @click="tRowDel" title="행 삭제">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M2.5 8h11"/><path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8"/></svg></button>
        <span class="tb-sep"></span>
        <button type="button" class="tb-b tb-ic" @click="tHeaderRow" title="헤더 행 토글">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M2.5 6.5h11"/><rect class="fillbar" x="2.5" y="2.5" width="11" height="4"/></svg></button>
        <button type="button" class="tb-b tb-ic tb-del" @click="tTableDel" title="표 삭제">
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/><path d="M2.5 6.5h11M6.5 2.5v11"/><path d="M10 10l3.5 3.5M13.5 10L10 13.5"/></svg></button>
      </div>
      <div v-if="restored" class="cmt-restored">
        <span>작성 중이던 내용을 복원했습니다.</span>
        <button type="button" class="cmt-ed-btn ghost" @click="discardDraft">새로 쓰기</button>
      </div>
      <div ref="ed" class="cmt-ed-host"
           :style="!maximized && hostH ? { height: hostH + 'px', maxHeight: 'none' } : null"></div>
      <!-- 세로 크기 조절 손잡이 — 최대화 모드에는 없다(거기선 창이 높이를 정한다).
           얇은 선이 아니라 잡을 수 있는 띠로 둔다: 1~2px 짜리는 조준하다 지친다. -->
      <div v-if="!maximized" class="cmt-ed-grip" :class="{ on: resizing }"
           @pointerdown="startResize" @dblclick="resetHeight"
           title="끌어서 높이 조절 · 더블클릭하면 기본 높이"><i></i></div>
      <div v-if="hideFooter && err" class="cmt-ed-msg solo">{{ err }}</div>
      <div v-if="!hideFooter" class="cmt-ed-bar">
        <span v-if="err" class="cmt-ed-msg">{{ err }}</span>
        <button class="cmt-ed-btn ghost" :disabled="busy" @click="$emit('cancel')">취소</button>
        <button class="cmt-ed-btn primary" :disabled="busy || !ready" @click="submit">
          {{ busy ? busyLabel : submitLabel }}</button>
      </div>
    </template>
  </div>`;

export default COMMENT_EDITOR_TEMPLATE;
