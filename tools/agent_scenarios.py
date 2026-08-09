# tools/agent_scenarios.py — 복합 시나리오 배터리 v2 (실 LLM, 수동 실행 전용).
#
# 실행: python -X utf8 tools/agent_scenarios.py [모델] [케이스ID ...]
#   기본 gpt-4o-mini · 케이스ID 를 주면 그것만(교정 루프용) · --report 로 md 리포트 저장
#
# 각 케이스: 멀티턴 질의 → 규칙 체커(정확성: intent·경로·구조·키 실재)
#          + LLM judge(품질: 가시성/명확성/정보충족/근거병기 1~5 — Self-RAG 3-Check 확장).
# pytest 에 넣지 않는 이유: 실 키·비용. 릴리스 전 손으로 돌려 회귀·품질을 같이 본다.
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
_args = [a for a in sys.argv[1:] if a != "--report"]
MODEL = _args[0] if _args and not _args[0].isupper() else "gpt-4o-mini"
ONLY = set(a for a in _args if a.isupper())
REPORT = "--report" in sys.argv
os.environ["LAKE_AGENT_OPENAI_CHAT"] = MODEL

from app.agent.workflow import session  # noqa: E402


def _pending_items(o):
    return ((o.get("pending") or {}).get("items")) or []


def _has(o, *words):
    r = o.get("reply") or ""
    return all(w in r for w in words)


