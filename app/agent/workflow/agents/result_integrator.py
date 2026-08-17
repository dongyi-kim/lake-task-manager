"""Result Integrator — 지금까지 나온 것을 **사람이 읽을 한 덩어리**로 만든다.

앞의 다섯 역할은 전부 구조화된 데이터를 내놓는다. 그걸 화면이 표로 그리기도 하지만, 대화창에는
결국 문장이 필요하다. 그 문장을 각 역할이 조금씩 쓰게 하면 말투가 다섯 개가 되고 중복이 생긴다.
그래서 **말하는 입은 하나로 모은다.**

들어온 갈래에 따라 할 말이 다르다:
  · 질문이었다  → 조사 결과로 답한다
  · 되물을 게 있다 → 상황을 요약하고 질문을 던진다
  · 초안이 섰다 → 상황·초안·담당자 근거·검증 결과를 정리하고 **승인을 요청**한다
  · 실행했다   → 만들어진 것과 **실패한 것**을 보고한다
"""

from __future__ import annotations

import json
import re as _re

from app.agent.workflow.agents.base import TextAgent
from app.agent.workflow.agents.work_architect import draft_text
from app.agent.prompts.roles import SYSTEM_RESULT_INTEGRATOR
from app.agent.workflow.evidence_index import canonicalize_evidence_index
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import (AgentState, Intent, Node, last_user_text, note,
                                      is_memory_only_request, request_text)


