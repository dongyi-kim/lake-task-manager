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

from app.agent.workflow.agents.base import ToolAgent
from app.agent.workflow.agents.work_architect import draft_json, draft_text
from app.agent.prompts.roles import SYSTEM_ACTION_EXECUTOR
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import AgentState, Node, note

SCHEMA = {
    "type": "object",
    "properties": {
        "created": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "key": {"type": "string"}, "summary": {"type": "string"}}},
            "description": "Tickets actually created and returned by the write tool.",
        },
        "failed": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "summary": {"type": "string"}, "error": {"type": "string"}}},
            "description": "Failed items copied exactly from tool output; never suppress a failure.",
        },
        "updated": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "key": {"type": "string"},
                "fields": {"type": "array", "items": {"type": "string"}}}},
            "description": "Tickets actually updated and returned by the write tool.",
        },
        "note": {"type": "string", "description": "Exact Korean user-facing tool note, or empty."},
    },
    "required": ["created", "failed"],
}


class ActionExecutor(ToolAgent):
    name = Node.ACTION_EXECUTOR
    temperature = 0.0          # 실행은 창의적일 필요가 없다
    tier = "simple"            # 승인된 JSON 을 그대로 넘기는 일이다 — 판단이 얕다
                               # (modify 는 아예 LLM 없이 돌고, create 도 인자 전달 + 결과 보고뿐)

    def node(self):
        """실행은 **LLM 없이 결정적으로** — modify 도, create 도.

        실행에 판단이 없다: 승인된 인자를 도구에 넘기고 결과를 그대로 옮기면 끝이다.
        모델을 끼웠더니 생긴 실패 모드(전부 실측): modify 인데 create_tickets 를 부름,
        검증 **경고**(Epic 미연결 안내)를 '실패한 항목·후속 조치'로 각색해 보고 —
        사용자가 방금 '최상위로 두겠다'고 결정한 것을 다시 경고하는 셈이다.
        도구 결과만이 사실이다. ReAct 는 계획이 전혀 없는 예외 경로에만 남는다.
        """
        react = super().node()

        def run(state):
            plan = state.get("change_plan") or {}
            # 상태 전이 — transition_ticket 도구로(지문은 _propose 가 같은 모양으로 봉인).
            if plan.get("key") and (plan.get("transition") or {}).get("id"):
                from app.agent import tools as T
                cmt = (plan.get("comment") or "").strip()
                args = {"key": plan["key"], "transition_id": str(plan["transition"]["id"]),
                        "approval_token": state.get("approval_token") or ""}
                if cmt:
                    args["comment"] = cmt
                r = T.BY_NAME["transition_ticket"].invoke(args)
                if not r.get("ok"):
                    return {"result": {"created": [], "updated": [],
                                       "failed": [{"summary": plan["key"],
                                                   "error": r.get("error") or ""}]},
                            "trace": note(state, self.name, "전이 실패")}
                return {"result": {"created": [], "failed": [],
                                   "updated": [{"key": plan["key"],
                                                "fields": [f"status→{plan['transition'].get('name')}"]}]},
                        "trace": note(state, self.name,
                                      f"전이 {plan['transition'].get('name')}")}
            # 티켓 링크 — link_tickets 도구로.
            if plan.get("key") and (plan.get("link") or {}).get("other"):
                from app.agent import tools as T
                lk = plan["link"]
                r = T.BY_NAME["link_tickets"].invoke(
                    {"key": plan["key"], "other_key": lk["other"],
                     "relation": lk.get("relation") or "Relates",
                     "approval_token": state.get("approval_token") or ""})
                if not r.get("ok"):
                    return {"result": {"created": [], "updated": [],
                                       "failed": [{"summary": plan["key"],
                                                   "error": r.get("error") or ""}]},
                            "trace": note(state, self.name, "링크 실패")}
                return {"result": {"created": [], "failed": [],
                                   "updated": [{"key": plan["key"],
                                                "fields": [f"link {lk.get('relation')}→{lk['other']}"]}]},
                        "trace": note(state, self.name, f"링크 {lk['other']}")}
            # 조건 일괄 수정 — 지문은 update_tickets(bulk) payload 와 같은 모양이어야 한다.
            if plan.get("keys") and plan.get("changes"):
                from app.agent import tools as T
                rows = [{"key": str(k).strip(), "changes": dict(plan["changes"])}
                        for k in plan["keys"] if str(k).strip()]
                r = T.BY_NAME["update_tickets"].invoke(
                    {"items": rows, "approval_token": state.get("approval_token") or ""})
                if not r.get("ok") and not (r.get("updated") or []):
                    return {"result": {"created": [], "updated": [],
                                       "failed": (r.get("failed") or
                                                  [{"summary": ", ".join(plan["keys"][:5]),
                                                    "error": r.get("error") or ""}])},
                            "trace": note(state, self.name,
                                          f"일괄 변경 실패 — {str(r.get('error'))[:80]}")}
                return {"result": {"created": [], "failed": r.get("failed") or [],
                                   "updated": r.get("updated") or []},
                        "trace": note(state, self.name,
                                      f"일괄 변경 {len(r.get('updated') or [])}건")}
            if not plan.get("key"):
                return self._run_create(state, react)

            from app.agent import tools as T
            cmt0 = (plan.get("comment") or "").strip()
            if not plan.get("changes") and cmt0:
                # 댓글만 — 승인 토큰이 곧 add_ticket_comment 토큰이다.
                cr = T.BY_NAME["add_ticket_comment"].invoke(
                    {"key": plan["key"], "body": cmt0,
                     "approval_token": state.get("approval_token") or ""})
                if not cr.get("ok"):
                    return {"result": {"created": [], "updated": [],
                                       "failed": [{"summary": plan["key"],
                                                   "error": cr.get("error") or ""}]},
                            "trace": note(state, self.name, "코멘트 실패")}
                return {"result": {"created": [], "failed": [],
                                   "updated": [{"key": plan["key"], "fields": ["comment"]}],
                                   "note": ""},
                        "trace": note(state, self.name, "코멘트 1건")}

            args = {"key": plan["key"], "approval_token": state.get("approval_token") or ""}
            args.update(plan.get("changes") or {})
            r = T.BY_NAME["update_ticket"].invoke(args)
            if not r.get("ok"):
                return {"result": {"created": [], "updated": [],
                                   "failed": [{"summary": plan["key"], "error": r.get("error") or ""}]},
                        "trace": note(state, self.name, f"변경 실패 — {str(r.get('error'))[:80]}")}

            out = {"created": [], "failed": [],
                   "updated": [{"key": plan["key"], "fields": r.get("updated") or []}], "note": ""}
            cmt = (plan.get("comment") or "").strip()
            if cmt:
                cr = T.BY_NAME["add_ticket_comment"].invoke(
                    {"key": plan["key"], "body": cmt,
                     "approval_token": state.get("comment_token") or ""})
                if not cr.get("ok"):
                    out["note"] = f"필드는 바꿨지만 코멘트는 남기지 못했습니다: {cr.get('error') or ''}"
            return {"result": out,
                    "trace": note(state, self.name,
                                  f"변경 1건({', '.join(r.get('updated') or [])})"
                                  + (" · 코멘트" if cmt and not out["note"] else ""))}

        return run

    def _run_create(self, state, react):
        """생성 실행 — 승인된 초안을 create_tickets/create_epic 한 번에 넘긴다.
        결과는 도구가 준 그대로."""
        from app.agent import tools as T
        from app.agent.workflow.agents.work_architect import as_bulk_items, epic_payload
        draft = state.get("draft") or {}

        if (draft.get("mode") or "task") == "epic":
            p = epic_payload(draft)
            if not p.get("summary"):
                return react(state)
            r = T.BY_NAME["create_epic"].invoke(
                {**p, "approval_token": state.get("approval_token") or ""})
            created = [c for c in (r.get("created") or []) if isinstance(c, dict) and c.get("key")]
            failed = [] if created else [{"summary": p.get("summary", ""),
                                          "error": r.get("error") or ""}]
            note_txt = ("이 Epic 아래에 Task 를 이어서 만들 수 있습니다 — 원하시면 말씀해 주세요."
                        if created else "")
            return {"result": {"created": created, "failed": failed, "updated": [],
                               "note": note_txt},
                    "trace": note(state, self.name,
                                  f"Epic 생성 {len(created)}건" + (" · 실패" if failed else ""))}

        items = as_bulk_items(draft)
        if not items:
            return react(state)          # 계획이 없다 — 예외 경로만 모델에게
        from app.agent.workflow.agents.work_architect import child_items
        kids = child_items(draft)
        r = T.BY_NAME["create_tickets"].invoke(
            {"mode": draft.get("mode") or "task", "items": items,
             **({"children": kids} if kids else {}),
             "approval_token": state.get("approval_token") or ""})
        created = [c for c in (r.get("created") or []) if isinstance(c, dict) and c.get("key")]
        failed = [f for f in (r.get("failed") or []) if isinstance(f, dict)]
        if not r.get("ok") and not created and not failed:
            # 검증 거부·토큰 거부 — 항목별 실패가 아니라 배치 전체가 시작을 안 한 것
            failed = [{"summary": it.get("summary", ""), "error": r.get("error") or ""}
                      for it in items[:1]]
        return {"result": {"created": created, "failed": failed, "updated": [],
                           "note": ""},
                "trace": note(state, self.name,
                              f"생성 {len(created)}건" + (f" · 실패 {len(failed)}건" if failed else ""))}

    @property
    def tools(self):
        from app.agent import tools as T
        return T.WRITE_TOOLS + T.REVIEW_TOOLS

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
        return SCHEMA

    def apply(self, state, out):
        created = [c for c in (out.get("created") or []) if isinstance(c, dict) and c.get("key")]
        updated = [u for u in (out.get("updated") or []) if isinstance(u, dict) and u.get("key")]
        failed = [f for f in (out.get("failed") or []) if isinstance(f, dict)]
        summary = (f"변경 {len(updated)}건" if updated else f"생성 {len(created)}건") + (
            f" · 실패 {len(failed)}건" if failed else "")
        return {"result": {"created": created, "updated": updated, "failed": failed,
                           "note": out.get("note") or ""},
                "trace": note(state, self.name, summary)}