# (ID, 설명, [질의들], 기대 intent(마지막 턴), 체커(마지막 out, 전체 outs))
CASES = [
    ("EPIC1", "Epic 생성 인터뷰 → 초안 (기존 에픽 확인 질문 포함 2턴)", [
        "데이터 거버넌스 강화 에픽을 하나 새로 만들자. 목표는 전 테이블 메타데이터 등록률 100%야. 알아서 초안 잡아줘",
        "기존 에픽 말고 새로 만드는 게 맞아. 알아서 진행해"],
     "plan_work", lambda o, _: (_pending_items(o) and _pending_items(o)[0].get("type") == "Epic"
                                and _pending_items(o)[0].get("epic_name"))),
    ("TECH2", "기술 업무 + 분할 제안", [
        "Kafka 컨슈머 랙 모니터링 알림 개발이 필요해. 알아서 초안"],
     "plan_work", lambda o, _: bool(_pending_items(o)) or bool(o.get("questions"))),
    ("BULK3", "벌크 Sub-Task 개별 속성", [
        "DL-101 밑에 서브태스크 3개 만들어줘: 설계는 x1103, 구현은 x1042, 검증은 i2011 담당으로. 알아서"],
     "plan_work", lambda o, _: (lambda it: len(it) >= 3 and len({x.get("assignee") for x in it}) >= 3)
     (_pending_items(o))),
    ("KNOW4", "사내+외부 지식 정리", [
        "데이터 리니지가 뭐고 우리 프로젝트에서 관련해 뭘 했는지 정리해줘"],
     "ask", lambda o, _: _has(o, "DL-") or "사내 이력 없음" in (o.get("reply") or "")),
    ("ACT5", "티켓 유관자 활동", [
        "DL-101 관련자들이 요즘 어떤 일들을 하고 있는지 정리해줘"],
     None, lambda o, _: _has(o, "DL-") and "skcc." in (o.get("reply") or "")),
    ("GUIDE7", "LTM 사용법", [
        "LTM에서 티켓 담당자는 어떻게 바꿔? 그리고 강제 새로고침은 어디 있어?"],
     None, lambda o, _: any(w in (o.get("reply") or "") for w in ("인라인", "클릭", "다이얼로그"))
     and "새로고침" in (o.get("reply") or "")),
    ("MOD8", "라벨·컴포넌트 수정", [
        "DL-101에 라벨 data-quality 추가하고 컴포넌트를 Catalog로 바꿔줘"],
     "modify", lambda o, _: bool((o.get("pending") or {}).get("changes"))),
    ("REC9", "할 일 추천 → 좁히기(멀티턴)", [
        "지금 내가 할 만한 일 추천해줘",
        "그중에 마감이 아직 안 지난 것만 다시 보여줘"],
     "my_day", lambda o, outs: _has(o, "DL-")),
    ("JQL10", "자연어→JQL", [
        "우선순위가 P1이면서 진행중인데 5일 넘게 업데이트 없는 티켓을 JQL로 찾아줘"],
     None, lambda o, _: "JQL" in (o.get("reply") or "").upper()
     and (_has(o, "DL-") or "없습니다" in (o.get("reply") or ""))),
    ("FIT11", "담당 적합성 판단", [
        "DL-101을 skcc.i2011에게 맡기는 게 적절할까?"],
     "ask", lambda o, _: "i2011" in (o.get("reply") or "") and _has(o, "DL-")),
    ("CMT12", "댓글 + 멘션·문서 언급", [
        "DL-101에 '리니지 설계는 [~skcc.x1103]님과 논의 완료, 상세는 설계 노트 문서 참고' 라고 댓글 남겨줘"],
     "modify", lambda o, _: "[~skcc.x1103]" in ((o.get("pending") or {}).get("comment") or "")),
    ("REL14", "연관성 규율 — 신기술 질문에 뜬금 티켓 금지", [
        "ETL 파이프라인 상에서 적재 할 때 Iceberg Puffin NDV 통계정보를 생성하는 단계를 추가하려고해"],
     "plan_work", lambda o, _: not any(k in (o.get("reply") or "")
                                       for k in ("DL-5487", "DL-5876", "DL-5122"))),
    ("EPICQ15", "Epic 후보 질문은 choice(+없음)", [
        "카탈로그 품질 룰 자동화 작업을 시작하려고 해"],
     "plan_work", lambda o, _: (not any(q.get("field") == "epic" and q.get("kind") != "choice"
                                        for q in (o.get("questions") or [])))),
    ("EDGE13", "변칙 — 모호+오타+복합", [
        "카탈로그쪽 메타 등록 안된 태이블들 정리하는 일 누가 하면 좋을지랑 지금 상황 알려줘"],
     None, lambda o, _: len(o.get("reply") or "") > 100 and "skcc." in (o.get("reply") or "")),

    # ── 지식 추론(DATA*) — 답이 한 티켓에 없다. 여러 티켓·코멘트·changelog·문서를 이어야 나온다.
    #    데이터셋 질의(DATA1~3,5,6)는 **값 자체를 단언**한다. 틀린 값을 그럴듯하게 말하는 것이
    #    이 유형의 실패라서, 문장 품질(judge)만으로는 통과시키면 안 된다.
    ("DATA1", "적재주기 — 변경 티켓 changelog + 코멘트 + 문서를 이어야 나오는 현재 값", [
        "fdc.fdc_trace_summary_ic 데이터의 현재 적재주기는?"],
     "ask", lambda o, _: ("30분" in (o.get("reply") or "")
                          and "DL-9044" in (o.get("reply") or "")
                          # 변경 전 값(2시간)을 '현재'로 말하면 실패 — 언급하려면 과거로만
                          and not re.search(r"현재[^.\n]{0,20}2시간", o.get("reply") or ""))),

    ("DATA2", "스키마 + 변경 히스토리 — 컬럼은 문서 본문(발췌 밖)에만 전부 있다", [
        "fdc.fdc_trace_summary_ic 스키마 정보랑 지금까지 변경 히스토리 알려줘"],
     "ask", lambda o, _: (_has(o, "CHAMBER_ID", "DL-9045")
                          and (o.get("reply") or "").count("DL-") >= 2)),

    ("DATA3", "적재 job 이름 + 작업자 — 옛 job 이름을 현재로 말하면 실패", [
        "fdc.fdc_trace_summary_ic 를 적재하는 job 이름이랑 작업자가 누구야?"],
     "ask", lambda o, _: ("etl_fdc_trace_summary_ic_30m" in (o.get("reply") or "")
                          and "skcc.x1042" in (o.get("reply") or ""))),

    ("DATA4", "부분 지식 — 있는 것은 말하고 없는 것은 없다고", [
        "eqp.eqp_sensor_raw_1s 적재주기랑 스키마 컬럼 알려줘"],
     "ask", lambda o, _: (any(w in (o.get("reply") or "") for w in ("실시간", "스트리밍"))
                          and any(w in (o.get("reply") or "")
                                  for w in ("확인된 기록", "기록 없음", "확인되지 않", "찾지 못")))),

    ("DATA5", "미지 대상 — 정직한 '없음' + 다른 테이블 사실 전이 금지(이 유형의 전형적 실패)", [
        "mes.mes_wip_move_hist 적재주기랑 담당자 알려줘"],
     "ask", lambda o, _: (any(w in (o.get("reply") or "")
                              for w in ("확인된 기록", "기록 없음", "확인되지 않", "찾지 못",
                                        "확인된 바", "확인할 수 없", "없습니다"))
                          and not any(k in (o.get("reply") or "")
                                      for k in ("DL-9042", "DL-9044", "30분", "2시간"))
                          # 코멘트 작성자를 대상의 담당자로 둔갑시키면 실패 — 실측 오답
                          and not re.search(r"담당자[^.\n]{0,12}\*{0,2}skcc\.", o.get("reply") or ""))),

    ("DATA6", "교차 비교 — 두 테이블의 사실이 서로 다른 티켓 코멘트에 있다(멀티턴)", [
        "yms.yms_lot_yield_daily 랑 fdc.fdc_trace_summary_ic 는 뭐가 달라?",
        "그럼 두 테이블 적재주기를 각각 알려줘"],
     None, lambda o, _: _has(o, "4시간", "30분")),

    ("DATA7", "테이블이 아닌 주제 — 특정 기술의 사내 현황·현재 정책", [
        "Schema Registry 우리 어떻게 쓰고 있고 호환성 정책은 지금 뭐야?"],
     "ask", lambda o, _: ("FULL" in (o.get("reply") or "")
                          and "DL-9071" in (o.get("reply") or ""))),
    ("DATA8", "담당 이관 — 최초 구축 담당을 현재 담당으로 답하면 실패", [
        "wip.wip_lot_track_hist 지금 담당 누구야?"],
     "ask", lambda o, _: ("skcc.i2011" in (o.get("reply") or "")
                          # 옛 담당을 '현재'로 말하면 실패. 이력으로 언급하는 것은 허용
                          and not re.search(r"(현재|지금)[^.\n]{0,20}skcc\.x1103",
                                            o.get("reply") or ""))),
    ("PROG1", "티켓 진척 — 상태 한 단어가 아니라 네 갈래 근거를 이어야 한다", [
        "DL-9090 지금 어디까지 진행됐어?"],
     None, lambda o, _: (lambda r: (
         "2/3" in r or ("DL-9093" in r and "DL-9095" in r))      # 하위 완료 파악
         and "DL-9092" in r                                       # 막던 티켓 해소
         and any(w in r for w in ("성능 측정", "가이드"))          # 결과 문서의 '남은 일'
     )(o.get("reply") or "")),
    ("PROG2", "진척 후속 — 남은 일·리스크를 마감 대비로(멀티턴)", [
        "DL-9090 진척 어때?", "마감까지 위험한 건 뭐야?"],
     None, lambda o, _: (lambda r: ("DL-9090" in r or "DL-9095" in r)   # 대상을 놓치면 실패
                         and ("2026-08-15" in r or "마감" in r)
                         # 무관한 프로젝트 전체 티켓을 끌어오면 실패(실측 결함)
                         and not any(k in r for k in ("DL-9008", "DL-9028", "DL-9029")))
     (o.get("reply") or "")),
    ("DATA9", "티켓 0건 — 문서에만 사는 대상. '기록 없음'으로 끝내면 실패", [
        "qms.qms_defect_code_mst 적재주기랑 스키마 알려줘"],
     "ask", lambda o, _: ("주 1회" in (o.get("reply") or "")
                          and "DEFECT_CD" in (o.get("reply") or ""))),

    # ── 조기 확인(escalation) — 추측으로 긴 답을 던지는 대신 빨리 묻는다(실측 사고 재현)
    ("DATA10", "오탈자 식별자 — '기록 없음'도 추정 답도 아니고 **후보 객관식 확인**이 정답", [
        "fdc_flat_summary_ic 데이터의 히스토리"],
     None, lambda o, _: (any(q.get("kind") == "choice"
                             and any("fdc_trace_summary_ic" in str(op)
                                     for op in (q.get("options") or []))
                             for q in (o.get("questions") or []))
                         # 추정 대상의 실데이터를 확인 전에 쏟으면 실패
                         and "DL-9044" not in (o.get("reply") or ""))),
    ("DATA11", "오탈자 확인 후속 — 고르면 정확 표기로 **연표**가 나온다(현재 값만이면 실패)", [
        "fdc_flat_summary_ic 데이터의 히스토리",
        "fdc.fdc_trace_summary_ic 말한거야"],
     None, lambda o, _: (_has(o, "30분", "DL-9044")
                         and _ux_ok(o.get("reply") or "")
                         and _history_ok(o.get("reply") or ""))),
    ("DATA12", "히스토리 단일턴 — 요청·구축·장애·변경·진행중을 **처음부터 지금까지**", [
        "fdc trace summary ic 데이터의 히스토리"],
     None, lambda o, _: _history_ok(o.get("reply") or "")),
    ("DATA13", "확인 턴을 지나도 원 요청이 답의 성격을 정한다 — 보기 하나만 고른 턴", [
        "fdc flat trace ic 데이터 히스토리 정리",
        "fdc.fdc_trace_summary_ic"],
     None, lambda o, outs: (bool(outs[0].get("questions"))
                            and _history_ok(o.get("reply") or ""))),
]