class ResultIntegrator(TextAgent):
    name = Node.RESULT_INTEGRATOR

    def system(self, state):
        return persona(state, SYSTEM_RESULT_INTEGRATOR, role_id=self.name)

    def _run(self, state):
        """완전한 deterministic 집계는 다시 LLM에 요약시키지 않는다.

        사람·티켓 목록을 이미 코드가 확정했는데 마지막 모델이 일부를 생략하거나 무관 티켓을
        덧붙이는 것이 이번 실패의 직접 원인이었다. 이 갈래는 아래 renderer가 곧 최종 답이다.
        """
        completion = state.get("assignment_completion") or {}
        if completion.get("kind") == "incomplete_assignees":
            return self.apply(state, {"text": _assignment_completion_reply(completion)})
        # 질문 폼만 있는 턴은 구조화된 질문과 필수 사유가 이미 최종 데이터다. 예전에는
        # 35B 모델에 이 데이터를 다시 서술시킨 뒤 apply()에서 그 답을 전부 버리고
        # `_question_only_reply`로 교체했다. 사용자에게 보이지도 않는 호출이 로컬 MLX에서
        # 2분 이상 걸렸으므로, 같은 결정적 renderer를 호출 전에 사용한다.
        questions = [q for q in (state.get("questions") or []) if isinstance(q, dict)]
        if questions and not _has_executable_payload(state):
            return self.apply({**state, "_deterministic_reply": True},
                              {"text": _question_only_reply(state, questions)})
        # 승인 대기 응답은 최종 payload를 사람이 검토하기 위한 설명이다. 이미 코드가
        # 확정한 카드 값을 LLM에 다시 요약시키면 필드·담당·수치가 달라지거나, 요청하지
        # 않은 삭제/후속 작업을 덧붙였다. 승인 문장은 payload의 결정적 projection으로 만든다.
        if state.get("approval_token") and _has_executable_payload(state):
            return self.apply({**state, "_deterministic_reply": True},
                              {"text": _approval_reply(state)})
        person_work = state.get("person_work_snapshot") or {}
        if person_work:
            return self.apply({**state, "_deterministic_reply": True},
                              {"text": _person_work_reply(person_work)})
        daily = state.get("daily_priority_snapshot") or {}
        if daily:
            return self.apply({**state, "_deterministic_reply": True},
                              {"text": _daily_priority_reply(daily)})
        if is_memory_only_request(state):
            return self.apply(state, {"text": "확인. 이 대화의 후속 요청에 필요한 경우에만 참고"})
        return super()._run(state)

    def task(self, state):
        intent = state.get("intent") or Intent.PLAN_WORK
        result, review = state.get("result") or {}, state.get("review") or {}
        qs = state.get("questions") or []

        if result:
            goal = ("Report execution in three to five concise Korean sentences. List each created item on "
                    "one line with its verified key and title, and report only actual failures with their exact "
                    "reason. Never invent a failure, follow-up, or warning absent from `created` or `failed`. "
                    "Do not warn again about a deliberate user decision such as top-level placement.")
        elif (state.get("draft") or {}).get("structure_tree"):
            # ── 뼈대 합의 턴 — **아직 초안이 아니다.** 본문이 없으니 본문 이야기를 하면
            #    사용자는 없는 것을 읽으려 한다. 보여 줄 것은 나무 하나와 고칠 방법뿐이다.
            goal = ("This is a structure-alignment turn, not a completed ticket draft. In Korean: explain the "
                    "split in two sentences; copy the following tree as an unchanged code block because "
                    "indentation carries hierarchy; then state in one line that items may be merged, split, "
                    "added, removed, or renamed. Do not write background, scope, or DoD before the structure "
                    "is accepted.\n\n"
                    "```\n" + str((state.get("draft") or {}).get("structure_tree")) + "\n```")
        elif qs and (state.get("interpretation") or "").strip():
            goal = ("This is a pre-research interpretation turn. In at most five Korean sentences, first show "
                    "Interpretation Data under `### 제가 이해한 바` without rewriting it, then ask the user to "
                    "answer the structured form. Name the actual next stage: `조사` for research or "
                    "`변경 카드 작성` for a mutation request.")
        elif qs:
            goal = ("Summarize the established situation in two or three Korean sentences. The structured "
                    "form renders every question and option; do not repeat or number them in prose and do not "
                    "ask anything outside the form. End with the concise Korean line `아래에서 선택해 주세요`.")
        elif (state.get("change_plan") or {}).get("keys") \
                and not ((state.get("change_plan") or {}).get("changes") or {}) \
                and ((state.get("change_plan") or {}).get("comment")
                     or (state.get("change_plan") or {}).get("comments")):
            n = len((state.get("change_plan") or {}).get("keys") or [])
            goal = (f"Present the Korean comment-only approval draft for exactly {n} tickets. State that no "
                    "ticket field or status will change. Use a `| 티켓 | 제목 | 작업 |` table with one row "
                    "per target and write `댓글 추가` in the action column. Never describe current status as "
                    "a planned status change. Request approval and make clear nothing has been posted yet.")
        elif (state.get("change_plan") or {}).get("keys"):
            n = len(state.get("change_plan", {}).get("keys") or [])
            # ★ 자르기 안내는 **정말 자를 때만** 싣는다(사용자 관점 리뷰 F4).
            #   예전엔 건수와 무관하게 실려서, 2건짜리 계획에 모델이 그대로 받아
            #   "나머지 0건은 승인 카드에서 확인 가능합니다"라고 썼다 — 0건이라는 말은
            #   읽는 사람을 멈춰 세울 뿐 아무것도 알려 주지 않는다.
            #   **빈 수치를 문장으로 만들지 않는다**: 조건이 안 서면 그 문장 자체가 없어야 한다.
            cut = (f" The table is long: show the first ten rows and state in Korean that the remaining "
                   f"{n - 10} rows are visible on the approval card." if n > 10 else "")
            goal = (f"Present the {n}-ticket bulk change in Korean and request approval. Show the exact target "
                    "snapshot in a `| 티켓 | 제목 | 변경 |` table so the user knows what is being approved. "
                    "Include every row when there are at most ten and include the verified title, not only the "
                    "key." + cut + " Make clear that nothing has changed yet.")
        elif (state.get("change_plan") or {}).get("key"):
            goal = ("In Korean, summarize which verified ticket and fields the plan would change, request "
                    "approval, and make clear that nothing has changed yet.")
        elif state.get("draft", {}).get("items"):
            n_items = len(state.get("draft", {}).get("items") or [])
            goal = ("Organize the Korean response as situation, ticket draft, assignment evidence, and "
                    "validation result; request approval at the end. Make clear that no ticket has been created "
                    "and never write `만들었습니다`."
                    + ("\nFor multiple draft items, show every item in a `| # | 제목 | 모듈 | Epic | 마감 |` "
                       "table; never describe only the first item."
                       if n_items > 1 else ""))
        elif state.get("ticket_progress"):
            # 진척 질문에 "In Progress 입니다"는 답이 아니다 — 무엇이 끝났고 무엇이 남았는지를
            # 근거(코멘트·변동·하위 티켓·결과 문서)와 함께 시간순으로 서술한다.
            goal = ("Report ticket progress in Korean: current completion including child counts and completed "
                    "items; supporting events from progress comments, ticket changes, cleared blockers, or "
                    "updated result documents; and remaining work plus deadline risk. Do not return only a "
                    "status name. Preserve stated remaining work and attach exact ticket or document evidence.")
        elif intent in Intent.DIRECT_ANSWER and state.get("group_activity"):
            goal = ("Write a Korean three-layer group-activity narrative without a table: one paragraph "
                    "covering the full roster; two or three sentences on the module's combined contribution; "
                    "and one `###` section per person with verified ticket, comment, and document evidence. "
                    "Represent every person through a mention token and avoid repetitive filler.")
        elif intent in Intent.DIRECT_ANSWER:
            goal = ("Report the current-state result in Korean, preserving verified metrics and ticket "
                    "references. Attach a supplied action to its finding. Report an authorization denial exactly.")
        elif intent in (Intent.ASK, Intent.CHITCHAT):
            goal = "Answer the question in Korean from verified research. State directly when nothing was found in scope."
            # 상담형("어떻게 하는 게 좋을까") — 상황 요약만 하고 끝나면 조언이 아니다(실측:
            # '신속히 완료하세요' 수준). 선택지를 준다.
            if any(w in last_user_text(state) for w in ("어떻게 하는 게", "어떻게 해야",
                                                        "어쩌", "좋을까", "방안", "대안")):
                goal = ("This is an advice request. In Korean, give a one- or two-sentence evidenced situation; "
                        "a `| 옵션 | 영향 | 바로 할 일 |` table with two or three options actually supported "
                        "by the data; one recommended option with a reason; and one concrete next action that "
                        "can lead to a deterministic approval card when mutation is needed.")
        else:
            goal = "Summarize verified information in Korean and state the one next input or action required."

        asg = "\n".join(
            f"- [{a.get('index')}] {a.get('user') or '(미정)'} — {'; '.join(a.get('reasons') or [])}"
            + ("".join(f"\n    대안 {x.get('user')}: {x.get('why','')}"
                       for x in (a.get("alternates") or [])))
            for a in (state.get("assignments") or []))
        # Preserve source-specific observations and source-quality judgments for the one
        # role that writes the user-facing evidence chain.  The former flattened line
        # discarded comment provenance, confidence, fitness, and limitations immediately
        # before final composition.
        ev = json.dumps(state.get("evidence") or [], ensure_ascii=False, default=str)
        docs = "\n".join(f"- {d.get('title','')} {d.get('url','')}"
                         for d in (state.get("related_docs") or []))
        problems = "\n".join(f"- [{p.get('index')}] {p.get('message')} → {p.get('fix','')}"
                             for p in (review.get("problems") or []))
        errors = "\n".join(f"- [{e.get('index')}] {e.get('field')}: {e.get('message')}"
                           for e in (review.get("errors") or []))
        draft_items = [i for i in ((state.get("draft") or {}).get("items") or [])
                       if isinstance(i, dict) and i.get("summary")]
        made = "\n".join(f"- {c.get('key')} {c.get('summary','')}" for c in (result.get("created") or []))
        bad = "\n".join(f"- {f.get('summary','')}: {f.get('error','')}" for f in (result.get("failed") or []))

        pmo = "\n".join(
            f"- {f.get('key','')} {f.get('point','')}" + (f" → {f['action']}" if f.get("action") else "")
            for f in (state.get("pmo_findings") or []))
        # 지식 브리프(KnowledgeCurator) — 있으면 답변의 뼈대다: 개념 → 우리 상황 → 참고 → 공백 순.
        kb = state.get("knowledge_brief") or {}
        brief = ""
        if kb:
            brief = "\n".join(
                ["[개념]"] + [f"- {c.get('term')}: {c.get('explanation')}" for c in kb.get("concepts") or []]
                + ["[우리 상황]", kb.get("our_context") or ""]
                + ["[참고]"] + [f"- {r.get('ref')} — {r.get('why')}" for r in kb.get("references") or []]
                + ["[남은 공백]"] + [f"- {g}" for g in kb.get("gaps") or []])
            goal = ("Use Knowledge Brief Data as the sole content basis. Write Korean sections for concepts, "
                    "verified internal context with evidence, useful references, and unresolved gaps in that "
                    "order. Add nothing absent from the brief.")
        # ★ 자산·주제 조회는 브리프 순서(개념 먼저)가 오히려 방해다 — 실측에서 judge 가
        # "개념 설명이 길어 정작 물어본 값이 안 보인다"고 반복 지적했고, 컬럼 목록처럼
        # 자료에 그대로 있는 값이 답변에서 통째로 빠졌다. 이 유형은 **값이 먼저**다.
        # ★ 자산 형식(현재 값 표·타임라인·참조)은 **자산/지식 질의(ask)에만** — 별도 if 라
        #   초안·버그 응답까지 덮어써 억지 표와 무관 참조를 만들었다(실측: 버그 초안에
        #   '히스토리' 표 + 무관 티켓 참조).
        if state.get("topic_dossier") and not qs and not result \
                and (state.get("intent") or "") == Intent.ASK \
                and not state.get("draft", {}).get("items") \
                and not (state.get("change_plan") or {}).get("key"):
            goal = ("Lead with the exact value requested and use this Korean structure:\n\n"
                    "1. One or two conclusion sentences containing the requested value. For a follow-up, do "
                    "not repeat a table already shown; answer only the newly requested background or detail.\n"
                    "2. When multiple operational values exist, use `| 항목 | 값 | 근거 |`; the evidence "
                    "column contains only indices such as `[1]`.\n"
                    "3. When history is requested, use `| 날짜 | 사건 | 근거 |`, one event per row.\n"
                    "4. Preserve a supplied list such as all schema columns without omission.\n"
                    "5. For a value actually asked but absent, use `확인된 기록 없음` in one or two sentences. "
                    "Do not list unrelated absent fields or transfer a value from a similar asset.\n"
                    "6. Put `### 근거` last and assign one integer index to each real source. A ticket source "
                    "uses `{{ticket-detail:KEY}}`; a document uses its verified title and URL. When one source "
                    "supports multiple findings, list them as `[n-a]`, `[n-b]` below the source and cite those "
                    "child markers in the body. Ticket body, comments, and field history share one source.\n"
                    "   Compact citations in one sentence, clause, or table cell as `[4][5][10]`, with no "
                    "spaces or commas; every bracket must resolve to its own source.\n"
                    "7. Under `### 현재 진행 중인 Task`, use one `{{ticket-detail:KEY}}` bullet per ticket and "
                    "do not repeat key, title, assignee, status, or start date beside the badge.\n"
                    "8. Use inline code for identifiers, values, and Job names; Korean `###` headings for real "
                    "sections; bold for core values; and blockquotes for direct quotations.\n"
                    "When a value changed, identify current and prior values with the date and cite the change "
                    "ticket as the primary source. Use the explicit `[담당]` line for ownership; never infer "
                    "owner from a comment author.")
        # 회의 정리는 일반 자산 이력 템플릿보다 사용자가 준 결정·담당·기한이 주인공이다.
        # dossier의 과거 상태가 회의 당일 결정을 덮거나 담당 표가 빠진 MTG1 실측을 막는다.
        if not qs:
            from app.agent.workflow.meeting_context import is_meeting_request
            if is_meeting_request(state) and (state.get("intent") or "") == Intent.ASK:
                goal = (
                    "Write a compact Korean meeting brief from the Original Request, Current User Message, and "
                    "verified research. Start with `### 결정사항`. Add `### 참석자` with every resolved "
                    "person from the note, using mention tokens only. Then use `### 담당·기한` and a "
                    "`| 작업 | 담당 | 기한 |` table containing every explicitly named owner and deadline. "
                    "Use a verified mention token for every person. Follow with `### 조사로 보강한 맥락` for "
                    "only directly relevant internal history and external official findings, and `### 미결·검증` "
                    "for remaining uncertainty. Preserve explicit sample counts, pass/fail thresholds, hold or "
                    "exclusion decisions, and supplied local-term definitions. A pass criterion is not a passed "
                    "result. Keep speaker, requester, reviewer, and explicit assignee separate; never add a "
                    "responsibility row from an instruction or review statement. Do not list unrelated current "
                    "tickets, and do not replace a meeting decision with an older ticket status. Finish with the "
                    "single `### 근거` index; ticket sources use detail tokens and documents use verified links."
                )
        asked_for_quality = (request_text(state) + " " + last_user_text(state)).strip()
        if state.get("evidence"):
            goal += (
                "\nFor every material conclusion, add the matching `[n]` or `[n-a]` marker in the body. "
                "Do not leave a conclusion uncited merely because its source is listed at the end. Preserve "
                "comment observations and dated source conflicts; ticket status alone is not result evidence. "
                "Bind each claim only to a source whose supplied `observations[].text` directly supports it; "
                "`why` explains relevance but is not evidence. Never attribute a ticket-comment result to a "
                "meeting document merely because both discuss the same topic."
            )
        if any(word in asked_for_quality for word in ("신뢰도", "출처별", "요청 적합성", "적합성")):
            goal += (
                "\nAdd `### 출처 평가` before `### 근거` with a compact "
                "`| 출처 | 신뢰도 | 요청 적합성 | 한계 |` table. Use only the supplied confidence, fitness, "
                "limitations, authority, directness, recency, and corroboration evidence. Do not invent a "
                "numeric score. Separate external specification from internal production readiness."
            )
        data = wrap_data(
            data_block("Interpretation Data: Show Unchanged Under the Korean Heading 제가 이해한 바",
                       state.get("interpretation")),
            data_block("Knowledge Brief Data", brief),
            data_block("Complete Roster Activity Data",
                       state.get("group_activity")),
            data_block("Prefetched Ticket Progress: Changes, Comments, Children, and Documents",
                       state.get("ticket_progress")),
            # 주제 조사 원본 — 결론 문장(situation)만 실으면 조각의 출처(코멘트 작성자·
            # 변경 일자)가 사라져 "근거를 대라"는 요구를 만족시킬 수 없다.
            data_block("Topic Dossier: Missing Requested Values Must Be Reported as 확인된 기록 없음",
                       state.get("topic_dossier")),
            data_block("Verified Current Situation", state.get("situation")),
            data_block("PMO Findings", pmo),
            data_block("Interpretation Caution", state.get("pmo_caution")),
            data_block("Verified Evidence Sources With Observations and Quality", ev),
            data_block("Related Documents", docs),
            data_block("Ticket Draft: Not Yet Created", draft_text(state.get("draft"))),
            data_block("Change Plan: Not Yet Executed",
                       (lambda cp: f"{cp.get('key')}: " + ", ".join(
                           f"{k}: {(cp.get('before') or {}).get(k) or '없음'}→{v}"
                           for k, v in (cp.get('changes') or {}).items())
                        if cp.get("key") else
                        (f"일괄 {len(cp.get('keys'))}건 — 공통 변경: "
                         + ", ".join(f"{k}→{v}" for k, v in (cp.get('changes') or {}).items())
                         + "\n대상(키 · 제목):\n" + _key_titles(cp.get("keys"))
                         if cp.get("keys") else ""))(state.get("change_plan") or {})),
            data_block("Actual Change Results", "\n".join(
                f"- {u.get('key')} ({', '.join(u.get('fields') or [])})"
                for u in (result.get("updated") or []))),
            # 코드가 조회로 확정한 티켓 현재 값 — ResearchAnalyst 요약이 담당·마감을 떨구는 일이
            # 잦다(실측 Round P: 담당 skcc.x1402 를 "확인되지 않음"으로). 요약과 다르면
            # 이쪽이 사실이다.
            data_block("Deterministically Verified Current Ticket Values: Authoritative over Summaries",
                       "\n".join(l for l in str(state.get("pre_survey") or "").splitlines()
                                 if _re.match(r"\[[A-Z]+-\d+ (현재|변동|코멘트|하위|링크)\]", l))),
            # 문서 요약 요청의 재료는 **문서 본문**이다. ResearchAnalyst 요약은 "절차가 정리되어
            # 있습니다" 같은 메타 서술로 뭉개진다(실측 T3) — 원문을 그대로 준다.
            data_block("Document Bodies: Preserve Rules, Naming Conventions, Criteria, and Source URL",
                       _doc_body(state.get("pre_survey"))),
            # ★ 담당 후보 재료 — 여태 ResultIntegrator 에 **안 왔다**. pre_survey 에서 티켓 현재값과
            #   문서 본문만 잘라 썼기 때문에, 코드가 로스터·부하까지 조회해 실어 준 후보가
            #   ResearchAnalyst 의 situation 요약 한 겹을 지나며 사라졌다(실측 EDGE13: "누가 하면
            #   좋을지랑 지금 상황" 에 상황만 답하고 후보를 통째로 뺐다 — 세 번 연속).
            #   사람 이름은 사번 그대로 옮겨야 하므로 원문을 준다.
            data_block("Verified Assignment Candidates: When Asked Who, Return Two or Three Mention Tokens with Evidence",
                       _candidate_block(state.get("pre_survey"))),
            data_block("Structure Rationale", (state.get("draft") or {}).get("rationale")),
            data_block("Assignment Recommendations and Evidence", asg),
            data_block("Deterministic Validation Errors", errors),
            # 검토 의견은 **미해결일 때만** 사용자 몫이다. 검증을 통과해 승인 카드가 뜨는
            # 턴에 내부 지적("하나의 Task 로 통합하는 것이 좋습니다")을 그대로 옮기면
            # 카드와 모순되는 안내가 된다(실측 Round O, 2회 재발). 반영 여부는 WorkArchitect 가
            # 이미 판단했고 근거는 rationale 에 남는다.
            data_block("Unresolved Audit Feedback", "" if (draft_items and review.get("ok") and not errors)
                       else problems),
            data_block("Structured Questions Rendered Separately", "\n".join(f"- {q}" for q in qs)),
            data_block("Tickets Actually Created", made),
            data_block("Actual Failed Items", bad))

        # ── 답변 깊이 — 물어본 만큼만 답한다(사용자 요청).
        # 값 하나를 물었는데 개념 강의가 앞에 붙으면 정작 답이 묻힌다(judge 가 반복 지적).
        # 반대로 "왜/어떻게"를 물었는데 값만 던지면 불친절하다. RequestArchitect 가 가른다.
        # 어느 쪽이든 **더 깊은 설명은 다음 턴에** — 사용자가 요청하면 그때 푼다.
        depth = state.get("answer_depth") or "brief"
        if not qs:                       # 되묻는 턴은 질문 폼이 주인공이라 건드리지 않는다
            if depth == "explain":
                goal += ("\n\n## Answer Depth\n\nExplain relevant background, concept, and history after the "
                         "conclusion. Keep Korean prose compact, paragraphs at three or four lines, and use "
                         "headings only for materially different sections.")
            else:
                goal += ("\n\n## Answer Depth\n\nAnswer only what was asked. Use one or two Korean conclusion "
                         "sentences and a few evidence lines; omit generic background. When the requested answer "
                         "is a supplied list, preserve the complete list.")
            goal += ("\nDo not append a generic offer for more help. Add at most one concrete follow-up line "
                     "only when verified evidence identifies a specific next query or action.")

        # ── 원 요청을 함께 싣는다 — **답의 성격은 마지막 발화가 아니라 원 요청이 정한다.**
        # 실측: "fdc flat trace ic 데이터 히스토리 정리" → 표기 확인 질문 → 사용자가 보기
        # 하나("fdc.fdc_trace_summary_ic")를 고르자, 이 자리에 그 한 낱말만 실려서 답이
        # **연표가 아니라 현재 값 표**로 나왔다(티켓 8건 중 2건만 인용). 확인 턴을 지난
        # 대화는 마지막 발화가 짧은 선택지라, 그것만 보면 무엇을 묻는 대화인지 알 수 없다.
        # request_text 는 원 요청을 고정해 두려고 만든 장치인데 여기에만 연결이 없었다.
        req, last = (request_text(state) or "").strip(), (last_user_text(state) or "").strip()
        asked = (f"## Original Request Data: Determines Answer Scope\n\n{req}\n\n"
                 f"## Current User Message Data\n\n{last}") if req and req != last else \
                f"## User Request Data\n\n{last}"
        return f"# Task\n\n{goal}\n\nWrite the final answer in Korean.\n\n{asked}{data}"

    def apply(self, state, out):
        text = out.get("text") or ""
        # 모델이 내부 task wrapper를 답변으로 복창하거나 reference placeholder를 그대로
        # 노출하는 것은 내용 문제가 아니라 렌더링 계약 위반이다. grounding 전에 정규화해
        # 검사와 사용자 화면이 같은 문자열을 보게 한다.
        text = _strip_instruction_echo(text)
        # An inline attachment excerpt is user input, not a remotely verified source.  Drop
        # model-written filename/placeholder link rows before grounding so the diagnostic
        # itself does not leak into an otherwise valid answer.
        text = _drop_direct_input_source_rows(text)
        _qs = [q for q in (state.get("questions") or []) if isinstance(q, dict)]
        # A question-only turn has no executable payload for the prose model to summarize.
        # Letting it narrate the surrounding research produced invented Epic/module claims in
        # ASKD4 and BUG1.  The structured form owns the question; prose only states why input
        # is required.
        question_only = bool(_qs and not _has_executable_payload(state))
        if question_only:
            text = _question_only_reply(state, _qs)
        text = _canonicalize_meeting_reply(text, state)
        text = _canonicalize_person_mentions(text, state)
        text = _ensure_progress_child_coverage(text, state)
        text = _render_reply_tokens(text)
        if not state.get("_deterministic_reply"):
            text = _align_draft_claims(text, state)
        text = _ensure_research_status(text, state)
        text = _drop_unsupported_guarantees(text, state)

        # Normalize verified entities and the source index before grounding.
        # Previously these deterministic repairs ran only after the checker, so a
        # valid plain key or Confluence page id triggered a second full LLM rewrite
        # and still leaked an internal warning. Unknown entities are untouched and
        # remain visible to the grounding checker.
        text = _badgeify_known_ticket_mentions(text, state)
        text = _normalize_ticket_detail_sections(text)
        text = _normalize_badge_repetitions(text)
        text = _attach_known_doc_urls(text, state)
        text = _merge_evidence_index(text, state)

        # ── 접지 검사 — 답변의 티켓 키·제목·인명을 실물과 대조한다.
        # 지도·자료를 정확히 줘도 답변 단계에서 날조가 나왔다(없는 키, 바뀐 제목, "PM: 김철수").
        # 프롬프트로 세 번 막아 봤지만 재발 — 이 부류는 부탁할 일이 아니라 **검증할 일**이다.
        # 위반이 나오면 실값을 쥐여 주고 한 번 다시 쓰게 하고, 그래도 남으면 경고를 붙인다.
        # 조용히 고치지 않는 이유: 무엇이 걸렀는지 보여야 사용자가 시스템을 믿을 수 있다.
        # ★ 탐지와 교정을 분리한다. 예전엔 둘이 한 try 안에 있어서 **재작성 호출이 실패하면
        #   탐지 결과까지 통째로 버려졌다** — 검증기가 잡았는데 사용자는 아무것도 못 본다.
        #   실측: 위반(링크 없는 참조)이 잡힌 턴의 답에 경고도 재작성도 없이 그대로 나갔다.
        #   재작성은 시스템 프롬프트 전체 + 답 전문을 다시 보내는 **두 번째 LLM 호출**이라
        #   레이트리밋·길이로 죽을 수 있다. 그건 교정의 실패이지 탐지의 무효가 아니다.
        from app.agent.workflow import grounding
        grounding_warnings = []
        try:
            g = ({"ok": True} if state.get("_deterministic_reply") or question_only else
                 grounding.check(text, allowed_people=_dialogue_speakers(request_text(state))))
        except Exception:
            g = None                        # 검증기가 죽으면 답은 그대로 나간다
        if g and not g["ok"]:
            text2, g2 = "", None
            try:
                fixed = self.llm().invoke([
                    ("system", self.system(state)),
                    ("user", f"The previous Korean answer contains grounding violations. Rewrite the entire "
                             f"answer, correcting only the violations below and preserving all valid content.\n\n"
                             f"### Violations\n\n{grounding.violation_note(g)}\n\n"
                             f"### Previous Answer\n\n{text}")])
                text2 = str(getattr(fixed, "content", "") or "").strip()
                if text2:
                    g2 = grounding.check(
                        text2, allowed_people=_dialogue_speakers(request_text(state)))
            except Exception:
                text2, g2 = "", None        # 교정 실패 — 아래에서 원문 + 경고로 간다
            if g2 and g2["ok"] and _kept_substance(text, text2):
                text = text2
            else:                           # 못 고침 — 덜 틀린 쪽에 **반드시 경고를 단다**
                use2 = bool(g2) and _violations(g2) < _violations(g) \
                    and _kept_substance(text, text2)
                better, gb = (text2, g2) if use2 else (text, g)
                text = better
                warning = grounding.warning_block(gb).strip()
                if warning:
                    grounding_warnings.append(warning)

        # 전용 진행 Task bullet은 detail badge 하나로 기계화한다. 모델이 raw key+제목을
        # 출력해도 최종 문자열은 badge가 가진 정보를 중복하지 않는다.
        text = _badgeify_known_ticket_mentions(text, state)
        text = _normalize_ticket_detail_sections(text)
        text = _normalize_badge_repetitions(text)
        # 근거 인덱스 후처리 — 같은 출처가 두 번호를 받는 실측 미스([1]·[3]가 같은 티켓)를
        # 코드가 접는다. 규칙("같은 근거 같은 번호")은 프롬프트에 있지만 보장은 여기서.
        text = _dedupe_refs(text)
        # '확인된 기록 없음'만 채운 표 행·참조 줄은 정보가 아니라 소음이다 — md 로 두 번
        # 금지했는데 재발(실측 2회). 코드가 걷어낸다.
        text = _prune_empty_rows(text)
        # 같은 문장을 두 번 쓰는 버릇 — "아래 카드에서 확인 후 승인해 주세요."가 문단 끝과
        # 그다음 줄에 각각 나왔다(실측). 프롬프트로 막을 종류가 아니다(모델은 두 번 쓴 걸
        # 모른다). 표·목록은 같은 문구가 정당하게 반복되므로 **평문 문장만** 접는다.
        text = _dedupe_sentences(text)
        # 상투 맺음말 — 판단이 아니라 버릇이라 프롬프트로 안 잡힌다(위 주석).
        text = _drop_boilerplate_closers(text)
        text = _fill_empty_approval_heading(text, state)
        # 되묻는 턴 — 폼이 묻는 것을 본문에서 걷어낸다. 프롬프트로 두 번 금지했는데도
        # 질문·보기를 통째로 베껴 화면에 같은 말이 두 벌 뜬다(사용자 지적).
        # 문서를 요약해 놓고 **어느 문서인지 안 밝히면** 확인할 방법이 없다("자세한 내용은
        # 문서에서 확인할 수 있습니다"로 끝났다 — 실측 T3). 출처는 코드가 보장한다.
        _src = _re.findall(r"문서 본문 「([^」]+)」 \((https?://[^)\s]+)\)",
                           _doc_body(state.get("pre_survey")))
        # 조사에서 나온 문서도 후보다 — 본문을 안 읽고 답한 턴에도 출처는 있어야 한다.
        _src += [(str(d.get("title") or "문서"), str(d.get("url") or ""))
                 for d in (state.get("related_docs") or []) if d.get("url")]
        # 사전 조사의 문서 목록("- 제목 (URL)")까지 — 모델이 related_docs 를 안 채우는
        # 턴이 있다(실측: 문서를 요약해 놓고 "문서 링크를 참고하세요"로 끝냈다).
        _src += _re.findall(r"^-\s*(.+?)\s*\((https?://[^)\s]+)\)\s*$",
                            str(state.get("pre_survey") or ""), _re.M)
        if _src and _re.search(r"문서|가이드|절차|규정", last_user_text(state)):
            seen_u = set()
            for _t, _u in _src[:2]:
                if not _u or _u in text or _u in seen_u:
                    continue
                seen_u.add(_u)
                text = text.rstrip() + "\n\n출처: [" + _t + "](" + _markdown_url(_u) + ")"
        # 쓰다 만 링크 토막("[여기에서 확인할 수 있습니다.") — 여는 대괄호만 남으면
        # 화면에 대괄호가 글자로 보인다(실측). 짝 없는 `[` 는 지운다.
        text = _drop_dangling_bracket(text)
        # ★ 참조의 문서 URL 은 **코드가 붙인다.** 재료에는 URL 이 있는데(dossier 의
        #   `문서 「제목」 (URL)`) 모델이 참조 줄로 옮기지 않는 일이 반복됐다 — 프롬프트로
        #   두 라운드 고쳤는데도 실측 DATA9 는 세 줄 다 제목만 남겼다. 우리가 아는 URL 을
        #   그 제목에 붙이는 것은 **지어내는 것이 아니라 옮기는 것**이라 코드가 할 수 있다.
        text = _attach_known_doc_urls(text, state)
        text = _ensure_external_research_coverage(text, state)
        text = _render_requested_source_quality(text, state)
        # Persist one canonical source index in the reply itself.  Research state and
        # model-written references used to be rendered as separate UI blocks, causing
        # duplicate counts and divergent formats.  The server owns numbering and grouping;
        # the browser only renders this canonical Markdown (with a legacy-read fallback).
        text = _merge_evidence_index(text, state)
        text = _rebind_definition_citations(text)
        text = _rebind_explicit_source_citations(text)
        # Explicit citation-marker requests are a rendering contract.  The source index
        # already owns stable numbering, so bind uncited conclusion paragraphs to the
        # best-matching verified sources after numbering instead of trusting another LLM
        # rewrite to copy brackets reliably.
        text = _ensure_requested_body_citations(text, state)
        # ★ 여기서 **한 번 더** 본다. 위의 후처리들은 접지 검사 **뒤에** 돌기 때문에,
        #   후처리가 만든 결함은 검사를 통과한 셈이 된다. 실측: 대괄호 정리가 문서 링크를
        #   먹어 참조가 URL 없는 제목만 남았는데 아무도 못 잡았다. 마지막에 다시 보고,
        #   후처리가 부순 것이면 조용히 넘기지 않는다(재작성은 이미 끝난 자리라 경고만).
        try:
            _late = grounding._unlinked_refs(text)
            if _late and (not g or not g.get("unlinked_refs")):
                warning = grounding.warning_block({"unlinked_refs": _late}).strip()
                if warning and warning not in grounding_warnings:
                    grounding_warnings.append(warning)
        except Exception:
            pass

        if _qs:
            text = _drop_form_echo(text, _qs)
        # Jira 계층 규칙은 문장 생성의 재량이 아니다. 실측 RULE1에서 WorkArchitect가
        # 질문으로 멈췄는데도 요약문만 "부모 없이 Sub-Task를 생성"한다고 뒤집었다.
        # 사용자가 명시적으로 부모 없는 Sub-Task를 요구한 질문 턴은 가능한 선택지를
        # 카드가 렌더하므로, 본문에는 불가능한 이유만 결정적으로 남긴다.
        if _qs and _requests_parentless_subtask(state):
            text = ("### 요약\n\n"
                    "Sub-Task는 Task-tier 부모가 필수이므로 부모 없이 생성할 수 없음\n\n"
                    "### 상세\n\n아래에서 선택해 주세요")
        # 카드의 값과 문장의 값이 다르면 **카드가 사실**이다. 상대 날짜는 코드가 계산해
        # 계획에 넣는데(모델 산술이 흔들린다), 답변 문장에는 모델이 제 값을 그대로 써서
        # "2026-08-18로 연장" ↔ 카드 2026-08-14 로 어긋났다(실측 Round P).
        due = str(((state.get("change_plan") or {}).get("changes") or {}).get("duedate") or "")
        if not state.get("_deterministic_reply") and _re.match(r"^\d{4}-\d{2}-\d{2}$", due):
            text = _re.sub(r"\b\d{4}-\d{2}-\d{2}\b",
                           lambda m: due if m.group(0) != due else m.group(0), text)

        # 최종 사용자 말투는 prompt 권고로 끝내지 않는다. 모델·fallback·후처리 어느 경로에서
        # 왔든 같은 간결한 업무 브리프 문법으로 정규화한다. 질문·인용·code는 예외.
        text = _canonicalize_meeting_reply(text, state)
        text = _render_reply_tokens(text)
        text = _canonicalize_person_mentions(text, state)
        text = _enforce_reply_style(text)
        # Grounding diagnostics are not provenance.  Insert them before the already-built
        # evidence index; otherwise a legacy reference parser can absorb warning bullets as
        # observations, while appending after the index would violate the index-last contract.
        if grounding_warnings:
            warning_text = "\n\n".join(grounding_warnings)
            evidence_heading = _re.search(r"(?m)^### 근거\s*$", text)
            if evidence_heading:
                text = (text[:evidence_heading.start()].rstrip() + "\n\n" + warning_text
                        + "\n\n" + text[evidence_heading.start():].lstrip())
            else:
                text = text.rstrip() + "\n\n" + warning_text

        # ── 후검증 — **플레이북별 최소선**(사용자 지시: 주요 태스크는 결과도 검증)
        # 프롬프트에 적어 두면 '대체로' 지켜진다. 문제는 그 '대체로'다 — 같은 요청이
        # 어떤 날은 연표만 나오고 어떤 날은 현재 상태까지 나온다. 흔들림은 지시로 못 잡으니
        # **잴 수 있는 것은 코드가 재고**, 사용자 reply가 아니라 local debug trace에 남긴다.
        _bad = []
        try:
            from app.agent.workflow import postcheck
            _bad = postcheck.check(state, text)
        except Exception:
            pass

        from langchain_core.messages import AIMessage
        trace_note = f"{len(text)}자"
        if _bad:
            trace_note += f" · 내부 후검증: {postcheck.summary(_bad)}"
        return {"reply": text, "messages": [AIMessage(content=text)],
                "trace": note(state, self.name, trace_note)}


