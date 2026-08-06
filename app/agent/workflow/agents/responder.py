"""Responder — 지금까지 나온 것을 **사람이 읽을 한 덩어리**로 만든다.

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

from app.agent.workflow.agents.base import TextAgent
from app.agent.workflow.agents.refiner import draft_text
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import AgentState, Intent, Node, last_user_text, note


class Responder(TextAgent):
    name = Node.RESPONDER
    temperature = 0.4          # 사람에게 보일 문장이라 약간의 자연스러움이 필요하다

    def system(self, state):
        return persona(state, """\
너는 지금 **사용자에게 말한다**. 앞에서 조사·구체화·검토한 결과를 한 덩어리로 정리하라.

- **근거를 문장 안에 넣는다.** "관련 이력이 있습니다"가 아니라 "DL-118 에서 작년 11월에
  같은 검토가 있었고 소스 DB 부하 때문에 멈췄습니다".
- 자료에 없는 것을 지어내지 마라. 없으면 없다고 말한다.
- 마크다운을 쓴다. 티켓 초안은 목록으로, 담당자 근거는 그 사람 줄에 붙여서.
- 길게 쓰지 마라. 핵심 먼저, 세부는 목록으로.
- **아직 아무것도 만들어지지 않았다면 그 사실을 분명히 하고 승인을 요청**한다.
  "만들었습니다"라고 쓰면 사용자가 오해한다.""")

    def task(self, state):
        intent = state.get("intent") or Intent.PLAN_WORK
        result, review = state.get("result") or {}, state.get("review") or {}
        qs = state.get("questions") or []

        if result:
            goal = ("실행 결과를 보고하라. 실패한 항목이 있으면 **가장 먼저** 알리고 "
                    "무엇을 해야 하는지 말하라.")
        elif qs:
            goal = "지금까지 파악한 상황을 짧게 정리하고, 모자란 정보를 물어라."
        elif state.get("draft", {}).get("items"):
            goal = ("상황 → 티켓 초안 → 담당자 근거 → 검증 결과 순으로 정리하고, "
                    "**마지막에 승인을 요청**하라. 아직 만들어지지 않았음을 분명히 하라.")
        elif intent in Intent.DIRECT_ANSWER:
            goal = ("현황 조회 결과를 보고하라. 숫자와 티켓 키를 그대로 쓰고, "
                    "권하는 행동(action)이 있으면 항목마다 붙여라. 조회가 거부됐다면(권한) "
                    "그 사실을 그대로 전하라.")
        elif intent in (Intent.ASK, Intent.CHITCHAT):
            goal = "조사 결과로 질문에 답하라. 못 찾았으면 못 찾았다고 하라."
        else:
            goal = "지금까지 파악한 것을 정리하고 다음에 무엇이 필요한지 말하라."

        asg = "\n".join(
            f"- [{a.get('index')}] {a.get('user') or '(미정)'} — {'; '.join(a.get('reasons') or [])}"
            + ("".join(f"\n    대안 {x.get('user')}: {x.get('why','')}"
                       for x in (a.get("alternates") or [])))
            for a in (state.get("assignments") or []))
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')} — {e.get('why','')}"
                       for e in (state.get("evidence") or []))
        docs = "\n".join(f"- {d.get('title','')} {d.get('url','')}"
                         for d in (state.get("related_docs") or []))
        problems = "\n".join(f"- [{p.get('index')}] {p.get('message')} → {p.get('fix','')}"
                             for p in (review.get("problems") or []))
        errors = "\n".join(f"- [{e.get('index')}] {e.get('field')}: {e.get('message')}"
                           for e in (review.get("errors") or []))
        made = "\n".join(f"- {c.get('key')} {c.get('summary','')}" for c in (result.get("created") or []))
        bad = "\n".join(f"- {f.get('summary','')}: {f.get('error','')}" for f in (result.get("failed") or []))

        pmo = "\n".join(
            f"- {f.get('key','')} {f.get('point','')}" + (f" → {f['action']}" if f.get("action") else "")
            for f in (state.get("pmo_findings") or []))
        data = wrap_data(
            data_block("현재 상황(조사 결과)", state.get("situation")),
            data_block("현황 조회 결과", pmo),
            data_block("읽을 때 주의", state.get("pmo_caution")),
            data_block("근거 티켓", ev),
            data_block("관련 문서", docs),
            data_block("티켓 초안 (아직 만들어지지 않음)", draft_text(state.get("draft"))),
            data_block("쪼갠 이유", (state.get("draft") or {}).get("rationale")),
            data_block("담당자 제안과 근거", asg),
            data_block("검증에서 걸린 것", errors),
            data_block("검토 의견", problems),
            data_block("되물을 것", "\n".join(f"- {q}" for q in qs)),
            data_block("실제로 만들어진 티켓", made),
            data_block("실패한 항목", bad))

        return f"# 명령서\n{goal}\n\n## 사용자의 요청\n{last_user_text(state)}{data}"

    def apply(self, state, out):
        text = out.get("text") or ""
        from langchain_core.messages import AIMessage
        return {"reply": text, "messages": [AIMessage(content=text)],
                "trace": note(state, self.name, f"{len(text)}자")}
