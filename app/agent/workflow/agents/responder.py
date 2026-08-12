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
from app.agent.workflow.state import (AgentState, Intent, Node, last_user_text, note,
                                      request_text)


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
        elif (state.get("draft") or {}).get("structure_tree"):
            # ── 뼈대 합의 턴 — **아직 초안이 아니다.** 본문이 없으니 본문 이야기를 하면
            #    사용자는 없는 것을 읽으려 한다. 보여 줄 것은 나무 하나와 고칠 방법뿐이다.
            goal = ("**아직 티켓 내용을 쓰지 않았다 — 지금은 구조를 맞추는 단계다.**\n"
                    "① 왜 이렇게 나눴는지 두 문장 ② 아래 구조도를 **코드블록 그대로** "
                    "옮긴다(들여쓰기가 관계를 보여 준다 — 표로 바꾸지 마라) ③ 고칠 수 있는 "
                    "것을 한 줄로 안내한다(합치기·나누기·추가·삭제·이름 변경).\n"
                    "배경·작업 범위·완료 조건은 **쓰지 마라** — 구조가 확정되면 그때 채운다.\n\n"
                    "```\n" + str((state.get("draft") or {}).get("structure_tree")) + "\n```")
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
            # ★ 자르기 안내는 **정말 자를 때만** 싣는다(사용자 관점 리뷰 F4).
            #   예전엔 건수와 무관하게 실려서, 2건짜리 계획에 모델이 그대로 받아
            #   "나머지 0건은 승인 카드에서 확인 가능합니다"라고 썼다 — 0건이라는 말은
            #   읽는 사람을 멈춰 세울 뿐 아무것도 알려 주지 않는다.
            #   **빈 수치를 문장으로 만들지 않는다**: 조건이 안 서면 그 문장 자체가 없어야 한다.
            cut = (f" 표가 길다 — 앞의 10건만 쓰고 '나머지 {n - 10}건은 승인 카드에서 확인'"
                   "이라고 밝혀라." if n > 10 else "")
            goal = (f"**{n}건 일괄 변경** 계획이다 — {n}건을 **빠짐없이** 표"
                    "(| 티켓 | 제목 | 변경 |)로 보여 주고 승인을 요청하라. 건수가 적어도 "
                    "표를 생략하지 마라 — '다음 두 건에 대해'만 쓰면 **무엇을 승인하는지 "
                    "모른 채 승인하게 된다**(실측 지적). **제목을 반드시 넣어라** — 키만 "
                    "늘어놓아도 같은 문제다." + cut
                    + " 아직 아무것도 바뀌지 않았음을 분명히 하라.")
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
            # ★ 담당 후보 재료 — 여태 Responder 에 **안 왔다**. pre_survey 에서 티켓 현재값과
            #   문서 본문만 잘라 썼기 때문에, 코드가 로스터·부하까지 조회해 실어 준 후보가
            #   Historian 의 situation 요약 한 겹을 지나며 사라졌다(실측 EDGE13: "누가 하면
            #   좋을지랑 지금 상황" 에 상황만 답하고 후보를 통째로 뺐다 — 세 번 연속).
            #   사람 이름은 사번 그대로 옮겨야 하므로 원문을 준다.
            data_block("담당 후보 재료 (코드가 로스터·부하를 조회함 — 사용자가 '누가'를 "
                       "물었으면 여기서 2~3명을 **사번으로** 대고 근거를 붙여라. "
                       "'추천할 정보가 부족하다'로 끝내지 마라)",
                       _candidate_block(state.get("pre_survey"))),
            data_block("쪼갠 이유", (state.get("draft") or {}).get("rationale")),
            data_block("담당자 제안과 근거", asg),
            data_block("검증에서 걸린 것", errors),
            # 검토 의견은 **미해결일 때만** 사용자 몫이다. 검증을 통과해 승인 카드가 뜨는
            # 턴에 내부 지적("하나의 Task 로 통합하는 것이 좋습니다")을 그대로 옮기면
            # 카드와 모순되는 안내가 된다(실측 Round O, 2회 재발). 반영 여부는 Refiner 가
            # 이미 판단했고 근거는 rationale 에 남는다.
            data_block("검토 의견", "" if (draft_items and review.get("ok") and not errors)
                       else problems),
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

        # ── 원 요청을 함께 싣는다 — **답의 성격은 마지막 발화가 아니라 원 요청이 정한다.**
        # 실측: "fdc flat trace ic 데이터 히스토리 정리" → 표기 확인 질문 → 사용자가 보기
        # 하나("fdc.fdc_trace_summary_ic")를 고르자, 이 자리에 그 한 낱말만 실려서 답이
        # **연표가 아니라 현재 값 표**로 나왔다(티켓 8건 중 2건만 인용). 확인 턴을 지난
        # 대화는 마지막 발화가 짧은 선택지라, 그것만 보면 무엇을 묻는 대화인지 알 수 없다.
        # request_text 는 원 요청을 고정해 두려고 만든 장치인데 여기에만 연결이 없었다.
        req, last = (request_text(state) or "").strip(), (last_user_text(state) or "").strip()
        asked = (f"## 원래 요청 (이 대화가 시작된 질문 — **답의 성격은 이것이 정한다**)\n{req}\n\n"
                 f"## 이번 턴 사용자의 말\n{last}") if req and req != last else \
                f"## 사용자의 요청\n{last}"
        return f"# 명령서\n{goal}\n\n{asked}{data}"

    def apply(self, state, out):
        text = out.get("text") or ""
        # 모델이 내부 task wrapper를 답변으로 복창하거나 reference placeholder를 그대로
        # 노출하는 것은 내용 문제가 아니라 렌더링 계약 위반이다. grounding 전에 정규화해
        # 검사와 사용자 화면이 같은 문자열을 보게 한다.
        text = _strip_instruction_echo(text)
        text = _render_reply_tokens(text)
        text = _align_draft_claims(text, state)

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
        try:
            g = grounding.check(text, allowed_people=_dialogue_speakers(request_text(state)))
        except Exception:
            g = None                        # 검증기가 죽으면 답은 그대로 나간다
        if g and not g["ok"]:
            text2, g2 = "", None
            try:
                fixed = self.llm().invoke([
                    ("system", self.system(state)),
                    ("user", f"방금 쓴 답에 사실 오류가 있다. 아래만 고쳐 전체를 다시 써라. "
                             f"다른 내용은 유지하라.\n{grounding.violation_note(g)}\n\n"
                             f"### 방금 쓴 답\n{text}")])
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
                text = better + grounding.warning_block(gb)

        # 참조 인덱스 후처리 — 같은 출처가 두 번호를 받는 실측 미스([1]·[3]가 같은 티켓)를
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
        # ★ 여기서 **한 번 더** 본다. 위의 후처리들은 접지 검사 **뒤에** 돌기 때문에,
        #   후처리가 만든 결함은 검사를 통과한 셈이 된다. 실측: 대괄호 정리가 문서 링크를
        #   먹어 참조가 URL 없는 제목만 남았는데 아무도 못 잡았다. 마지막에 다시 보고,
        #   후처리가 부순 것이면 조용히 넘기지 않는다(재작성은 이미 끝난 자리라 경고만).
        try:
            _late = grounding._unlinked_refs(text)
            if _late and (not g or not g.get("unlinked_refs")):
                text += grounding.warning_block({"unlinked_refs": _late})
        except Exception:
            pass

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

        # ── 후검증 — **플레이북별 최소선**(사용자 지시: 주요 태스크는 결과도 검증)
        # 프롬프트에 적어 두면 '대체로' 지켜진다. 문제는 그 '대체로'다 — 같은 요청이
        # 어떤 날은 연표만 나오고 어떤 날은 현재 상태까지 나온다. 흔들림은 지시로 못 잡으니
        # **잴 수 있는 것은 코드가 재고**, 못 지켰으면 숨기지 않고 드러낸다.
        try:
            from app.agent.workflow import postcheck
            _bad = postcheck.check(state, text)
            if _bad:
                text += postcheck.note(_bad)
        except Exception:
            pass

        from langchain_core.messages import AIMessage
        return {"reply": text, "messages": [AIMessage(content=text)],
                "trace": note(state, self.name, f"{len(text)}자")}


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
    """Responder placeholder를 깨지지 않는 canonical Markdown link/mention으로 렌더한다."""
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

    out = _re.sub(r"\{\{+ref:([A-Za-z0-9_.:-]+)\}+\}", ref, str(text or ""))
    out = _re.sub(r"\{\{+mention:([A-Za-z0-9_.:-]+)\}+\}", r"[~\1]", out)
    return out


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
        text = _drop_lineage_game_drift(text, state)
        text = _align_story_point_claims(text, state, items)
        text = _ensure_dod_claims(text, items)
        text = _drop_unverified_reply_keys(text, state, items)
        text = _drop_false_epic_claims(text, items)
        text = _align_parent_labels(text, items)
        text = _align_item_owner_claims(text, items)
        text = _align_child_owner_claims(text, items)
        text = _align_assigned_owner_cautions(text, items)
        text = _align_workload_claims(text, state)
        text = _normalize_alternate_language(text)
        text = _drop_unsupported_assignment_experience(text, state)
        text = _drop_resolved_review_feedback(text, items)
        text = _align_child_presence_claims(text, items)
        text = _drop_unrequested_deployment_claims(text, state)
    return text