def _requests_parentless_subtask(state) -> bool:
    """사용자가 Sub-Task를 원하면서 부모 부재를 명시했는지 판별한다."""
    said = (request_text(state) + " " + last_user_text(state)).replace(" ", "")
    wants_subtask = bool(_re.search(r"(?:서브태스크|하위태스크|Sub-?Task)", said, _re.I))
    says_no_parent = bool(_re.search(
        r"(?:부모(?:는|가|티켓은|티켓이)?(?:없|없이|필요없)|최상위(?:로|에))", said))
    return wants_subtask and says_no_parent


def _ensure_progress_child_coverage(text: str, state) -> str:
    """Expose every verified child when a progress answer silently drops some of them.

    The LLM usually summarizes completed children and names only the open one.  That loses
    the evidence behind the aggregate ``2/3`` count.  The pre-aggregator already has exact
    child keys and states, so append a compact canonical badge snapshot only when coverage
    is incomplete.  ``ticket-list`` is the intentionally small multi-ticket badge.
    """
    if (state.get("intent") or "") != Intent.PROGRESS:
        return str(text or "")
    material = str(state.get("ticket_progress") or "")
    children: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _re.finditer(
        r"(?m)^\s*-\s+([A-Z][A-Z0-9]*-\d+)\s+\"[^\"]*\"\s+(완료|진행중)\b",
        material,
    ):
        key, status = match.group(1), match.group(2)
        if key not in seen:
            seen.add(key)
            children.append((key, status))
    if not children or all(key in str(text or "") for key, _status in children):
        return str(text or "")

    rows = ["### 하위 작업 현황", ""]
    for status, label in (("완료", "완료"), ("진행중", "진행 중")):
        keys = [key for key, current in children if current == status]
        if keys:
            rows.append(f"- {label}: " + " ".join(
                f"{{{{ticket-list:{key}}}}}" for key in keys
            ))
    block = "\n".join(rows)
    source = str(text or "").rstrip()
    anchor = _re.search(r"(?m)^###\s*(?:근거|참조)\s*$", source)
    if anchor:
        return source[:anchor.start()].rstrip() + "\n\n" + block + "\n\n" + source[anchor.start():]
    return source + "\n\n" + block


def _has_executable_payload(state) -> bool:
    draft = state.get("draft") or {}
    plan = state.get("change_plan") or {}
    result = state.get("result") or {}
    return bool(draft.get("items") or draft.get("structure_tree")
                or plan.get("key") or plan.get("keys") or result)


_FIELD_LABELS = {
    "summary": "제목", "description": "본문", "assignee": "담당자",
    "duedate": "기한", "priority": "우선순위", "labels": "라벨",
    "components": "컴포넌트", "status": "상태", "link": "관계",
}


def _cell(value) -> str:
    if value in (None, "", []):
        return "—"
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    return str(value).replace("|", "\\|").replace("\n", " ")


def _approval_reply(state) -> str:
    """승인 카드와 동일한 state에서만 만드는 사용자 설명.

    이 함수는 판단하거나 보강하지 않는다. 확정 payload를 읽기 쉬운 표로 투영할 뿐이라
    카드와 답변의 건수·필드·담당·기한이 구조적으로 어긋날 수 없다.
    """
    plan = state.get("change_plan") or {}
    if plan.get("key") or plan.get("keys"):
        keys = [str(k) for k in (plan.get("keys") or [plan.get("key")]) if k]
        changes = plan.get("changes") or {}
        comments = [c for c in (plan.get("comments") or []) if isinstance(c, dict)]
        comment = str(plan.get("comment") or "").strip()

        if not changes and (comments or comment):
            rows = ["### 댓글 승인 초안", "",
                    f"**대상 {len(keys)}건 · 필드·상태 변경 없음 · 아직 게시되지 않음**", ""]
            by_key = {str(c.get("key") or ""): str(c.get("body") or "").strip()
                      for c in comments}
            for key in keys:
                body = by_key.get(key) or comment
                quoted = "\n".join(f"  > {line}" if line else "  >"
                                   for line in body.splitlines())
                rows += [f"- {{{{ticket-detail:{key}}}}}", quoted, ""]
            rows += ["### 승인", "", "아래 카드에서 대상과 댓글 전문 확인 후 게시 승인"]
            return "\n".join(rows).strip()

        rows = ["### 변경 승인 초안", ""]
        if len(keys) == 1:
            rows += [f"{{{{ticket-detail:{keys[0]}}}}}", ""]
        else:
            rows += [" ".join(f"{{{{ticket-list:{key}}}}}" for key in keys), ""]
        rows += ["| 필드 | 현재 | 변경 |", "|---|---|---|"]
        before = plan.get("before") or {}
        if (plan.get("transition") or {}).get("name"):
            changes = {"status": plan["transition"]["name"]}
        elif (plan.get("link") or {}).get("other"):
            link = plan["link"]
            changes = {"link": f"{link.get('relation') or 'Relates'} · {link['other']}"}
        for field, value in changes.items():
            rows.append(f"| {_FIELD_LABELS.get(field, field)} | {_cell(before.get(field))} | {_cell(value)} |")
        if comment:
            rows += ["", "### 함께 게시할 댓글", "", f"> {comment}"]
        rows += ["", "### 승인", "", "아직 변경되지 않음 · 아래 카드에서 값 확인 후 승인"]
        return "\n".join(rows).strip()

    from app.agent.workflow.agents.work_architect import as_bulk_items, child_items
    items = as_bulk_items(state.get("draft") or {})
    children = child_items(state.get("draft") or {})
    rows = ["### 티켓 승인 초안", "",
            f"**총 {len(items) + len(children)}건 · 아직 생성되지 않음**", "",
            "| # | 유형 | 제목 | 상위 | 담당 | 기한 |", "|---:|---|---|---|---|---|"]
    for index, item in enumerate(items, 1):
        parent = item.get("parent") or item.get("epic") or "최상위"
        owner = f"{{{{mention:{item['assignee']}}}}}" if item.get("assignee") else "미정"
        rows.append(f"| {index} | {_cell(item.get('type') or 'Task')} | {_cell(item.get('summary'))} | "
                    f"{_cell(parent)} | {owner} | {_cell(item.get('duedate'))} |")
    for offset, child in enumerate(children, len(items) + 1):
        parent_index = int(child.get("parent_index") or 0)
        parent = items[parent_index].get("summary") if parent_index < len(items) else "부모 Task"
        owner = f"{{{{mention:{child['assignee']}}}}}" if child.get("assignee") else "미정"
        rows.append(f"| {offset} | Sub-Task | {_cell(child.get('summary'))} | "
                    f"{_cell(parent)} | {owner} | {_cell(child.get('duedate'))} |")

    assignments = [a for a in (state.get("assignments") or []) if isinstance(a, dict)]
    reasons = []
    for index, item in enumerate(items):
        matched = next((a for a in assignments if a.get("index") == index), {})
        if item.get("assignee") and matched.get("reasons"):
            reasons.append((str(item.get("summary") or f"#{index + 1}"),
                            str(item["assignee"]),
                            "; ".join(map(str, matched["reasons"]))))
        child_assignments = {
            child.get("index"): child for child in (matched.get("children") or [])
            if isinstance(child, dict) and isinstance(child.get("index"), int)
        }
        for child_index, child in enumerate((state.get("draft") or {}).get("items", [])[index]
                                            .get("children") or []):
            if not isinstance(child, dict) or not child.get("assignee"):
                continue
            assigned = child_assignments.get(child_index) or {}
            why = str(assigned.get("why") or "").strip()
            if why:
                reasons.append((str(child.get("summary") or f"Sub-Task #{child_index + 1}"),
                                str(child["assignee"]), why))
    if reasons:
        rows += ["", "### 배정 근거", "", "| 티켓 | 담당 | 근거 |", "|---|---|---|"]
        rows += [f"| {_cell(title)} | {{{{mention:{owner}}}}} | {_cell(why)} |"
                 for title, owner, why in reasons]
    rows += ["", "### 승인", "", "아래 카드에서 본문·배치·완료 조건 확인 후 생성 승인"]
    return "\n".join(rows).strip()


def _person_work_reply(data: dict) -> str:
    from collections import Counter

    uid = str(data.get("user_id") or "").strip()
    tickets = [t for t in (data.get("tickets") or []) if isinstance(t, dict) and t.get("key")]
    who = f"{{{{mention:{uid}}}}}" if uid else "해당 사용자"
    if not tickets:
        return f"### 현재 담당 업무\n\n**{who}의 미완료 할당 티켓 없음**"
    statuses = Counter(str(t.get("status") or "미분류") for t in tickets)
    priorities = Counter(str(t.get("priority") or "미지정") for t in tickets)
    status_summary = " · ".join(f"{key} {value}건" for key, value in statuses.most_common())
    priority_summary = " · ".join(f"{key} {value}건" for key, value in priorities.most_common())
    rows = [
        "### 현재 담당 업무", "",
        f"**{who} · 미완료 {len(tickets)}건**", "",
        "| 구분 | 요약 |", "|---|---|",
        f"| 상태 | {status_summary} |",
        f"| 우선순위 | {priority_summary} |", "",
        "### 최근 갱신 업무", "",
        "| 티켓 | 상태 | 우선순위 | 기한 |", "|---|---|---|---|",
    ]
    # execute_jql_all already returns updated DESC.  A summary should expose a useful
    # sample, not dump dozens of compact badges in one unreadable line.
    for ticket in tickets[:5]:
        rows.append(
            f"| {{{{ticket-inline:{ticket['key']}}}}} | {_cell(ticket.get('status'))} | "
            f"{_cell(ticket.get('priority'))} | {_cell(ticket.get('duedate'))} |"
        )
    remaining = len(tickets) - 5
    if remaining > 0:
        rows += ["", f"최근 갱신 순 5건 표시 · 외 {remaining}건"]
    rows += ["", "현재 담당자로 지정된 미완료 티켓 기준 · 최근 활동 로그와 구분"]
    return "\n".join(rows)


def _daily_priority_reply(data: dict) -> str:
    key = str(data.get("key") or "").strip()
    if not key:
        return "### 지금 시작할 업무\n\n확인된 열린 업무 없음"
    facts = [str(data.get("priority") or "").strip()]
    due = str(data.get("due") or "").strip()
    if due:
        facts.append(f"마감 {due}" + (" · 초과" if data.get("overdue") else ""))
    basis = " · ".join(value for value in facts if value)
    return (f"### 지금 시작할 업무\n\n{{{{ticket-detail:{key}}}}}\n\n"
            f"**1순위** — {basis}\n\n"
            "열린 업무 중 마감 구간을 먼저, 같은 구간에서는 Jira 우선순위를 적용")


def _question_only_reply(state, questions: list[dict]) -> str:
    """Render only verified reasons for a form-only turn; never summarize speculative context."""
    interpretation = str(state.get("interpretation") or "").strip()
    reasons = []
    for question in questions:
        reason = str(question.get("why_required") or "").strip().rstrip(".。")
        if reason and reason not in reasons:
            reasons.append(reason)
    if not reasons:
        reasons = ["요청을 확정하려면 사용자 입력 필요"]
    prompt = ("아래 입력란에 필요한 내용을 적어 주세요"
              if all(str(q.get("kind") or "text") == "text" for q in questions)
              else "아래에서 선택해 주세요")
    blocks = []
    if interpretation:
        blocks += ["### 제가 이해한 바", "", interpretation, ""]
    blocks += ["### 확인 필요", "", "\n".join(f"- {reason}" for reason in reasons), "", prompt]
    return "\n".join(blocks).strip()


# 맺음말·상투구 — **끝에 붙는 빈 문장**. common.md 가 이미 금지하는데 실사용 리뷰에서
# 다섯 흐름 중 넷에 나왔다("추가적인 정보가 필요하면 말씀 주세요", "변경 경위나 관련 티켓
# 내용이 더 궁금하면 말씀 주세요", "남은 일과 리스크에 대한 추가 정보가 필요하면 …").
#
# 왜 프롬프트로 안 되나: 이건 판단이 아니라 **버릇**이다. 모델은 매 답을 예의 바르게 닫으려
# 하고, 그 한 줄은 어떤 문맥에서도 "틀리지" 않는다 — 그래서 규칙을 읽고도 계속 쓴다.
# 판단이 아니면 코드가 지운다(이 저장소의 규율).
#
# ★ 지우는 것은 **아무것도 제안하지 않는 되물음**뿐이다. "다음은 성능 측정을 잡을까요?"
#   처럼 **구체적인 다음 행동**을 제안하는 줄은 정보라서 남긴다 — 그래서 티켓 키·구체
#   명사가 든 줄은 건드리지 않는다.
_CLOSER_RE = _re.compile(
    r"^(?:그\s*밖에|또한|추가로)?\s*[^.!?\n]{0,60}?"
    r"(?:궁금|필요|문의|질문|도움).{0,30}?"
    r"(?:말씀|알려|문의)\s*(?:해\s*)?(?:주세요|주십시오|주시기|주시면|드리겠)[^.!?\n]{0,20}[.!?]?$")


def _fill_empty_approval_heading(text: str, state) -> str:
    """승인 헤딩만 남은 답은 사용자가 무엇을 승인하는지 알 수 없으므로 한 문장을 채운다."""
    if not ((state.get("draft") or {}).get("items") or []):
        return text
    return _re.sub(r"(?mi)^(#{2,4}\s*승인\s*요청)\s*$\s*(?=\Z)",
                   r"\1\n위 티켓 초안과 담당자 배정을 검토한 뒤 승인해 주세요.",
                   str(text or "").rstrip())


def _strip_instruction_echo(text: str) -> str:
    """사용자에게 보이면 안 되는 내부 `# 명령서` 머리말을 제거한다."""
    return _re.sub(r"^\s*#{1,3}\s*명령서\s*\n+", "", str(text or ""), count=1,
                   flags=_re.I).lstrip()


def _render_reply_tokens(text: str) -> str:
    """ResultIntegrator placeholder를 깨지지 않는 canonical Markdown link/mention으로 렌더한다."""
    try:
        from app.infra.settings import get_settings
        base = str(get_settings().jira_base or "").rstrip("/")
    except Exception:
        base = ""

    def ref(m):
        rid = m.group(1)
        if _re.match(r"^[A-Z][A-Z0-9]*-\d+$", rid):
            url = f"{base}/browse/{rid}" if base else f"/browse/{rid}"
            return f"[{rid}]({url})"
        return rid                         # 알 수 없는 typed id를 깨진 토큰으로 노출하지 않는다

    out = str(text or "")
    # Typed UI tokens are rendered by the client. A model sometimes wraps one
    # in inline-code backticks, making Markdown and the badge renderer overlap.
    # Remove only backticks whose complete content is one strict typed token;
    # ordinary inline code remains untouched.
    out = _re.sub(
        r"`(\{\{+(?:ticket-(?:list|inline|detail)|ref|mention):"
        r"[A-Za-z0-9_.:-]+\}+\})`",
        r"\1", out,
    )
    out = _re.sub(r"\{\{+ref:([A-Za-z0-9_.:-]+)\}+\}", ref, out)
    out = _re.sub(r"\{\{+mention:([A-Za-z0-9_.:-]+)\}+\}", r"[~\1]", out)
    return out


