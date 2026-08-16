"""Suite- and case-specific qualitative review contracts for LTM Agent batteries.

The common five-axis rubric answers *how good* an output is.  These declarations
answer *what this exact battery case must inspect*: entities, retrieval paths,
source classes, preserved values, and forbidden inventions.

This module is data-only.  Importing it must never configure or invoke the Agent.
"""

from __future__ import annotations

from typing import Any


def _element(
    element_id: str,
    dimension: str,
    question: str,
    major_when: str,
    evidence_sources: list[str],
    expected: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": element_id,
        "dimension": dimension,
        "question": question,
        "majorWhen": major_when,
        "evidenceSources": evidence_sources,
        "expected": expected,
    }


def _case(goal: str, *elements: dict[str, Any]) -> dict[str, Any]:
    return {"goal": goal, "elements": list(elements)}


_REPLY = ["output.reply", "output.pending", "output.questions"]
_RETRIEVAL = [
    "evaluationEvidence.queryPlan",
    "evaluationEvidence.queryResults",
    "evaluationEvidence.queryArtifacts",
    "evaluationEvidence.evidence",
    "evaluationEvidence.relatedDocs",
    "evaluationEvidence.webContext",
]
_EDITOR = ["input", "output.html", "output.note", "output.references"]


SUITE_REVIEW_ELEMENTS = {
    "conversation": [
        _element(
            "conversation_retrieval_path",
            "contract_actionability",
            "질문의 성격에 맞는 Jira·Confluence·comment·people·web source를 계획하고 실제 조회했는가",
            "필요한 source class를 조회하지 않거나 무관한 source를 사용해 결론이 달라짐",
            _RETRIEVAL,
            {"rule": "요청별 필요 source와 실제 queryPlan/queryResults를 대조"},
        ),
        _element(
            "conversation_evidence_chain",
            "factual_grounding",
            "최종 답변의 핵심 주장·entity·수치가 실행 근거의 식별 가능한 항목과 연결되는가",
            "핵심 결론을 뒷받침하는 source가 실행 근거에 없거나 다른 entity를 근거로 사용",
            _REPLY + _RETRIEVAL,
            {"rule": "material claim마다 ticket/document/comment/external provenance 확인"},
        ),
    ],
    "meeting": [
        _element(
            "meeting_identity_normalization",
            "safety_uncertainty",
            "@이름·{{이름:식별자}}·이름 일부+호칭을 실제 사용자로 정규화하고, 한 명으로 확정되지 않는 호칭은 후보를 제시해 인터뷰했는가",
            "모호한 호칭을 임의의 사용자로 확정하거나, 이미 식별 가능한 사람까지 되물어 진행을 막음",
            _REPLY + _RETRIEVAL,
            {
                "recognizedForms": ["@displayName", "{{displayName:identifier}}", "partial-name+title"],
                "ambiguousCandidates": ["skcc.x1103", "skcc.x1327"],
                "rule": "조회 후에도 단일 사용자로 확정되지 않을 때만 후보 인터뷰",
            },
        ),
        _element(
            "meeting_research_then_interview",
            "safety_uncertainty",
            "회의록의 기술어·내부 약어·히스토리를 Jira·Confluence·comment·외부 자료로 먼저 조사하고, 그래도 행동에 필요한 뜻·범위·소유자·기한이 불명확하면 추가 인터뷰했는가",
            "조사 가능한 내용을 곧바로 되묻거나, 조사 후에도 남은 핵심 공백을 추측해 결론·write 초안을 만듦",
            _REPLY + _RETRIEVAL,
            {
                "order": ["internal-research", "safe-external-research", "interview-unresolved-only"],
                "noDraftBeforeResolution": True,
                "resumeWithoutRepeatingResolvedQuestions": True,
            },
        ),
        _element(
            "meeting_decision_to_action_boundary",
            "contract_actionability",
            "회의의 결정·담당·기한·미결을 구분하고 create/comment/update 요청은 확정된 항목만 정확한 승인 payload로 만들었는가",
            "미결 사항을 결정으로 바꾸거나 댓글·필드·대상 범위를 섞거나 승인 전에 실행",
            _REPLY + ["evaluationEvidence.requestPlan", "evaluationEvidence.queryPlan"],
            {"rule": "결정표와 pending action·target·field·value를 대조", "execute": False},
        ),
    ],
    "ctx-chg": [
        _element(
            "ctx_latest_request_precedence",
            "request_fulfillment",
            "대화 중 요청이 바뀌면 마지막 유효 요청을 우선하고 취소·대체된 목표와 write 초안을 폐기했는가",
            "이전 요청의 target·field·comment를 최종 답변이나 pending payload에 남김",
            _REPLY + ["evaluationEvidence.requestPlan"],
            {"rule": "각 turn의 취소·대체 표현과 final payload를 역순 대조"},
        ),
        _element(
            "ctx_relevant_memory_selection",
            "factual_grounding",
            "과거 대화와 수집 근거 중 최신 요청에 필요한 정보만 유지하고 무관한 주제·사람·수치를 섞지 않았는가",
            "이전 조사 결과를 새 대상의 사실이나 근거처럼 재사용해 결론이 오염됨",
            _REPLY + _RETRIEVAL,
            {"rule": "최종 요청에 필요한 entity/source와 실제 답변·근거의 교집합 검토"},
        ),
        _element(
            "ctx_pending_replacement",
            "contract_actionability",
            "write 요청이 변경될 때 이전 pending action을 누적하지 않고 마지막 action 하나의 정확한 payload만 만들었는가",
            "취소된 변경값·댓글·대상이 최종 pending에 함께 남거나 복수 action이 충돌",
            _REPLY,
            {"rule": "final pending은 마지막 요청의 target·action·fields만 포함"},
        ),
    ],
    "editor": [
        _element(
            "editor_context_preservation",
            "request_fulfillment",
            "사용자 prompt·seed·ticket context의 핵심 의미와 수치를 결과 본문에 보존했는가",
            "사용자가 준 핵심 문장·수치·목적을 삭제하거나 다른 내용으로 대체",
            _EDITOR,
            {"rule": "입력 visible text와 결과 visible text를 대조"},
        ),
        _element(
            "editor_reference_fidelity",
            "communication_rendering",
            "ticket·person·document 참조가 해석 결과와 일치하고 올바른 marker/link로 렌더되는가",
            "resolved reference를 미확인으로 표시하거나 marker/HTML 중첩으로 식별 불가",
            _EDITOR,
            {"rule": "references와 html/note의 entity·resolved 상태를 대조"},
        ),
    ],
    "create": [
        _element(
            "create_request_to_payload",
            "contract_actionability",
            "원 요청과 후속 답변의 확정 조건이 최종 reply·pending payload에 같은 값으로 반영됐는가",
            "사용자가 확정한 대상·type·parent·변경값과 다른 payload 생성",
            _REPLY + ["evaluationEvidence.requestPlan", "evaluationEvidence.queryPlan"],
            {"rule": "turn별 확정값을 final payload field와 대조"},
        ),
        _element(
            "create_interview_boundary",
            "safety_uncertainty",
            "필수 입력만 질문하고 답변 전에는 해당 값을 발명한 draft·write payload를 만들지 않았는가",
            "필수값을 추정하거나 내부 조회·위임으로 해결 가능한 값을 되물어 진행 차단",
            _REPLY,
            {"rule": "각 turn의 questions, pending, 다음 turn 반영을 순서대로 검토"},
        ),
        _element(
            "create_domain_shape",
            "contract_actionability",
            "issue type·parent·Done 상태·field 조합이 LTM/Jira 생성·수정 규칙상 유효한가",
            "금지된 hierarchy, Done field 변경, reply와 payload type 불일치",
            _REPLY,
            {"rule": "Epic → Task-tier → Sub-Task 및 action별 허용 field 대조"},
        ),
    ],
}


