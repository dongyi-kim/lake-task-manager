"""Action Executor — 승인된 것을 **실행**한다. 그래프에서 유일하게 쓰기 도구를 가진 노드.

이 노드 **앞에서 그래프가 멈춘다**(`interrupt_before`). 사용자가 화면에서 승인 카드를 누르기
전까지는 아예 여기 도달하지 않는다. 승인이 나면 `approval_token` 이 State 에 실려 재개된다.

두 겹으로 막는 이유 — 그래프의 interrupt 는 "여기서 멈춘다"는 **흐름 제어**이고, 도구의
승인 토큰은 "이 내용이 승인됐다"는 **내용 보증**이다. 흐름은 코드 실수로 우회될 수 있지만
토큰은 못 우회한다(내용 해시에 묶여 있다). 반대로 토큰만 있으면 사용자는 언제 물어볼지 모른다.
둘 다 필요하다.

**Task 를 먼저, Sub-Task 를 나중에.** Sub-Task 는 부모가 실재해야 만들 수 있어서 한 번에 섞어
보낼 수 없다. 그래서 mode 가 subtask 면 부모 키가 이미 있어야 하고, 새 Task 밑에 Sub-Task 를
달려면 **두 번의 승인**을 거친다(첫 승인으로 Task 를 만들고, 그 키로 두 번째 초안을 짠다).
"""

from __future__ import annotations

from app.agent.workflow.agents.base import Agent
from app.agent.workflow.agents.work_architect import draft_json, draft_text
from app.agent.prompts.roles import SYSTEM_ACTION_EXECUTOR
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Node, note

# Explicit execution adapters are an allowlist, not a mirror that automatically trusts every
# future write tool. ``_validate_action_registry`` makes drift fail loud: adding a new tool to
# WRITE_TOOLS cannot silently make it executable, and removing/renaming an adapter cannot leave an
# approval action that crashes only after the user clicks the card.
SUPPORTED_WRITE_ACTIONS = frozenset({
    "create_tickets", "create_epic", "update_ticket", "add_ticket_comment",
    "update_tickets", "add_ticket_comments", "transition_ticket", "link_tickets",
    "attach_document",
})


