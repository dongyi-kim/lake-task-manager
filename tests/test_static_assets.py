"""정적 자산(JS/CSS)의 **기계적 결함**을 커밋 전에 잡는다.

브라우저가 없어도 잡을 수 있는 것들이다. 렌더가 조용히 깨지는 사고가 반복됐고
(정규식의 `\\b` 가 편집 과정에서 **백스페이스 문자(0x08)** 로 파일에 박혀 매칭이
영구히 실패한 실측 사고), 그런 것은 눈으로 코드를 봐도 보이지 않는다 —
파일에 그대로 있는 것처럼 보이기 때문이다. 그래서 바이트로 검사한다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
ROOT = STATIC.parents[1]
ASSETS = sorted(list(STATIC.rglob("*.js")) + list(STATIC.rglob("*.css")))

# 소스에 있어서는 안 되는 제어문자 — 탭·개행·CR 만 허용한다.
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _rel(p: Path) -> str:
    return str(p.relative_to(STATIC.parent.parent))


def test_home_shows_only_five_daily_release_notes():
    home = (STATIC / "components" / "views" / "HomeView.js").read_text(encoding="utf-8")
    notes = (STATIC / "lib" / "releaseNotes.js").read_text(encoding="utf-8")
    guide = (STATIC.parents[1] / "AGENTS.md").read_text(encoding="utf-8")

    assert "RELEASES.slice(0, 10)" in home
    assert ':key="r.version"' in home and "{{ r.version }}" in home
    assert "r.date" not in home and "hn-date" not in home
    versions = re.findall(r'version:\s*"(v\d{4}\.\d{2}\.\d{2})"', notes)
    assert len(versions) >= 5
    assert versions == sorted(versions, reverse=True)
    assert len(versions) == len(set(versions))
    assert not re.search(r'version:\s*"v\d{4}\.\d{2}\.\d{2}\.\d+"', notes)
    assert not re.search(r'\bdate\s*:', notes)
    assert "같은 날짜의 기존 항목에 변경 내용을 통합" in guide
    assert "별도 `date` 필드" in guide
    assert "최신 5개 날짜만 표시" in guide


def test_static_assets_exist():
    assert len(ASSETS) > 20, "정적 자산을 못 찾았다 — 경로 규약이 바뀌었나"


@pytest.mark.parametrize("path", ASSETS, ids=_rel)
def test_no_control_characters(path: Path):
    """제어문자 0개. 특히 0x08 — `\\b` 를 쓴 정규식이 편집 도구를 거치며 박히는 사고가
    있었다(실측). 파일을 열어 봐도 정상으로 보이는데 정규식은 절대 매칭되지 않는다."""
    src = path.read_text(encoding="utf-8")
    hits = [(src[:m.start()].count("\n") + 1, hex(ord(m.group())))
            for m in CTRL_RE.finditer(src)]
    assert not hits, f"{_rel(path)} 에 제어문자: {hits[:5]}"


OURS = [p for p in ASSETS if p.suffix == ".js" and "vendor" not in p.parts]


@pytest.mark.parametrize("path", OURS, ids=_rel)
def test_javascript_parses(path: Path):
    """우리 JS 가 **문법적으로 성립**하는가. 문자열 치환으로 파일을 고치다 보면 토막이
    남아 파일 전체가 죽는데, 그러면 화면이 통째로 비고 원인은 콘솔에만 남는다.
    (esprima 가 없는 환경에서는 건너뛴다 — 개발 의존성이다.)"""
    esprima = pytest.importorskip("esprima")
    src = path.read_text(encoding="utf-8")
    # esprima 는 ES2020 이전까지만 안다 — optional chaining·nullish 를 동등한 옛 문법으로
    # 낮춰 준다(구조 검증이 목적이지 문법 감시가 목적이 아니다).
    src = src.replace("?.(", "(").replace("?.[", "[").replace("?.", ".").replace("??", "||")
    try:
        esprima.parseModule(src)
    except Exception as e:                       # noqa: BLE001 — 파서가 뭘 던지든 실패다
        pytest.fail(f"{_rel(path)} 파싱 실패: {e}")


@pytest.mark.parametrize("path", [p for p in ASSETS if p.suffix == ".js"], ids=_rel)
def test_no_inline_event_handlers(path: Path):
    """인라인 이벤트 핸들러 금지 — CSP 에서 막히면 **조용히** 동작하지 않는다
    (프사 실패를 숨기는 onerror 가 안 돌아 깨진 이미지가 그대로 남았다 — 실측).
    리스너는 코드에서 addEventListener 로 붙인다."""
    src = path.read_text(encoding="utf-8")
    hits = re.findall(r"\bon(?:error|load|click|change|input)\s*=\s*[\"']", src)
    assert not hits, f"{_rel(path)} 인라인 핸들러: {hits[:3]}"


VUE_COMPONENTS = [p for p in ASSETS
                  if p.suffix == ".js" and "components" in p.parts]


@pytest.mark.parametrize("path", VUE_COMPONENTS, ids=_rel)
def test_templates_do_not_call_imported_modules(path: Path):
    """Vue 템플릿은 **컴포넌트 인스턴스 프로퍼티만** 본다. 템플릿 표현식에서 import 한
    모듈(agentApi·api…)을 부르면 예외도 없이 **조용히 아무 일도 일어나지 않는다**.

    실측: 설정 창을 닫을 때 `@close="… agentApi.status() …"` 로 모델 표시를 갱신하게
    해 뒀는데 한 번도 실행되지 않아, 모델을 바꿔도 좌상단이 옛 값 그대로였다.
    """
    src = path.read_text(encoding="utf-8")
    m = re.search(r"template:\s*`", src)
    if not m:
        pytest.skip("템플릿 없음")
    tpl = src[m.end():]
    mods = re.findall(r"^import\s+(?:\{\s*([\w,\s]+)\s*\}|(\w+))\s+from", src, re.M)
    names = {n.strip() for a, b in mods for n in (a or b or "").split(",") if n.strip()}
    names -= {"h", "ref", "computed"}          # 렌더 함수용 — 템플릿과 무관
    bad = []
    for name in names:
        for mm in re.finditer(r'[@:]?[\w.-]+="[^"]*\b' + re.escape(name) + r"\.\w", tpl):
            bad.append(mm.group(0)[:60])
    assert not bad, f"{_rel(path)} 템플릿이 모듈을 직접 부른다: {bad[:3]}"


def test_create_dialog_descriptions_do_not_share_comment_drafts():
    """생성창의 본문은 새 댓글 초안 저장소를 쓰면 안 된다.

    ticket key가 생성 전 `__new__`였다가 생성 후 실 key로 바뀌므로, comment kind로 두면 제출 때
    다른 key를 삭제하고 `new:__new__`에 완료된 본문이 남아 다음 Task 본문에 복원된다.
    """
    for name in ("EpicCreateDialog.js", "NewChildDialog.js"):
        src = (STATIC / "components" / "ui" / name).read_text(encoding="utf-8")
        editors = re.findall(r"<CommentEditor\b[^>]+>", src, re.S)
        assert editors and all('kind="description"' in tag for tag in editors), name


def test_create_dialogs_send_description_with_create_and_surface_save_failures():
    """본문은 최초 생성 payload에도 포함하고 후속 저장 오류를 삼키지 않는다."""
    editor = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")
    assert "htmlValue()" in editor and "hasPendingUploads()" in editor
    for name in ("EpicCreateDialog.js", "NewChildDialog.js"):
        src = (STATIC / "components" / "ui" / name).read_text(encoding="utf-8")
        assert "descriptionHtml: createDesc" in src, name
        assert "if (!key)" in src, name
        assert "this.$refs.ded.err" in src, name
        assert "await this.$refs.ded.submit(); } catch" not in src, name


def test_ticket_create_dialogs_use_wider_responsive_defaults():
    css = (STATIC / "styles" / "ticket.css").read_text(encoding="utf-8")
    assert ".nk { width: min(720px, 94vw)" in css
    assert ".nk-epic { width: min(780px, 96vw)" in css


def test_uniform_subtask_status_reuses_solo_parent_and_foldable_children_in_one_column():
    """한 상태 그룹은 단독 Task 목록에 편입되고 모든 그룹이 같은 하단 폴더블 바를 쓴다."""
    view = (STATIC / "components" / "views" / "MyTasksView.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "mytasks.css").read_text(encoding="utf-8")

    assert "singleStatus: uniformStatusCategory(all)" in view
    assert "parentCard: this.card(g, g, !!g.mine)" in view
    assert 'const compact = this.axis === "h" ? grouped.filter((p) => this.compactStatus(p)) : [];' in view
    assert "const solo = this.soloPanel(" in view
    assert "compactPanel: p" in view
    assert "statusCategory: p.singleStatus" not in view
    assert 'const rawParent = p?.parentCard?.statusCategory;' in view
    assert 'return uniform && this.bandOpen(parentStatus) ? parentStatus : null;' in view
    assert 'kind: "solo", cards: this.sorted(vis)' in view
    assert '<TaskCard v-if="!c.compactPanel" :card="c"' in view
    assert 'class="mt-compact-flow"' in view
    assert '<TaskCard :card="c" :style="sigStyle(c)"' in view
    assert "const SubtaskFoldBar = {" in view
    assert "components: { Avatar }" in view
    assert '<span class="mt-subfoot-toggle"' in view
    assert '<strong>{{ total }}</strong> Subtasks' in view
    assert 'class="mt-subfoot-owners"' in view
    assert 'v-for="owner in assignees"' in view
    assert 'class="mt-subfoot-sep mt-subfoot-progress-sep"' in view
    assert 'role="progressbar"' in view
    assert '<em>{{ done }} / {{ total }}</em>' in view
    assert "const seenAssignees = new Set();" in view
    assert "allCount: all.length, assignees" in view
    assert view.count("<SubtaskFoldBar") == 3  # 1축 compact + 가로 3축 + 세로 상태축
    assert ':closed="isGroupClosed(c.compactPanel)"' in view
    assert ':closed="isGroupClosed(p)"' in view
    assert 'class="mt-roll"' not in view
    assert 'mode === "all" || mode === "collapsed"' in view
    assert 'class="mt-gbody one mt-compact-children"' in view
    assert "cellCards(c.compactPanel, c.compactPanel.singleStatus)" in view
    assert "overflowed(c.compactPanel, c.compactPanel.singleStatus)" in view
    assert "cellHidden(c.compactPanel, c.compactPanel.singleStatus)" in view
    # 실제 그룹들은 먼저, standalone/compact 공유 목록은 마지막에 붙는다.
    assert view.index("const out = grouped.filter") < view.index("if (solo) out.push(solo)")
    assert ".mt-gslot > .mt-gcard2 { grid-column: 1 / -1;" in css
    assert ".mt-compact-flow { width: 100%; min-width: 0; border-radius: 3px;" in css
    assert ".mt-compact-flow:hover { box-shadow: 0 6px 16px" in css
    assert ".mt-compact-head > .mt-card.two { width: 100%; box-sizing: border-box;\n  border-radius: 3px 3px 0 0; box-shadow: none;" in css
    assert ':root[data-theme="dark"] .ax-h .mt-compact-flow {' in css
    assert ".mt-compact-children { display: grid; grid-template-columns: minmax(0, 1fr);" in css
    assert ".mt-subfoot { display: flex; align-items: center; gap: 7px; width: 100%; height: 30px;" in css
    assert ".mt-subfoot-toggle { flex: none; display: inline-grid; place-items: center; width: 19px; height: 19px;" in css
    assert ".mt-subfoot-owners { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; gap: 10px;" in css
    assert ".mt-subfoot-progress-sep { margin-left: auto; }" in css
    assert view.count('class="mt-owner mt-sub-owner"') == 3
    assert view.count('class="mt-subdue-sep"') == 3
    assert 'v-if="!c.mine || subView === \'all\'" class="mt-owner"' not in view
    assert 'v-if="!sub.mine || subView === \'all\'" class="mt-owner"' not in view
    assert ".mt-sub-owner { flex: 0 1 auto; min-width: 0; max-width: 108px; overflow: hidden; }" in css
    assert "@container mtc (max-width: 340px)" in css
    assert ".mt-compact-flow > .mt-subfoot { border: 1px solid var(--border-hi); border-top: 0;" in css
    assert ".mt-compact-head > .mt-fold" not in css
    assert "mt-gslot.one-status" not in css


def test_mytasks_uses_axis_pagination_without_splitting_subtask_groups():
    view = (STATIC / "components" / "views" / "MyTasksView.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "mytasks.css").read_text(encoding="utf-8")

    assert "const AXIS_PAGE_SIZE = 40;" in view
    assert "axisEntries()" in view and 'add(state.k, "panel:" + panel.key)' in view
    assert "Task with SubTask는 자식을 쪼개지 않고 한 항목으로 센다" in view
    assert 'v-for="c in pagedCards(p, st.k)"' in view
    assert 'panelPageVisibleAny(p)' in view
    assert 'v-show="panelPageVisible(p, st.k)"' in view
    assert view.count('class="mt-axis-more-btn"') == 2
    assert "{{ axisHidden(st.k) }}개 더 보기" in view
    assert ".mt-axis-more-row { display: grid;" in css
    assert ".mt-axis-more-btn { display: inline-flex;" in css


def test_mytasks_streams_leaf_models_and_hydrates_groups_without_stale_filter_overwrite():
    view = (STATIC / "components" / "views" / "MyTasksView.js").read_text(encoding="utf-8")
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")

    assert '"&deferred=1"' in api
    assert "myTasksStream: (opts, onEvent, signal)" in api
    assert "myTasksEpicMeta: (keys)" in api
    epic_api = api[api.index("myTasksEpicMeta: (keys)"):api.index("search: (q, scope, only)")]
    assert "return req(" in epic_api
    assert "get(" not in epic_api
    assert ".filter(Boolean))).sort()" in epic_api
    assert "response.body.getReader" in api
    assert "myTasksGroup: (syncId, key)" in api
    assert "myTasksEpics: (syncId)" in api
    assert 'event.contract !== "task-snapshot.v1"' in view
    assert "event.requestToken !== requestToken" in view
    assert "eventSequence <= lastSequence" in view
    assert "event.completedLeaves && event.completedLeaves.length" in view
    assert "hydrationPromise = this._hydrateModel(cache[key], seq, key, cache);" in view
    assert "_mergeStreamModel" not in view
    assert "_mergeTaskGroup" not in view
    assert "_normalizeStreamGroups" not in view
    assert "this._streamAbort.abort()" in view
    assert "export function reconcileTaskModel(current, incoming)" in view
    assert "const old = new Map(rows.filter((row) => row && row.key)" in view
    assert "rows.splice(0, rows.length, ...next);" in view
    assert "this._cacheModel(cache, key, reconciled)" in view
    assert "this._queueEpicMetadata(reconciled, cache)" in view
    # Progressive/key-only snapshots preserve existing object/DOM identity and must not overwrite
    # already-known Epic titles before metadata is synchronously reapplied.
    stream_replace = view[view.index("if (event.replace !== false && event.model &&"):
                          view.index("if (active && event.done)")]
    assert stream_replace.index("reconcileTaskModel") < stream_replace.index("this._queueEpicMetadata")
    assert "this.model = next" not in stream_replace and "this.model = cache[key]" not in stream_replace
    hydrate_start = view.index("cache[cacheKey] = reconcileTaskModel")
    hydrate_replace = view[hydrate_start:view.index("} catch (e) {", hydrate_start)]
    assert hydrate_replace.index("reconcileTaskModel") < hydrate_replace.index("this._queueEpicMetadata")
    assert "this.model = next" not in hydrate_replace
    assert 'return !epic || !!epic.pending;' in view
    assert 'epicDisplayTitle(k) { return this.epicPending(k) ? "Epic 이름 확인 중"' in view
    assert "_epicMetaAttempts" in view
    assert "_settleMissingEpicMetadata(exhausted, cache)" in view
    assert "attempt <= TASK_RETRY_DELAYS.length" in view
    assert "this._epicMetaKnown.delete(changedKey)" in view
    assert "this._epicMetaKnown = new Map();" in view
    assert 'result.contract !== "task-snapshot.v1"' in view
    assert "Number(result.sequence) <= snapshotSequence" in view
    assert 'if (kind === "permission") continue;' in view
    assert 'title: kind === "auth" ? "일부 Task를 인증 문제로 불러오지 못했습니다"' in view
    assert "Parent Task는 준비됨" not in view
    assert "mt-sub-sync-row" not in view
    # A changed filter stops not-yet-started child jobs. The server owns completed leaf caches,
    # while only a matching request token and monotonic sequence may touch visible UI.
    assert 'if (seq !== this._loadSeq) return;   // 아직 시작하지 않은 옛 필터 보강' in view
    assert "cache[cacheKey] = reconcileTaskModel" in view
    assert "this._loadSeq === seq && this.model && this.model.syncId === syncId" in view
    assert "await Promise.allSettled([worker(), worker()]);" in view
    assert "if (opts.awaitHydration && hydrationPromise) await hydrationPromise;" in view
    assert "const changedKey = String((view && view.key) || detail.key ||" in view
    assert "const wasVisible = this._taskModelHasKey(changedKey);" in view
    assert "this.load({ quiet: true, awaitHydration: true })" in view
    assert "refreshSeq !== this._loadSeq" in view
    assert "this._toastExcluded([changedKey])" in view
    assert "const gone = [...before]" not in view
    assert "_mergeHydration" not in view
    assert "if (!seen.has(atom.key))" in view
    assert "if (p?.group?.childrenPending) return null;" in view


def test_mytasks_quiet_refresh_keeps_visible_dom_and_patches_only_changed_keys():
    view = (STATIC / "components" / "views" / "MyTasksView.js").read_text(encoding="utf-8")

    assert 'const preserveVisible = !!(opts.quiet && this.model && this._activeCacheKey === key);' in view
    assert "else if (!preserveVisible) this.model = this._emptyTaskModel();" in view
    assert "else cache[key] = this.model;" in view
    assert "event.model && (!preserveVisible || finalUsable)" in view
    assert "event.done && !streamHadAuthFailure && !streamHadOtherFailure" in view
    assert "if (!preserveVisible || finalUsable) {" in view
    assert "hydrationPromise = this._hydrateModel(cache[key], seq, key, cache);" in view
    assert "active && !preserveVisible && completedLeaves.length" in view
    assert "active && !preserveVisible) this.streamProgress" in view
    assert "reconcileTaskRows(current.groups, incoming.groups, reconcileTaskGroup)" in view
    assert "reconcileTaskRows(current.atoms, incoming.atoms, patchTaskData)" in view
    assert "reconcileTaskRows(current.others, incoming.others, patchTaskData)" in view
    assert 'import { reactive } from "../../vendor/vue.esm-browser.prod.js";' in view
    assert "this._cardCache || (this._cardCache = new WeakMap())" in view
    assert "current = reactive(next);" in view
    assert "patchTaskData(current, next);" in view
    assert "this._compactCardCache || (this._compactCardCache = new WeakMap())" in view
    assert "vis.push(this.compactCard(p.parentCard, p));" in view
    assert "vis.push(Object.assign({}, p.parentCard" not in view
    assert "const TASK_RETRY_DELAYS = [800, 2400];" in view
    assert "this.load({ quiet: true, retryAttempt: attempt });" in view
    assert "streamHadOtherFailure && !streamHadAuthFailure" in view
    assert "성공한 티켓은 그대로 두고 실패분만 다시 받습니다." in view
    assert "jobs.push(Object.assign({}, job, { attempt: attempt + 1 }))" in view
    assert "if (kind === \"permission\") continue" in view
    assert "key, view: { key, statusCategory: zone }" in view
    assert "Number(g.kidsDone) + doneDelta" in view


def test_mytasks_defaults_to_my_module_and_reloads_identity_after_auth():
    """선택 이력이 없으면 내 모듈을 쓰고, prod 최초 인증 실패 뒤에도 모듈 목록을 복구한다."""
    view = (STATIC / "components" / "views" / "MyTasksView.js").read_text(encoding="utf-8")

    assert "export function resolveDefaultModule(selected, explicit, mine, all)" in view
    assert 'const next = mineList.find((module) => !allList.length || known.has(module)) || "";' in view
    assert 'if (explicit && (!current || !allList.length || known.has(current)))' in view
    assert "moduleSelExplicit: false" in view
    assert "this.moduleSel = resolved.selected;" in view
    assert "this.moduleSelExplicit = resolved.explicit;" in view
    auth = view[view.index('window.addEventListener("auth-ok"'):view.index("this._mq = window.matchMedia")]
    assert "this.refreshMe();" in auth
    assert "this.load({ quiet: true });" in auth
    assert 'if (this.scope === "module" && before !== this.apiScope) this.load();' in view
    assert 'if (typeof saved.moduleSelExplicit === "boolean")' in view
    assert "else this.moduleSelExplicit = !!saved.moduleSel;" in view
    assert "moduleSelExplicit: this.moduleSelExplicit" in view
    pick = view[view.index("onModulePick(v) {"):view.index("runJql() {")]
    assert pick.index("this.moduleSelExplicit = true;") < pick.index('this.scope = "module"')


def test_comment_submit_waits_for_pending_draft_before_final_delete():
    """제출 성공 뒤 예약된 saveDraft가 완료 글을 되살리는 경쟁 상태를 막는다."""
    src = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")
    success = src.index("await this.submitFn(html);")
    cancel = src.index("clearTimeout(this._dt)", success)
    wait = src.index("await this._draftWrite", cancel)
    delete = src.index("await clearDraft(dk)", wait)
    assert success < cancel < wait < delete


def test_agent_ticket_badges_have_compact_and_detail_modes():
    """답변 티켓은 목록·소수 인라인·bullet 상세 세 형식을 사용한다."""
    md = (STATIC / "lib" / "agentMd.js").read_text(encoding="utf-8")
    view = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "agent.css").read_text(encoding="utf-8")
    for variant in ("jira-badge-list", "jira-badge-inline", "jira-badge-detail"):
        assert variant in md or variant in css
    assert "agent-ticket-details" in md
    assert "a.tkt::before" not in css
    assert "typeIconSvg" in view
    assert "TICKET_TOKEN_RE" in md
    assert "dedupeTicketTail" in view and "ticketAssignee" in view
    assert ".jira-badge-detail .jb-owner" in css
    assert '.agent-md a.tkt[data-key]:not([data-filled])' in view


def test_plain_ticket_keys_are_short_but_jira_links_are_detailed():
    """단순 티켓 번호 자동링크만 Short이고, URL 붙여넣기·링크 삽입은 Detailed다."""
    dialog = (STATIC / "components" / "ui" / "TicketDialog.js").read_text(encoding="utf-8")
    editor = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "ticket.css").read_text(encoding="utf-8")
    assert 'plainKey ? "jira-badge-list" : "jira-badge-detail"' in dialog
    assert '!a.classList.contains("jira-link-explicit")' in dialog
    assert 'a.className = "jira-badge jira-badge-detail tkt"' in editor
    assert '"web-badge jira-link-explicit"' in editor
    assert ".tkt-desc .jira-badge-list .jb-name" in css
    assert ".cmt-ed-host .jira-badge-list .jb-meta" in css


def test_agent_ticket_references_always_use_detail_badges():
    """참조의 ticket은 raw key·token·Jira link 입력 모두 detail badge로 정규화한다."""
    md = (STATIC / "lib" / "agentMd.js").read_text(encoding="utf-8")
    view = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "agent.css").read_text(encoding="utf-8")
    ref_row = md[md.index("function refRow"):md.index("function _render")]
    assert "src.match(KEY_RE)" in ref_row
    assert 'keyBadge(ticketKey, "detail")' in ref_row
    assert "ref-tkt" not in ref_row
    assert "dedupeTicketReference" in view
    assert ".agent-ref-item .jira-badge .jb-meta { display:none; }" not in css
    assert ".agent-ref-item .jira-badge-detail" in css


def test_agent_evidence_renderer_accepts_canonical_and_legacy_headings():
    """`### 근거`를 clickable source index로 렌더하고 기존 참조 출력도 읽는다."""
    md = (STATIC / "lib" / "agentMd.js").read_text(encoding="utf-8")
    render = md[md.index("export function renderMarkdown"):md.index("function refRow")]
    assert "근거|참조" in render
    assert "#{1,4}" in render
    assert "<summary>근거 " in render
    assert 'keyBadge(ticketKey, "detail")' in md


def test_agent_evidence_has_one_hierarchical_renderer_without_system_duplicate_panels():
    """답변/시스템 근거를 한 source index로 합치고 소스별 발견은 하위번호로 그린다."""
    md = (STATIC / "lib" / "agentMd.js").read_text(encoding="utf-8")
    view = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "agent.css").read_text(encoding="utf-8")

    assert "export function mergeEvidenceMarkdown" in md
    assert "systemEvidence" in md and "relatedDocs" in md
    assert "ref-observation" in md and "subRef" in md
    assert "CITATION_RE" in md and "CITATION_RUN_RE" in md
    assert "compactAdjacentCitations" in md
    assert "ref-citations" in md and 'links.join("")' in md
    assert '">[${n}]</a>' in md
    assert "md(t) { return renderMarkdown(t.text, t.people, t.evidence, t.docs); }" in view
    assert 'v-html="md(t)"' in view
    assert 'class="agent-ev"' not in view
    assert 'class="agent-docs"' not in view
    assert ".ref-observation" in css
    assert ".ref-citations" in css
    assert '.ref-observation[data-ref="' in view


def test_agent_source_quality_table_keeps_readable_columns_in_narrow_chat():
    """출처 평가 detail badge가 판정·한계 열을 한 글자 폭으로 밀어내지 않는다."""
    md = (STATIC / "lib" / "agentMd.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "agent.css").read_text(encoding="utf-8")

    assert 'class="agent-source-quality"' in md
    assert 'class="agent-source-quality-scroll"' in md
    assert ".agent-source-quality-scroll" in css
    assert "min-width:620px" in css
    assert ".agent-source-quality td:nth-child(2)" in css


def test_agent_approval_card_actions_remain_readable_with_preview_open():
    css = (STATIC / "styles" / "agent.css").read_text(encoding="utf-8")
    assert ".agent-card-act" in css and "flex-wrap:wrap" in css
    assert ".agent-card-act .ag-ok" in css and "white-space:nowrap" in css
    assert "@media (max-width: 1180px)" in css
    assert ".agent-side" in css and "position:absolute" in css


def test_agent_ticket_badges_never_nest_inside_inline_code():
    """`DL-123`은 badge 하나, `key = DL-123`은 code 하나로 렌더해야 한다."""
    md = (STATIC / "lib" / "agentMd.js").read_text(encoding="utf-8")
    view = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")
    code_stash = md.index('.replace(/`([^`]+)`/g')
    key_badge = md.index('.replace(TICKET_TOKEN_RE')
    assert code_stash < key_badge
    assert "const token =" in md and "const key =" in md
    assert "return keep(`<code>${code}</code>`)" in md
    assert '.agent-md code > a.jira-badge:only-child' in view
    assert "code.replaceWith(badge)" in view


def test_agent_reference_picker_keeps_recent_urls_and_sends_them_to_model():
    """빈 검색의 최근 항목과 검색 결과 모두 실제 주소가 포함된 Agent 입력이 되어야 한다."""
    picker = (STATIC / "components" / "ui" / "LinkPicker.js").read_text(encoding="utf-8")
    editor = (STATIC / "components" / "ui" / "CommentEditor.js").read_text(encoding="utf-8")
    view = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")

    assert 'api.recent(20, this.isJira ? "jira" : "confluence")' in picker
    assert 'url: r.url || ("/browse/" + key)' in picker
    assert 'const base = (await jiraBase()) || location.origin' in editor
    assert 'href ? `[${label || href}](${href})` : label' in view
    assert 'p.set("kind", kind)' in api


def test_agent_settings_use_named_configs_instead_of_fixed_provider_tabs():
    dialog = (STATIC / "components" / "ui" / "AgentSettingsDialog.js").read_text(encoding="utf-8")
    api_src = (STATIC / "lib" / "agentApi.js").read_text(encoding="utf-8")
    assert "내 설정" in dialog and "+ 추가" in dialog and "설정 이름" in dialog
    assert "같은 연결 방식도" in dialog
    assert 'class="ag-tabs"' not in dialog
    for call in ("createConfig", "updateConfig", "configModels", "probeConfigAuth",
                 "probeConfig", "activateConfig"):
        assert call in dialog and call in api_src
    assert 'configModels: (id)' in api_src
    assert '/api/agent/configs/' in api_src
    assert 'chatModelSimpleProfile' in dialog
    assert '간단한 역할 모델 프로파일 (선택)' in dialog


def test_agent_sidebar_identifies_named_environment_and_missing_configs():
    view = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")
    assert "status.runtimeConfigSource === 'named'" in view
    assert "status.activeConfig.name" in view
    assert "status.runtimeConfigSource === 'environment'" in view
    assert "환경 설정 · {{ status.provider }}" in view
    assert "연결 설정 없음" in view


def test_local_agent_chat_copy_includes_progress_diagnostics_but_prod_is_gated():
    view = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")
    assert 'this.appMeta.env !== "prod"' in view
    assert '"## Local debug"' in view
    assert "turn.debug.events.push" in view and "turn.debug.plan" in view
    assert "tokenChars" in view
    assert '["node", "label", "parent", "note", "message", "error", "thread_id"]' in view


def test_agent_reference_actions_have_visible_ticket_and_document_labels():
    view = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "agent.css").read_text(encoding="utf-8")
    assert '> 티켓 넣기' in view
    assert '> 문서 넣기' in view
    assert ".agent-chatbox-bar > .agent-ref-add" in css


def test_reference_hover_is_shared_by_all_ticket_links_and_person_mentions():
    """에이전트 전용 pseudo tooltip이 아니라 앱 전체의 한 컨트롤러를 사용한다."""
    root = (STATIC / "components" / "app-root.js").read_text(encoding="utf-8")
    hover = (STATIC / "lib" / "referenceHover.js").read_text(encoding="utf-8")
    api = (STATIC / "lib" / "api.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "ticket.css").read_text(encoding="utf-8")
    assert 'import { installReferenceHover } from "../lib/referenceHover.js"' in root
    assert "installReferenceHover()" in root
    assert '.tkt[data-key]' in hover
    assert '.jira-badge[data-key]' in hover and "a[href*='/browse/']" in hover
    assert "data-type='mention'" in hover and ".md-person[data-uid]" in hover
    assert "a.user-hover" in hover and "ViewProfile.jspa" in hover
    for label in ("티켓 번호", "티켓 타입", "제목", "담당자", "진행상황",
                  "상위 Epic", "기한", "최근 업데이트",
                  "Full Display Name", "username"):
        assert label in hover
    assert "ticketBadge" in hover and "userBadge" in hover
    assert "const ticketCache" not in hover
    assert 'ticketBadge: (key) => get("/api/ticket/"' in api
    assert "userBadge:" in api and "/api/mention/user/" in api
    assert ".reference-hover" in css
    assert ".tkt-desc a.user-hover" in css
    dialog = (STATIC / "components" / "ui" / "TicketDialog.js").read_text(encoding="utf-8")
    assert 'a.setAttribute("role", "button")' in dialog
    assert 'a.setAttribute("tabindex", "0")' in dialog


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


def test_feature_guides_explain_search_recents_quick_open_and_browser_access():
    guides = (STATIC / "lib" / "guides.js").read_text(encoding="utf-8")
    spot = (STATIC / "components" / "ui" / "GuideSpot.js").read_text(encoding="utf-8")

    assert 'id: "global-search-recent-1"' in guides
    assert 'anchor: ".search-trig"' in guides
    assert "/ 키를 누르면 통합 검색창이 열립니다" in guides
    assert "최근 열어본" in guides and "티켓·문서·웹 링크" in guides
    assert 'id: "quick-open-hotkey-1"' in guides
    assert 'anchor: ".setmenu-trig"' in guides
    assert "body: ({ hotkey }) => hotkey" in guides
    assert 'export function hotkeyLabel(spec)' in guides
    assert 'api.prefs().then((p) =>' in spot and "p.quickOpenHotkey" in spot
    assert 'id: "browser-localhost-access-1"' in guides
    assert 'body: ({ port }) =>' in guides and '"웹 브라우저 주소창에서 localhost:" + port' in guides
    assert 'port: window.location.port || "4457"' in spot
    assert 'this.g.body({ hotkey: hotkeyLabel(this.hotkey), port:' in spot
    assert "{{ bodyText() }}" in spot


def test_global_search_keeps_up_to_twenty_unique_recent_items():
    search = (STATIC / "components" / "ui" / "SearchOverlay.js").read_text(encoding="utf-8")
    assert "const RECENT_MAX = 20;" in search
    assert "const RECENT_FETCH = 100;" in search
    assert "api.recent(RECENT_FETCH)" in search
    assert ").slice(0, RECENT_MAX);" in search


def test_global_search_revalidates_kept_results_when_reopened():
    search = (STATIC / "components" / "ui" / "SearchOverlay.js").read_text(encoding="utf-8")
    typeahead = (STATIC / "lib" / "typeahead.js").read_text(encoding="utf-8")

    assert "clear() { m.clear(); }" in typeahead
    assert "return { run, cancel, clear };" in typeahead
    assert "Object.values(this._src).forEach((t) => t.clear());" in search
    assert "if (this.q.trim()) this.run();" in search


# ── 파이썬 소스 위생 ────────────────────────────────────────────────────────
# 같은 편집 사고의 파이썬 판. heredoc 으로 소스를 고치면 줄바꿈이 **공백으로 뭉개져**
# `if a  <공백 17칸>  and b:` 같은 줄이 남는다. 문법은 멀쩡해서 테스트도 전부 통과하고
# 리뷰에서도 넘어가지만, 그 줄은 아무도 다시 읽지 못한다(실측 5건).
_ROOT = Path(__file__).resolve().parents[1]
AGENT_PY = sorted((_ROOT / "app" / "agent").rglob("*.py"))
# ★ **배터리 소스도 같은 검사를 받는다.** 여기 0x08 이 박히면 체커의 정규식이 조용히
#   달라져 **무엇을 재는지가 바뀐다** — 실측: `DL-` 이 `DL-` 로 박혀 사람 조사
#   케이스 두 건이 통과할 답에도 FAIL 로 떨어졌다. app/agent 만 보고 tools/ 를 안 봐서
#   pytest 1035 이 초록인 채로 지나갔다 — 가드가 사고가 난 자리를 안 덮고 있었다.
TOOLS_PY = sorted((_ROOT / "tools").glob("agent_*.py"))


def _code_only(src: str) -> list:
    """문자열·주석 **내용**을 지운 줄 목록. 리터럴 안의 정렬 공백은 정상이다."""
    import io
    import token as T
    import tokenize
    lines = src.splitlines()
    masked = list(lines)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type not in (T.STRING, T.COMMENT):
            continue
        (r1, c1), (r2, c2) = tok.start, tok.end
        if r1 == r2:
            ln = masked[r1 - 1]
            masked[r1 - 1] = ln[:c1] + "X" * (c2 - c1) + ln[c2:]
        else:
            for r in range(r1, r2 + 1):
                masked[r - 1] = ""
    return masked


@pytest.mark.parametrize("path", AGENT_PY, ids=lambda p: p.name)
def test_agent_source_has_no_collapsed_newlines(path):
    src = path.read_text(encoding="utf-8")
    bad = [(i, raw.strip()[:90])
           for i, (raw, m) in enumerate(zip(src.splitlines(), _code_only(src)), 1)
           if len(raw) > 100 and m.strip() and "      " in m.lstrip()]
    assert not bad, (
        f"{path.name} 에 줄바꿈이 공백으로 뭉개진 코드 줄이 있다 — "
        + "; ".join(f"L{i}: {s}" for i, s in bad)
        + " (heredoc 대신 Edit 도구로 고칠 것)")


@pytest.mark.parametrize("path", AGENT_PY + TOOLS_PY, ids=lambda p: p.name)
def test_agent_source_has_no_control_chars(path):
    hit = CTRL_RE.search(path.read_text(encoding="utf-8"))
    assert not hit, (f"{path.name} 에 제어문자 0x{ord(hit.group()):02x} 가 박혔다 — "
                     r"정규식의 \b 가 백스페이스로 변한 그 사고다")