def _canonicalize_meeting_reply(text: str, state) -> str:
    """Apply confirmed meeting identities and guarantee a complete attendee badge section."""
    from app.agent.workflow.meeting_context import (
        attendee_mentions, canonicalize_meeting_owner_table,
        canonicalize_reply_mentions, is_meeting_request, prune_resolved_reply_gaps,
    )

    out = canonicalize_reply_mentions(state, text)
    out = prune_resolved_reply_gaps(state, out)
    out = canonicalize_meeting_owner_table(state, out)
    try:
        from app.agent.workflow.meeting_context import meeting_request_text
        original = meeting_request_text(state)
    except Exception:
        original = ""
    # Preserve an explicit decision word from the minutes. ``운영 반영 보류`` was once
    # paraphrased as ``검증 완료 후 진행``; semantically close, but the former is the
    # auditable current decision and distinguishes it from a future action.
    if _re.search(r"운영\s*반영\s*보류", original) and not _re.search(
            r"운영\s*반영(?:은|는|이|가|을|를)?\s*보류", out):
        out = _re.sub(
            r"운영\s*반영(?:은|는|이|가|을|를)?\s*"
            r"(.{0,100}?(?:검증|증거).{0,50}?(?:후|뒤)(?:에)?)\s*진행",
            r"운영 반영 보류 — \1 진행", out, count=1, flags=_re.I,
        )
    try:
        from app.agent.workflow.meeting_context import meeting_owner_records
        has_confirmed_owner = any(str(row.get("owner") or "").strip()
                                  for row in meeting_owner_records(state))
    except Exception:
        has_confirmed_owner = False
    has_confirmed_owner = has_confirmed_owner or bool(_re.search(
        r"(?m)^\s*\|[^\n]*\{\{mention:skcc\.[^}\n]+\}\}[^\n]*\|\s*$", out, _re.I))
    if has_confirmed_owner:
        out = _re.sub(
            r"(?mi)^\s*[-*]\s*[^\n]{0,120}담당자[^\n]{0,60}"
            r"(?:확인되지\s*않|미확정|알\s*수\s*없)[^\n]*\n?",
            "", out,
        )
    if not is_meeting_request(state) or (state.get("intent") or "") != Intent.ASK \
            or state.get("questions"):
        return out
    attendees = attendee_mentions(state)
    if attendees:
        block = "### 참석자\n\n" + " ".join(f"{{{{mention:{uid}}}}}" for uid in attendees)
        pattern = r"(?ms)^###\s*참석자\s*\n.*?(?=^###\s|\Z)"
        if _re.search(pattern, out):
            out = _re.sub(pattern, block + "\n\n", out, count=1)
        else:
            anchor = _re.search(r"(?m)^###\s*담당[·ㆍ\s-]*기한\s*$", out)
            if anchor:
                out = (out[:anchor.start()].rstrip() + "\n\n" + block
                       + "\n\n" + out[anchor.start():])
            else:
                out = out.rstrip() + "\n\n" + block
    return _ensure_explicit_meeting_ticket_sources(out, state)


def _ensure_explicit_meeting_ticket_sources(text: str, state) -> str:
    """Keep every ticket explicitly cited by the minutes in the combined evidence section."""
    try:
        from app.agent.workflow.meeting_context import meeting_request_text
        original = meeting_request_text(state)
    except Exception:
        return str(text or "")
    # Korean particles are commonly attached directly (``DL-7001에서``), so a
    # Unicode word boundary after the number would miss the key.
    keys = list(dict.fromkeys(_re.findall(
        r"(?<![A-Z0-9-])([A-Z][A-Z0-9]*-\d+)(?!\d)", original)))
    out = str(text or "")
    missing = [key for key in keys if not _re.search(
        rf"(?:ticket-(?:detail|inline|compact):{_re.escape(key)}|"
        rf"\[{_re.escape(key)}(?:\s|\]))", out, _re.I)]
    if not missing:
        return out
    bullets = "\n".join(f"- {{{{ticket-detail:{key}}}}}" for key in missing)
    section = _re.search(r"(?m)^###\s*(?:근거|참고)\s*$", out)
    if not section:
        return out.rstrip() + "\n\n### 근거\n\n" + bullets
    next_heading = _re.search(r"(?m)^###\s+", out[section.end():])
    end = section.end() + (next_heading.start() if next_heading else len(out[section.end():]))
    return out[:end].rstrip() + "\n" + bullets + "\n\n" + out[end:].lstrip()


def _canonicalize_person_mentions(text: str, state) -> str:
    """state에서 ID가 확인된 사람의 평문 이름을 canonical mention으로 바꾼다.

    동명이인과 식별자 없는 이름은 추측하지 않는다. 확정된 mapping만 기계적으로 치환한다.
    """
    by_name = {}

    def add(uid, name):
        uid, name = str(uid or "").strip(), str(name or "").strip()
        if uid and name and uid != name and len(name) >= 2:
            by_name.setdefault(name, set()).add(uid)

    def walk(value):
        if isinstance(value, dict):
            for id_key, name_key in (("assigneeId", "assignee"), ("reporterId", "reporter"),
                                     ("authorId", "author"), ("user_id", "name"),
                                     ("uid", "name")):
                add(value.get(id_key), value.get(name_key))
            uid = value.get("id")
            if isinstance(uid, str) and "." in uid:
                add(uid, value.get("name") or value.get("displayName"))
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(state)
    # Workload material is intentionally compact text, but it is still verified runtime
    # data (``- user.id Display Name — 진행중 N건``).  Include that mapping so a model's
    # plain-name assignment rationale becomes the mandatory mention badge as well.
    for line in str(state.get("roster_load") or "").splitlines():
        match = _re.match(
            r"\s*-\s*([A-Za-z][A-Za-z0-9.]+)\s+(.+?)\s+[—–-]\s+", line)
        if match:
            add(match.group(1), match.group(2))
    out = str(text or "")
    for name in sorted(by_name, key=len, reverse=True):
        ids = by_name[name]
        if len(ids) == 1:
            out = out.replace(name, f"[~{next(iter(ids))}]")
    return out


def _assignment_completion_reply(data: dict) -> str:
    """미완료 담당자 집계를 질문 축 그대로 짧게 렌더한다."""
    topic = str(data.get("topic") or "해당 업무")
    if data.get("error"):
        return (f"**{topic} 미완료 담당자를 확정하지 못했습니다.**\n\n"
                f"조회 사유: {data['error']}")
    parents = [p for p in (data.get("parents") or []) if isinstance(p, dict)]
    if not parents:
        return (f"**검색 범위에서 ‘{topic}’에 해당하는 상위 Task와 직계 Sub-Task를 "
                "찾지 못했습니다.**\n\n정확한 상위 Task 키나 제목을 알려주면 그 하위만 다시 확인할 수 있습니다.")

    people = [p for p in (data.get("people") or []) if isinstance(p, dict)]
    unassigned = [x for x in (data.get("unassigned") or []) if isinstance(x, dict)]
    incomplete = int(data.get("incompleteSubtasks") or 0)
    total = int(data.get("totalSubtasks") or 0)
    done = int(data.get("doneSubtasks") or 0)
    if incomplete:
        missing_note = f" 담당 미지정 {len(unassigned)}건이 포함됩니다." if unassigned else ""
        lines = [f"**{topic} 미완료는 {len(people)}명·{incomplete}건입니다.** "
                 f"전체 Sub-Task {total}건 중 {done}건이 완료됐습니다.{missing_note}",
                 "", "### 미완료자"]
        for person in people:
            name, uid = str(person.get("name") or "").strip(), str(person.get("id") or "").strip()
            who = f"{{{{mention:{uid}}}}}" if uid else (name or "담당자 미상")
            keys = " ".join(f"{{{{ticket-list:{t.get('key')}}}}}"
                            for t in person.get("tickets") or [] if t.get("key"))
            lines.append(f"- {who} — {keys}")
        if unassigned:
            keys = " ".join(f"{{{{ticket-list:{t.get('key')}}}}}"
                            for t in unassigned if t.get("key"))
            lines.append(f"- 담당자 미지정 — {keys}")
    else:
        lines = [f"**{topic}의 미완료자는 없습니다.** 전체 Sub-Task {total}건이 완료됐습니다."]

    lines += ["", "기준은 다음의 상위 Task입니다."]
    for parent in parents:
        lines.append(f"- {{{{ticket-detail:{parent.get('key')}}}}} — "
                     f"직계 Sub-Task {parent.get('total')}건 중 "
                     f"완료 {parent.get('done')}건, 미완료 {len(parent.get('incomplete') or [])}건")
    lines += ["", "판정 기준: 완료 상태가 아닌 직계 Sub-Task"]
    return "\n".join(lines)


def _enforce_reply_style(text: str) -> str:
    """최종 reply를 짧은 업무 브리프 문체로 정규화한다.

    사실의 구조나 markdown token은 바꾸지 않는다. 확실히 기계화 가능한 존댓말 종결만
    명사형으로 바꾸고, 여러 내용 block인데 heading이 전혀 없을 때만 요약/상세 section을
    보완한다. 직접 인용·blockquote·질문·code fence는 구술 문맥이라 그대로 둔다.
    """
    value = str(text or "").strip()
    if not value:
        return value

    def compact_line(line: str) -> str:
        stripped = line.strip()
        if not stripped or stripped.startswith(">") or stripped.startswith("#"):
            return line
        if stripped.endswith("?") or stripped.endswith("？"):
            return line
        # 따옴표가 있는 줄은 발언·원문 인용일 수 있으므로 전체를 보존한다.
        if _re.search(r'["“”‘’][^"“”‘’]+["“”‘’]', line):
            return line
        out = line
        replacements = (
            (r"보였습니다", "보였음"),
            (r"보입니다", "보임"),
            (r"([가-힣]+)되어야\s*합니다", r"\1 필요"),
            (r"([가-힣]+)해야\s*합니다", r"\1 필요"),
            (r"([가-힣]+(?:되지|하지))\s*않았습니다", r"\1 않음"),
            (r"([가-힣]+(?:되지|하지))\s*않습니다", r"\1 않음"),
            (r"([가-힣]+)하였습니다", r"\1함"),
            (r"([가-힣]+)했습니다", r"\1함"),
            (r"([가-힣]+)되었습니다", r"\1됨"),
            (r"([가-힣]+)됐습니다", r"\1됨"),
            (r"([가-힣]+)됩니다", r"\1됨"),
            (r"있습니다", "있음"),
            (r"없습니다", "없음"),
            (r"필요했습니다", "필요했음"),
            (r"필요합니다", "필요"),
            (r"가능했습니다", "가능했음"),
            (r"가능합니다", "가능"),
            (r"([가-힣]+)합니다", r"\1"),
            (r"어렵습니다", "어려움"),
            (r"쉽습니다", "쉬움"),
            (r"높습니다", "높음"),
            (r"낮습니다", "낮음"),
            (r"같습니다", "같음"),
            (r"아닙니다", "아님"),
            (r"입니다", ""),
        )
        for pattern, replacement in replacements:
            out = _re.sub(pattern, replacement, out)
        out = _re.sub(r"([가-힣]+)[이가] 필요(?=\s|[,.!?]|$)", r"\1 필요", out)
        return _re.sub(r"\.(?=\s*$)", "", out)

    rendered, in_code = [], False
    for line in value.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            rendered.append(line)
        else:
            rendered.append(line if in_code else compact_line(line))
    value = "\n".join(rendered).strip()

    # 한 줄 답에 장식 heading 금지. 둘 이상의 내용 block인데 heading이 하나도 없을 때만
    # 첫 block=요약, 나머지=상세로 최소 구조를 보완한다.
    if not _re.search(r"^#{2,4}\s+\S", value, _re.M):
        blocks = [b.strip() for b in _re.split(r"\n\s*\n", value) if b.strip()]
        substantive = [b for b in blocks if not b.startswith(">")]
        if len(substantive) >= 2:
            value = "### 요약\n\n" + blocks[0]
            if len(blocks) > 1:
                value += "\n\n### 상세\n\n" + "\n\n".join(blocks[1:])
    return value


def _align_draft_claims(text: str, state) -> str:
    """최종 문장과 실제 draft payload의 존재 여부가 모순되면 payload를 기준으로 고친다."""
    items = [i for i in ((state.get("draft") or {}).get("items") or [])
             if isinstance(i, dict) and i.get("summary")]
    negative = _re.search(r"(?:티켓|초안|작업).{0,30}(?:만들|생성|진행).{0,12}(?:수\s*없|불가능)",
                          text, _re.I | _re.S)
    if items and negative:
        rows = ["| # | 유형 | 제목 |", "|---:|---|---|"]
        rows += [f"| {n} | {it.get('type') or 'Task'} | {it.get('summary')} |"
                 for n, it in enumerate(items)]
        return (f"아래 {len(items)}건은 아직 생성되지 않은 티켓 초안입니다. 실제 생성 payload와 "
                "모순되는 안내를 제거했습니다.\n\n" + "\n".join(rows)
                + "\n\n승인 카드에서 내용과 배치를 확인해 주세요.")
    # payload가 없는데 초안을 승인하라고 말하면 없는 카드를 찾게 된다. 구조 설명/질문은
    # 화면의 별도 폼이 담당하므로 이유만 남긴다.
    claims_draft = _re.search(r"(?:티켓\s*)?초안.{0,30}(?:승인|확인해\s*주)|"
                              r"(?:승인\s*(?:요청|카드)).{0,20}(?:초안|티켓)", text,
                              _re.I | _re.S)
    if not items and claims_draft:
        reason = str((state.get("draft") or {}).get("rationale")
                     or state.get("situation") or "요청 조건을 다시 확인해야 합니다.").strip()
        return reason + "\n\n현재 승인할 티켓 초안은 없습니다."
    if items:
        owner_items = _assignment_aligned_items(items, state.get("assignments") or [])
        text = _drop_lineage_game_drift(text, state)
        text = _align_story_point_claims(text, state, items)
        text = _ensure_dod_claims(text, items)
        text = _align_scope_labels(text)
        text = _drop_unverified_reply_keys(text, state, items)
        text = _drop_false_epic_claims(text, items)
        text = _align_parent_labels(text, items)
        text = _align_item_owner_claims(text, owner_items)
        text = _align_child_owner_claims(text, owner_items)
        text = _align_assigned_owner_cautions(text, owner_items)
        text = _align_workload_claims(text, state)
        text = _render_assignment_section(text, owner_items, state.get("assignments") or [])
        text = _align_due_claims(text, items)
        text = _normalize_alternate_language(text)
        text = _drop_unsupported_assignment_experience(text, state)
        text = _drop_resolved_review_feedback(text, items)
        text = _align_child_presence_claims(text, items)
        text = _drop_unrequested_deployment_claims(text, state)
    return text


def _assignment_aligned_items(items: list, assignments: list) -> list:
    """Project final advisor rows onto a copy used only to validate the prose.

    In the fan-out graph the model-authored sentence can still reflect the pre-merge
    assignee while the approval payload already contains the merged advisor result.
    `assignments` is aligned to that payload by the join node, so it is authoritative for
    owner wording while the original draft remains untouched.
    """
    out = [dict(item) for item in (items or [])]
    rows = {row.get("index"): row for row in (assignments or [])
            if isinstance(row, dict) and isinstance(row.get("index"), int)}
    for index, item in enumerate(out):
        row = rows.get(index)
        if not row:
            continue
        if row.get("user"):
            item["assignee"] = str(row["user"])
        children = [dict(child) for child in (item.get("children") or [])
                    if isinstance(child, dict)]
        child_rows = {child.get("index"): child for child in (row.get("children") or [])
                      if isinstance(child, dict) and isinstance(child.get("index"), int)}
        for child_index, child in enumerate(children):
            child_row = child_rows.get(child_index)
            if child_row and child_row.get("user"):
                child["assignee"] = str(child_row["user"])
        if children:
            item["children"] = children
    return out


def _align_scope_labels(text: str) -> str:
    """An exclusion copied under a DoD label is still scope, not a completion check."""
    lines = []
    for line in str(text or "").splitlines():
        if "제외" in line and _re.search(r"완료\s*조건|DoD", line, _re.I):
            line = _re.sub(r"완료\s*조건(?:\s*\(DoD\))?|DoD", "제외 범위", line,
                           flags=_re.I)
        lines.append(line)
    return "\n".join(lines)


def _render_assignment_section(text: str, items: list, assignments: list) -> str:
    """Render the recommendation table from the exact rows merged into the approval payload."""
    rows = [row for row in (assignments or []) if isinstance(row, dict)
            and isinstance(row.get("index"), int) and row.get("user")]
    if not rows:
        return str(text or "")
    table = ["### 담당 제안", "", "| 티켓 | 추천 | 근거 | 대안 |", "|---|---|---|---|"]
    for row in rows:
        index = int(row["index"])
        title = (str(items[index].get("summary") or f"#{index + 1}")
                 if 0 <= index < len(items) else f"#{index + 1}")
        reasons = "<br>".join(str(x) for x in (row.get("reasons") or []) if str(x).strip()) or "-"
        alternates = "<br>".join(
            f"[~{alt.get('user')}] — {alt.get('why')}"
            for alt in (row.get("alternates") or [])
            if isinstance(alt, dict) and alt.get("user") and alt.get("user") != row.get("user")) or "-"
        table.append(f"| {title} | [~{row['user']}] | {reasons} | {alternates} |")
    block = "\n".join(table)
    source = str(text or "")
    pattern = (r"(?ms)^###\s*(?:할당(?:\s+(?:증거|근거))?|배정\s*근거|"
               r"담당(?:자)?\s*(?:제안|추천)|담당자?\s*및\s*배정\s*근거|"
               r"할당\s+증거\s+및\s+추천)"
               r"[^\n]*\n.*?(?=^###\s|\Z)")
    # Remove every model-authored assignment section.  A response may contain both a stale
    # prose list and a later table; replacing only the first leaves contradictory owners.
    source = _re.sub(pattern, "", source).strip()
    anchor = _re.search(r"(?m)^###\s*(?:검증|승인)", source)
    if anchor:
        return source[:anchor.start()].rstrip() + "\n\n" + block + "\n\n" + source[anchor.start():]
    return source.rstrip() + "\n\n" + block


def _align_due_claims(text: str, items: list) -> str:
    """Align a single draft's displayed deadline with the exact approval payload.

    Historical or evidence dates on unrelated lines remain untouched. Multi-item drafts can legitimately carry
    different dates, so they are left to the item table rather than guessed by position.
    """
    due_dates = {str(item.get("duedate") or "").strip()
                 for item in (items or []) if str(item.get("duedate") or "").strip()}
    if len(due_dates) != 1:
        return str(text or "")
    actual = next(iter(due_dates))
    lines = []
    for line in str(text or "").splitlines():
        if _re.search(r"마감|기한|due\s*date|duedate", line, _re.I):
            line = _re.sub(r"\b\d{4}-\d{2}-\d{2}\b", actual, line)
        lines.append(line)
    return "\n".join(lines)


