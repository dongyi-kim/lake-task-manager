"""Task creation and MyTasks static UI contracts."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from frontend.static_assets.support import ROOT, STATIC

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
    fold = (STATIC / "components" / "ui" / "SubtaskFoldBar.js").read_text(encoding="utf-8")
    model = (STATIC / "components" / "mytasks" / "taskModel.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "mytasks.css").read_text(encoding="utf-8")

    assert "export function uniformStatusCategory(cards)" in model
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
    assert 'import SubtaskFoldBar from "../ui/SubtaskFoldBar.js"' in view
    assert "components: { Avatar }" in fold
    assert '<span class="mt-subfoot-toggle"' in fold
    assert '<strong>{{ total }}</strong> Subtasks' in fold
    assert 'class="mt-subfoot-owners"' in fold
    assert 'v-for="owner in assignees"' in fold
    assert 'class="mt-subfoot-sep mt-subfoot-progress-sep"' in fold
    assert 'role="progressbar"' in fold
    assert '<em>{{ done }} / {{ total }}</em>' in fold
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
    model = (STATIC / "components" / "mytasks" / "taskModel.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "mytasks.css").read_text(encoding="utf-8")

    assert "export const AXIS_PAGE_SIZE = 40;" in model
    assert "axisEntries()" in view and 'add(state.k, "panel:" + panel.key)' in view
    assert "Task with SubTask는 자식을 쪼개지 않고 한 항목으로 센다" in model
    assert 'v-for="c in pagedCards(p, st.k)"' in view
    assert 'panelPageVisibleAny(p)' in view
    assert 'v-show="panelPageVisible(p, st.k)"' in view
    assert view.count('class="mt-axis-more-btn"') == 2
    assert "{{ axisHidden(st.k) }}개 더 보기" in view
    assert ".mt-axis-more-row { display: grid;" in css
    assert ".mt-axis-more-btn { display: inline-flex;" in css


def test_mytasks_streams_leaf_models_and_hydrates_groups_without_stale_filter_overwrite():
    view = (STATIC / "components" / "views" / "MyTasksView.js").read_text(encoding="utf-8")
    model = (STATIC / "components" / "mytasks" / "taskModel.js").read_text(encoding="utf-8")
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
    assert "export function reconcileTaskModel(current, incoming)" in model
    assert "const old = new Map(rows.filter((row) => row && row.key)" in model
    assert "rows.splice(0, rows.length, ...next);" in model
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
    model = (STATIC / "components" / "mytasks" / "taskModel.js").read_text(encoding="utf-8")

    assert 'const preserveVisible = !!(opts.quiet && this.model && this._activeCacheKey === key);' in view
    assert "else if (!preserveVisible) this.model = this._emptyTaskModel();" in view
    assert "else cache[key] = this.model;" in view
    assert "event.model && (!preserveVisible || finalUsable)" in view
    assert "event.done && !streamHadAuthFailure && !streamHadOtherFailure" in view
    assert "if (!preserveVisible || finalUsable) {" in view
    assert "hydrationPromise = this._hydrateModel(cache[key], seq, key, cache);" in view
    assert "active && !preserveVisible && completedLeaves.length" in view
    assert "active && !preserveVisible) this.streamProgress" in view
    assert "reconcileTaskRows(current.groups, incoming.groups, reconcileTaskGroup)" in model
    assert "reconcileTaskRows(current.atoms, incoming.atoms, patchTaskData)" in model
    assert "reconcileTaskRows(current.others, incoming.others, patchTaskData)" in model
    assert 'import { reactive } from "../../vendor/vue.esm-browser.prod.js";' in view
    assert "this._cardCache || (this._cardCache = new WeakMap())" in view
    assert "current = reactive(next);" in view
    assert "patchTaskData(current, next);" in view
    assert "this._compactCardCache || (this._compactCardCache = new WeakMap())" in view
    assert "vis.push(this.compactCard(p.parentCard, p));" in view
    assert "vis.push(Object.assign({}, p.parentCard" not in view
    assert "export const TASK_RETRY_DELAYS = [800, 2400];" in model
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
    model = (STATIC / "components" / "mytasks" / "taskModel.js").read_text(encoding="utf-8")

    assert "export function resolveDefaultModule(selected, explicit, mine, all)" in model
    assert 'const next = mineList.find((module) => !allList.length || known.has(module)) || "";' in model
    assert 'if (explicit && (!current || !allList.length || known.has(current)))' in model
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