class ActionExecutor(Agent):
    name = Node.ACTION_EXECUTOR
                               # (modify 는 아예 LLM 없이 돌고, create 도 인자 전달 + 결과 보고뿐)

    def node(self):
        """Execute the exact approved payload without any LLM or ReAct fallback.

        The approval record is authoritative: it stores the action and payload that the user saw,
        and every write tool consumes the same payload fingerprint.  Missing, unapproved, cross-thread,
        malformed, or unknown actions fail closed instead of asking a model to choose a write tool.
        """
        return self._run

    def _run(self, state):
        from app.agent import approval

        token = str(state.get("approval_token") or "")
        record = approval.peek(token) if token else None
        if not record:
            return self._failed(state, "승인된 실행 계획이 없거나 만료되었습니다.")
        if not record.get("approved"):
            return self._failed(state, "아직 사용자가 승인하지 않았습니다.")
        thread_id = str(state.get("thread_id") or "")
        record_thread = str(record.get("thread") or "")
        if not thread_id:
            return self._failed(state, "실행할 대화 식별자가 없어 승인 작업을 거부했습니다.")
        if not record_thread:
            approval.reject(token)
            return self._failed(state, "대화에 연결되지 않은 승인 작업을 거부했습니다.")
        if record_thread != thread_id:
            return self._failed(state, "이 대화에서 승인한 실행 계획이 아닙니다.")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            approval.reject(token)
            return self._failed(state, "승인된 실행 payload 형식이 올바르지 않습니다.")

        # Compound cards are two separately consumable fingerprints. Validate their reciprocal
        # server-owned binding before executing the primary; otherwise a missing/stale token can
        # silently drop the comment, or a valid comment capability for another ticket can be
        # spliced into this update.
        secondary_actions = {
            "update_ticket": "add_ticket_comment",
            "update_tickets": "add_ticket_comments",
            "link_tickets": "add_ticket_comment",
        }
        expected_secondary = secondary_actions.get(str(record.get("action") or ""))
        comment_token = str(state.get("comment_token") or "")
        comment_record = approval.peek(comment_token) if comment_token else None
        bundle = str(record.get("bundle") or "")
        if bundle:
            pair_ok = bool(
                expected_secondary and comment_token and comment_record
                and record.get("bundle_role") == "primary"
                and str(record.get("peer_token") or "") == comment_token
                and str(record.get("peer_action") or "") == expected_secondary
                and str(comment_record.get("bundle") or "") == bundle
                and comment_record.get("bundle_role") == "secondary"
                and str(comment_record.get("peer_token") or "") == token
                and str(comment_record.get("peer_action") or "") == record.get("action")
                and str(comment_record.get("thread") or "") == thread_id
                and comment_record.get("approved") is True
                and comment_record.get("action") == expected_secondary
                and isinstance(comment_record.get("payload"), dict)
                and str(record.get("peer_fp") or "")
                    == approval.fingerprint(comment_record.get("payload"))
                and str(comment_record.get("peer_fp") or "")
                    == approval.fingerprint(payload)
            )
            if not pair_ok:
                approval.reject(token)
                bound_peer = str(record.get("peer_token") or "")
                bound_record = approval.peek(bound_peer) if bound_peer else None
                if bound_record and str(bound_record.get("thread") or "") == thread_id:
                    approval.reject(bound_peer)
                if (comment_token and comment_token != bound_peer and comment_record
                        and str(comment_record.get("thread") or "") == thread_id):
                    approval.reject(comment_token)
                return self._failed(
                    state,
                    "같은 승인 카드의 primary·comment 실행 지문이 서로 결속되지 않아 실행하지 않았습니다.",
                )
        elif comment_token:
            # A loose secondary token is never a compound approval, even when action/thread
            # happen to match. Reject before the primary so no half-card side effect occurs.
            approval.reject(token)
            if comment_record and str(comment_record.get("thread") or "") == thread_id:
                approval.reject(comment_token)
            return self._failed(
                state, "서로 결속되지 않은 코멘트 승인 토큰이 있어 변경을 실행하지 않았습니다.",
            )

        result, label = self._dispatch(str(record.get("action") or ""), payload, token)

        # ``consume`` happens inside each write tool, but several tools deliberately validate
        # Jira state before consuming.  Once ActionExecutor has attempted an approved card, a
        # pre-validation failure must not leave the same capability reusable by a direct caller.
        # External failures already consume it; ``reject`` is therefore an idempotent cleanup.
        if result.get("failed") and approval.peek(token):
            approval.reject(token)

        # A change and its accompanying comment have separate one-use fingerprints, but both
        # were visible on the same approval card. Execute the comment only after the primary
        # mutation succeeded, preserving the established partial-success report for singular,
        # bulk and link changes.
        if expected_secondary and bundle and comment_token:
            comment_same_thread = bool(
                comment_record and str(comment_record.get("thread") or "") == thread_id
            )
            if result.get("failed"):
                # The comment was one card's secondary action. If the primary field update did
                # not happen, posting or retaining that separately approved comment later would
                # violate the partial-success contract. Never touch a foreign thread's token.
                if comment_same_thread:
                    approval.reject(comment_token)
                result["note"] = (
                    "primary 변경이 전부 완료되지 않아 같은 승인 카드의 코멘트는 "
                    "게시하지 않았습니다."
                )
            elif (comment_same_thread and comment_record.get("approved")
                  and comment_record.get("action") == expected_secondary
                  and isinstance(comment_record.get("payload"), dict)):
                comment_result, _ = self._dispatch(
                    expected_secondary, comment_record["payload"], comment_token,
                )
                by_key = {str(row.get("key") or ""): row
                          for row in (result.get("updated") or [])
                          if isinstance(row, dict) and row.get("key")}
                for row in comment_result.get("updated") or []:
                    key = str((row or {}).get("key") or "")
                    if key in by_key:
                        fields = by_key[key].setdefault("fields", [])
                        for field in row.get("fields") or []:
                            if field not in fields:
                                fields.append(field)
                    elif key:
                        result.setdefault("updated", []).append(row)
                if comment_result.get("failed"):
                    result.setdefault("failed", []).extend(comment_result["failed"])
                    if approval.peek(comment_token):
                        approval.reject(comment_token)
                    result["note"] = (
                        "필드는 바꿨지만 코멘트는 남기지 못했습니다: "
                        + str(comment_result["failed"][0].get("error") or "")
                    )
                else:
                    label += " · 코멘트"
            else:
                # A malformed same-thread secondary capability is no longer reachable from the
                # finished graph, but rejecting it here also prevents accidental direct reuse.
                if comment_same_thread:
                    approval.reject(comment_token)
                result["note"] = "필드는 바꿨지만 코멘트 승인 정보가 유효하지 않습니다."
        return {"result": result, "trace": note(state, self.name, label)}

    @staticmethod
    def _empty_result() -> dict:
        return {"created": [], "updated": [], "failed": [], "note": ""}

    def _failed(self, state, message: str, summary: str = "승인 작업") -> dict:
        result = self._empty_result()
        result["failed"] = [{"summary": summary, "error": str(message or "")[:300]}]
        return {"result": result,
                "trace": note(state, self.name, f"실행 거부 — {str(message or '')[:80]}")}

    @staticmethod
    def _failure_rows(raw: dict, summary: str) -> list[dict]:
        rows = [row for row in (raw.get("failed") or []) if isinstance(row, dict)]
        if not raw.get("ok") and not rows:
            rows = [{"summary": summary, "error": str(raw.get("error") or "실행 실패")[:300]}]
        return rows

    def _dispatch(self, action: str, payload: dict, token: str) -> tuple[dict, str]:
        """Dispatch one explicit approved action through its canonical registry tool."""
        from app.agent import approval
        from app.agent import tools as T

        try:
            self._validate_action_registry(T)
        except RuntimeError as exc:
            approval.reject(token)
            return ({"created": [], "updated": [],
                     "failed": [{"summary": action or "승인 작업",
                                 "error": str(exc)[:300]}], "note": ""},
                    "쓰기 도구 registry 불일치 거부")
        if action not in SUPPORTED_WRITE_ACTIONS:
            # An approved token for an action this version cannot execute must not remain as a
            # reusable ambient capability. The user can request a new supported plan and approve it.
            approval.reject(token)
            return ({"created": [], "updated": [],
                     "failed": [{"summary": action or "승인 작업",
                                 "error": "지원하지 않는 승인 작업이라 실행하지 않았습니다."}],
                     "note": ""}, "지원하지 않는 승인 작업 거부")

        if action == "create_tickets":
            raw = T.BY_NAME[action].invoke({**payload, "approval_token": token}) or {}
            created = [row for row in (raw.get("created") or [])
                       if isinstance(row, dict) and row.get("key")]
            failed = self._failure_rows(
                raw, str(((payload.get("items") or [{}])[0] or {}).get("summary") or "티켓 생성"),
            )
            return ({"created": created, "updated": [], "failed": failed, "note": ""},
                    f"생성 {len(created)}건" + (f" · 실패 {len(failed)}건" if failed else ""))

        if action == "create_epic":
            raw = T.BY_NAME[action].invoke({**payload, "approval_token": token}) or {}
            created = [row for row in (raw.get("created") or [])
                       if isinstance(row, dict) and row.get("key")]
            failed = self._failure_rows(raw, str(payload.get("summary") or "Epic 생성"))
            followup = ("이 Epic 아래에 Task 를 이어서 만들 수 있습니다 — 원하시면 말씀해 주세요."
                        if created else "")
            return ({"created": created, "updated": [], "failed": failed, "note": followup},
                    f"Epic 생성 {len(created)}건" + (" · 실패" if failed else ""))

        if action == "update_ticket":
            key = str(payload.get("key") or "")
            changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
            raw = T.BY_NAME[action].invoke(
                {"key": key, **changes, "approval_token": token},
            ) or {}
            failed = self._failure_rows(raw, key or "티켓 변경")
            updated = ([] if failed else
                       [{"key": key, "fields": list(raw.get("updated") or [])}])
            return ({"created": [], "updated": updated, "failed": failed, "note": ""},
                    (f"변경 1건({', '.join(raw.get('updated') or [])})" if updated else "변경 실패"))

        if action == "add_ticket_comment":
            key = str(payload.get("key") or "")
            raw = T.BY_NAME[action].invoke({**payload, "approval_token": token}) or {}
            failed = self._failure_rows(raw, key or "코멘트")
            updated = [] if failed else [{"key": key, "fields": ["comment"]}]
            return ({"created": [], "updated": updated, "failed": failed, "note": ""},
                    "코멘트 1건" if updated else "코멘트 실패")

        if action == "update_tickets":
            raw = T.BY_NAME[action].invoke({**payload, "approval_token": token}) or {}
            updated = [row for row in (raw.get("updated") or []) if isinstance(row, dict)]
            keys = [str((row or {}).get("key") or "") for row in (payload.get("items") or [])]
            failed = self._failure_rows(raw, ", ".join(k for k in keys[:5] if k) or "일괄 변경")
            return ({"created": [], "updated": updated, "failed": failed, "note": ""},
                    f"일괄 변경 {len(updated)}건" + (f" · 실패 {len(failed)}건" if failed else ""))

        if action == "add_ticket_comments":
            raw = T.BY_NAME[action].invoke({**payload, "approval_token": token}) or {}
            posted = [row for row in (raw.get("created") or []) if isinstance(row, dict)]
            updated = [{"key": str(row.get("key") or ""), "fields": ["comment"]}
                       for row in posted if row.get("key")]
            keys = [str((row or {}).get("key") or "") for row in (payload.get("items") or [])]
            failed = self._failure_rows(raw, ", ".join(k for k in keys[:5] if k) or "일괄 코멘트")
            return ({"created": [], "updated": updated, "failed": failed, "note": ""},
                    f"코멘트 {len(updated)}건" + (f" · 실패 {len(failed)}건" if failed else ""))

        if action == "transition_ticket":
            key = str(payload.get("key") or "")
            transition_id = str(payload.get("transition") or "")
            args = {"key": key, "transition_id": transition_id,
                    "approval_token": token}
            for field in ("comment", "assignee"):
                if field in payload:
                    args[field] = payload[field]
            raw = T.BY_NAME[action].invoke(args) or {}
            failed = self._failure_rows(raw, key or "상태 전이")
            updated = [] if failed else [{"key": key, "fields": [f"status→{transition_id}"]}]
            return ({"created": [], "updated": updated, "failed": failed, "note": ""},
                    f"전이 {transition_id}" if updated else "전이 실패")

        if action == "link_tickets":
            key, other = str(payload.get("key") or ""), str(payload.get("other") or "")
            relation = str(payload.get("relation") or "Relates")
            raw = T.BY_NAME[action].invoke(
                {"key": key, "other_key": other, "relation": relation,
                 "approval_token": token},
            ) or {}
            failed = self._failure_rows(raw, key or "티켓 링크")
            updated = [] if failed else [{"key": key, "fields": [f"link {relation}→{other}"]}]
            return ({"created": [], "updated": updated, "failed": failed, "note": ""},
                    f"링크 {other}" if updated else "링크 실패")

        # attach_document
        key = str(payload.get("key") or "")
        raw = T.BY_NAME[action].invoke({**payload, "approval_token": token}) or {}
        failed = self._failure_rows(raw, key or "문서 첨부")
        updated = [] if failed else [{"key": key, "fields": ["document"]}]
        return ({"created": [], "updated": updated, "failed": failed, "note": ""},
                "문서 연결 1건" if updated else "문서 연결 실패")

    @staticmethod
    def _validate_action_registry(registry) -> None:
        """Reject write-registry drift until an explicit deterministic adapter is reviewed."""
        registered = {str(getattr(tool, "name", "") or "")
                      for tool in (registry.WRITE_TOOLS or [])}
        missing = SUPPORTED_WRITE_ACTIONS - registered
        unreviewed = registered - SUPPORTED_WRITE_ACTIONS
        if missing or unreviewed:
            details = []
            if missing:
                details.append("registry missing: " + ", ".join(sorted(missing)))
            if unreviewed:
                details.append("unreviewed write tools: " + ", ".join(sorted(unreviewed)))
            raise RuntimeError("ActionExecutor/WRITE_TOOLS drift — " + " | ".join(details))

    @property
    def tools(self):
        from app.agent.workflow.role_manifest import tools_for_role
        # Permission inventory only. ``node`` never passes this list to an LLM.
        return tools_for_role(self.name)

    def system(self, state):
        return persona(state, SYSTEM_ACTION_EXECUTOR, lite=True)  # 결정적 실행 위주 — 축약판이면 충분

    def task(self, state):
        draft = state.get("draft") or {}
        return f"""\
# Task

Execute exactly the approved ticket draft. Do not infer, add, remove, normalize, or retry any argument.

## Approved Execution Arguments

mode: {draft.get('mode') or 'task'}
approval_token: {state.get('approval_token') or '(missing: do not execute)'}

## Exact Items JSON

Pass this JSON unchanged.

{draft_json(draft)}

## Human-Readable Preview Data

This section is context only; the JSON above is authoritative.

{draft_text(draft)}"""

    def schema(self):
        return {}

    def apply(self, state, out):
        created = [c for c in (out.get("created") or []) if isinstance(c, dict) and c.get("key")]
        updated = [u for u in (out.get("updated") or []) if isinstance(u, dict) and u.get("key")]
        failed = [f for f in (out.get("failed") or []) if isinstance(f, dict)]
        summary = (f"변경 {len(updated)}건" if updated else f"생성 {len(created)}건") + (
            f" · 실패 {len(failed)}건" if failed else "")
        return {"result": {"created": created, "updated": updated, "failed": failed,
                           "note": out.get("note") or ""},
                "trace": note(state, self.name, summary)}
