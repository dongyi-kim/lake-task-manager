"""Ticket dialog, editor, picker, and transport static UI contracts."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from frontend.static_assets.support import ROOT, STATIC

def test_comment_submit_waits_for_pending_draft_before_final_delete():
    """제출 성공 뒤 예약된 saveDraft가 완료 글을 되살리는 경쟁 상태를 막는다."""
    src = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")
    success = src.index("await this.submitFn(html);")
    cancel = src.index("clearTimeout(this._dt)", success)
    wait = src.index("await this._draftWrite", cancel)
    delete = src.index("await clearDraft(dk)", wait)
    assert success < cancel < wait < delete
def test_frontend_requests_time_out_without_turning_transport_stalls_into_login():
    """브라우저 fetch도 무한 대기하지 않고, 연결 지연은 별도 상태로 안내한다."""
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")
    banner = (STATIC / "components" / "ui" / "StatusBanner.js").read_text(encoding="utf-8")
    refresh = (STATIC / "components" / "ui" / "FloatingRefresh.js").read_text(encoding="utf-8")
    assert "const REQUEST_TIMEOUT_MS" in api
    assert "new AbortController()" in api and "ctl.abort()" in api
    assert "timeoutMs: 310 * 1000" in api       # 사람 로그인은 일반 조회보다 길게
    assert "timeoutMs: 16 * 60 * 1000" in api  # 대용량 첨부도 일반 조회 상한을 쓰지 않음
    assert 'this.mode === "degraded"' in banner
    assert 'st.mode === "degraded"' in refresh
    assert "api.login()" in refresh             # degraded 분기 뒤에서만 인증 흐름 진입


def test_ticket_timeline_is_deferred_and_never_blocks_loaded_dialog_sections():
    """타임라인 cold build/실패는 자기 패널만 기다리고 이미 로드된 필드 조작을 막지 않는다."""
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")
    dialog = (STATIC / "components" / "ui" / "TicketDialog.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "ticket.css").read_text(encoding="utf-8")

    assert "const TIMELINE_TIMEOUT_MS" in api
    assert '"/timeline?deferred=1&children="' in api
    assert "ticketTimeline: (key, children) => req(" in api      # pending 응답은 browser memo 금지
    assert '/timeline?deferred=1&children=" + (children ? "1" : "0")' in api
    assert 'spineLoading: true, timelineLoading: true, timelineErr: ""' in dialog
    assert "async loadTimeline(" in dialog and "result.pending" in dialog
    assert "TIMELINE_WAIT_MS" in dialog and "retryTimeline()" in dialog
    assert "toggleChildTimeline" in dialog and "childTimelineLoading" in dialog
    assert "하위 티켓 히스토리도 보기" in dialog
    # 최초 load는 children 인자를 주지 않는다. 명시적 버튼 메서드에서만 true로 요청한다.
    assert "this.loadTimeline(key, my);" in dialog
    toggle = dialog[dialog.index("async toggleChildTimeline"):dialog.index("hardRefresh()")]
    assert "api.ticketTimeline(key, true)" in toggle
    assert 'v-else-if="timelineErr"' in dialog and "@click=\"retryTimeline\"" in dialog
    assert ".tl-error button" in css
    # editmeta가 먼저 요청되어 완료된 본문/필드가 타임라인 때문에 읽기 전용으로 남지 않는다.
    assert dialog.index("api.editmeta(key)") < dialog.index("this.loadTimeline(key, my)")
    assert "Promise.allSettled([_sib, _tl])" not in dialog


def test_new_comment_composer_is_docked_outside_ticket_body_in_dialog_and_page():
    """새 댓글 작성창은 본문 스크롤과 분리하고, 기존 댓글 수정창은 각 댓글 자리에 둔다."""
    dialog = (STATIC / "components" / "ui" / "TicketDialog.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "ticket.css").read_text(encoding="utf-8")

    body_end = dialog.index('</div><!-- /.tkt-body -->')
    dock = dialog.index('class="tkt-compose-dock"')
    compose = dialog.index('class="tkt-cmt-compose"')
    assert body_end < dock < compose
    assert dialog.count('class="tkt-cmt-compose"') == 1
    assert dialog.index('v-if="editingId === c.id"') < body_end
    assert 'class="tkt-refresh"' in dialog[dock:]
    assert ".tkt-compose-dock" in css and "flex: none" in css
    assert ".tkt-dlg.page .tkt-body { min-height: 0; overflow: hidden auto" in css
    assert ".tkt-dlg.page .tkt-compose-dock { border-radius: 0; }" in css
    assert "padding-bottom: 42vh" not in css
    assert ".tkt-page .tkt-refresh { position: fixed" not in css


def test_new_comment_composer_hides_without_unmounting_and_shows_text_only_preview():
    """가리기는 에디터 상태를 유지하고, 접힌 바에는 이미지·표를 제외한 텍스트만 보여준다."""
    dialog = (STATIC / "components" / "ui" / "TicketDialog.js").read_text(encoding="utf-8")
    editor = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "ticket.css").read_text(encoding="utf-8")

    assert 'composeCollapsed: false, composePreview: "", composeHasDraft: false' in dialog
    assert 'v-if="composing" v-show="!composeCollapsed" class="tkt-compose-editor"' in dialog
    assert 'class="tkt-compose-hide"' in dialog and "@click=\"collapseCompose\"" in dialog
    assert '<b>가리기</b><span class="tkt-compose-hide-chevron"' in dialog and "⌄" not in dialog
    assert 'class="tkt-compose-resize"' in dialog and '@pointerdown="resizeCompose"' in dialog
    assert "startResizeFromTop" in dialog and "startResizeFromTop" in editor
    assert 'height-key="cmtEditorComposeH" :initial-height="180"' in dialog
    assert "loadEditorHeight(this.heightKey, this.initialHeight)" in editor
    assert 'ref="newCommentEditor"' in dialog and "await ed.flushDraft()" in dialog
    assert 'class="tkt-cmt-draft-v"' in dialog and "{{ composePreview || '텍스트 미리보기 없음' }}" in dialog
    assert "async cancelCompose()" in dialog and "await ed.discardDraft()" in dialog
    assert 'clone.querySelectorAll("img, table, pre, hr, .img-wrap, .tableWrapper")' in editor
    assert "async flushDraft()" in editor and "async discardDraft()" in editor
    assert ".tkt-compose-hide { position: absolute; top: 0; left: 50%" in css
    assert ".tkt-compose-resize { position: absolute" in css and "cursor: row-resize" in css
    assert ".tkt-compose-hide-chevron" in css and "border-bottom: 1.5px solid currentColor" in css
    assert ".tkt-compose-editor:has(.cmt-editor.maximized) .tkt-compose-hide" in css
    assert ".tkt-compose-editor .cmt-ed-bar > .cmt-ed-btn.ghost { color: var(--danger)" in css
    assert ".tkt-cmt-addbtn.draft" in css and ".tkt-cmt-draft-v" in css


def test_editor_root_handles_file_drops_missed_by_prosemirror():
    """툴바·여백에 놓은 파일도 티켓 첨부로 새지 않고 본문 삽입 경로를 탄다."""
    editor = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")

    drop = editor[editor.index("onDropFiles(e) {"):editor.index("startResize(e)")]
    assert "e.defaultPrevented" in drop
    assert "e.preventDefault()" in drop and "e.stopPropagation()" in drop
    assert "this.insertFiles(e.dataTransfer.files)" in drop
    assert '@drop="onDropFiles"' in editor


def test_agent_wiki_mentions_render_as_person_badges_even_before_name_hydration():
    md = (STATIC / "lib" / "agentMd.js").read_text(encoding="utf-8")
    assert "MENTION_RE" in md
    assert "personBadge" in md
    assert "[~" in md


def test_editor_and_rendered_mentions_share_stable_avatar_badge_ui():
    """멘션은 로딩 상태로 모양이 바뀌지 않고 사진 성공 시에만 @ 폴백을 덮는다."""
    badge = (STATIC / "lib" / "mentionBadge.js").read_text(encoding="utf-8")
    editor = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")
    dialog = (STATIC / "components" / "ui" / "TicketDialog.js").read_text(encoding="utf-8")
    agent = (STATIC / "lib" / "agentMd.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "ticket.css").read_text(encoding="utf-8")

    assert 'avatar.textContent = "@"' in badge
    assert 'img.className = "mention-av-img"' in badge and 'img.classList.add("on")' in badge
    assert "avatar.isConnected" not in badge       # cached load도 사진 표시
    assert "paintMentionBadge" in editor and "addNodeView()" in editor
    assert "enhanceMentionBadges(root)" in dialog
    assert "mention mention-badge" in agent and 'aria-hidden="true">@</span>' in agent
    assert ".mention-badge, .tkt-desc .mention, .tkt-desc a.user-hover, .agent-md .md-person" in css
    assert "gap: 4px; vertical-align: middle;" in css
    assert "gap: 4px; vertical-align: -2px;" not in css
    assert "this.v.descriptionEditHtml = v.descriptionEditHtml;" in dialog
    assert 'v.descriptionEditHtml !== undefined ? v.descriptionEditHtml : v.descriptionHtml' in dialog
    assert ".mention-av > img.mention-av-img.on { opacity: 1; }" in css


def test_field_edit_and_mentions_share_user_defaults_and_managed_popup():
    """추천은 한 구현을 쓰고 팝업 수명·위치는 최신 TipTap Suggestion이 관리한다."""
    shared = (STATIC / "lib" / "userSuggestions.js").read_text(encoding="utf-8")
    field = (STATIC / "components" / "ui" / "FieldEdit.js").read_text(encoding="utf-8")
    editor = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")
    dialog = (STATIC / "components" / "ui" / "TicketDialog.js").read_text(encoding="utf-8")
    popup = (STATIC / "lib" / "suggestionPopup.js").read_text(encoding="utf-8")
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")
    package = json.loads((ROOT / "tools" / "tiptap-bundle" / "package.json").read_text(encoding="utf-8"))
    bundle = (STATIC / "vendor" / "tiptap.bundle.mjs").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "ticket.css").read_text(encoding="utf-8")

    assert 'const RECENT_KEY = "userSuggestions.recent"' in shared
    assert "createUserTypeahead" in field and "defaultUserSuggestions" in field
    assert "createManagedMentionItems" in editor and "rememberUser(user)" in editor
    assert "initialItems: mentionInitialUsers(localUsers)" in editor
    assert "debounce: typeaheadDelay()" in editor
    assert "suggestion: mentionSuggestion(ticketKey, localUsers)" in editor
    assert "props.mount(element)" in popup and "unmount()" in popup
    assert 'if (key === "Escape") return false' in popup
    assert "loading && query && settings.hideItemsWhileLoading ? [] : nextItems" in popup
    assert "settings.showLoadingWithItems" in popup and 'class="mn-loading"' in popup
    assert "hideItemsWhileLoading: true" in editor and "showLoadingWithItems: true" in editor
    assert "사용자 검색 중…" in editor
    assert "document.body.appendChild(el)" not in editor and "_mentionPopupCleanup" not in editor
    assert "api.mentionUsers(q, ticketKey, { signal })" in shared
    assert "serverItems, localItems, recentUsers()" in shared
    assert "Number(user.contextRank) === 0" in shared
    assert "user.display !== user.name && currentIsShort" in shared
    assert "user.displayName || user.display || rawName || id" in shared
    assert "currentNameIsId" in shared and "current.name = user.name" in shared
    assert "보강한다. 별도 사용자 조회는 하지 않는다." in shared
    assert ':mention-users="mentionUsers"' in dialog
    assert "mentionUsers: (q, key, opts)" in api
    assert package["dependencies"]["@tiptap/suggestion"] == "3.30.3"
    assert "AbortController" in bundle
    assert ".mention-badge .mention-av > img.mention-av-img" in css
    assert "height: 100%; max-width: none" in css and "border: 0; border-radius: inherit" in css
    assert ".ProseMirror-selectednode:not(.mention-badge) img" in css


def test_comment_editor_runs_on_one_tiptap_v3_runtime():
    """에디터는 lock된 v3 패키지를 하나의 로컬 번들로만 로드한다."""
    loader = (STATIC / "lib" / "tiptap.js").read_text(encoding="utf-8")
    editor = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")
    package = json.loads((ROOT / "tools" / "tiptap-bundle" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "tools" / "tiptap-bundle" / "package-lock.json").read_text(encoding="utf-8"))
    entry = (ROOT / "tools" / "tiptap-bundle" / "entry.mjs").read_text(encoding="utf-8")
    bundle = STATIC / "vendor" / "tiptap.bundle.mjs"

    tiptap_versions = {version for name, version in package["dependencies"].items() if name.startswith("@tiptap/")}
    assert tiptap_versions == {"3.30.3"}
    assert lock["packages"]["node_modules/@tiptap/core"]["version"] == "3.30.3"
    assert bundle.is_file() and bundle.stat().st_size < 1024 * 1024
    assert not (STATIC / "vendor" / "esm").exists()
    assert 'import("/vendor/tiptap.bundle.mjs")' in loader
    assert "{ TableKit }" in entry and "{ TextStyleKit }" in entry
    assert "@tiptap/extension-font-family" not in package["dependencies"]
    assert "fontColorExt" not in editor
    assert "T.TableKit.configure" in editor and "T.TextStyleKit" in editor
    assert 'T.StarterKit.configure({ codeBlock: false, link: false })' in editor
    assert 'commands.setContent(html, { emitUpdate: false })' in editor


def test_field_edit_shows_offline_defaults_immediately_and_pins_none_option():
    """최근/local 추천은 서버를 기다리지 않고, 없음은 어떤 검색어에도 필터링되지 않는다."""
    src = (STATIC / "components" / "ui" / "FieldEdit.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "ticket.css").read_text(encoding="utf-8")
    root = (STATIC / "components" / "app-root.js").read_text(encoding="utf-8")
    tasks = (STATIC / "components" / "views" / "MyTasksView.js").read_text(encoding="utf-8")

    labels = src[src.index("suggest(q) {"):src.index("searchEpics(q) {")]
    epics = src[src.index("searchEpics(q) {"):src.index("searchWho(q) {")]
    users = src[src.index("searchWho(q) {"):src.index("// ── 최근 사용값")]
    assert labels.index("this.opts = this._prepRecentStr(base);") < labels.index('api.options("labels", "")')
    assert epics.index("this.opts = this._prepRecent(base") < epics.index('api.options("epics", "")')
    assert users.index("this.who = defaultUserSuggestions([], base);") < users.index("this._ta.run(q)")
    assert "this._lookupCurrent(token, q)" in src and "lookupSeq += 1" in src
    assert ".catch(() => { this.opts = []; })" not in src
    assert "hasNoneOption()" in src
    assert src.count('class="fe-i fe-empty"') == 3
    assert '@click="clearMulti"' in src and '@click="clearUser"' in src
    assert 'v-if="hasNoneOption" class="fe-clear"' in src
    assert ".fe-i.fe-empty" in css
    assert ".fe-i > span:not(.avt):not(.fe-empty-mark)" in css
    assert root.count("api.warmGlobals()") == 2    # 최초 인증 성공 + 재인증 복귀
    assert "api.warmGlobals()" not in tasks        # Task 본 데이터 완료 여부에 종속되지 않음


def test_ticket_epic_and_person_pickers_render_local_defaults_before_network():
    """티켓·Epic·사람 선택기는 최근/관련자/없음을 먼저 그리고 서버 결과를 뒤에 합친다."""
    recent = (STATIC / "lib" / "recent.js").read_text(encoding="utf-8")
    field = (STATIC / "components" / "ui" / "FieldEdit.js").read_text(encoding="utf-8")
    child = (STATIC / "components" / "ui" / "NewChildDialog.js").read_text(encoding="utf-8")
    picker = (STATIC / "components" / "ui" / "LinkPicker.js").read_text(encoding="utf-8")
    search = (STATIC / "components" / "ui" / "SearchOverlay.js").read_text(encoding="utf-8")
    dialog = (STATIC / "components" / "ui" / "TicketDialog.js").read_text(encoding="utf-8")
    user_pick = (STATIC / "components" / "ui" / "UserPickDialog.js").read_text(encoding="utf-8")
    transition = (STATIC / "components" / "ui" / "TransitionDialog.js").read_text(encoding="utf-8")
    options = (STATIC / "lib" / "optionRepository.js").read_text(encoding="utf-8")

    assert 'const LOCAL_KEY = "recent.items"' in recent
    assert recent.index("save(merge(") < recent.index("api.recentAdd(payload)")
    assert "export function recentItems" in recent and "export function hydrateRecent" in recent
    assert "recent: recentItems(20" in picker
    assert picker.index("recent: recentItems(20") < picker.index("api.recent(20")
    assert "recent: recentItems(RECENT_MAX)" in search
    assert "this.recent = recentItems(RECENT_MAX)" in search

    epics = field[field.index("searchEpics(q) {"):field.index("searchWho(q) {")]
    assert epics.index("recentEpicOptions()") < epics.index('api.options("epics", "")')
    assert "fieldObjectSnapshot" in field and "fieldStringSnapshot" in field
    assert 'const CACHE_KEY = "optionRepository.v1"' in options
    assert 'const LEGACY_CREATE_CACHE = "newTicket.optionCache.v1"' in options
    assert ':choices="mentionUsers"' in dialog
    assert dialog.count(':choices="mentionUsers"') == 2

    parents = child[child.index("searchParents(q) {"):child.index("pickParent(item) {")]
    assert parents.index("this.plist = local;") < parents.index("api.parentTaskCandidates(q)")
    assert "SPECIALS.filter(matches)" in child
    assert "defaultUserSuggestions([], [])" in user_pick
    assert "defaultUserSuggestions([], [])" in transition


def test_creation_dialog_keeps_last_options_and_never_deadlocks_on_type_lookup():
    """타입/기본 선택지는 브라우저에 보존하고 원격 타입 조회 실패가 생성 폼을 먹통으로 만들지 않는다."""
    child = (STATIC / "components" / "ui" / "NewChildDialog.js").read_text(encoding="utf-8")
    field = (STATIC / "components" / "ui" / "FieldEdit.js").read_text(encoding="utf-8")
    options = (STATIC / "lib" / "optionRepository.js").read_text(encoding="utf-8")
    assert 'const CACHE_KEY = "optionRepository.v1"' in options
    assert "cachedOptions" in child and "recentEpicOptions" in child and "rememberOptions" in child
    assert 'const DEFAULT_TASK_TYPES = ["Task", "Story", "Bug", "Improvement", "New Feature"]' in child
    assert 'const DEFAULT_SUBTASK_TYPES = ["Sub-Task"]' in child
    special = child[child.index("if (item.special)"):child.index("const parent = item.key")]
    assert special.index("this._resolve(") < special.index("this._loadTypes(")
    assert "기존 목록을 사용합니다" in child and "rememberOptions(kind, list)" in child
    save = field[field.index("async save(v, extra) {"):field.index("saveMulti() {")]
    assert save.index("this.close();") < save.index("await api.updateFields")
    assert "field-save:" in save and "pushToast" in save