def _ensure_research_status(text: str, state) -> str:
    """Preserve material internal checks and explicit external gaps from the topic dossier."""
    asked = (request_text(state) + " " + last_user_text(state)).strip()
    dossier = str(state.get("topic_dossier") or "")
    if not dossier or not ("조사" in asked and ("외부" in asked or "내부" in asked)):
        return str(text or "")
    internal = _re.search(r"(?:h2\.\s*)?내부\s*확인\s*(.*?)(?=(?:h2\.\s*)?외부\s*확인|$)",
                          dossier, _re.I | _re.S)
    external = _re.search(r"(?:h2\.\s*)?외부\s*확인\s*필요\s*(.*?)(?=\n\n|$)",
                          dossier, _re.I | _re.S)

    def facts(match):
        if not match:
            return []
        clean = _re.split(r"\s+이\s*문서의\s+", match.group(1), 1)[0]
        return [piece.strip(" -*.;") for piece in _re.split(r"\s+\*\s+", clean)
                if 4 <= len(piece.strip(" -*.;")) <= 220]

    rows = [("내부 확인", fact) for fact in facts(internal)]
    rows += [("외부 확인 필요", fact) for fact in facts(external)]
    normalized_text = _re.sub(r"\s+", "", str(text or ""))
    rows = [(kind, fact) for kind, fact in rows
            if _re.sub(r"\s+", "", fact) not in normalized_text]
    if not rows:
        return str(text or "")
    block = ["### 현재 상태", "", "| 구분 | 확인 결과 |", "|---|---|"]
    block += [f"| {kind} | {fact.replace('|', '·')} |" for kind, fact in rows[:8]]
    block_text = "\n".join(block)
    reference = _re.search(r"(?m)^###\s*(?:근거|참조)\s*$", str(text or ""))
    if reference:
        return (str(text or "")[:reference.start()].rstrip() + "\n\n" + block_text
                + "\n\n" + str(text or "")[reference.start():])
    return str(text or "").rstrip() + "\n\n" + block_text


def _drop_resolved_review_feedback(text: str, items: list) -> str:
    """최종 payload에서 이미 고친 DoD에 대한 이전 Auditor 의견을 답변에서 걷는다."""
    from app.agent.workflow.agents.work_architect import _dod_rows, _vague_dod

    targets = list(items)
    targets += [c for i in items for c in (i.get("children") or []) if isinstance(c, dict)]
    rows = [d for item in targets for d in _dod_rows(item.get("description") or "")]
    if not rows or _vague_dod(rows):
        return text
    kept = []
    for line in str(text or "").splitlines():
        if ("DoD" in line or "완료 조건" in line) and (
                "검증 가능하지" in line or "수정해야" in line or "불명확" in line
                or "누락" in line or "기술되지" in line):
            continue
        kept.append(line)
    return "\n".join(kept)


def _align_child_presence_claims(text: str, items: list) -> str:
    """실제 children이 있는데 없다고 말하는 응답을 payload 기준으로 정정한다."""
    count = sum(len(i.get("children") or []) for i in items if isinstance(i, dict))
    if not count:
        return text
    out = _re.sub(r"자식\s*작업은\s*별도로\s*설정되지\s*않았습니다?\.?”?",
                  f"자식 작업 {count}건이 설정되었습니다.", str(text or ""))
    out = _re.sub(r"하위\s*작업은\s*별도로\s*설정되지\s*않았습니다?\.?”?",
                  f"하위 작업 {count}건이 설정되었습니다.", out)
    out = _re.sub(r"(?m)^.*(?:하위\s*(?:Task|작업)|Sub-?Task)[^\n]{0,30}"
                  r"(?:별도로\s*)?제안할\s*예정[^\n]*$",
                  f"Sub-Task {count}건이 초안에 포함됨", out, flags=_re.I)
    out = _re.sub(r"(?m)^.*승인\s*후[^\n]{0,25}(?:하위\s*(?:Task|작업)|Sub-?Task)"
                  r"[^\n]{0,20}제안[^\n]*$", "", out, flags=_re.I)
    return out


def _drop_unrequested_deployment_claims(text: str, state) -> str:
    """요청하지 않은 배포 약속이 요약 문장에 되살아나지 않게 한다."""
    req = request_text(state)
    if _re.search(r"배포|릴리(?:스|즈)|운영\s*반영|production|prod\b", req, _re.I):
        return text
    out = _re.sub(r"(?:가이드|문서)가\s*최종\s*승인되고\s*배포됨",
                  "가이드 링크와 리뷰 결과가 parent ticket에 기록됨", str(text or ""))
    return out


def _dialogue_speakers(request: str) -> set[str]:
    """붙여넣은 대화의 화자는 담당자 주장이 아니라 사용자가 제공한 원문 인용이다."""
    return {
        m.group(1)
        for m in _re.finditer(
            r"(?m)^\s*(?:\[[^\]\n]+\]\s*)?([가-힣]{2,4})\s*:\s*\S", request or "")
    }


def _markdown_url(url: str) -> str:
    """Confluence 제목의 대괄호·괄호가 Markdown 링크 destination을 깨지 않게 한다."""
    from urllib.parse import quote
    return quote(str(url or ""), safe=":/?&=#%+@;,$!-_.~*'")


def _drop_lineage_game_drift(text: str, state) -> str:
    """데이터 리니지 요청의 답에서 게임 서사 문장만 제거한다."""
    req = request_text(state)
    game_terms = ("게임", "플레이어", "캐릭터", "클라이맥스", "결말", "몰입감")
    if "리니지" not in req or any(w in req for w in game_terms):
        return text
    lines = []
    for line in str(text or "").splitlines():
        parts = _re.split(r"(?<=[.!?])\s+", line)
        kept = [p for p in parts if not any(w in p for w in game_terms)]
        if any(p.strip() for p in kept):
            lines.append(" ".join(p for p in kept if p.strip()))
    return "\n".join(lines)


def _align_story_point_claims(text: str, state, items: list) -> str:
    """지원하지 않는 Story Point를 생성 payload에 넣었다는 주장을 제거하고 사실을 알린다."""
    req = request_text(state)
    m = _re.search(r"(?:스토리\s*포인트|Story\s*Points?|\bSP)\s*(?:를|은|:|=)?\s*(\d+)",
                   req, _re.I)
    if not m:
        return text
    # 현재 create payload 계약에는 Story Point 필드가 없다.
    lines = []
    for line in str(text or "").splitlines():
        parts = _re.split(r"(?<=[.!?])\s+", line)
        kept = []
        for sentence in parts:
            mentions = _re.search(r"스토리\s*포인트|Story\s*Points?|\bSP\b", sentence, _re.I)
            # 긍정/부정 어느 쪽이든 먼저 지우고 아래 canonical 안내를 정확히 한 번 둔다.
            if mentions:
                continue
            kept.append(sentence)
        if any(p.strip() for p in kept):
            lines.append(" ".join(p for p in kept if p.strip()))
    note = (f"**Story Point {m.group(1)} 미포함**: 에이전트 생성 payload가 지원하지 않는 "
            "필드이므로 티켓 생성 후 화면에서 직접 설정해야 합니다.")
    out = "\n".join(lines).rstrip()
    approval = _re.search(r"(?m)^(?:#{2,3}\s*|\*\*)승인", out)
    return (out[:approval.start()].rstrip() + "\n\n" + note + "\n" + out[approval.start():]
            if approval else out + "\n\n" + note)


def _ensure_dod_claims(text: str, items: list) -> str:
    """채팅 요약의 비거나 날조된 DoD 대신 실제 draft description의 DoD를 보장한다."""
    records = []
    for item in items:
        body = str(item.get("description") or "")
        section = _re.search(
            r"<h3>\s*(?:완료\s*조건(?:\s*\(DoD\))?|DoD)[^<]*</h3>\s*(.*?)(?=<h3>|$)",
            body, _re.S | _re.I)
        if not section:
            continue
        rows = [_re.sub(r"<[^>]+>", "", x).strip() for x in
                _re.findall(r"<li[^>]*>(.*?)</li>", section.group(1), _re.S | _re.I)]
        rows = [r for r in rows if r]
        if rows:
            records.append((str(item.get("summary") or "티켓"), rows))
    if not records:
        return text
    out = str(text or "")
    # 내부 보정 메모를 값처럼 쓴 줄과 내용 없는 DoD 줄은 제거한다.
    out = _re.sub(r"(?m)^\s*-?\s*\*\*완료\s*조건(?:\s*\(DoD\))?\*\*\s*:\s*"
                  r"(?:\[[^\n]*(?:placeholder|작성\s*지시|본문\s*미완성|작성\s*필요|"
                  r"기입\s*필요|기술되지\s*않음|최소한의\s*설명)[^\n]*\]|"
                  r"\([^\n]*(?:데이터\s*누락|누락|미정|확인\s*필요)[^\n]*\)|\s*)$", "", out,
                  flags=_re.I)
    actual_rows = [row for _title, rows in records for row in rows]
    out = "\n".join(
        line for line in out.splitlines()
        if not (_re.search(r"\*\*(?:완료\s*조건(?:\s*\(DoD\))?|DoD)\*\*\s*:", line,
                           _re.I)
                and not any(_re.sub(r"\s+", " ", row)[:24]
                            in _re.sub(r"\s+", " ", line) for row in actual_rows)))
    if len(records) > 1:
        table = ["### 실제 완료 조건", "| 티켓 | 완료 조건 |", "|---|---|"]
        table += [f"| {title} | {'<br>'.join(rows)} |" for title, rows in records]
        block = "\n".join(table)
        pattern = r"(?ms)^### 실제 완료 조건\s*$.*?(?=^#{2,3}\s|\Z)"
        if _re.search(pattern, out):
            out = _re.sub(pattern, block + "\n", out)
        else:
            approval = _re.search(r"(?m)^(?:#{2,4}\s*|\*\*)승인", out)
            out = (out[:approval.start()].rstrip() + "\n\n" + block + "\n" + out[approval.start():]
                   if approval else out.rstrip() + "\n\n" + block)
        return _re.sub(r"\n{3,}", "\n\n", out).strip()
    normalized = _re.sub(r"\s+", " ", out)
    missing = [(title, rows) for title, rows in records
               if not all(_re.sub(r"\s+", " ", row)[:24] in normalized for row in rows)]
    if not missing:
        return _re.sub(r"\n{3,}", "\n\n", out).strip()
    table = ["### 실제 완료 조건", "| 티켓 | 완료 조건 |", "|---|---|"]
    table += [f"| {title} | {'<br>'.join(rows)} |" for title, rows in missing]
    block = "\n".join(table)
    approval = _re.search(r"(?m)^(?:#{2,4}\s*|\*\*)승인", out)
    out = (out[:approval.start()].rstrip() + "\n\n" + block + "\n" + out[approval.start():]
           if approval else out.rstrip() + "\n\n" + block)
    return _re.sub(r"\n{3,}", "\n\n", out).strip()


def _drop_unverified_reply_keys(text: str, state, items: list) -> str:
    """생성 답변의 ticket key를 조사·사용자 지목·실제 payload 관계로 한정한다."""
    allowed = {str(k).upper() for k in (state.get("mentioned_keys") or []) if str(k)}
    allowed |= {str(e.get("key") or "").upper() for e in (state.get("evidence") or [])
                if isinstance(e, dict) and e.get("key")}
    for item in items:
        allowed |= {str(item.get(k) or "").upper() for k in ("parent", "epic")
                    if str(item.get(k) or "")}
    lines = []
    for line in str(text or "").splitlines():
        parts = _re.split(r"(?<=[.!?])\s+", line)
        kept = []
        for sentence in parts:
            keys = {k.upper() for k in
                    _re.findall(r"(?<![A-Z0-9])[A-Z][A-Z0-9]*-\d+(?![A-Z0-9])",
                                sentence, _re.I)}
            if keys and not keys <= allowed:
                continue
            kept.append(sentence)
        joined = " ".join(p for p in kept if p.strip()).strip()
        if joined:
            lines.append(joined)
    return "\n".join(lines)


