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
                    "질문에 답해 달라고 짧게 청하라. 조사는 답을 받은 뒤 시작한다고 말하라. "
                    "전체 5문장 이내 — 이 턴의 값어치는 빠른 왕복이다.")
        elif qs:
            goal = "지금까지 파악한 상황을 짧게 정리하고, 모자란 정보를 물어라."
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
        if state.get("topic_dossier") and not qs and not result:
            goal = ("**질문이 요구한 값부터, 읽히는 구조로 답하라.** 형식(가시성 실측 지적 반영):\n"
                    "① 결론 1~2문장 — 물어본 값의 핵심만.\n"
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
                    "   `[2] http://…/pages/123/문서-제목 — 스키마·Job 정리` "
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
                           f"{k}→{v}" for k, v in (cp.get('changes') or {}).items())
                        if cp.get("key") else "")(state.get("change_plan") or {})),
            data_block("변경 결과", "\n".join(
                f"- {u.get('key')} ({', '.join(u.get('fields') or [])})"
                for u in (result.get("updated") or []))),
            data_block("쪼갠 이유", (state.get("draft") or {}).get("rationale")),
            data_block("담당자 제안과 근거", asg),
            data_block("검증에서 걸린 것", errors),
            data_block("검토 의견", problems),
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

    rows = _re.findall(r"(?:^|\n)\s*-?\s*\[(\d+)\]\s*([^\n]*)", block)
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


def _violations(g: dict) -> int:
    return len(g.get("fake_keys") or []) + len(g.get("wrong_titles") or {}) \
        + len(g.get("fake_people") or [])