def _drop_resolved_review_feedback(text: str, items: list) -> str:
    """최종 payload에서 이미 고친 DoD에 대한 이전 Reviewer 의견을 답변에서 걷는다."""
    from app.agent.workflow.agents.refiner import _dod_rows, _vague_dod

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
    # 모델이 카드의 실제 유형보다 한 단계 크게 소개하는 경우가 있다. 단건 카드의 명시적
    # 유형 줄은 버리지 말고 payload 유형으로 고쳐 제목을 보존한다.
    if not actual and not actual_epics and len(items) == 1:
        actual_type = str(items[0].get("type") or "Task")
        text = _re.sub(r"(?mi)^(\s*-?\s*\*\*)(?:Epic|에픽)(\*\*\s*:\s*)",
                       rf"\1{actual_type}\2", str(text or ""))
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
            false_draft_type = bool(not actual and not actual_epics and mentions_epic
                                    and _re.search(r"초안|생성되지|만들", sentence))
            if false_draft_type or (not negative and (
                    (false_key and mentions_epic) or (positive and false_generic))):
                continue
            kept.append(sentence)
        joined = " ".join(p for p in kept if p.strip()).strip()
        if joined:
            lines.append(joined)
    out = "\n".join(lines)
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
        rows += [f"| {c['summary']} | {c['assignee']} |" for c in children]
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
    """사번 옆 진행중 건수와 부하의 정성 표현을 최종 Assigner 근거와 맞춘다."""
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
        if current and "대안" not in line and (
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


def _candidate_block(pre) -> str:
    """사전 조사에서 **담당 후보(로스터·부하)** 블록만 뽑는다(있을 때만).

    이 블록은 Responder 에 **오지 않고 있었다** — pre_survey 에서 티켓 현재값과 문서 본문만
    잘라 썼기 때문이다. 그래서 코드가 로스터·부하까지 조회해 실어 준 후보가 Historian 의
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
        + len(g.get("fake_people") or []) + len(g.get("unlinked_refs") or [])


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