# 이 대상의 사내 이력 전부 — 재료(topic_dossier)에는 늘 8건이 실린다.
_FDC_TICKETS = ("DL-9041", "DL-9042", "DL-9043", "DL-9044",
                "DL-9045", "DL-9046", "DL-9047", "DL-9062")


def _history_ok(reply: str, keys=_FDC_TICKETS, need: int = 6) -> bool:
    """히스토리 질문의 최소선 — **연표가 나왔는가.**

    실측 사고: 재료에는 관련 티켓 8건이 전부(문서 2건도 URL 까지) 실려 있는데 답은 3건만
    썼다. 인용된 3건은 '변경 이력'·'코멘트 근거'에 사실 한 줄이 붙은 것과 정확히 일치했다 —
    나머지는 제목뿐이라 모델이 할 말이 없어 버렸다. 그래서 탄생(VoC 요청 → Job 개발)도,
    주기 단축의 계기가 된 지연 장애도, 지금 진행 중인 안정화도 빠졌다.

    ★ 이 체커를 왜 늘렸나: DATA11 이 **이미 이 흐름을 돌고 있었는데** 체커가 '30분·DL-9044'
    만 봐서 2/8 짜리 답을 green 으로 넘겼다. 배터리가 통과한다고 품질이 보장되지 않는다는
    이 저장소의 규율이 정확히 여기서 증명된다 — 체커는 **답이 아니라 기대**를 적어야 한다.

    셋을 본다: ①이력 대부분을 인용했는가 ②진행 중인 일을 말했는가(안 하면 '지금'이 없다)
    ③확인 불가한 출처가 없는가(참조에 키도 링크도 없는 줄).
    """
    from app.agent.workflow.grounding import _unlinked_refs
    cited = sum(1 for k in keys if k in reply)
    ongoing = any(k in reply for k in ("DL-9047", "DL-9062"))
    return cited >= need and ongoing and not _unlinked_refs(reply)