def _drop_false_epic_claims(text: str, items: list) -> str:
    """draft에 없는 Epic 연결/포함 주장을 문장에서 제거한다."""
    actual = {str(i.get("epic") or "").upper() for i in items if str(i.get("epic") or "")}
    actual_epics = [i for i in items if str(i.get("type") or "").lower() == "epic"]
    had_false_type = bool(not actual_epics and _re.search(
        r"(?:새(?:로운)?\s*)?(?:Epic|에픽)(?:\s*(?:티켓|초안))?[^.\n]{0,40}"
        r"(?:생성|만들)|(?:Epic|에픽)\s*초안", str(text or ""), _re.I))
    if not actual_epics:
        text = _re.sub(r"(?mi)^(#{1,4}\s*)(?:Epic|에픽)\s*초안\s*$", r"\1티켓 초안",
                       str(text or ""))
    # 모델이 카드의 실제 유형보다 한 단계 크게 소개하는 경우가 있다. 단건 카드의 명시적
    # 유형 줄은 버리지 말고 payload 유형으로 고쳐 제목을 보존한다.
    if not actual_epics and len(items) == 1:
        actual_type = str(items[0].get("type") or "Task")
        text = _re.sub(r"(?mi)^(\s*-?\s*\*\*)(?:Epic|에픽)(\*\*\s*:\s*)",
                       rf"\1{actual_type}\2", str(text or ""))
        text = _re.sub(r"(?mi)^(\s*-?\s*\*\*)(?:Epic|에픽)\s*이름(\*\*\s*:\s*)",
                       rf"\1{actual_type} 제목\2", text)
        text = _re.sub(r"(?:Epic|에픽)(?:\s*의)?\s*(?:총괄\s*)?담당자",
                       f"{actual_type} 담당자", text, flags=_re.I)
    lines = []
    for line in str(text or "").splitlines():
        parts = _re.split(r"(?<=[.!?])\s+", line)
        kept = []
        for sentence in parts:
            if not actual_epics and _re.search(r"Epic\s*Name|에픽\s*이름", sentence, _re.I):
                continue
            epic_keys = {k.upper() for k in
                         _re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", sentence)}
            positive = bool(_re.search(r"(?:Epic|에픽).{0,100}(?:포함|연결|붙|관리|선택|배치|생성|"
                                       r"만들|격상|범위)|(?:포함|연결|붙|관리|선택|배치|생성|"
                                       r"만들|격상|범위).{0,100}(?:Epic|에픽)",
                                       sentence, _re.I))
            negative = bool(_re.search(r"없이|없음|아니|않|못|제거|뺐|보류", sentence))
            false_key = bool(epic_keys and not (epic_keys & actual))
            false_generic = not actual and positive
            mentions_epic = bool(_re.search(r"Epic|에픽", sentence, _re.I))
            # A Task/Story linked to an existing Epic may truthfully mention its *parent* Epic,
            # but it must never be introduced as a newly created Epic.  The old condition used
            # `not actual`, so merely having a valid Epic link disabled this protection (STARR1).
            false_draft_type = bool(not actual_epics and mentions_epic
                                    and _re.search(r"(?:새(?:로운)?\s*)?(?:Epic|에픽)"
                                                   r"(?:\s*(?:티켓|초안))?[^.\n]{0,36}"
                                                   r"(?:생성|만들)|(?:Epic|에픽)\s*초안",
                                                   sentence, _re.I))
            if false_draft_type or (not negative and (
                    (false_key and mentions_epic) or (positive and false_generic))):
                continue
            kept.append(sentence)
        joined = " ".join(p for p in kept if p.strip()).strip()
        if joined:
            lines.append(joined)
    out = "\n".join(lines)
    if had_false_type and len(items) == 1:
        item = items[0]
        issue_type = str(item.get("type") or "Task")
        identity = f"**실제 티켓 초안**: {issue_type} · {item.get('summary')}"
        if item.get("epic"):
            identity += f" · 상위 Epic {item['epic']}"
        out = identity + ("\n\n" + out.strip() if out.strip() else "")
    if items and "승인" not in out:
        out = out.rstrip() + "\n\n이 티켓 초안은 아직 생성되지 않았습니다. 승인 카드에서 확인해 주세요."
    return out


def _align_parent_labels(text: str, items: list) -> str:
    """Sub-Task의 parent를 Epic으로 표시하는 계층 라벨 오류를 고친다."""
    is_subtasks = bool(items) and all(
        str(i.get("type") or "").lower().startswith("sub") for i in items)
    has_parent = any(i.get("parent") for i in items)
    if not (is_subtasks and has_parent):
        return text
    out = _re.sub(r"(?mi)^\|([^\n]*?)\bEpic\b([^\n]*?)\|$", r"|\1부모\2|", str(text or ""))
    return _re.sub(r"(?mi)^(\s*-\s*\*\*)Epic(\*\*\s*:)", r"\1부모\2", out)


def _align_child_owner_claims(text: str, items: list) -> str:
    """답변의 Sub-Task 담당을 실제 child payload와 맞춘다."""
    children = [c for i in items for c in (i.get("children") or [])
                if isinstance(c, dict) and c.get("summary") and c.get("assignee")]
    out = str(text or "")
    if not children:
        return out
    out = _re.sub(r"(?mi)^(#{1,4}\s*)하위\s*Task\s*$", r"\1Sub-Task", out)
    # 기술 주제를 보존하려고 payload 제목은 길게 만들지만, 답변은 흔히 공통 접두를 생략하고
    # "설계 완료"처럼 쓴다. 전체 제목만 찾으면 바로 그 줄의 잘못된 담당자를 놓친다.
    import os
    titles = [str(c["summary"]) for c in children]
    common = os.path.commonprefix(titles)
    common = common[:common.rfind(" ") + 1] if " " in common else ""
    aliases = []
    for child, title in zip(children, titles):
        short = title[len(common):].strip() if common and title.startswith(common) else ""
        aliases.append((child, [a for a in (title, short) if a]))

    seen = set()
    current_child = None
    lines = []
    for line in out.splitlines():
        matched = [(c, aa) for c, aa in aliases if any(a in line for a in aa)]
        if len(matched) == 1:
            current_child = matched[0][0]
        if current_child is not None and "담당" in line:
            child = current_child
            actual = str(child["assignee"])
            line = _re.sub(
                r"(?<![A-Za-z0-9.])(?:skcc\.)?[a-z]{1,2}\d{2,6}(?![A-Za-z0-9])",
                actual, line, flags=_re.I)
            line = _re.sub(r"\s*[（(]\s*(?:임시|가안|조정\s*가능)\s*[)）]", "", line)
            seen.add(str(child["summary"]))
        lines.append(line)
    out = "\n".join(lines)
    exact_block = all(str(c["summary"]) in out and str(c["assignee"]) in out
                      for c in children)
    if not exact_block:
        # 축약 제목·순서로 담당이 뒤바뀔 수 있으므로 실제 payload 표를 한 번 보장한다.
        rows = ["### Sub-Task 담당", "| Sub-Task | 담당 |", "|---|---|"]
        rows += [f"| {c['summary']} | [~{c['assignee']}] |" for c in children]
        block = "\n".join(rows) + "\n\n"
        approval = _re.search(r"(?m)^#{2,3}\s*승인", out)
        out = (out[:approval.start()] + block + out[approval.start():]
               if approval else out.rstrip() + "\n\n" + block.rstrip())
    return out


def _normalize_alternate_language(text: str) -> str:
    """'대안인데 고려하지 않음'이라는 자기모순을 검토 가능한 후보 표현으로 고친다."""
    return _re.sub(
        r"부하가\s*높(?:아|아서)\s*(?:대안으로\s*)?(?:고려하지\s*않(?:음|습니다)|"
        r"적합하지\s*않습니다|제외(?:했|함|합니다)?)",
        "현재 담당자보다 부하가 높지만 담당 변경이 필요할 때 검토할 수 있음",
        str(text or ""), flags=_re.I)


def _drop_unsupported_assignment_experience(text: str, state) -> str:
    """부하 수치만 조회했는데 모듈 경험까지 있다고 확장한 문구를 제거한다."""
    reasons = [str(r) for a in (state.get("assignments") or []) if isinstance(a, dict)
               for r in (a.get("reasons") or [])]
    if any(_re.search(r"유사|관련.+(?:경험|담당)|경험.+(?:티켓|작업)", r) for r in reasons):
        return text
    return _re.sub(r",\s*[^,.\n]{0,80}(?:경험|이력)(?:이\s*)?(?:있|풍부|보유)[^,.\n]*",
                   "", str(text or ""), flags=_re.I)


def _align_workload_claims(text: str, state) -> str:
    """사번 옆 진행중 건수와 부하의 정성 표현을 최종 PeopleAdvisor 근거와 맞춘다."""
    loads = {}
    for row in (state.get("assignments") or []):
        if not isinstance(row, dict):
            continue
        candidates = [(row.get("user"), " ".join(str(x) for x in row.get("reasons") or []))]
        candidates += [(x.get("user"), x.get("why")) for x in (row.get("children") or [])
                       if isinstance(x, dict)]
        candidates += [(x.get("user"), x.get("why")) for x in (row.get("alternates") or [])
                       if isinstance(x, dict)]
        for user, why in candidates:
            m = _re.search(r"진행\s*중(?:인)?\s*(?:티켓|작업)?\s*(\d+)\s*건|"
                           r"진행중\s*(\d+)\s*건", str(why or ""))
            if user and m:
                loads[str(user)] = next(x for x in m.groups() if x is not None)
    lines = []
    for line in str(text or "").splitlines():
        users = [u for u in loads if u in line]
        if len(users) == 1:
            value = loads[users[0]]
            line = _re.sub(r"(진행\s*중(?:인)?\s*(?:티켓|작업)?\s*)\d+(\s*건)|"
                           r"(진행중\s*)\d+(\s*건)",
                           lambda m: ((m.group(1) or m.group(3)) + value
                                      + (m.group(2) or m.group(4))), line)
            values = sorted({int(x) for x in loads.values()})
            if len(values) >= 2:
                numeric = int(value)
                level = ("가장 낮" if numeric == values[0]
                         else "가장 높" if numeric == values[-1]
                         else "중간 수준")
                causal = _re.search(
                    r"(?:상대적으로\s*)?부하가\s*(?:가장\s*)?(?:적어|낮아|높아)", line)
                if causal:
                    phrase = (f"부하가 {level}아서" if level != "중간 수준"
                              else "부하가 중간 수준이어서")
                    line = line[:causal.start()] + phrase + line[causal.end():]
                else:
                    line = _re.sub(
                        r"(?:상대적으로\s*)?부하가\s*(?:가장\s*)?(?:적|낮|높)"
                        r"(?:음|습니다|은\s*편(?:임|입니다)?)?",
                        f"부하가 {level}음" if level != "중간 수준" else "부하가 중간 수준임",
                        line)
        # `A 10건, 대안 B 13건보다 부하가 높음`처럼 두 사람을 한 문장에 비교한 경우는
        # 전역 순위보다 그 문장 안의 수치를 직접 비교한다.
        nums = [int(x) for x in _re.findall(r"(\d+)\s*건", line)]
        if len(nums) >= 2 and "보다" in line and "부하" in line:
            relation = "더 낮음" if nums[0] < nums[1] else "더 높음" if nums[0] > nums[1] else "같음"
            line = _re.sub(
                r"(?:상대적으로\s*)?부하가\s*(?:가장\s*)?(?:적|낮|높)"
                r"(?:음|습니다|은\s*편(?:임|입니다)?)?",
                f"부하가 {relation}", line)
        lines.append(line)
    return "\n".join(lines)


def _align_item_owner_claims(text: str, items: list) -> str:
    """top-level Task/Sub-Task의 담당 문장을 실제 payload와 맞추고 정규 표를 보장한다."""
    assigned = [(str(i.get("summary") or ""), str(i.get("assignee") or ""))
                for i in items if i.get("summary") and i.get("assignee")]
    if not assigned:
        return text
    alias_rows = []
    bracket_counts = {}
    for title, _ in assigned:
        m = _re.match(r"^\s*\[([^]]+)\]", title)
        if m:
            bracket_counts[m.group(1).strip()] = bracket_counts.get(m.group(1).strip(), 0) + 1
    for title, who in assigned:
        plain = _re.sub(r"^\s*\[[^]]+\]\s*", "", title).strip()
        aliases = {title, plain}
        trimmed = _re.sub(r"\s+(?:수행|완료|진행)$", "", plain).strip()
        if trimmed:
            aliases.add(trimmed)
        bracket = _re.match(r"^\s*\[([^]]+)\]", title)
        if bracket and bracket_counts.get(bracket.group(1).strip()) == 1:
            aliases.add(bracket.group(1).strip())
        alias_rows.append((title, who, sorted((a for a in aliases if a), key=len, reverse=True)))

    lines, current = [], None
    for line in str(text or "").splitlines():
        matches = []
        for title, who, aliases in alias_rows:
            if any(a and a in line for a in aliases):
                matches.append((title, who))
        if not matches:
            line_tokens = [t for t in _re.findall(r"[A-Za-z0-9가-힣]+", line)
                           if len(t) >= 2 and t not in {"담당자", "담당", "근거", "현재"}]
            scored = []
            for title, who, _ in alias_rows:
                title_tokens = [t for t in _re.findall(r"[A-Za-z0-9가-힣]+", title)
                                if len(t) >= 2]
                score = sum(1 for token in line_tokens
                            if any(token in part or part in token for part in title_tokens))
                if score >= 2:
                    scored.append((score, title, who))
            if scored:
                best = max(x[0] for x in scored)
                winners = [(title, who) for score, title, who in scored if score == best]
                if len(winners) == 1:
                    matches = winners
        if len(matches) == 1:
            current = matches[0]
        if current and "대안" not in line and "후보" not in line and (
                "담당" in line or len(matches) == 1):
            title, actual = current
            line = _re.sub(r"(?<![A-Za-z0-9.])(?:skcc\.)?[a-z]{1,2}\d{2,6}(?![A-Za-z0-9])",
                           actual, line, flags=_re.I)
        lines.append(line)
    out = "\n".join(lines)
    if len(assigned) > 1:
        rows = ["### 실제 담당자", "| 티켓 | 담당 |", "|---|---|"]
        rows += [f"| {title} | {who} |" for title, who in assigned]
        block = "\n".join(rows)
        approval = _re.search(r"(?m)^(?:#{2,4}\s*|\*\*)승인|^위의?\s+초안", out)
        out = (out[:approval.start()].rstrip() + "\n\n" + block + "\n" + out[approval.start():]
               if approval else out.rstrip() + "\n\n" + block)
    return out


def _align_assigned_owner_cautions(text: str, items: list) -> str:
    """실제 child 담당자를 '제외/부적합'이라고 설명하는 문장을 정정한다."""
    assigned = {str(i.get("assignee") or "") for i in items if i.get("assignee")}
    child_assigned = {
        str(c.get("assignee") or "")
        for i in items
        for c in (i.get("children") or [])
        if isinstance(c, dict) and c.get("assignee")
    }
    assigned |= child_assigned
    lines = []
    for line in str(text or "").splitlines():
        owners = [u for u in assigned if u and (u in line or u.split(".")[-1] in line)]
        who = owners[0] if owners else ""
        if who and _re.search(r"부하\s*조정\s*필요|재검토\s*필요", line):
            cleaned = _re.sub(r"\s*[（(]?\s*(?:부하\s*조정\s*필요|재검토\s*필요)\s*[)）]?",
                              "", line).rstrip()
            lines.append(cleaned)
            continue
        if who and _re.search(r"제외|고려하지|부적합|존재하지|확인되지|실재하지|찾을 수 없|"
                              r"부하\s*조정\s*필요|재검토\s*필요", line):
            prefix = "- " if line.lstrip().startswith("-") else ""
            if len(owners) > 1:
                lines.append(f"{prefix}사용자 지정 담당({', '.join(sorted(owners))})은 실재 사용자 "
                             "검증을 거쳐 승인 payload에 반영되었습니다.")
            else:
                assignment = ("Sub-Task 담당으로 분량 배분됨" if who in child_assigned
                              else "승인 payload의 담당자로 반영됨")
                lines.append(f"{prefix}**{who}**: {assignment}; 현재 부하는 승인 화면에서 "
                             "함께 검토합니다.")
        else:
            lines.append(line)
    return "\n".join(lines)


def _drop_boilerplate_closers(text: str) -> str:
    """답 **끝에 달린 상투 맺음말**을 지운다. 문장 단위로 본다(줄 끝에 붙어 오기도 한다)."""
    lines = (text or "").splitlines()
    # 뒤에서부터 본다 — 상투구는 마지막 문단에 붙는다. 표·목록·참조 줄은 지나친다.
    checked = 0
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if not s:
            continue
        if s.startswith(("|", "- ", "* ", ">", "#", "[")) or _re.match(r"^\d+[.)]", s):
            continue                       # 표·목록·참조 — 여기서 끝나는 답이 흔하다
        parts = _re.split(r"(?<=[.!?])\s+|(?<=다\.)\s+", s)
        while parts and _CLOSER_RE.match(parts[-1].strip()) and "DL-" not in parts[-1]:
            parts.pop()
        lines[i] = " ".join(p for p in parts if p.strip())
        checked += 1
        if checked >= 2:                   # 마지막 두 문단이면 충분하다
            break
    out = "\n".join(lines)
    return _re.sub(r"\n{3,}", "\n\n", out).strip()


def _dedupe_sentences(text: str) -> str:
    """평문에서 **똑같이 반복된 문장**을 뒤엣것부터 지운다.

    표(`|`)·목록(`-`,`1.`)·인용·참조 줄은 건드리지 않는다 — 거기서는 같은 문구가
    정당하게 되풀이된다. 문장 하나가 통째로 겹칠 때만 접으므로, 비슷하지만 다른
    문장은 둘 다 남는다.
    """
    import re as _re
    seen, out_lines = set(), []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith(("|", "-", "*", ">", "#", "[")) or _re.match(r"^\d+[.)]", s):
            out_lines.append(line)
            continue
        parts = _re.split(r"(?<=[.!?])\s+|(?<=다\.)\s+", s)
        kept = []
        for p in parts:
            k = _re.sub(r"\s+", " ", p).strip()
            if len(k) >= 10 and k in seen:
                continue                    # 이미 한 말이다
            if len(k) >= 10:
                seen.add(k)
            kept.append(p)
        joined = " ".join(x for x in kept if x.strip())
        if joined.strip():
            out_lines.append(line[:len(line) - len(line.lstrip())] + joined)
    # 문장이 통째로 빠져 생긴 빈 줄 3연속은 2줄로
    return _re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()


def _normalize_ticket_detail_sections(text: str) -> str:
    """전용 진행 Task bullet을 one-ticket `ticket-detail` token으로 정규화한다.

    모델이 raw key, 다른 typed token, 제목을 병기해도 key만 보존한다. 상세 badge가 이미
    key/title/assignee/status를 채우므로 뒤 텍스트는 중복이며, 다음 heading부터는 건드리지 않는다.
    """
    heading = _re.compile(r"^#{2,4}\s*현재\s*진행\s*중인\s*(?:Task|태스크)\s*$", _re.I)
    key = _re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\b")
    out, active = [], False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if heading.match(stripped):
            active = True
            out.append(line)
            continue
        if active and stripped.startswith("#"):
            active = False
        if active and _re.match(r"^[-*+]\s+", stripped):
            found = key.search(stripped)
            if found:
                indent = line[:len(line) - len(line.lstrip())]
                out.append(f"{indent}- {{{{ticket-detail:{found.group(1)}}}}}")
                continue
        out.append(line)
    return "\n".join(out)


def _normalize_badge_repetitions(text: str) -> str:
    """typed ticket badge가 이미 가진 key/title/assignee/status의 평문 반복을 제거한다."""
    out = []
    inline_title = _re.compile(
        r"(\{\{ticket-inline:[A-Z][A-Z0-9]*-\d+\}\})"
        r"\s*(?:[\"“][^\"”\n]+[\"”]|\[[^\]\n]+\]\s*[^\n|·—]{2,80})",
        _re.I,
    )
    detail = _re.compile(r"^(\s*(?:\[\d+\]\s*)?(?:[-*+]\s*)?"
                         r"\{\{ticket-detail:[A-Z][A-Z0-9]*-\d+\}\})"
                         r"\s*(?:[—·|:-]\s*)?(.+)$", _re.I)
    duplicate_prefix = _re.compile(
        r"^\s*(?:담당|assignee|상태|status|진행\s*중\s*$|완료\s*$|할당\s*$|"
        r"reopen(?:ed)?\s*$|우선순위|마감|기한|\[[A-Za-z]+\]|[\"“])", _re.I)
    for line in str(text or "").splitlines():
        line = inline_title.sub(r"\1", line)
        matched = detail.match(line)
        if matched:
            suffix = matched.group(2)
            # 제목만 바로 반복한 뒤 새로운 근거 사실이 이어지는 경우에는 사실까지 버리지 않는다.
            suffix = _re.sub(r'^\s*["“][^"”\n]+["”]\s*(?:[—·|,:-]\s*)?', '', suffix)
            if not suffix or duplicate_prefix.search(suffix):
                line = matched.group(1)
            elif suffix != matched.group(2):
                line = matched.group(1) + " — " + suffix
        out.append(line.rstrip())
    return "\n".join(out)


def _badgeify_known_ticket_mentions(text: str, state) -> str:
    """검증된 티켓의 평문 key를 용도에 맞는 최소 inline badge로 기계화한다.

    이미 typed token 안에 든 key는 건드리지 않는다. 제목이 바로 이어지면 badge 자체의
    hover 정보와 중복되므로 제목도 함께 접는다.
    """
    known = {str(key).upper() for key in (state.get("mentioned_keys") or [])
             if _re.match(r"^[A-Z][A-Z0-9]*-\d+$", str(key), _re.I)}
    for evidence in (state.get("evidence") or []):
        if isinstance(evidence, dict):
            key = str(evidence.get("key") or "").upper()
            if _re.match(r"^[A-Z][A-Z0-9]*-\d+$", key):
                known.add(key)
    # Research material is produced by scoped Jira/Confluence retrieval, unlike raw user
    # prose.  Ticket keys found there are verified references and must obey the same badge
    # contract even when the Research Analyst represented them only inside a document
    # observation rather than as a top-level evidence row.
    for field in ("topic_dossier", "pre_survey"):
        known.update(_re.findall(
            r"(?<![0-9A-Z-])([A-Z][A-Z0-9]*-\d+)(?![0-9A-Z-])",
            str(state.get(field) or ""), _re.I))
    known.update(_re.findall(
        r"(?<![0-9A-Z-])([A-Z][A-Z0-9]*-\d+)(?![0-9A-Z-])",
        str(state.get("ticket_progress") or ""), _re.I))
    value = str(text or "")
    for key in sorted(known, key=len, reverse=True):
        # ':' 앞은 {{ticket-*:KEY}} 내부이므로 제외. 영숫자/하이픈 경계도 엄격히 유지.
        pattern = (rf"(?<![:A-Z0-9-]){_re.escape(key)}(?![A-Z0-9-])"
                   r"(?:\s*[\"“][^\"”\n]{1,160}[\"”])?")
        value = _re.sub(pattern, f"{{{{ticket-inline:{key}}}}}", value)
    return value


def _ensure_external_research_coverage(text: str, state) -> str:
    """내부+외부 공식 조사를 요청했으면 검증된 외부 URL을 답에 보존한다.

    Semantic conflict resolution belongs to Research Analyst, where source
    scope, dates, and provenance are all present. The former string scanner
    treated an older ``not yet`` record plus later completion evidence as an
    unresolved contradiction and rewrote a correct conclusion after synthesis.
    This rendering guard therefore owns only the deterministic URL contract.
    """
    asked = request_text(state) + " " + last_user_text(state)
    if not ("외부" in asked and any(w in asked for w in ("조사", "자료", "공식", "근거"))):
        return text
    sources = []
    for evidence in (state.get("evidence") or []):
        if not isinstance(evidence, dict):
            continue
        url = str(evidence.get("url") or "").strip()
        if _is_external_source_url(url):
            sources.append((str(evidence.get("title") or "공식 자료").strip(), url,
                            str(evidence.get("why") or "").strip()))
    if not sources:
        for title, url in _re.findall(
                r"^-\s*(.+?)\s*·\s*공식\s*—[^\n]*\((https?://[^)\s]+)\)\s*$",
                str(state.get("web_context") or ""), _re.M):
            # Navigation pages and language API indices are search artifacts, not useful
            # decision evidence unless the user explicitly asked for those APIs.
            if _re.search(r"Search the documentation|Namespace Reference|\s-\sRust$|API Reference",
                          title, _re.I):
                continue
            sources.append((title.strip(), url, "공식 자료"))

    value = str(text or "").rstrip()
    if sources:
        # 조사 전 상태 스냅샷의 '확인 필요'를 최종 답에 그대로 두면 실제 외부조사를 하지
        # 않은 것처럼 읽힌다. 확인됐다고 과장하지 않고, 조사한 쟁점이라는 중립 라벨로 전환.
        value = value.replace("| 외부 확인 필요 |", "| 외부 조사 범위 |")
    missing = [(title, url, why) for title, url, why in sources if url not in value]
    if missing:
        lines = ["### 외부 공식 근거", ""]
        for title, url, why in missing[:3]:
            lines.append(f"- [{title}]({_markdown_url(url)})" + (f" — {why}" if why else ""))
        value += "\n\n" + "\n".join(lines)

    return value


def _is_external_source_url(url: str) -> bool:
    """Jira/Confluence/localhost 등 내부 링크가 외부 공식 근거로 승격되지 않게 분류한다."""
    from urllib.parse import urlparse
    import ipaddress

    try:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or not host or host == "localhost" \
                or host.endswith((".local", ".internal")):
            return False
        try:
            if ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback:
                return False
        except ValueError:
            pass
        try:
            from app.infra.settings import get_settings
            settings = get_settings()
            internal_hosts = {
                (urlparse(str(base or "")).hostname or "").lower().rstrip(".")
                for base in (getattr(settings, "jira_base", ""),
                             getattr(settings, "confluence_base", ""))
                if base
            }
            if host in internal_hosts:
                return False
        except Exception:
            pass
        return True
    except Exception:
        return False


def _drop_unsupported_guarantees(text: str, state) -> str:
    """Do not turn a format description into an unsupported outcome guarantee.

    ``보장`` is a materially stronger claim than ``저장한다`` or ``지원한다``.  If no
    request or verified research material uses that guarantee, remove only the attached
    comma-clause.  Standalone guarantee sentences are retained as an explicit validation
    gap instead of being silently presented as fact.
    """
    source = " ".join(str(state.get(key) or "") for key in (
        "request_text", "topic_dossier", "pre_survey", "situation",
        "knowledge_brief", "evidence",
    ))
    value = str(text or "")
    if "보장" not in source:
        value = _re.sub(
            r"\s*[,，]\s*[^,.\n]{2,120}?(?:을|를|이|가)?\s*보장(?:함|됨|한다|합니다)?(?=[.\n]|$)",
            "", value,
        )
        value = _re.sub(
            r"(?m)^(\s*[-*]?\s*)?([^\n.]{2,160}?보장(?:함|됨|한다|합니다)?)[.]?\s*$",
            lambda m: ((m.group(1) or "") + "해당 보장 효과는 검증 필요"),
            value,
        )
    # A search-result snippet is candidate material, not a selected source.  Do not let a
    # model attach an uncited optimization/quality benefit unless the request or structured
    # research evidence actually retained that effect.
    if not _re.search(r"쿼리\s*최적화|query\s*optim", source, _re.I):
        value = _re.sub(
            r"\s*NDV\s*통계(?:는|가)?\s*쿼리\s*최적화에\s*사용될\s*수\s*있음[.]?",
            "", value, flags=_re.I,
        )
        value = _re.sub(
            r"이는\s+[^.\n]{0,180}?성능\s*최적화에\s*기여할\s*수\s*있음을\s*시사하지만,?\s*",
            "", value, flags=_re.I,
        )
    unresolved_reader = bool(_re.search(
        r"StarRocks[^.\n]{0,100}(?:Puffin|NDV)[^.\n]{0,120}"
        r"(?:확인되지|미확인|검증\s*필요|지원\s*여부)", source, _re.I,
    ))
    latest = last_user_text(state)
    support_confirmed_now = bool(_re.search(
        r"StarRocks[^.\n]{0,100}(?:Puffin|NDV)[^.\n]{0,100}"
        r"(?:소비\s*(?:지원|확인|성공|완료)|지원(?:함|한다|됨|된다))",
        latest, _re.I,
    ))
    if unresolved_reader and not support_confirmed_now:
        value = _re.sub(
            r"(?m)(?:^|(?<=[.!?])\s+)StarRocks[^.!?\n]{0,220}?"
            r"Puffin[^.!?\n]{0,120}?(?:소비할\s*수\s*있음|소비를?\s*지원(?:함|한다|됨|된다))"
            r"[.!?]?\s*",
            "", value, flags=_re.I,
        )
    value = _re.sub(r"([가-힣]+)이며\.", r"\1임.", value)
    value = _re.sub(r"([가-힣]+)되며\.", r"\1됨.", value)
    value = _re.sub(r"([가-힣]+)하며\.", r"\1함.", value)
    value = _re.sub(r"\s+([.,!?])", r"\1", value)
    return value


def _dedupe_refs(text: str) -> str:
    """Legacy-compatible wrapper around the single evidence-index owner."""
    return canonicalize_evidence_index(text)


def _fold_standalone_sources(text: str) -> str:
    """Move legacy standalone and external-source blocks into the canonical index.

    The old document-link safeguard runs before the evidence index and can still emit a
    standalone source.  Leaving it in the body creates two visible source lists after the
    canonical index hydrates the same URL.  Treat the line as an input grammar only and let
    the one index owner renumber/deduplicate it.
    """
    value = str(text or "")
    found: list[str] = []

    def take(match):
        source = match.group(1).strip()
        if source not in found:
            found.append(source)
        return ""

    value = _re.sub(
        r"(?m)^\s*출처\s*:\s*(\[[^\n]+?\]\(https?://[^\s)]+\)|https?://\S+)\s*$",
        take, value,
    )

    def take_external_section(match):
        before = len(found)
        for line in match.group(1).splitlines():
            source = _re.sub(r"^\s*[-*+]\s+", "", line).strip()
            if _re.match(r"\[[^\]]+\]\(https?://", source) and source not in found:
                found.append(source)
        return "" if len(found) > before else match.group(0)

    value = _re.sub(
        r"(?ms)^###\s*외부\s*공식\s*근거\s*$\s*(.*?)(?=^###\s|\Z)",
        take_external_section, value,
    )
    if not found:
        return value
    heading = _re.search(r"(?m)^#{1,4}\s*(?:근거|참조)\s*$", value)
    rows = "\n".join(f"[{9000 + index}] {source}" for index, source in enumerate(found, 1))
    if heading:
        value = value[:heading.end()] + "\n" + rows + value[heading.end():]
    else:
        value = value.rstrip() + "\n\n### 근거\n\n" + rows
    return value.strip()


_DIRECT_INPUT_SOURCE_RE = _re.compile(
    r"^(?:대화\s*기록|사용자\s*(?:입력|제공\s*내용)|제공된\s*(?:내용|대화)|회의록\s*원문|"
    r"(?:첨부\s*)?(?:문서|메모)(?:\s*발췌)?|[^/\\]+\.(?:docx?|pdf|txt|md|xlsx?|pptx?))$",
    _re.I,
)


def _is_direct_input_pseudo_source(item: dict) -> bool:
    """User-provided prose is request data, not a linkable research source.

    Treating pasted chat as an ``external`` source produced a dead ``대화 기록`` row and
    an internal grounding warning in the approval reply.  Direct input still grounds the
    draft, but it must not pretend to be a hyperlinkable evidence item.
    """
    if not isinstance(item, dict) or str(item.get("url") or "").strip():
        return False
    key = str(item.get("key") or "").strip()
    title = str(item.get("title") or "").strip()
    return bool(_DIRECT_INPUT_SOURCE_RE.fullmatch(title)
                or _DIRECT_INPUT_SOURCE_RE.fullmatch(key)
                or ("대화" in key and title == "대화 기록")
                # Model-generated labels for pasted chat vary by speaker names. With
                # no URL/key, both "김운영 대화" and "김운영과 이개발의 대화" are still
                # the user's request payload, never an independently verifiable source.
                or bool(_re.search(r"(?:대화|대화록)$", key))
                or bool(_re.search(r"(?:대화|대화록)$", title)))


def _is_negative_search_pseudo_source(item: dict) -> bool:
    """A failed lookup is an uncertainty, not a source users can open and verify.

    Research sometimes materializes ``topic X was not found`` as evidence whose key and
    title are both the raw topic.  Rendering that row creates a dead citation and exposes a
    grounding diagnostic in otherwise valid Bug drafts.  Keep the uncertainty in research
    state, but never number it as provenance.
    """
    if not isinstance(item, dict) or str(item.get("url") or "").strip():
        return False
    key = str(item.get("key") or "").strip()
    title = str(item.get("title") or "").strip()
    if not key or _re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", key, _re.I):
        return False
    observations = " ".join(
        str(row.get("text") or "") for row in (item.get("observations") or [])
        if isinstance(row, dict)
    )
    material = " ".join((str(item.get("why") or ""), str(item.get("limitations") or ""), observations))
    negative = bool(_re.search(
        r"찾지\s*못|확인(?:되지\s*않|할\s*수\s*없)|기록이\s*없|"
        r"검색(?:된|\s*결과).{0,80}(?:0\s*(?:건|개)|없|존재하지\s*않|나타나지\s*않|찾지\s*못)|"
        r"나타나지\s*않",
        material,
    ))
    if not negative:
        return False
    # Mismatched synthetic ids such as `internal-duplicate-check` are common for query
    # artifacts. Low-confidence/context-only misses still are uncertainties, not sources.
    return (key == title
            or str(item.get("fitness") or "").strip() == "context-only"
            or str(item.get("confidence") or "").strip() == "low"
            or any(str(row.get("source") or "") in {"query", "web"}
                   for row in (item.get("observations") or []) if isinstance(row, dict)))


def _is_non_renderable_evidence(item: dict) -> bool:
    return _is_direct_input_pseudo_source(item) or _is_negative_search_pseudo_source(item)


def _drop_direct_input_source_rows(text: str) -> str:
    """Remove legacy pseudo-source rows before canonical numbering and late grounding."""
    lines = str(text or "").splitlines()
    out: list[str] = []
    dropping = False
    for line in lines:
        root = _re.match(r"^\s*\[(\d+)\]\s+(.+?)\s*$", line)
        if root:
            label = root.group(2).strip()
            markdown = _re.fullmatch(r"\[([^]]+)\]\(([^)]+)\)", label)
            if markdown and (markdown.group(2).strip().casefold() == "verified url"
                             or _DIRECT_INPUT_SOURCE_RE.fullmatch(markdown.group(1).strip())):
                label = markdown.group(1).strip()
            else:
                label = _re.sub(r"\s*[—–-].*$", "", label).strip()
            dropping = bool(_DIRECT_INPUT_SOURCE_RE.fullmatch(label))
            if dropping:
                continue
        elif dropping:
            if _re.match(r"^\s*[-*+]\s+", line) or not line.strip():
                continue
            dropping = False
        if not dropping:
            out.append(line)
    value = "\n".join(out)
    # The sole pseudo source commonly leaves an empty heading.  The canonical index can
    # later append real sources if any exist.
    value = _re.sub(r"(?ms)^###\s*(?:근거|참조)\s*$\s*(?=^###\s|\Z)", "", value)
    return _re.sub(r"\n{3,}", "\n\n", value).strip()


def _merge_evidence_index(text: str, state) -> str:
    """Union model references and structured research provenance in the persisted reply."""
    evidence = [item for item in (state.get("evidence") or [])
                if isinstance(item, dict) and not _is_non_renderable_evidence(item)]
    return canonicalize_evidence_index(
        _drop_direct_input_source_rows(_fold_standalone_sources(text)),
        evidence=evidence,
        related_docs=state.get("related_docs") or [],
    )


def _rebind_definition_citations(text: str) -> str:
    """Bind general technical definitions to an external specification source.

    Meeting summaries sometimes copied an old internal marker onto a sentence that defines
    a public file format.  Once the single index is built, source numbers are stable and a
    definition can be rebound to the best title-matching external source without an LLM.
    Project-state claims keep their internal citations.
    """
    value = str(text or "")
    heading = _re.search(r"(?m)^###\s*근거\s*$", value)
    if not heading:
        return value
    body, index = value[:heading.start()].rstrip(), value[heading.start():]
    external = []
    tickets = {}
    for number, source in _re.findall(r"(?m)^\[(\d+)\]\s+(.+)$", index):
        ticket = _re.search(r"\{\{ticket-detail:([A-Z][A-Z0-9]*-\d+)\}\}", source, _re.I)
        if ticket:
            tickets[ticket.group(1).upper()] = number
        url = next(iter(_re.findall(r"https?://[^\s)]+", source)), "")
        if url and _is_external_source_url(url):
            external.append((number, _citation_words(source)))
    if not external:
        return value
    definition = _re.compile(
        r"파일\s*형식|공개\s*(?:표준|사양)|공식\s*(?:정의|사양)|"
        r"(?:spec|specification|standard|format)\b", _re.I)
    frequency = {}
    for _number, source_words in external:
        for word in source_words:
            frequency[word] = frequency.get(word, 0) + 1

    def best_external(sentence: str) -> tuple[float, str]:
        words = _citation_words(_BODY_CITATION_RE.sub("", sentence))
        ranked = []
        for number, source_words in external:
            overlap = words & source_words
            score = sum(1 / frequency[word] for word in overlap)
            ranked.append((score, -int(number), number))
        score, _order, number = max(ranked, default=(0.0, 0, ""))
        return score, number

    def bind(sentence: str, numbers) -> str:
        # Once the server can identify the claim's source, remove every model-supplied
        # plain marker in that sentence.  Keeping an interior citation run produced UI such
        # as ``티켓[1][2][3]. [2]`` even though the sentence named exactly one ticket.
        clean = _BODY_CITATION_RE.sub("", sentence)
        clean = _re.sub(r"\s+([.,!?])", r"\1", clean).strip()
        chosen = list(dict.fromkeys(
            [str(number) for number in ([numbers] if isinstance(numbers, str) else numbers)
             if str(number)]))
        marker = "".join(f"[{number}]" for number in chosen)
        return f"{clean} {marker}" if clean and marker else sentence

    lines = []
    for line in body.splitlines():
        # Tables and source-evaluation rows are already structurally bound to their source
        # cell. Appending ``[n]`` after the closing pipe creates a fifth column and breaks
        # Markdown rendering.
        if line.lstrip().startswith(("|", "#", ">", "```")):
            lines.append(line)
            continue
        # One generated paragraph often contains a public definition, a product claim,
        # and an internal project-state claim followed by one marker.  A line-level rewrite
        # makes that last marker appear to support every sentence.  Bind each sentence to
        # the source it actually names, carrying an explicit ticket only across an immediate
        # "이/해당 티켓" continuation.
        if not (definition.search(line) or _re.search(r"\{\{ticket-detail:", line, _re.I)):
            lines.append(line)
            continue
        parts = _re.split(r"(?<=[.!?])\s+", line)
        rebound = []
        last_ticket_number = ""
        for sentence in parts:
            ticket_keys = _re.findall(
                r"\{\{ticket-detail:([A-Z][A-Z0-9]*-\d+)\}\}", sentence, _re.I)
            ticket_numbers = [tickets[key.upper()] for key in ticket_keys if tickets.get(key.upper())]
            if ticket_numbers:
                last_ticket_number = ticket_numbers[-1]
                sentence = bind(sentence, ticket_numbers)
            elif last_ticket_number and _re.match(
                    r"\s*(?:이|해당)\s*(?:티켓|작업|항목)(?:에서는|은|는|의|에서)", sentence):
                sentence = bind(sentence, last_ticket_number)
            else:
                score, number = best_external(sentence)
                # A definition needs one specific-title match.  Other technical claims need
                # at least two title terms so ordinary prose is not mechanically over-cited.
                threshold = 0.5 if definition.search(sentence) else 1.5
                if score >= threshold:
                    sentence = bind(sentence, number)
            rebound.append(sentence)
        lines.append(" ".join(rebound))
    return "\n".join(lines).rstrip() + "\n\n" + index.lstrip()


def _rebind_explicit_source_citations(text: str) -> str:
    """Bind an explicitly named source to its own canonical index number.

    A model can correctly name a meeting/document but leave the marker from the
    adjacent external source. Once the canonical index exists, the exact source
    title and number are deterministic. Replace only the first citation after
    one uniquely named source in that sentence; implicit analytical claims stay
    untouched.
    """
    value = str(text or "")
    heading = _re.search(r"(?m)^###\s*근거\s*$", value)
    if not heading:
        return value
    body, source_index = value[:heading.start()].rstrip(), value[heading.start():]
    titles = []
    for number, row in _re.findall(r"(?m)^\[(\d+)\]\s+(.+)$", source_index):
        link = _re.match(r"\[([^\n]+?)\]\(https?://", row)
        if link:
            title = link.group(1).strip()
            if len(title) >= 3:
                titles.append((title, number))
    if not titles:
        return value

    rendered = []
    for line in body.splitlines():
        if line.lstrip().startswith(("|", "#", ">")):
            rendered.append(line)
            continue
        sentences = _re.split(r"(?<=[.!?])\s+", line)
        fixed = []
        for sentence in sentences:
            matches = [(title, number, sentence.find(title))
                       for title, number in titles if title in sentence]
            if len(matches) == 1:
                _title, number, position = matches[0]
                citation = _BODY_CITATION_RE.search(sentence, position + len(_title))
                if citation:
                    sentence = (sentence[:citation.start()] + f"[{number}]"
                                + sentence[citation.end():])
            fixed.append(sentence)
        rendered.append(" ".join(fixed))
    return "\n".join(rendered).rstrip() + "\n\n" + source_index.lstrip()


def _source_quality_requested(state) -> bool:
    asked = (request_text(state) + " " + last_user_text(state)).casefold()
    return any(word in asked for word in ("신뢰도", "출처별", "요청 적합성", "출처 적합성"))


def _render_requested_source_quality(text: str, state) -> str:
    """Project structured source judgments into one complete, deterministic table."""
    evidence = [row for row in (state.get("evidence") or [])
                if isinstance(row, dict) and not _is_non_renderable_evidence(row)]
    if not evidence or not _source_quality_requested(state):
        return str(text or "")
    confidence = {"high": "높음", "medium": "중간", "low": "낮음", "unknown": "미확인"}
    fitness = {"direct": "직접", "supporting": "보조", "context-only": "맥락", "unknown": "미확인"}
    rows = ["### 출처 평가", "", "| 출처 | 신뢰도 | 요청 적합성 | 한계 |", "|---|---|---|---|"]
    represented_urls = set()
    for item in evidence:
        key = str(item.get("key") or "").strip().upper()
        title = str(item.get("title") or item.get("key") or "출처").strip()
        url = str(item.get("url") or "").strip()
        if _re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", key):
            source = f"{{{{ticket-detail:{key}}}}}"
        elif url.startswith(("http://", "https://")):
            source = f"[{title}]({_markdown_url(url)})"
            represented_urls.add(url)
        else:
            source = title
        raw_confidence = str(item.get("confidence") or "").lower()
        raw_fitness = str(item.get("fitness") or "").lower()
        limitation = str(item.get("limitations") or "").strip()
        if _is_external_source_url(url):
            # An external specification can be authoritative about its own format, never
            # direct proof that this LTM/Jira project is production-ready.
            official = bool(_re.search(
                rf"(?m)^-\s*[^\n]*·\s*공식\s*—[^\n]*{_re.escape(url)}",
                str(state.get("web_context") or "")))
            raw_confidence = "high" if official else "medium"
            raw_fitness = "supporting"
            limitation = limitation or "내부 운영 적용 여부는 직접 판단하지 않음"
        if not limitation:
            kinds = {str(obs.get("source") or "") for obs in (item.get("observations") or [])
                     if isinstance(obs, dict)}
            if raw_fitness == "direct" and "comment" in kinds:
                limitation = "기록된 결과 이후의 변경·후속 검증은 별도 확인 필요"
            elif raw_fitness == "direct" and kinds <= {"description", "field"}:
                limitation = "계획·정책 근거이며 실행 결과는 별도 확인 필요"
            elif raw_fitness == "direct" and "document" in kinds:
                limitation = "문서 기록 시점 이후 변경은 반영되지 않을 수 있음"
            else:
                limitation = {
                    "direct": "이 출처가 기록한 범위 밖의 결과는 판단하지 않음",
                    "supporting": "보조 근거로 단독 결론 불가",
                    "context-only": "배경 이해용으로 직접 판단 근거가 아님",
                }.get(raw_fitness, "근거 한계 미확인")
        rows.append(
            f"| {_cell(source)} | {confidence.get(raw_confidence, '미확인')} | "
            f"{fitness.get(raw_fitness, '미확인')} | {_cell(limitation)} |"
        )
    external_section = _re.search(
        r"(?ms)^###\s*외부\s*공식\s*근거\s*$\s*(.*?)(?=^###\s|\Z)", str(text or ""))
    if external_section:
        for title, url in _re.findall(r"\[([^\n]+?)\]\((https?://[^\s)]+)\)",
                                      external_section.group(1)):
            if url in represented_urls:
                continue
            rows.append(
                f"| {_cell(f'[{title}]({url})')} | 높음 | 보조 | "
                "내부 운영 적용 여부는 직접 판단하지 않음 |"
            )
            represented_urls.add(url)
    block = "\n".join(rows)
    source = _re.sub(r"(?ms)^###\s*출처\s*평가\s*$.*?(?=^###\s|\Z)", "", str(text or "")).strip()
    evidence_heading = _re.search(r"(?m)^###\s*(?:근거|참조)\s*$", source)
    if evidence_heading:
        return (source[:evidence_heading.start()].rstrip() + "\n\n" + block + "\n\n"
                + source[evidence_heading.start():].lstrip())
    return source.rstrip() + "\n\n" + block


_BODY_CITATION_RE = _re.compile(r"\[\d+(?:-[a-z])?\](?!\()", _re.I)
_CITATION_WORD_RE = _re.compile(r"[A-Za-z][A-Za-z0-9.+-]{2,}|[가-힣]{2,}")
_CITATION_STOP = {
    "현재", "관련", "상태", "작업", "확인", "정보", "결과", "프로젝트", "내부", "외부",
    "문서", "티켓", "진행", "완료", "검증", "적용", "여부", "official", "documentation",
}


def _citation_words(value: str) -> set[str]:
    return {token.casefold().strip("._+-") for token in _CITATION_WORD_RE.findall(str(value or ""))
            if len(token.strip("._+-")) >= 3 and token.casefold().strip("._+-") not in _CITATION_STOP}


def _ensure_requested_body_citations(text: str, state) -> str:
    """Attach only resolvable source markers to material prose when the user required them."""
    asked = (request_text(state) + " " + last_user_text(state)).casefold()
    if not any(word in asked for word in ("근거 marker", "근거 마커", "참조번호", "인용 marker", "인용 마커")):
        return str(text or "")
    value = str(text or "")
    heading = _re.search(r"(?m)^###\s*근거\s*$", value)
    if not heading:
        return value
    body, index = value[:heading.start()].rstrip(), value[heading.start():]
    root_rows = _re.findall(r"(?m)^\[(\d+)\]\s+(.+)$", index)
    if not root_rows:
        return value

    numbered = []
    for item in (state.get("evidence") or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        number = next((n for n, row in root_rows
                       if (key and key.upper() in row.upper()) or (url and url in row)
                       or (title and title.casefold() in row.casefold())), "")
        if not number:
            continue
        material = " ".join([
            title, str(item.get("why") or ""),
            *(str(obs.get("text") or "") if isinstance(obs, dict) else str(obs)
              for obs in (item.get("observations") or [])),
        ])
        numbered.append({"number": number, "words": _citation_words(material),
                         "direct": str(item.get("fitness") or "").lower() == "direct"})
    if not numbered:
        return value

    frequency = {}
    for item in numbered:
        for word in item["words"]:
            frequency[word] = frequency.get(word, 0) + 1
    blocks, material_count = body.split("\n\n"), 0
    for index_no, block in enumerate(blocks):
        stripped = block.strip()
        if (not stripped or stripped.startswith(("#", "|", "-", ">", "```"))
                or "### 출처 평가" in stripped or _BODY_CITATION_RE.search(stripped)
                or len(stripped) < 35):
            continue
        material_count += 1
        words = _citation_words(stripped)
        ranked = []
        for item in numbered:
            overlap = words & item["words"]
            score = sum(1 / frequency[word] for word in overlap)
            if score:
                ranked.append((score, item["number"]))
        ranked.sort(reverse=True)
        chosen = [number for score, number in ranked if score >= 0.75][:3]
        if not chosen and material_count == 1:
            chosen = [item["number"] for item in numbered if item["direct"]][:3]
        if chosen:
            marker = "".join(f"[{number}]" for number in dict.fromkeys(chosen))
            blocks[index_no] = stripped.rstrip() + " " + marker
        # Citation-heavy research memos need their conclusion and explanatory paragraph,
        # not every later housekeeping line, mechanically annotated.
        if material_count >= 3:
            break
    return "\n\n".join(blocks).rstrip() + "\n\n" + index.lstrip()


def _prune_empty_rows(text: str) -> str:
    """'확인된 기록 없음'만으로 채워진 표 행·참조 줄·그 결과 빈 표를 걷어낸다."""
    import re as _re
    out = []
    rows_removed = False
    for ln in (text or "").split("\n"):
        s = ln.strip()
        # 표 행: 첫 셀이 '없음'이거나 두 셀 이상이 '없음'이면 정보가 아니다(변형 실측).
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells and (cells[0].startswith("확인된 기록 없음")
                          or s.count("확인된 기록 없음") >= 2):
                rows_removed = True
                continue
        # 참조 줄: 출처 자리가 '없음'으로 시작하면 참조가 아니다("[3] 확인된 기록 없음 — …").
        if _re.match(r"-?\s*\[\d+\]\s*확인된 기록 없음", s):
            continue
        out.append(ln)
    text = "\n".join(out)
    if rows_removed:
        # 헤더+구분선만 남은 표(내용 행 0)는 표째 제거
        text = _re.sub(r"(?:^|\n)\|[^\n]*\|\n\|[\s:|-]+\|(?=\n(?!\|)|$)", "", text)
    # 내용 없는 섹션 헤딩("### 히스토리" 뒤가 바로 다음 헤딩/근거/끝) — 헤딩만 남기지 않는다
    # (실측: 표를 걷어낸 뒤, 또는 모델이 애초에 빈 헤딩을 냈다).
    # 맺음말("…더 궁금하면 말씀 주세요")도 섹션 내용이 아니다 — 그 앞의 빈 헤딩을 살려
    # 두면 "### 히스토리" 밑에 안내문만 붙는 꼴이 된다(실측 Round P).
    text = _re.sub(r"(?:^|\n)(#{2,4}\s+[^\n]+|\*\*[^\n*]+\*\*)\n+"
                   r"(?=(#{2,4}\s|\*\*(?:근거|참조)\*\*|[^\n]*(?:궁금하면 말씀|말씀 주세요)|$))",
                   "\n", text)
    return text


def _key_titles(keys) -> str:
    """일괄 변경 대상의 **제목**을 코드가 조회해 붙인다.

    키만 늘어놓은 표로는 30건의 우선순위를 올리면서도 무엇을 바꾸는지 알 수 없다
    (실측 U1). 제목은 판단의 재료지 장식이 아니다.
    """
    out = []
    try:
        from app.agent import tools as T
        for k in list(keys or [])[:10]:
            t = T.BY_NAME["get_ticket"].invoke({"key": k}) or {}
            out.append(f"- {k} · {t.get('summary') or '(제목 확인 안 됨)'}"
                       f" ({t.get('status') or ''})")
    except Exception:
        return "\n".join(f"- {k}" for k in list(keys or [])[:10])
    if len(keys or []) > 10:
        out.append(f"- (나머지 {len(keys) - 10}건은 승인 카드에서 확인)")
    return "\n".join(out)


def _doc_body(pre) -> str:
    """사전 조사에서 **문서 본문** 블록만 뽑는다(있을 때만)."""
    src = str(pre or "")
    i = src.find("문서 본문 「")
    return src[i:i + 4000] if i >= 0 else ""


def _candidate_block(pre) -> str:
    """사전 조사에서 **담당 후보(로스터·부하)** 블록만 뽑는다(있을 때만).

    이 블록은 ResultIntegrator 에 **오지 않고 있었다** — pre_survey 에서 티켓 현재값과 문서 본문만
    잘라 썼기 때문이다. 그래서 코드가 로스터·부하까지 조회해 실어 준 후보가 ResearchAnalyst 의
    situation 요약 한 겹을 지나며 사라졌다(실측 EDGE13: "누가 하면 좋을지랑 지금 상황" 에
    상황만 답하고 후보를 통째로 뺐다 — 세 번 연속, 재료에는 사번까지 있었다).
    """
    src = str(pre or "")
    i = src.find("후보 재료 —")
    if i < 0:
        return ""
    j = src.find("\n\n", i)
    return src[i:j if j > 0 else len(src)][:2500]


def _drop_form_echo(text: str, qs: list) -> str:
    """되묻기 폼에 이미 있는 것을 본문에서 걷어낸다.

    화면은 질문을 **카드 폼**으로 그린다. 같은 질문과 보기를 답변 산문에도 늘어놓으면
    같은 말이 두 벌 보이고, 정작 상황 요약이 묻힌다(사용자 지적). 판정은 폼의 실제
    질문·보기 문자열로만 한다 — 모델이 무슨 말을 썼는지 추측하지 않는다.
    """
    def norm(s):
        return _re.sub(r"\s+", "", str(s or "")).strip(" .·—-…?!\"'`*").lower()

    qtexts = [norm(q.get("question")) for q in qs if isinstance(q, dict)]
    opts = [norm(o) for q in qs if isinstance(q, dict) for o in (q.get("options") or [])]
    qtexts = [t for t in qtexts if len(t) >= 8]
    opts = [o for o in opts if len(o) >= 3]

    kept, dropped = [], 0
    for line in (text or "").split("\n"):
        core = norm(_re.sub(r"^\s*(?:[-*>]|\d+[).]|\(\d+\))\s*", "", line))
        if not core:
            kept.append(line)
            continue
        # ① 폼의 질문 문장을 그대로 옮긴 줄. **통째로 겹칠 때만** — 앞부분만 비교하면
        #    같은 식별자를 언급한 상황 요약까지 날아간다(자체 실측).
        echo_q = any(t in core or (len(core) >= 12 and core in t) for t in qtexts)
        # ② 보기 하나만으로 이뤄진 줄("1) fdc.fdc_trace_summary_ic")
        echo_o = any(core == o or (o in core and len(core) <= len(o) + 12) for o in opts)
        # ③ 폼으로 넘기는 유도 문구만 있는 줄
        lead = bool(_re.match(r"^(확인\s*(부탁|필요)|아래\s*중|다음\s*질문|아래\s*질문|"
                              r"질문에\s*답)", core))
        # ④ 폼에 없는 것을 산문으로 추가로 묻는 줄 — 물어볼 것은 폼에만 있어야 한다
        #    (result_integrator.md 의 규칙인데 실측에서 "대상 환경/조회 범위/맥락"을 덧붙였다).
        #    화면으로 넘기는 안내("아래에서 골라 주세요")는 남긴다.
        asks = bool(_re.search(r"(\?|알려\s*주세요|알려주세요|적어\s*주세요|"
                               r"입력해\s*주세요|말씀해\s*주세요)$", core)) \
            and not _re.search(r"아래|폼|카드|선택", core)
        # ⑤ ④ 로 지운 요구의 하위 항목("대상 환경: 개발/스테이징/운영")은 함께 지운다
        follow = bool(dropped and kept and not kept[-1].strip()
                      and _re.match(r"^[^:：\n]{2,14}[:：]\s*\S", line.strip()))
        if echo_q or echo_o or lead or asks or follow:
            dropped += 1
            continue
        kept.append(line)
    if not dropped:
        return text
    out = _re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    # 전부 걷어내 버렸다면(질문만 있던 답변) 최소한의 안내는 남긴다.
    return out or "확인이 필요합니다 — 아래에서 골라 주세요."


def _known_doc_urls(state) -> dict:
    """이 턴의 재료에 실린 **문서 제목 → URL**. 세 군데에서 모은다(모두 코드가 만든 자료다).

    · topic_dossier 의 `문서 「제목」 (URL) 발췌:`
    · pre_survey 의 `- 제목 (URL)`
    · state["related_docs"] 의 {title, url}
    """
    out = {}
    blob = " ".join(str(state.get(k) or "") for k in ("topic_dossier", "pre_survey"))
    for t, u in _re.findall(r"「([^」]+)」\s*\((https?://[^)\s]+)\)", blob):
        out.setdefault(t.strip(), u)
    for t, u in _re.findall(r"^-\s*(.+?)\s*\((https?://[^)\s]+)\)\s*$", blob, _re.M):
        out.setdefault(t.strip(), u)
    for d in (state.get("related_docs") or []):
        t, u = str(d.get("title") or "").strip(), str(d.get("url") or "").strip()
        if t and u:
            out.setdefault(t, u)
    return out


def _attach_known_doc_urls(text: str, state) -> str:
    """참조 줄이 문서를 **제목만으로** 인용했으면, 재료에 있는 URL 을 코드가 붙인다.

    재료에는 URL 이 있는데 모델이 참조로 옮기지 않는 일이 반복됐다(실측 DATA9: 세 줄 다
    제목만). 프롬프트로 두 라운드 고쳤는데도 재발했다 — **우리가 아는 URL 을 그 제목에
    붙이는 것은 지어내는 것이 아니라 옮기는 것**이라, 부탁할 일이 아니라 코드가 할 일이다.

    제목이 **정확히 그대로** 들어 있을 때만 바꾼다(부분 일치로 엉뚱한 문서를 붙이지 않는다).
    이미 링크가 있는 줄은 건드리지 않는다.
    """
    urls = _known_doc_urls(state)
    if not urls:
        return text
    from app.agent.workflow.grounding import LINKED_RE, REF_LINE_RE
    # 긴 제목부터 — 짧은 제목이 긴 제목의 일부일 때 잘못 걸리지 않게.
    titles = sorted(urls, key=len, reverse=True)

    def fix(m):
        line = m.group(0)
        if LINKED_RE.search(line):
            return line
        for t in titles:
            if t in line:
                return line.replace(t, f"[{t}]({_markdown_url(urls[t])})", 1)
        return line
    return REF_LINE_RE.sub(fix, text or "")


def _drop_dangling_bracket(text: str) -> str:
    """쓰다 만 링크 토막("[여기에서 확인할 수 있습니다.")의 **여는 대괄호만** 지운다.

    ★ 완성된 마크다운 링크는 건드리지 않는다. 예전에는 `\\[(?=[^\\]\\n]{0,60}(?:\\n|$))`
    로 60자를 내다봤는데, 그 lookahead 는 **제목 자체에 대괄호가 든 링크**를 링크째 뭉갰다:

        [1] [[데이터카탈로그] qms_… 정의](http://…) — 주 1회   ← 모델이 제대로 쓴 것
        [1] http://… — 주 1회                                  ← 코드가 먹은 뒤

    우리 Confluence 문서 제목은 **전부** `[데이터카탈로그] …` 꼴이라 이 경로를 늘 탔다.
    그래서 답변의 문서 참조가 제목만 남거나 URL 만 남았고, "링크 없는 문서 제목"을
    프롬프트로 몇 라운드나 쫓았다 — 모델은 링크를 제대로 쓰고 있었다.

    지금은 줄 단위로 **닫는 짝이 아예 없는 마지막 `[` 하나만** 지운다.
    """
    out = []
    for ln in (text or "").split("\n"):
        i = ln.rfind("[")
        if i >= 0 and "]" not in ln[i + 1:]:
            ln = ln[:i] + ln[i + 1:]
        out.append(ln)
    return "\n".join(out)


def _violations(g: dict) -> int:
    return len(g.get("fake_keys") or []) + len(g.get("wrong_titles") or {}) \
        + len(g.get("fake_people") or []) + len(g.get("unlinked_refs") or []) \
        + len(g.get("name_as_id") or {})


def _kept_substance(before: str, after: str) -> bool:
    """재작성이 답을 **망가뜨리지 않았는지** — 검사를 통과했다고 좋은 답은 아니다.

    위반을 없애는 가장 쉬운 방법은 **내용을 지우는 것**이다. 실측(fake 프로브): 재작성
    결과가 지시문을 복창한 껍데기였는데, 거기엔 티켓 키도 참조도 없으니 검사는 깨끗이
    통과했고 그 껍데기가 멀쩡한 답을 대체했다. 검사 통과만으로 채택하면 이 길이 열린다.

    두 가지만 본다 — 분량이 절반 아래로 줄지 않았는가, 원래 인용한 티켓 키가 절반은
    남아 있는가(위반 키는 지워지는 게 맞으므로 전부 유지를 요구하지는 않는다).
    """
    from app.agent.workflow.grounding import KEY_RE
    if not after or len(after) < len(before) * 0.5:
        return False
    keys_b = set(KEY_RE.findall(before or ""))
    if not keys_b:
        return True
    return len(set(KEY_RE.findall(after)) & keys_b) >= max(1, len(keys_b) // 2)