CASE_REVIEW_SPECS = {
    "conversation": {
        "S1-생성": _case(
            "Iceberg Puffin NDV PoC를 사용자가 정한 Batch Job과 단계별 Sub-Task로 구체화",
            _element(
                "s1_external_technology_research",
                "factual_grounding",
                "Iceberg Puffin NDV라는 외부 기술 주장의 근거가 필요할 때 어떤 일반 기술어로 외부 검색했고 무엇을 확인했는가",
                "호환성·효과·표준 동작을 주장하면서 외부 검색 시도·URL·실패 한계가 모두 없음",
                _RETRIEVAL,
                {
                    "requiredSourceClasses": ["internal", "web-or-github-attempt"],
                    "externalQueryTermsAny": ["Iceberg", "Puffin", "NDV", "statistics"],
                    "forbiddenExternalTokens": ["DL-", "username", "private project name"],
                },
            ),
            _element(
                "s1_poc_scope",
                "request_fulfillment",
                "PoC, Lake 내 Iceberg 배치적재 테이블, 통계 생성 Batch Job, 단계별 Sub-Task가 모두 유지됐는가",
                "핵심 산출물 또는 단계 분할을 누락하거나 다른 목표로 대체",
                _REPLY,
                {"requiredConcepts": ["PoC", "Iceberg", "Batch Job", "Sub-Task stages"]},
            ),
        ),
        "S2-버그": _case(
            "리니지 뷰어 2홉 빈 화면을 재현 가능한 Bug 초안으로 변환",
            _element(
                "s2_reproduction_fidelity",
                "request_fulfillment",
                "Chrome, 2홉 이상 확장, 빈 화면, 기대 그래프 렌더가 재현·기대·실제로 분리됐는가",
                "재현 단계나 기대/실제 중 하나를 누락해 Bug를 재현할 수 없음",
                _REPLY,
                {"requiredConcepts": ["Chrome", "2 hops", "blank screen", "graph rendered"]},
            ),
        ),
        "S3-이력": _case(
            "fdc.fdc_trace_summary_ic의 최초 요청부터 현재 진행까지 완전한 시간순 이력 재구성",
            _element(
                "s3_history_ticket_coverage",
                "factual_grounding",
                "이력에 필요한 티켓 DL-9041~DL-9047과 DL-9062가 각각 어떤 사건으로 언급됐는가",
                "중요 변화 티켓을 누락하거나 무관 티켓을 이력에 포함해 흐름을 왜곡",
                _REPLY + _RETRIEVAL,
                {
                    "requiredTicketKeys": [
                        "DL-9041", "DL-9042", "DL-9043", "DL-9044",
                        "DL-9045", "DL-9046", "DL-9047", "DL-9062",
                    ],
                    "forbiddenUnrelatedTickets": True,
                },
            ),
            _element(
                "s3_history_event_sequence",
                "factual_grounding",
                "신규 요청·Job 개발·지연·주기 변경·schema 변경·catalog 등록·모니터링·정합성 비교를 날짜순으로 구분했는가",
                "사건 순서나 현재/과거 상태를 뒤바꿔 사용자가 잘못된 현재 상태를 이해",
                _REPLY,
                {
                    "requiredMilestones": [
                        "request", "job implementation", "delay incident", "2h-to-30m change",
                        "CHAMBER_ID", "catalog", "monitoring", "metric reconciliation",
                    ]
                },
            ),
        ),
        "S4-사람": _case(
            "이다은의 현재 업무를 전체 범위와 상태를 보존해 설명",
            _element(
                "s4_person_work_completeness",
                "factual_grounding",
                "동명이인 해소 후 현재 미완료 할당 전체 건수와 티켓을 제시하고 완료·다른 사람 업무를 제외했는가",
                "일부 티켓만 보여 주거나 완료 티켓·최근 활동·다른 사람 업무를 포함",
                _REPLY + _RETRIEVAL,
                {"requires": ["resolved person mention", "total unfinished count", "all unfinished assignments"],
                 "forbidden": ["completed assignments", "unrelated activity"]},
            ),
        ),
        "S5-내일": _case(
            "현재 사용자가 시작할 한 가지 최우선 업무를 근거와 함께 결정",
            _element(
                "s5_priority_decision",
                "request_fulfillment",
                "priority·overdue·status를 함께 비교해 하나의 최우선 티켓과 선택 이유를 제시했는가",
                "후보만 나열하고 결정을 하지 않거나 낮은 우선순위를 근거 없이 먼저 추천",
                _REPLY + _RETRIEVAL,
                {"requires": ["one primary recommendation", "priority", "due/overdue", "status"]},
            ),
        ),
        "S6-진척": _case(
            "DL-9090의 자식 진행률과 남은 작업을 source conflict까지 반영해 설명",
            _element(
                "s6_progress_evidence",
                "factual_grounding",
                "하위 티켓 완료/진행 수, 남은 티켓, Jira 상태와 댓글·문서 완료 보고의 충돌을 구분했는가",
                "댓글 보고만으로 In Progress 티켓을 완료 처리하거나 child 집계를 잘못 계산",
                _REPLY + _RETRIEVAL,
                {"requires": ["child count", "remaining ticket", "Jira state", "source conflict"]},
            ),
        ),
        "S7-내외부조사": _case(
            "Iceberg Puffin NDV 적용 가능성을 내부 이력과 외부 공식 자료를 분리·연결해 조사",
            _element(
                "s7_internal_research_coverage",
                "factual_grounding",
                "내부 Jira·Confluence·comment에서 기존 PoC·설계·운영 제약을 어떤 검색어와 source로 조사했는가",
                "내부 적용 가능성을 말하면서 내부 source 검색·근거가 없음",
                _RETRIEVAL,
                {
                    "requiredSourceClasses": ["jira", "confluence-or-comment"],
                    "internalQueryTermsAny": ["Iceberg", "Puffin", "NDV", "통계"],
                    "requiredTicketKeys": ["DL-7001"],
                    "requiredDocumentTitles": ["[Lake] Iceberg Puffin NDV 적용 검토 노트"],
                    "requiredInternalFacts": [
                        "candidate tables 20", "writer version checked",
                        "PoC not run", "StarRocks consumption unconfirmed",
                    ],
                },
            ),
            _element(
                "s7_external_research_coverage",
                "factual_grounding",
                "외부 공식 문서나 신뢰 가능한 기술 자료를 일반화된 검색어로 조회하고 URL·핵심 주장을 남겼는가",
                "외부 지식이 핵심인데 web/GitHub 검색 시도도, 차단 사실도, 외부 URL도 없음",
                _RETRIEVAL,
                {
                    "requiredSourceClasses": ["web-or-github-attempt"],
                    "externalQueryTermsAny": ["Apache Iceberg", "Puffin", "NDV statistics"],
                    "forbiddenExternalTokens": ["DL-", "username", "private project name"],
                },
            ),
            _element(
                "s7_internal_external_separation",
                "communication_rendering",
                "내부 확인 사실·외부 일반 지식·LTM 적용 inference·추후 확인 gap을 구분해 산출했는가",
                "외부 일반론을 내부 구현 완료 사실처럼 섞어 잘못된 의사결정을 유도",
                _REPLY,
                {"requiredSections": ["내부 근거", "외부 근거", "판단", "확인 필요"]},
            ),
        ),
        "S8-복합근거품질": _case(
            "Jira ticket/comment·Confluence·외부 공식 문서를 함께 조사한 의사결정 근거의 결과·신뢰도·적합성·실제 렌더링을 평가",
            _element(
                "s8_source_results",
                "factual_grounding",
                "각 source에서 무엇을 발견했으며 그 발견이 DL-7001·Puffin 검토 문서·관련 댓글·외부 공식 문서의 실제 내용과 일치하는가",
                "핵심 source class를 누락하거나 source에 없는 결과를 만들어 운영 적용 판단을 바꿈",
                _REPLY + _RETRIEVAL,
                {
                    "requiredSourceClasses": ["jira-ticket", "jira-comment", "confluence", "official-web"],
                    "requiredTicketKeys": ["DL-7001"],
                    "requiredDocumentTitles": ["[Lake] Iceberg Puffin NDV 적용 검토 노트"],
                    "requiredFindings": [
                        "candidate tables 20 and writer version in the older design note",
                        "newer writer result for five samples in a Jira comment",
                        "older PoC-not-run note preserved as a dated conflict rather than current truth",
                        "StarRocks consumption unconfirmed", "official Puffin/NDV behavior",
                    ],
                },
            ),
            _element(
                "s8_source_confidence_and_fitness",
                "safety_uncertainty",
                "출처의 직접성·권위·최신성·내부 적용 범위를 근거로 신뢰도와 요청 적합성을 과신 없이 평가했는가",
                "외부 일반론을 내부 구현 증거로 취급하거나 단일·간접·오래된 source를 확정 근거로 과신",
                _REPLY + _RETRIEVAL,
                {
                    "confidenceFactors": ["authority", "directness", "recency", "corroboration"],
                    "fitnessFactors": ["claim coverage", "internal applicability", "decision impact"],
                    "requiredOpenFacts": ["PoC result", "StarRocks actual consumption"],
                    "forbiddenInference": "external specification proves internal production readiness",
                },
            ),
            _element(
                "s8_single_source_index",
                "communication_rendering",
                "본문 marker와 하나의 `### 근거` 인덱스가 연결되고 같은 source의 여러 발견은 같은 정수 아래 `[n-a]`·`[n-b]`로 묶였는가",
                "근거·참조·관련 문서가 별도 영역으로 갈라지거나 같은 source가 여러 번호를 받아 claim-source 관계를 추적할 수 없음",
                _REPLY,
                {
                    "exactEvidenceHeadings": 1,
                    "forbiddenHeadings": ["참조", "관련 문서", "시스템 근거"],
                    "oneIntegerPerSource": True,
                    "multipleFindingsUse": ["[n-a]", "[n-b]"],
                    "citationClusters": "multiple sources at one location use [4][5][10]",
                    "everyCitationBracketHyperlinked": True,
                    "sourceKinds": ["ticket-detail badge", "Confluence link", "web link"],
                },
            ),
            _element(
                "s8_visual_rendering",
                "communication_rendering",
                "실제 LTM UI에서 source index·하위 발견·ticket detail badge·문서/웹 link·본문 marker가 겹침이나 파손 없이 읽히고 동작하는가",
                "raw Markdown은 맞아도 실제 화면에서 badge/code 중첩, 중복 panel, 끊긴 link, 잘못된 marker 이동으로 근거를 확인할 수 없음",
                ["output.reply", "manualUi.desktopScreenshot", "manualUi.narrowScreenshot", "manualUi.interactionNotes"],
                {
                    "requiredViewports": ["desktop", "narrow-with-agent-side-panel"],
                    "requiredInteractions": ["marker jump/highlight", "ticket badge detail", "document/web link"],
                    "forbiddenRendering": ["duplicate evidence panel", "badge-code overlap", "orphan marker", "clipped source text"],
                    "artifactPolicy": "screenshots stay under ignored .cache; concise findings go in the evaluation report",
                },
            ),
        ),
    },
    "meeting": {
        "MTG1": _case(
            "회의록을 내부·외부 근거로 보강하고 모호한 사람·약어를 인터뷰한 뒤 결정·담당·기한·미결을 요약",
            _element(
                "mtg1_source_coverage",
                "factual_grounding",
                "DL-7001·Puffin 검토 문서·관련 댓글과 Iceberg/StarRocks 공식 자료를 실제로 조사하고 내부 사실과 외부 일반 지식을 구분했는가",
                "핵심 기술 판단을 하면서 내부 이력 또는 외부 공식 자료 시도가 없거나 사내 식별자를 외부 검색어로 전송",
                _REPLY + _RETRIEVAL,
                {
                    "requiredTicketKeys": ["DL-7001"],
                    "requiredDocumentTitles": ["[Lake] Iceberg Puffin NDV 적용 검토 노트"],
                    "externalQueryTermsAny": ["Apache Iceberg", "Puffin", "NDV", "StarRocks"],
                    "forbiddenExternalTokens": ["DL-", "skcc."],
                },
            ),
            _element(
                "mtg1_decision_summary",
                "request_fulfillment",
                "5개 표본·운영 반영 보류·세 담당자의 기한·PSR 통과 조건과 미확인 위험을 빠짐없이 구분했는가",
                "결정·담당·기한 중 하나를 누락하거나 검증 전 StarRocks 지원을 확정",
                _REPLY,
                {"requires": ["5 tables", "hold production", "three owners and due dates", "PSR threshold", "open risks"]},
            ),
        ),
        "MTG2": _case(
            "모호한 담당자·RGP를 인터뷰로 확정한 뒤 Epic DL-9200 아래 Task 세 건의 승인 초안 생성",
            _element(
                "mtg2_task_mapping",
                "contract_actionability",
                "세 작업의 담당자·기한·범위·완료 조건과 parent가 회의 결정 그대로 payload에 매핑됐는가",
                "건수·담당자·기한·parent 오류 또는 회의록에 없는 필드 발명",
                _REPLY,
                {
                    "itemCount": 3,
                    "parent": "DL-9200",
                    "assignees": ["skcc.i2011", "skcc.x1402", "skcc.x1103"],
                    "dueDates": ["2026-08-22", "2026-08-25", "2026-08-28"],
                    "forbiddenUndecidedFields": ["component", "priority", "labels"],
                },
            ),
        ),
        "MTG3": _case(
            "모호한 최종 검토자를 인터뷰한 뒤 관련 두 Task에만 회의 결정 댓글 승인 초안 생성",
            _element(
                "mtg3_comment_scope",
                "contract_actionability",
                "DL-9201·DL-9202에만 comment-only payload를 만들고 DL-7001과 field 변경은 제외했는가",
                "대상 누락·추가, DL-7001 댓글, field 변경, 검토자 오식별",
                _REPLY,
                {"exactTargets": ["DL-9201", "DL-9202"], "forbiddenTarget": "DL-7001",
                 "commentOnly": True, "semanticAction": "add_ticket_comments", "reviewer": "skcc.x1327"},
            ),
        ),
        "MTG4": _case(
            "모호한 기준 소유자·약어를 인터뷰한 뒤 DL-9203의 지정 필드와 본문만 변경",
            _element(
                "mtg4_exact_update",
                "contract_actionability",
                "제목·priority·due·labels 전체값·세 본문 section만 실제 변경하고, 이미 같은 component는 제외했는가",
                "지정값 오류, 동일값 component 재기록, 추가 field 변경, labels 병합, 댓글 생성 또는 미확인 StarRocks 지원 확정",
                _REPLY,
                {
                    "target": "DL-9203",
                    "exactFields": ["summary", "priority", "duedate", "labels", "description"],
                    "unchangedFields": ["components"],
                    "forbidComment": True,
                    "owner": "skcc.x1103",
                },
            ),
        ),
        "MTG5": _case(
            "내부 자료 조사로도 확정되지 않는 부분 이름·호칭과 회의 한정 약어를 인터뷰한 뒤 Task 초안 재개",
            _element(
                "mtg5_research_gap_interview",
                "safety_uncertainty",
                "준서TL 후보 두 명과 PSR 의미 공백을 구체적으로 질문하고 답변 전 초안을 만들지 않은 뒤 확정값을 최종 payload에 반영했는가",
                "단일 사용자·약어 뜻을 추측하거나 첫 turn에 draft 생성, 답변 후에도 확정값 누락",
                _REPLY + _RETRIEVAL,
                {
                    "requiredCandidates": ["skcc.x1103", "skcc.x1327"],
                    "unresolvedTerm": "PSR",
                    "noDraftBeforeChoice": True,
                    "finalAssignee": "skcc.x1103",
                    "reviewer": "skcc.x1042",
                },
            ),
        ),
    },
    "ctx-chg": {
        "CTX1": _case(
            "fdc 이력 조사에서 완전히 다른 DL-9203 priority-only 변경으로 전환",
            _element(
                "ctx1_unrelated_switch",
                "request_fulfillment",
                "최종 payload가 DL-9203 priority=P2-Major 하나뿐이고 fdc·DL-904x 맥락을 출력하지 않는가",
                "이전 데이터셋 조사 내용 또는 추가 변경 field가 최종 결과에 남음",
                _REPLY,
                {"target": "DL-9203", "exactChanges": {"priority": "P2-Major"}, "forbidden": ["fdc_trace_summary_ic", "DL-904"]},
            ),
        ),
        "CTX2": _case(
            "공유된 fdc 점검 정보를 답변 대상이 아닌 메모로만 취급하고 DL-9090 진행상황 조회",
            _element(
                "ctx2_shared_info_boundary",
                "factual_grounding",
                "DL-9090과 세 하위 Task의 상태·남은 작업만 설명하고 2026-08-24 fdc 정보를 근거로 섞지 않았는가",
                "공유 정보가 새 요청의 사실·일정·권장행동으로 오염되거나 하위 Task 누락",
                _REPLY + _RETRIEVAL,
                {"requiredTicketKeys": ["DL-9090", "DL-9093", "DL-9094", "DL-9095"], "forbidden": ["2026-08-24", "fdc"]},
            ),
        ),
        "CTX3": _case(
            "필드 변경→댓글→제목 변경으로 뒤집힌 요청에서 마지막 제목 변경만 유지",
            _element(
                "ctx3_superseded_writes",
                "contract_actionability",
                "priority·due·댓글 초안을 모두 폐기하고 DL-9203 summary 변경 하나만 최종 pending에 남겼는가",
                "취소된 값 또는 action이 final pending/reply에 하나라도 남음",
                _REPLY,
                {"target": "DL-9203", "exactChanges": {"summary": "[Catalog] Puffin NDV 결과 템플릿 정리"}, "forbidden": ["P1-Critical", "2026-08-31", "comment"]},
            ),
        ),
        "CTX4": _case(
            "DL-9090→이다은 업무→DL-9090으로 복귀해 남은 하위 Task에만 comment-only 초안 생성",
            _element(
                "ctx4_return_to_prior_topic",
                "contract_actionability",
                "현재 Jira 상태로 남은 DL-9095만 골라 comment-only payload를 만들고 중간 사람 조회를 섞지 않았는가",
                "완료 하위 Task 포함, DL-9095 누락, field 변경 또는 이다은 업무 맥락 잔존",
                _REPLY + _RETRIEVAL,
                {"exactTargets": ["DL-9095"], "commentOnly": True,
                 "semanticAction": "add_ticket_comment",
                 "middleTurnRequires": ["skcc.i2011", "current unfinished assignments"],
                 "middleTurnForbidden": ["DL-9090", "DL-9095"],
                 "finalForbidden": ["skcc.i2011", "이다은"]},
            ),
        ),
    },
    "editor": {
        "CMP1": _case("DL-9090 최근 진행 코멘트의 source conflict를 안전하게 이어 씀",
            _element("cmp1_progress_conflict", "factual_grounding", "완료 보고와 Jira In Progress 상태를 구분했는가", "미완료 티켓을 완료로 확정", _EDITOR, {"requires": ["completion report", "Jira In Progress", "confirmation caveat"]})),
        "CMP2": _case("기존 본문을 배경·범위·작업·DoD 4섹션으로 보강",
            _element("cmp2_four_sections", "request_fulfillment", "요구된 4개 섹션을 맥락에 맞게 작성하고 임의 성능 목표를 넣지 않았는가", "필수 섹션 누락 또는 미요청 API/성능 목표 발명", _EDITOR, {"requiredSections": ["배경", "범위", "작업", "DoD"]})),
        "CMP3": _case("작성 중 seed를 보존하고 미확정 비교 방향을 발명하지 않음",
            _element("cmp3_seed_exactness", "request_fulfillment", "seed의 p95 측정 문맥을 그대로 보존하고 높음/낮음은 확인 필요로 남겼는가", "seed를 대체하거나 문법만으로 p95 방향·원인을 확정", _EDITOR, {"requiresExactVisibleSeed": True, "requires": ["confirmation marker"], "forbiddenInference": ["higher", "lower", "cause"]})),
        "CMP4": _case("정보가 없는 Editor 요청에서 본문을 발명하지 않고 목적·대상을 질문",
            _element("cmp4_need_info", "safety_uncertainty", "대상과 목적을 묻는 NEED_INFO를 반환했는가", "정보 없이 본문·댓글을 생성", _EDITOR, {"requires": ["question", "no generated body"]})),
        "CMP5": _case("짧은 상태 공유에서도 미완료를 완료로 뒤집지 않음",
            _element("cmp5_status_safety", "factual_grounding", "Jira 상태를 우선 보존하고 충돌 문장을 중복하지 않았는가", "In Progress를 완료로 확정", _EDITOR, {"requires": ["Jira state preserved", "no duplicate caveat"]})),
        "CMP6": _case("담당자 검토 요청을 mention badge와 구체 검토 대상으로 작성",
            _element("cmp6_review_target", "communication_rendering", "담당 mention, 2홉 100노드 측정 기준, 실제 설계 문서 link가 정상 렌더됐는가", "평문 인명·깨진 link 또는 기준·문서 누락", _EDITOR, {"requires": ["person mention", "2-hop 100-node measurement", "resolved document reference"]})),
        "CMP7": _case("티켓과 무관한 요청에서 맥락 확인",
            _element("cmp7_relevance_guard", "safety_uncertainty", "현재 티켓에 쓸 목적·종류를 질문하고 무관한 글을 만들지 않았는가", "무관한 내용을 현재 티켓 본문/댓글로 생성", _EDITOR, {"requires": ["context question", "no body"]})),
        "CMP8": _case("부모 본문은 목적·범위를 설명하고 자식 실행 세부를 반복하지 않음",
            _element("cmp8_parent_child_boundary", "request_fulfillment", "부모의 목적·범위와 자식 책임을 분리했는가", "자식 제목·완료 작업을 부모 DoD에 그대로 반복", _EDITOR, {"requires": ["parent why/scope", "no child-title repetition"]})),
        "CMP9": _case("짧은 지시라도 현재 티켓 맥락만으로 간결한 본문 작성",
            _element("cmp9_ticket_context", "factual_grounding", "현재 티켓에서 확인된 기능·검증·문서만 사용했는가", "사용자 만족·UX 효과 등 미확인 효익 발명", _EDITOR, {"forbiddenClaims": ["user satisfaction", "positive feedback", "unverified UX benefit"]})),
    },
    "create": {
        "ONE1": _case("Workbench 쿼리 편집기 팝업을 단일 작업으로 작성", _element("one1_scope", "request_fulfillment", "단일 UI 변경의 대상·행동을 보존하고 무관 참조·효익을 넣지 않았는가", "다른 컴포넌트나 미확인 사용자 효과를 핵심 범위로 추가", _REPLY, {"requires": ["Workbench", "query editor", "popup", "single item"]})),
        "ONE2": _case("작은 checkbox 수정을 과잉 분해하지 않음", _element("one2_atomicity", "request_fulfillment", "하나의 Task-tier 항목으로 끝내고 요청한 표시·필터 동작을 유지했는가", "여러 티켓으로 분해하거나 다른 목표 추가", _REPLY, {"itemCount": 1})),
        "STR1": _case("30명을 15명씩 두 Sub-Task로 균등 분할", _element("str1_partition", "contract_actionability", "두 자식의 인원 합계 30, 중복·누락 없음, 각 15명인가", "합계·중복·누락 오류로 담당 범위가 틀림", _REPLY, {"childCount": 2, "partitionSizes": [15, 15], "total": 30})),
        "STR2": _case("명시된 세 산출물을 위임된 구조로 정확히 한 번씩 분리", _element("str2_module_partition", "request_fulfillment", "성능 측정·인덱스 조정·사용 가이드를 각각 정확히 한 Task로 만들고, 요청하지 않은 평가 Task·단계 Sub-Task·Epic·label을 추가하지 않았는가", "산출물 누락·중복, 평가와 실행의 이중 티켓화, 미요청 하위 작업, 잘못된 모듈/parent 또는 제약 발명", _REPLY, {"exactTopLevelItems": 3, "requiredDeliverables": ["performance measurement", "index adjustment", "user guide"], "forbidden": ["necessity-assessment duplicate", "unrequested children", "arbitrary epic", "unrequested label", "duplicated DoD"]})),
        "STR3": _case("근거 없는 새 Epic 대신 기존 Epic 아래 보수적 배치", _element("str3_existing_epic", "safety_uncertainty", "DL-102 재사용 가능성을 확인하고 중복 Epic을 만들지 않았는가", "기존 범위를 무시하고 새 Epic 생성", _REPLY + _RETRIEVAL, {"preferredExistingParent": "DL-102"})),
        "PAR1": _case("DL-9090 아래 역할별 Sub-Task를 지정 담당자에게 배치", _element("par1_assignment_mapping", "contract_actionability", "성능·가이드·회귀 작업과 세 담당자의 매핑·parent가 정확한가", "작업-담당 매핑이나 parent 오류", _REPLY, {"parent": "DL-9090", "requiresDistinctAssignments": True})),
        "PAR2": _case("사용자가 지목한 Epic을 그대로 사용", _element("par2_epic_fidelity", "contract_actionability", "지목 Epic의 key·실제 제목을 조회해 payload에 보존했는가", "placeholder 제목이나 다른 Epic 사용", _REPLY + _RETRIEVAL, {"requires": ["exact epic key", "resolved epic title"]})),
        "SUB1": _case("Sub-Task를 parent로 쓰지 않고 합법적 대안 질문", _element("sub1_legal_parent", "safety_uncertainty", "대상이 Sub-Task임을 확인하고 형제/최상위 Task/취소 선택을 물었는가", "Sub-Task 아래 자식 생성", _REPLY + _RETRIEVAL, {"requiresOptions": ["sibling under actual parent", "top-level Task", "cancel"]})),
        "SUB2": _case("기존 Task의 현재 자식과 겹치지 않는 새 Sub-Task 추가", _element("sub2_child_dedup", "factual_grounding", "기존 자식을 조회하고 중복 없이 요청한 자식만 추가했는가", "기존 자식과 중복 또는 범위 밖 배포 작업 추가", _REPLY + _RETRIEVAL, {"requires": ["existing-child lookup", "no duplicate child"]})),
        "SUB3": _case("여러 대상이 모두 Sub-Task이면 생성 보류", _element("sub3_multi_parent_legality", "safety_uncertainty", "각 대상의 type/parent를 확인하고 합법적 대안을 질문했는가", "어느 한 Sub-Task 아래라도 자식 생성", _REPLY + _RETRIEVAL, {"requires": ["all targets resolved", "no draft", "legal alternatives"]})),
        "PASTE1": _case("VoC를 재현 가능한 Bug 언어로 변환", _element("paste1_voc_conversion", "request_fulfillment", "사용자 불편 문장을 재현·기대·실제로 구조화하고 그대로 복사하지 않았는가", "VoC를 재현/기대에 반복해 실행 불가", _REPLY, {"requiresSections": ["reproduction", "expected", "actual"]})),
        "PASTE2": _case("장애 대화록의 DAG·시간·재실행 사실을 Bug로 보존", _element("paste2_transcript_fidelity", "factual_grounding", "대화록의 정확한 DAG/시각/재발/재실행 결과를 보존했는가", "대화록에 없는 원인·module을 발명하거나 반복 정보를 누락", _REPLY, {"requires": ["DAG identity", "time", "recurrence", "rerun result"]})),
        "ASKD1": _case("작업 대상·범위가 없으면 위임으로 발명하지 않고 질문", _element("askd1_required_scope", "safety_uncertainty", "대상·범위·규칙 중 생성에 필요한 값을 묻고 draft를 보류했는가", "Catalog·scope·담당·참조를 임의 생성", _REPLY, {"requires": ["scope question", "no draft"]})),
        "ASKD2": _case("parent만 주어진 요청에서 작업 내용을 질문 후 정확히 반영", _element("askd2_progressive_answer", "safety_uncertainty", "첫 turn에는 질문만 하고 둘째 turn의 회귀 테스트를 DL-9090 Sub-Task로 반영했는가", "첫 turn 발명 항목을 답변 후에도 유지하거나 parent를 바꿈", _REPLY, {"turn1": "question/no draft", "turn2": "DL-9090 regression Sub-Task"})),
        "ASKD3": _case("댓글 내용·목적이 없으면 질문", _element("askd3_comment_content", "safety_uncertainty", "전달할 내용·목적을 묻고 빈 update/comment plan을 만들지 않았는가", "댓글 내용을 발명하거나 빈 plan 생성", _REPLY, {"requires": ["comment-purpose question", "no pending"]})),
        "AMB1": _case("동명이인을 username 후보로 명확히 식별", _element("amb1_identity_choice", "safety_uncertainty", "test.same01/test.same02 후보를 제시하고 선택 전 assignee 변경을 보류했는가", "무관 username을 임의 선택", _REPLY + _RETRIEVAL, {"requiredCandidates": ["test.same01", "test.same02"], "noDraftBeforeChoice": True})),
        "ASK1": _case("범위가 없는 생성 요청에서 목표 대상을 먼저 질문", _element("ask1_target_first", "safety_uncertainty", "무엇을 만들지 결정할 target/scope를 질문하고 placement 같은 후순위 질문을 미뤘는가", "대상 없이 초안 생성하거나 Epic만 질문", _REPLY, {"requiredQuestion": "target or scope"})),
        "ASK2": _case("여러 turn의 필수정보를 순차 수집해 마지막에만 초안", _element("ask2_progressive_interview", "safety_uncertainty", "target을 포함한 필수정보가 모두 모일 때까지 draft를 보류하고 각 답을 최종 payload에 보존했는가", "target 없이 조기 draft 또는 이전 답 누락", _REPLY, {"requires": ["multi-turn questions", "no early draft", "all answers preserved"]})),
        "DUP1": _case("중복 후보 결정을 먼저 받고 불필요한 질문을 하지 않음", _element("dup1_decision_order", "safety_uncertainty", "DL-9072의 실제 key·제목·중복 근거를 보여 주고 기존 확장/별도 분리만 물었는가", "후보를 숨기거나 Epic·배경 등 후순위 질문 추가", _REPLY + _RETRIEVAL, {"maxQuestions": 1, "requiredCandidate": "DL-9072", "requires": ["exact title", "overlap reason", "extend-or-separate decision"]})),
        "ATTR1": _case("사용자가 지정한 priority·due·label과 mutation 값을 보존", _element("attr1_field_values", "contract_actionability", "모든 명시 field와 변경값이 정확히 payload에 들어갔는가", "threshold 등 핵심 변경값 누락·변형", _REPLY, {"requires": ["priority", "due", "label", "mutation value"]})),
        "ASKD4": _case("기존 속성이 충분해도 새 mutation 값이 없으면 질문", _element("askd4_missing_mutation", "safety_uncertainty", "변경할 threshold 값을 구체적으로 묻고 기존값을 임의 변경하지 않았는가", "핵심 변경값 없이 임의 update plan 생성", _REPLY, {"requiredQuestion": "new threshold value", "noPending": True})),
        "ATTR2": _case("새 label을 막지 않고 신규 값으로 명시", _element("attr2_new_label", "contract_actionability", "없는 label을 신규 값으로 보존하고 다른 속성을 발명하지 않았는가", "label을 누락·대체하거나 생성 불가로 잘못 거절", _REPLY, {"requires": ["exact requested label", "new-label indication"]})),
        "STARR1": _case("고유 기술어·계층·본문 규율을 reply와 payload에 일치", _element("starr1_cross_output", "contract_actionability", "StarRocks/Puffin/NDV, type, parent, child 수가 reply와 payload에서 동일한가", "reply는 Epic/Task인데 payload는 다른 type 또는 parent 없음", _REPLY, {"requires": ["topic terms", "same type", "legal parent", "same child structure"]})),
        "BUG1": _case("재현 정보가 없으면 질문", _element("bug1_reproduction_question", "safety_uncertainty", "이미 말한 실제 증상은 보존하고 화면 경로·브라우저/환경·조건/빈도만 한 질문으로 묻는가", "실제 증상을 다시 묻거나 여러 일반 질문, 또는 재현 정보 없이 draft 생성", _REPLY, {"maxQuestions": 1, "requiredQuestionTerms": ["path", "environment", "condition-or-frequency"], "preserveActual": "intermittently not visible", "noDraft": True})),
        "BUG2": _case("제공된 재현 정보로 Bug 작성", _element("bug2_bug_sections", "request_fulfillment", "재현·기대·실제와 관련 티켓을 분리해 보존했는가", "핵심 재현 정보 누락 또는 실제와 기대 혼합", _REPLY, {"requiredSections": ["reproduction", "expected", "actual", "related ticket"]})),
        "BUG3": _case("동일 증상 요청에서 중복·재현 대상을 먼저 확인", _element("bug3_identity_before_draft", "safety_uncertainty", "DAG/Job·환경·발생 시각·대표 오류 로그를 한 질문으로 묻고 실제 timeout 증상은 보존했는가", "일반 완료 조건/기대·실제를 되묻거나 곧바로 새 Bug 생성", _REPLY + _RETRIEVAL, {"maxQuestions": 1, "requiredQuestionTerms": ["DAG-or-Job", "environment", "occurrence time", "error log"], "noDraft": True})),
        "RULE1": _case("최상위 Sub-Task 요청을 합법적 구조로 교정", _element("rule1_legal_shape", "safety_uncertainty", "Task-tier parent 지정 또는 최상위 Task 전환을 질문했는가", "parent 없는 Sub-Task 생성", _REPLY, {"requiredOptions": ["select parent Task", "convert to top-level Task", "cancel"]})),
        "RULE2": _case("생성 payload에 Story Point를 넣지 않음", _element("rule2_story_point_guard", "contract_actionability", "요청을 설명하되 생성 payload에서 storyPoint/SP field를 제외했는가", "지원하지 않는 Story Point field 포함", _REPLY, {"forbiddenFields": ["storyPoint", "sp"]})),
    },
}


def review_specs(suite: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return the immutable suite elements and per-case review contracts."""
    return SUITE_REVIEW_ELEMENTS[suite], CASE_REVIEW_SPECS[suite]
