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


def test_maxis_evidence_workspace_separates_fact_inference_and_open_facts():
    """원격 workflow의 판단 근거는 raw 응답 없이 검토 가능한 projection으로 표시한다."""
    view = (STATIC / "components" / "views" / "AgentView.js").read_text(encoding="utf-8")
    template = (STATIC / "components" / "agent" / "agentViewTemplate.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "agent.css").read_text(encoding="utf-8")

    assert "evidenceWorkspace: ev.evidenceWorkspace || ev.evidence_workspace || null" in view
    assert "workspaceFacts(t)" in view and 'row.kind === "fact"' in view and '"open_fact"' in view
    assert "workspaceFreshness(t)" in view and "qualityGate.ok" in template
    assert "source 본문" in view and "제거" in view
    assert "Evidence Workspace" in template
    assert "확인된 사실" in template and "추론·제안" in template and "미확인·추가 확인" in template
    assert "출처와 데이터 시점" in template and "검증 경고" in template
    assert 'v-if="sourceUrl(row)"' in template
    assert "sourceUrl(row)" in view and "^\\/browse\\/" in view
    assert "row.content" not in template
    assert ".evidence-workspace" in css and ".ew-fresh.stale" in css


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


def test_maxis_settings_are_a_separate_workflow_surface_with_safe_credentials():
    dialog = (STATIC / "components" / "ui" / "AgentSettingsDialog.js").read_text(encoding="utf-8")
    panel = (STATIC / "components" / "ui" / "MaxisSettingsPanel.js").read_text(encoding="utf-8")
    assert 'v-else-if="selected.stage === \'dev\'"' in panel
    assert 'v-else-if="selected.stage === \'internal-pilot\'"' in panel
    assert "내부 시범은 DEV와 별도 환경이며 아직 실행할 수 없습니다" in panel
    assert "설정·증적 등록만으로 활성화되지 않습니다" in panel
    api_src = (STATIC / "lib" / "agentApi.js").read_text(encoding="utf-8")
    css = (STATIC / "styles" / "agent.css").read_text(encoding="utf-8")

    assert 'import MaxisSettingsPanel from "./MaxisSettingsPanel.js"' in dialog
    assert '<MaxisSettingsPanel v-if="mode === \'maxis\'"' in dialog
    assert "MAXIS / Dify Workflow" in dialog and "LLM 연결" in dialog
    for label in ("Workflow bundle", "Knowledge version", "Dify Knowledge dataset UUID",
                  "Knowledge release sentinel SHA-256", "Knowledge manifest",
                  "Knowledge 원격 평가", "25-case 결과물",
                  "Dify 원격 실행 로그 정책", "승인 정책 ID", "보존 기간(일)",
                  "Data policy", "Fingerprint",
                  "기능별 실행 경로", "MAXIS 긴급 중단", "AI 효과", "운영 승격"):
        assert label in panel
    for mode in ("local", "shadow", "maxis"):
        assert f'key: "{mode}"' in panel
    for field in ("agentCoreApiKey", "editorAuthorApiKey", "pmoBriefApiKey"):
        assert field in panel
    assert 'type="password"' in panel and "Windows DPAPI" in panel
    assert "deploymentOverrideNames" in panel and "workflowVersions" in panel
    assert "missingParameters" in panel and "필수 변수 누락" in panel
    assert "knowledgeRetrievalVerified" in panel and "Knowledge 조회 확인" in panel
    assert "remoteLogPolicyId" in panel and "remoteLogRetentionDays" in panel
    assert "Dify 설정을 자동 검사하지 않습니다" in panel
    assert "실제 Dify 평가 실행을 자동 검증했다는 뜻은 아닙니다" in panel
    assert "knowledgeEvalReady" in panel and "knowledgeEvalAttestation" in panel
    assert "Shadow만 사용할 수 있으며 MAXIS 운영 경로는 저장할 수 없습니다" in panel
    assert "합성 데이터 canary·Shadow 검증 전용" in panel
    assert "canManage" in panel and "읽기 전용입니다" in panel and "매니저만" in panel
    assert "promotionDetail" in panel and "minimumSamples" in panel
    assert "impactSegments" in panel and "LTM Local과 MAXIS/Dify" in panel
    assert "기존 Jira Dashboard/수작업 기준선" in panel
    assert "API Key" not in panel[panel.index("mx-impact"):]
    for path in ("/api/agent/maxis/status", "/api/agent/maxis/profiles/",
                 "/api/agent/maxis/credentials/", "/api/agent/maxis/probe/",
                 "/api/agent/maxis/activate/", "/api/agent/maxis/routes",
                 "/api/agent/maxis/kill-switch", "/api/agent/maxis/impact"):
        assert path in api_src
    assert ".mx-panel" in css and ".mx-impact" in css and ".mx-promotion-gate" in css
    assert ".mx-impact-segments" in css and ".mx-impact-row" in css