def _ux_ok(reply: str) -> bool:
    """가시성 결정적 체커 — judge(주관) 이전의 **최소선**.

    ① '확인된 기록 없음' 나열 금지(3회 이상이면 벽이다 — 실측 6회)
    ② 근거 마커 [N] 를 3개 이상 쓰면 반드시 **참조** 섹션이 있어야 한다
    ③ 참조 줄에 티켓 키도 링크도 없으면 검증 불가한 출처다(실측: 링크 없는 문서 제목)
    ④ 같은 문장을 두 번 쓰지 않는다(실측 재발 — 프롬프트로 막을 종류가 아니다)
    ⑤ 티켓을 5건 이상 나열하면 표로 준다 — 불릿 벽은 읽히지 않는다(responder.md 규칙)
    """
    from app.agent.workflow.grounding import _unlinked_refs
    if reply.count("확인된 기록 없음") >= 3:
        return False
    markers = len(set(re.findall(r"\[(\d{1,2})\](?!\()", reply)))
    if markers >= 3 and "참조" not in reply:
        return False
    if _unlinked_refs(reply):
        return False
    sents = [s.strip() for s in re.split(r"[.\n]", reply) if len(s.strip()) >= 25]
    if len(sents) != len(set(sents)):
        return False
    if len(set(re.findall(r"\bDL-\d+", reply))) >= 5 and reply.count("|") < 6:
        return False
    return True

