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

import re as _re

from app.agent.workflow.agents.base import TextAgent
from app.agent.workflow.agents.refiner import draft_text
from app.agent.prompts.roles import SYSTEM_RESPONDER
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import AgentState, Intent, Node, last_user_text, note


class Responder(TextAgent):
    name = Node.RESPONDER
    temperature = 0.4          # 사람에게 보일 문장이라 약간의 자연스러움이 필요하다

    def system(self, state):
        return persona(state, SYSTEM_RESPONDER)

    def task(self, state):
        intent = state.get("intent") or Intent.PLAN_WORK
        result, review = state.get("result") or {}, state.get("review") or {}
        qs = state.get("questions") or []

        if result:
            goal = ("실행 결과를 **짧게** 보고하라: 만든 것 한 줄씩(키+제목), 실제 실패가 "
                    "있으면 그것만 사유와 함께. 실패·후속 조치·주의 항목을 **지어내지 마라** — "
                    "자료의 created/failed 에 없는 말은 전부 날조다. 사용자가 이미 내린 결정"
                    "(예: Epic 없이 최상위로)을 다시 경고하지 마라. 3~5문장이면 충분하다.")
        elif qs and (state.get("interpretation") or "").strip():
            goal = ("조사 전 **해석 확인** 턴이다. ① 자료의 '요청 해석'을 \"제가 이해한 바\"로 "
                    "먼저 보여라(사용자가 바로잡을 수 있게 — 고치지 말고 그대로) ② 이어서 "
                    "질문에 답해 달라고 짧게 청하라. 답을 받은 뒤에 다음 단계로 간다고 "
                    "말하되, **그 단계를 정확히** 말하라 — 조사가 필요한 요청이면 '조사', "
                    "변경 요청이면 '변경 카드를 만들겠다'다(실측: 상태 전이 요청에 "
                    "'조사를 시작하겠습니다'라고 답했다). "
                    "전체 5문장 이내 — 이 턴의 값어치는 빠른 왕복이다.")
        elif qs:
            goal = ("지금까지 파악한 상황을 **2~3문장으로** 정리하고 끝내라. "
                    "★ 질문은 **아래 폼이 묻는다** — 질문 문장·보기·번호 목록을 산문으로 "
                    "다시 쓰지 마라(실측: 폼에 있는 것을 통째로 베껴 화면에 같은 말이 두 벌 "
                    "떴다). 폼에 없는 것을 추가로 요구하지도 마라. "
                    "마지막 줄은 '아래에서 선택해 주세요' 정도면 충분하다.")
        elif (state.get("change_plan") or {}).get("keys"):
            n = len(state.get("change_plan", {}).get("keys") or [])
            goal = (f"**{n}건 일괄 변경** 계획이다 — 표(| 티켓 | 제목 | 변경 |)로 보여 주고 "
                    "승인을 요청하라. **제목을 반드시 넣어라** — 키만 늘어놓으면 무엇의 "
                    "우선순위를 올리는지 모른 채 승인하게 된다(실측 지적). 표가 길면 "
                    f"앞의 10건만 쓰고 '나머지 {max(0, n - 10)}건은 승인 카드에서 확인'이라고 "
                    "밝혀라. 아직 아무것도 바뀌지 않았음을 분명히 하라.")
        elif (state.get("change_plan") or {}).get("key"):
            goal = ("어떤 티켓의 무엇을 어떻게 바꾸려는지 요약하고 **승인을 요청**하라. "
                    "아직 아무것도 바뀌지 않았음을 분명히 하라.")
        elif state.get("draft", {}).get("items"):
            n_items = len(state.get("draft", {}).get("items") or [])
            goal = ("상황 → 티켓 초안 → 담당자 근거 → 검증 결과 순으로 정리하고, "
                    "**마지막에 승인을 요청**하라. 아직 만들어지지 않았음을 분명히 하라 — "
                    "\"만들었습니다\"라고 쓰면 사용자가 오해한다."
                    + ("\n★ 초안이 여러 건이다 — **전 항목을 표**(| # | 제목 | 모듈 | Epic | "
                       "마감 |)로 보여라. 첫 항목만 풀어 쓰고 나머지를 생략하면 사용자는 "
                       "카드를 열기 전까지 무엇을 승인하는지 모른다(실측 지적)."
                       if n_items > 1 else ""))
        elif state.get("ticket_progress"):
            # 진척 질문에 "In Progress 입니다"는 답이 아니다 — 무엇이 끝났고 무엇이 남았는지를
            # 근거(코멘트·변동·하위 티켓·결과 문서)와 함께 시간순으로 서술한다.
            goal = ("티켓 진척을 보고하라 — ① 지금 어디까지 왔나(하위 완료 개수와 끝난 항목) "
                    "② 그렇게 판단한 근거(진행 보고 코멘트·티켓 변동·막던 티켓 해소·결과 문서의 "
                    "최근 수정) ③ 남은 일과 리스크(마감 대비). 상태 이름만 옮기지 말고, "
                    "근거마다 티켓 키+제목 또는 문서 제목·수정일을 붙여라. 자료에 적힌 '남은 일'은 "
                    "그대로 옮긴다.")
        elif intent in Intent.DIRECT_ANSWER and state.get("group_activity"):
            goal = ("그룹 활동 보고 — **3층 구조로, 표 없이 서술**하라(사용자가 명시한 형식): "
                    "① 로스터: 이 모듈에 누가 있는지 한 문단. "
                    "② 모듈 전체: 이 기간 팀이 한 기여를 2~3문장으로 묶어 서술. "
                    "③ 사람별: 각자 소제목(### 이름)으로 주로 한 일을 서술 — 근거 티켓 키+제목, "
                    "코멘트·문서 활동 포함. '확인해 볼 만하다' 같은 기계적 문구 반복 금지.")
        elif intent in Intent.DIRECT_ANSWER:
            goal = ("현황 조회 결과를 보고하라. 숫자와 티켓 키를 그대로 쓰고, "
                    "권하는 행동(action)이 있으면 항목마다 붙여라. 조회가 거부됐다면(권한) "
                    "그 사실을 그대로 전하라.")
        elif intent in (Intent.ASK, Intent.CHITCHAT):
            goal = "조사 결과로 질문에 답하라. 못 찾았으면 못 찾았다고 하라."
            # 상담형("어떻게 하는 게 좋을까") — 상황 요약만 하고 끝나면 조언이 아니다(실측:
            # '신속히 완료하세요' 수준). 선택지를 준다.
            if any(w in last_user_text(state) for w in ("어떻게 하는 게", "어떻게 해야",
                                                        "어쩌", "좋을까", "방안", "대안")):
                goal = ("상담 요청이다 — ① 상황 1~2문장(근거 키 포함) ② **선택지 2~3개를 표**"
                        "(| 옵션 | 영향 | 바로 할 일 |)로: 예컨대 마감 연기/범위 축소/"
                        "재배분·헬프 요청 중 자료에 비추어 실제로 가능한 것만 ③ 네가 추천하는 "
                        "옵션 하나와 이유 한 줄 ④ '원하시면 바로 진행하겠습니다'로 실행 제안"
                        "(마감 변경·재배분은 승인 카드로 이어질 수 있는 일이다).")
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
        draft_items = [i for i in ((state.get("draft") or {}).get("items") or [])
                       if isinstance(i, dict) and i.get("summary")]
        made = "\n".join(f"- {c.get('key')} {c.get('summary','')}" for c in (result.get("created") or []))
        bad = "\n".join(f"- {f.get('summary','')}: {f.get('error','')}" for f in (result.get("failed") or []))

        pmo = "\n".join(
            f"- {f.get('key','')} {f.get('point','')}" + (f" → {f['action']}" if f.get("action") else "")
            for f in (state.get("pmo_findings") or []))
        # 지식 브리프(Curator) — 있으면 답변의 뼈대다: 개념 → 우리 상황 → 참고 → 공백 순.
        kb = state.get("knowledge_brief") or {}
        brief = ""
        if kb:
            brief = "\n".join(
                ["[개념]"] + [f"- {c.get('term')}: {c.get('explanation')}" for c in kb.get("concepts") or []]
                + ["[우리 상황]", kb.get("our_context") or ""]
                + ["[참고]"] + [f"- {r.get('ref')} — {r.get('why')}" for r in kb.get("references") or []]
                + ["[남은 공백]"] + [f"- {g}" for g in kb.get("gaps") or []])
            goal = ("지식 브리프를 뼈대로 답하라: 개념 설명 → 우리 프로젝트의 상황(근거 병기) → "
                    "참고할 것 → 아직 모르는 것 순. 브리프에 없는 내용을 보태지 마라.")
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
            goal = ("**질문이 요구한 값부터, 읽히는 구조로 답하라.** 형식(가시성 실측 지적 반영):\n"
                    "① 결론 1~2문장 — 물어본 값의 핵심만.\n"
                    "⓪ 후속 질문(대화에 직전 답이 있음)이면 **직전 답에 이미 보인 표를 "
                    "반복하지 마라** — 새로 물은 것(배경·이유·세부)만 서술로 답한다(실측: "
                    "같은 현재 값 표가 두 턴 연속 출력됐다).\n"
                    "② **현재 값 표** — | 항목 | 값 | 근거 | 3열. 주기·Job·담당·스키마처럼 "
                    "자료에 있는 운영 값을 행으로. 근거 열은 [1] 같은 참조 번호만.\n"
                    "③ 히스토리는 **표**로 — | 날짜 | 사건 | 근거 | 3열, 한 사건 한 행.\n"
                    "④ 자료에 목록이 있으면(컬럼 8개 등) 생략·요약하지 말고 그대로 옮겨라.\n"
                    "⑤ 없는 값: 사용자가 **실제로 물은 것**에 한해 '확인된 기록 없음'을 밝히되 "
                    "한두 문장으로 묶는다 — 안 물은 항목까지 '없음'으로 나열하는 것 금지"
                    "(실측: 없음 불릿 6줄이 답을 덮었다). 비슷한 다른 대상의 값 전이 금지.\n"
                    "⑥ ★ **참조 인덱스** — 본문 문장마다 티켓 제목·작성자·날짜를 끼워 넣지 "
                    "마라(가독성을 죽인다). 본문에는 `[1]` `[2]` 번호만 달고, 답 **맨 끝**에 "
                    "`**참조**` 섹션으로 모은다(그 뒤에 다른 내용 금지 — 화면이 접이식 영역으로 "
                    "그린다). 형식 — **불릿(-) 없이** 번호로 시작하는 한 줄씩:\n"
                    "   `[1] DL-9044 — 적재주기 변경(2시간→30분)의 1차 근거`\n"
                    "   `[2] <실제 문서 URL> — 스키마·Job 정리` (형식 예시다 — 이 줄을 복사하지 마라) "
                    "(문서는 **URL 만** — 제목을 다시 쓰지 마라, 뱃지가 제목을 보여 준다)\n"
                    "   `[3] DL-9062 코멘트 (skcc.x1103, 2026-08-05) — 담당·시간축 불일치`\n"
                    "   같은 근거는 같은 번호 재사용.\n"
                    "⑦ 서식을 사람 눈을 위해 써라 — 식별자·값·Job 이름은 `인라인 코드`, 섹션은 "
                    "### 헤딩, 핵심 값은 **볼드**, 원문 인용은 > 인용, 필요하면 구분선(---).\n"
                    "값이 바뀐 적 있으면 '현재 X (이전 Y, 언제 변경 [N])' — 그 값을 **바꾼** "
                    "티켓이 1차 출처다(인용만 한 티켓으로 대체 금지). 담당은 자료의 `[담당]` "
                    "줄이 곧 답이다 — 코멘트 작성자를 담당자로 지어내지 마라.")
        data = wrap_data(
            data_block("요청 해석 (조사 전 확인용 — \"제가 이해한 바\"로 그대로 보여라)",
                       state.get("interpretation")),
            data_block("지식 브리프(Curator 정리)", brief),
            data_block("그룹 활동 자료(로스터 전원 — 이것으로 3층을 쓴다)",
                       state.get("group_activity")),
            data_block("티켓 진척 자료 (코드가 변동·코멘트·하위·문서를 취합함)",
                       state.get("ticket_progress")),
            # 주제 조사 원본 — 결론 문장(situation)만 실으면 조각의 출처(코멘트 작성자·
            # 변경 일자)가 사라져 "근거를 대라"는 요구를 만족시킬 수 없다.
            data_block("주제 조사 자료 (여기 없는 값은 '확인된 기록 없음'이라고 답한다)",
                       state.get("topic_dossier")),
            data_block("현재 상황(조사 결과)", state.get("situation")),
            data_block("현황 조회 결과", pmo),
            data_block("읽을 때 주의", state.get("pmo_caution")),
            data_block("근거 티켓", ev),
            data_block("관련 문서", docs),
            data_block("티켓 초안 (아직 만들어지지 않음)", draft_text(state.get("draft"))),
            data_block("변경 계획 (아직 바뀌지 않음)",
                       (lambda cp: f"{cp.get('key')}: " + ", ".join(
                           f"{k}: {(cp.get('before') or {}).get(k) or '없음'}→{v}"
                           for k, v in (cp.get('changes') or {}).items())
                        if cp.get("key") else
                        (f"일괄 {len(cp.get('keys'))}건 — 공통 변경: "
                         + ", ".join(f"{k}→{v}" for k, v in (cp.get('changes') or {}).items())
                         + "\n대상(키 · 제목):\n" + _key_titles(cp.get("keys"))
                         if cp.get("keys") else ""))(state.get("change_plan") or {})),
            data_block("변경 결과", "\n".join(
                f"- {u.get('key')} ({', '.join(u.get('fields') or [])})"
                for u in (result.get("updated") or []))),
            # 코드가 조회로 확정한 티켓 현재 값 — Historian 요약이 담당·마감을 떨구는 일이
            # 잦다(실측 Round P: 담당 skcc.x1402 를 "확인되지 않음"으로). 요약과 다르면
            # 이쪽이 사실이다.
            data_block("지목 티켓의 현재 값 (코드가 조회로 확정 — 요약과 다르면 이쪽이 맞다)",
                       "\n".join(l for l in str(state.get("pre_survey") or "").splitlines()
                                 if _re.match(r"\[[A-Z]+-\d+ (현재|변동|코멘트|하위|링크)\]", l))),
            # 문서 요약 요청의 재료는 **문서 본문**이다. Historian 요약은 "절차가 정리되어
            # 있습니다" 같은 메타 서술로 뭉개진다(실측 T3) — 원문을 그대로 준다.
            data_block("문서 본문 (요약은 이걸로 — 문서가 정한 규칙·명명 규약·기준을 "
                       "빠뜨리지 말고, 출처 링크를 함께 남겨라)",
                       _doc_body(state.get("pre_survey"))),
            data_block("쪼갠 이유", (state.get("draft") or {}).get("rationale")),
            data_block("담당자 제안과 근거", asg),
            data_block("검증에서 걸린 것", errors),
            # 검토 의견은 **미해결일 때만** 사용자 몫이다. 검증을 통과해 승인 카드가 뜨는
            # 턴에 내부 지적("하나의 Task 로 통합하는 것이 좋습니다")을 그대로 옮기면
            # 카드와 모순되는 안내가 된다(실측 Round O, 2회 재발). 반영 여부는 Refiner 가
            # 이미 판단했고 근거는 rationale 에 남는다.
            data_block("검토 의견", "" if (draft_items and not errors) else problems),
            data_block("되물을 것", "\n".join(f"- {q}" for q in qs)),
            data_block("실제로 만들어진 티켓", made),
            data_block("실패한 항목", bad))

        # ── 답변 깊이 — 물어본 만큼만 답한다(사용자 요청).
        # 값 하나를 물었는데 개념 강의가 앞에 붙으면 정작 답이 묻힌다(judge 가 반복 지적).
        # 반대로 "왜/어떻게"를 물었는데 값만 던지면 불친절하다. Planner 가 가른다.
        # 어느 쪽이든 **더 깊은 설명은 다음 턴에** — 사용자가 요청하면 그때 푼다.
        depth = state.get("answer_depth") or "brief"
        if not qs:                       # 되묻는 턴은 질문 폼이 주인공이라 건드리지 않는다
            if depth == "explain":
                goal += ("\n\n[답변 깊이: 설명형] 배경·개념·경위를 함께 설명하되 **간결한 요약체**를 "
                         "유지하라. 문단은 3~4줄 이내, 소제목은 꼭 필요할 때만. 결론을 먼저 두고 "
                         "설명을 뒤에 붙인다.")
            else:
                goal += ("\n\n[답변 깊이: 결론형] **물어본 것만** 답하라. 개념 설명·배경·일반론을 "
                         "덧붙이지 마라. 결론 한두 문장 + 근거 몇 줄이면 끝이다. 자료에 목록이 "
                         "있고 사용자가 그 목록을 물었으면 목록은 그대로 싣는다(그게 답이다).")
            goal += ("\n마지막 줄에 더 알아볼 만한 것을 **한 줄만** 짧게 제안하라 — 예: "
                     "'변경 경위나 관련 티켓 내용이 더 궁금하면 말씀 주세요.' 여러 줄로 늘어놓거나 "
                     "승인·생성을 다시 묻지는 마라.")

        return f"# 명령서\n{goal}\n\n## 사용자의 요청\n{last_user_text(state)}{data}"

    def apply(self, state, out):
        text = out.get("text") or ""

        # ── 접지 검사 — 답변의 티켓 키·제목·인명을 실물과 대조한다.
        # 지도·자료를 정확히 줘도 답변 단계에서 날조가 나왔다(없는 키, 바뀐 제목, "PM: 김철수").
        # 프롬프트로 세 번 막아 봤지만 재발 — 이 부류는 부탁할 일이 아니라 **검증할 일**이다.
        # 위반이 나오면 실값을 쥐여 주고 한 번 다시 쓰게 하고, 그래도 남으면 경고를 붙인다.
        # 조용히 고치지 않는 이유: 무엇이 걸렀는지 보여야 사용자가 시스템을 믿을 수 있다.
        from app.agent.workflow import grounding
        try:
            g = grounding.check(text)
            if not g["ok"]:
                fixed = self.llm().invoke([
                    ("system", self.system(state)),
                    ("user", f"방금 쓴 답에 사실 오류가 있다. 아래만 고쳐 전체를 다시 써라. "
                             f"다른 내용은 유지하라.\n{grounding.violation_note(g)}\n\n"
                             f"### 방금 쓴 답\n{text}")])
                text2 = str(getattr(fixed, "content", "") or "").strip() or text
                g2 = grounding.check(text2)
                if g2["ok"]:
                    text = text2
                else:                       # 재작성으로도 못 고침 — 덜 틀린 쪽에 경고를 단다
                    better = text2 if _violations(g2) <= _violations(g) else text
                    gb = g2 if better is text2 else g
                    text = better + grounding.warning_block(gb)
        except Exception:
            pass                            # 검증기가 죽어도 답은 나가야 한다

        # 참조 인덱스 후처리 — 같은 출처가 두 번호를 받는 실측 미스([1]·[3]가 같은 티켓)를
        # 코드가 접는다. 규칙("같은 근거 같은 번호")은 프롬프트에 있지만 보장은 여기서.
        text = _dedupe_refs(text)
        # '확인된 기록 없음'만 채운 표 행·참조 줄은 정보가 아니라 소음이다 — md 로 두 번
        # 금지했는데 재발(실측 2회). 코드가 걷어낸다.
        text = _prune_empty_rows(text)
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
                text = text.rstrip() + "\n\n출처: [" + _t + "](" + _u + ")"
        # 쓰다 만 링크 토막("[여기에서 확인할 수 있습니다.") — 여는 대괄호만 남으면
        # 화면에 대괄호가 글자로 보인다(실측). 짝 없는 `[` 는 지운다.
        text = _re.sub(r"\[(?=[^\]\n]{0,60}(?:\n|$))", "", text)

        _qs = [q for q in (state.get("questions") or []) if isinstance(q, dict)]
        if _qs:
            text = _drop_form_echo(text, _qs)
        # 카드의 값과 문장의 값이 다르면 **카드가 사실**이다. 상대 날짜는 코드가 계산해
        # 계획에 넣는데(모델 산술이 흔들린다), 답변 문장에는 모델이 제 값을 그대로 써서
        # "2026-08-18로 연장" ↔ 카드 2026-08-14 로 어긋났다(실측 Round P).
        due = str(((state.get("change_plan") or {}).get("changes") or {}).get("duedate") or "")
        if _re.match(r"^\d{4}-\d{2}-\d{2}$", due):
            text = _re.sub(r"\b\d{4}-\d{2}-\d{2}\b",
                           lambda m: due if m.group(0) != due else m.group(0), text)

        from langchain_core.messages import AIMessage
        return {"reply": text, "messages": [AIMessage(content=text)],
                "trace": note(state, self.name, f"{len(text)}자")}


