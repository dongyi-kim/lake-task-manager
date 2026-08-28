"""Agent view static UI contracts."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from frontend.static_assets.support import ROOT, STATIC, agent_view_source, comment_editor_source

def test_agent_view_facade_delegates_feature_specific_modules():
    """대화 orchestration 본체가 템플릿·DOM 보강·변환·패널 저장 구현을 다시 품지 않는다."""
    agent_dir = STATIC / "components" / "agent"
    facade = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")

    assert len(facade) < 55 * 1024
    for module in (
        "agentViewTemplate.js",
        "badgeHydration.js",
        "contentTransforms.js",
        "panelLayout.js",
    ):
        assert (agent_dir / module).is_file()
        assert f'../agent/{module}' in facade
    assert "augmentAgentBadges(this.$el)" in facade
    assert "export function augmentAgentBadges(root)" in agent_view_source()
    assert "template: AGENT_VIEW_TEMPLATE" in facade
    assert "function dedupeTicketTail" not in facade
    assert "new DOMParser()" not in facade


def test_agent_ticket_badges_have_compact_and_detail_modes():
    """답변 티켓은 목록·소수 인라인·bullet 상세 세 형식을 사용한다."""
    md = (STATIC / "lib" / "agentMd.js").read_text(encoding="utf-8")
    view = agent_view_source()
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
    editor = comment_editor_source()
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
    view = agent_view_source()
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
    view = agent_view_source()
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
    view = agent_view_source()
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
    editor = comment_editor_source()
    view = agent_view_source()
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
    view = agent_view_source()
    assert "status.runtimeConfigSource === 'named'" in view
    assert "status.activeConfig.name" in view
    assert "status.runtimeConfigSource === 'environment'" in view
    assert "환경 설정 · {{ status.provider }}" in view
    assert "연결 설정 없음" in view


def test_local_agent_chat_copy_includes_progress_diagnostics_but_prod_is_gated():
    view = agent_view_source()
    assert 'this.appMeta.env !== "prod"' in view
    assert '"## Local debug"' in view
    assert "turn.debug.events.push" in view and "turn.debug.plan" in view
    assert "tokenChars" in view
    assert '["node", "label", "parent", "note", "message", "error", "thread_id"]' in view


def test_agent_reference_actions_have_visible_ticket_and_document_labels():
    view = agent_view_source()
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