# 품질 하한 — 체커만 통과하고 답이 형편없으면 그건 통과가 아니다.
# 여태 passed 는 결정적 체커만 봤고 judge 점수는 **표시만** 했다. 그래서 2점짜리 답이
# green 으로 집계됐다(실측 DATA11). 환경변수로 낮출 수는 있게 두되 기본은 조인다.
QUALITY_MIN = float(os.environ.get("LAKE_SCENARIO_QUALITY_MIN", "3.5"))
JUDGE_AXES = ("visibility", "clarity", "completeness", "grounding", "interaction", "relevance")

JUDGE_SYS = (
    "너는 PMO 어시스턴트 답변의 채점자다. 아래 6축을 1~5로 채점하라. "
    "5=흠잡을 데 없음, 3=쓸 만하나 아쉬움, 1=실패. JSON 만 출력: "
    '{"visibility":n,"clarity":n,"completeness":n,"grounding":n,"interaction":n,'
    '"relevance":n,"worst":"가장 아쉬운 점 한 문장"} '
    "★ 채점 기준은 '이 케이스가 보려는 것'이다 — 그것을 못 했으면 문장이 아무리 매끄러워도 "
    "completeness 는 2점 이하다. 그리고 답의 성격은 **원래 요청**이 정한다: 히스토리를 "
    "물었는데 현재 값만 답했으면, 그 값이 맞더라도 다른 질문에 답한 것이다.\n"
    "— visibility(구조가 사람을 위해 있나: 값 질문엔 표, **이력 질문엔 시간순 타임라인**, "
    "근거 3개↑면 본문엔 [N] 마커만 두고 끝에 참조 목록. 본문 문장마다 제목·작성자·날짜를 "
    "끼워 넣어 벽을 만들었으면 2점 이하. 티켓 5건 이상을 불릿으로 늘어놓았으면 3점 이하)\n"
    "— clarity(한 번 읽고 이해되나 — 결론이 첫 1~2문장에 있나. 군말·되풀이는 감점)\n"
    "— completeness(**케이스가 보려는 것과 원래 요청이 요구한 것을 다 담았나.** 자료에 목록이 "
    "있는데 요약으로 뭉갰으면 감점. 이력 질문에서 시작(요청·구축)이나 **현재 진행 중인 일**이 "
    "빠졌으면 3점 이하 — '왜 이렇게 됐나'와 '지금 어디까지 왔나'가 이력의 알맹이다)\n"
    "— grounding(주장마다 근거 번호·티켓 키·수치가 있나. **참조 줄에 티켓 키도 링크도 없으면 "
    "검증 불가한 출처다 — 그런 줄이 하나라도 있으면 2점 이하**. 티켓 키는 제목과 짝지어 쓰나)\n"
    "— interaction(모호한 요청에 추측으로 답하는 대신 확인 질문·후보 선택지를 냈나 — "
    "확실한데도 되물었으면 그것도 감점)\n"
    "— relevance(질문의 **구체적 개념**과 관련된 것만 실었나. 모듈이 같다거나 팀이 같다는 "
    "이유로 끌어온 티켓·문서는 노이즈다 — 하나라도 있으면 3점 이하. 관련 이력이 없으면 "
    "'없음'이 정답이고, 그렇게 답했다면 감점하지 마라)")


def judge(question, reply, original="", expect=""):
    """original = **첫 턴의 원 요청**, expect = 이 케이스가 보려는 것.

    둘 다 없으면 judge 는 마지막 턴만 보고 채점한다 — 그건 draft judge 에서 이미 데인
    맹점이다(확인 질문에 답한 턴은 마지막 발화가 '보기 하나'라, 그것만 주면 무엇을 묻는
    대화인지 알 수 없어 현재 값만 답한 것을 만점으로 준다). 여기도 같은 구멍이 있었다.
    """
    from app.agent import config as C
    try:
        out = C.get_llm(temperature=0, tier="simple").invoke([
            ("system", JUDGE_SYS),
            ("user", f"### 이 케이스가 보려는 것\n{expect or '(명시 없음)'}\n\n"
                     f"### 원래 요청 (첫 턴 — 답의 성격은 이것이 정한다)\n{original or question}\n\n"
                     f"### 이번 턴 질문\n{question}\n\n### 답변\n{reply[:4000]}")])
        txt = str(getattr(out, "content", "") or "")
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception as e:
        return {"error": str(e)[:120]}