def _dedupe_refs(text: str) -> str:
    """`**참조**` 섹션의 중복 출처를 병합하고 번호를 다시 매긴다.

    출처 정체성: 코멘트(키+괄호 출처) > 문서(URL) > 티켓(키 집합) > 문구.
    같은 티켓의 '티켓 참조'와 '코멘트 참조'는 다른 출처다(내용이 다르다).
    본문에서 안 쓰인 참조는 떨군다 — 규칙상 만들면 안 되는 것이라서다."""
    import re as _re
    m = _re.search(r"\*\*참조\*\*\s*\n((?:\s*-?\s*\[\d+\][^\n]*\n?)+)", text)
    if not m:
        return text
    head, block, tail = text[:m.start(1)], m.group(1), text[m.end(1):]
    body = head + tail

    def _sig(desc: str):
        keys = tuple(_re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", desc))
        com = _re.search(r"코멘트\s*\(([^)]*)\)", desc)
        if com:
            return ("comment", keys, com.group(1).strip())
        url = _re.search(r"\((https?://[^)]+)\)", desc)
        if url and not keys:
            return ("doc", url.group(1))
        if keys:
            return ("ticket", keys)
        return ("text", desc.strip().lower()[:60])

    rows = [(n, d) for n, d in
            _re.findall(r"(?:^|\n)\s*-?\s*\[(\d+)\]\s*([^\n]*)", block)
            if "…" not in d and "<실제 문서" not in d]   # 프롬프트 형식 예시 복사 차단(실측)
    survivors, alias = [], {}          # [(old, desc)], old→대표 old
    seen = {}
    for old, desc in rows:
        s = _sig(desc)
        if s in seen:
            alias[old] = seen[s]
        else:
            seen[s] = old
            alias[old] = old
            survivors.append((old, desc))
    # 본문에 실제로 인용된 대표만 남기고 1..k 재부여(본문 등장 순서).
    cited = _re.findall(r"\[(\d+)\](?!\()", body)
    order, used = [], set()
    for c in cited:
        rep = alias.get(c)
        if rep and rep not in used:
            used.add(rep)
            order.append(rep)
    if not order:
        return text
    newno = {rep: str(i + 1) for i, rep in enumerate(order)}
    mapping = {old: newno[rep] for old, rep in alias.items() if rep in newno}
    if not mapping:
        return text
    # 병합할 게 없어도 계속 간다 — 불릿 제거·문서 중복 표기 정리는 항상 적용된다.
    out_body = _re.sub(r"\[(\d+)\](?!\()",
                       lambda mm: f"[{mapping.get(mm.group(1), mm.group(1))}]", body)
    # 불릿 없이 — `[n]` 자체가 마커라 `- [n]` 은 이중 표식이다(실측 지적). 문서 참조는
    # "제목 (URL)" 중복 표기를 URL 만 남긴다 — 뱃지가 제목을 보여 준다.
    def _clean_desc(d: str) -> str:
        return _re.sub(r"^([^—\n]*?)\s*\((https?://[^\s)]+)\)", r"\2", d.strip())
    lines = [f"[{newno[old]}] {_clean_desc(desc)}" for old, desc in survivors if old in newno]
    lines.sort(key=lambda ln: int(_re.match(r"\[(\d+)\]", ln).group(1)))
    # 참조 섹션을 원래 자리(head 끝)에 다시 꽂는다.
    ref_block = "\n".join(lines) + "\n"
    cut = len(head)
    return out_body[:cut] + ref_block + out_body[cut:]


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
    # 내용 없는 섹션 헤딩("### 히스토리" 뒤가 바로 다음 헤딩/참조/끝) — 헤딩만 남기지 않는다
    # (실측: 표를 걷어낸 뒤, 또는 모델이 애초에 빈 헤딩을 냈다).
    # 맺음말("…더 궁금하면 말씀 주세요")도 섹션 내용이 아니다 — 그 앞의 빈 헤딩을 살려
    # 두면 "### 히스토리" 밑에 안내문만 붙는 꼴이 된다(실측 Round P).
    text = _re.sub(r"(?:^|\n)(#{2,4}\s+[^\n]+|\*\*[^\n*]+\*\*)\n+"
                   r"(?=(#{2,4}\s|\*\*참조\*\*|[^\n]*(?:궁금하면 말씀|말씀 주세요)|$))",
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
        #    (responder.md 의 규칙인데 실측에서 "대상 환경/조회 범위/맥락"을 덧붙였다).
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


def _violations(g: dict) -> int:
    return len(g.get("fake_keys") or []) + len(g.get("wrong_titles") or {}) \
        + len(g.get("fake_people") or [])