rows, total_cost = [], 0.0
for cid, desc, turns, want_intent, check in CASES:
    if ONLY and cid not in ONLY:
        continue
    t0, outs, tid = time.time(), [], ""
    try:
        for q in turns:
            out = session.ask(q, thread_id=tid)
            tid = out["thread_id"]
            outs.append(out)
            total_cost += (out.get("usage") or {}).get("costUsd", 0) or 0
        last = outs[-1]
        ok_intent = (want_intent is None) or (last.get("intent") == want_intent)
        ok_check = bool(check(last, outs))
        # ★ judge 는 **실패한 케이스에도** 돌린다. 예전엔 통과했을 때만 채점해서, 정작
        #   진단이 가장 필요한 실패 케이스에 품질 정보가 없었다(그리고 평균은 통과분만
        #   집계돼 실패가 많을수록 평균이 좋아 보였다).
        j = judge(turns[-1], last.get("reply") or "", original=turns[0], expect=desc)
        # 6축 전부를 평균에 넣는다 — interaction 은 채점만 하고 **버리고 있었다**.
        score = round(sum(j.get(k, 0) for k in JUDGE_AXES) / len(JUDGE_AXES), 2) \
            if j and "error" not in j else 0
        # ★ 품질 하한 — 체커만 통과하고 답이 형편없으면 통과가 아니다.
        ok_quality = score >= QUALITY_MIN
        passed = ok_intent and ok_check and ok_quality
        mark = "✓" if passed else "✗"
        print(f"{mark} {cid} {desc}: intent={last.get('intent')}"
              f"{'' if ok_intent else f'(기대 {want_intent})'} 체커={'ok' if ok_check else 'FAIL'}"
              f" 품질={score}{'' if ok_quality else f'(하한 {QUALITY_MIN})'} {time.time()-t0:.0f}s")
        if not passed:
            print(f"   reply: {(last.get('reply') or '')[:200]}")
            if j and "error" not in j:
                print("   축별: " + " · ".join(f"{k}={j.get(k)}" for k in JUDGE_AXES))
        if j.get("worst"):
            print(f"   judge: {j['worst'][:110]}")
        rows.append({"id": cid, "desc": desc, "turns": turns, "passed": passed,
                     "intent": last.get("intent"), "score": score, "judge": j,
                     "ok_check": ok_check, "ok_quality": ok_quality,
                     "reply": (last.get("reply") or "")[:1500]})
    except Exception as e:
        print(f"✗ {cid}: 예외 {str(e)[:150]}")
        rows.append({"id": cid, "desc": desc, "passed": False, "error": str(e)[:300]})

n_ok = sum(1 for r in rows if r.get("passed"))
# 평균은 **전 케이스**로 낸다 — 통과분만 평균 내면 실패가 많을수록 평균이 좋아 보인다.
scored = [r for r in rows if r.get("score")]
avg = round(sum(r["score"] for r in scored) / max(1, len(scored)), 2)
n_qfail = sum(1 for r in rows if r.get("ok_check") and not r.get("ok_quality"))
print(f"\n{n_ok}/{len(rows)} 통과 · 품질 평균 {avg}/5 (전 케이스, 하한 {QUALITY_MIN})"
      f" · 체커는 통과했으나 품질 미달 {n_qfail}건 · 총비용 ${round(total_cost, 3)}")

if REPORT:
    lines = [f"# 에이전트 복합 시나리오 리포트 ({MODEL})", "",
             f"통과 {n_ok}/{len(rows)} · 품질 평균 {avg}/5 · 비용 ${round(total_cost, 3)}", "",
             "| ID | 시나리오 | 판정 | 품질 | 아쉬운 점 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['desc']} | {'통과' if r.get('passed') else '실패'} "
                     f"| {r.get('score', '-')} | {(r.get('judge') or {}).get('worst', '')[:80]} |")
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "docs", "agent-scenarios-report.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("리포트:", p)
