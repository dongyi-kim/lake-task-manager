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
# 사람이 없는 실행이다 — 설정 화면의 확인 게이트를 면제한다(config._env_supplied).
os.environ["LAKE_AGENT_SKIP_VERIFY"] = "1"
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
MODEL = _args[0] if _args and not _args[0].isupper() else "gpt-4o-mini"
ONLY = set(a for a in _args if a.isupper())
REPORT = "--report" in sys.argv
# --dump: **사람(또는 Claude)이 직접 채점하기 위한** 전문 덤프. 요약 표가 아니라 대화 전문 +
# 기대 계약을 나란히 적는다. LLM judge 는 보조 신호일 뿐 통과 권한이 없다는 것이 이 배터리의
# 입장이고(실측: 다른 질문에 답한 것에 6축 만점), 그러면 **읽을 수 있는 산출물**이 있어야 한다.
DUMP = "--dump" in sys.argv
os.environ["LAKE_AGENT_OPENAI_CHAT"] = MODEL

from app.agent.workflow import session  # noqa: E402


def _pending_items(o):
    return ((o.get("pending") or {}).get("items")) or []


def _has(o, *words):
    r = o.get("reply") or ""
    return all(w in r for w in words)


def _duedate(key: str) -> str:
    """그 티켓의 **지금** 마감일. 절대 날짜를 배터리에 박으면 안 된다.

    mock world 는 `today` 기준 결정적 생성이라 **날이 바뀌면 전 데이터가 하루 밀린다.**
    실측: 배터리에 박아 둔 "2026-08-15" 가 이틀 만에 2026-08-17 이 됐다 — 체커에 `or "마감"`
    폴백이 있어 **조용히 통과하고 있었다**(기대는 이미 틀렸는데 판정은 green).
    날짜가 필요하면 그때 세계에 물어본다.
    """
    try:
        from app.agent.tools._ctx import client
        return str(((client().get_issue(key) or {}).get("fields") or {}).get("duedate") or "")
    except Exception:
        return "\0"        # 조회 실패 시 어떤 답에도 안 걸리는 값(폴백이 판정을 대신하게)


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
    # ★ 부모는 **Task** 여야 한다 — 예전엔 DL-101(Epic)을 부모로 썼는데, Sub-Task 는 Epic
    #   밑에 못 붙는다(knowledge/01: Epic → Story/Task → Sub-Task). 에이전트가 "먼저 Task 를
    #   만들어야 한다"고 지적한 것이 **옳았고 케이스가 틀렸다**. 규칙이 금지한 것을 요구하는
    #   케이스는 무엇을 재는지 알 수 없다 — 이 케이스가 보는 것은 **지정 담당 보존**이다.
    ("BULK3", "벌크 Sub-Task 개별 속성", [
        "DL-9090 밑에 서브태스크 3개 만들어줘: 설계는 x1103, 구현은 x1042, 검증은 i2011 담당으로. 알아서"],
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
    # 체커가 "changes 가 비어 있지 않다"만 봐서, **컴포넌트 변경이 조용히 사라져도**
    # 라벨 하나로 통과했다(실측: change 스키마에 components 가 아예 없었다). 둘 다 본다.
    ("MOD8", "라벨·컴포넌트 수정", [
        "DL-101에 라벨 data-quality 추가하고 컴포넌트를 Catalog로 바꿔줘"],
     "modify", lambda o, _: (lambda ch: bool(ch)
                             and any("data-quality" in str(v) for v in ch.values())
                             and any("Catalog" in str(v) for v in ch.values()))
     ((o.get("pending") or {}).get("changes") or {})),
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
                         and (_duedate("DL-9090") in r or "마감" in r)
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
     # 이 케이스가 보는 것은 **깊이 유지**다(연표가 나오는가) — 망라는 DATA11·12 가 본다.
     # 체커를 계약보다 세게 잡으면 통과/실패가 케이스의 뜻과 무관해진다.
     None, lambda o, outs: (bool(outs[0].get("questions"))
                            and _history_ok(o.get("reply") or "", need=4))),

    # ── 사람 조사 (실사용 사고 재현) ─────────────────────────────────
    # 사고: "지금 이다은이 담당한 테스크들" 에 ①"최근 3일 활동 기록이 없습니다"
    # ②"그 모듈 로스터에 없습니다" 로 답했다. 둘 다 틀렸다 — 그 사람은 ETL 모듈이고
    # 미완료 티켓을 21건 들고 있었다. **일하고 있는 사람을 놀고 있다고 말한 것**이다.
    ("WHO1", "사람 담당 업무 — 모듈로 좁히지 말고 프로젝트 전체에서", [
        "지금 이다은이 담당한 테스크들"],
     None, lambda o, _: (lambda r: (
         # ① 실제 담당 티켓 키가 답에 있다(없다고 하면 즉시 실패)
         len(set(re.findall(r"DL-\d+", r))) >= 3
         # ② '없다/기록 없음' 으로 끝내지 않았다
         and not any(w in r for w in ("활동 기록이 없", "담당한 테스크는 없", "확인된 기록이 없"))
         # ③ 로스터 없음을 이유로 대지 않았다
         and "로스터" not in r
     ))(o.get("reply") or "")),

    ("WHO2", "호칭이 붙은 이름 — 직함째로 못 찾았다고 하면 실패", [
        "이다은 책임이 지금 맡고 있는 일 알려줘"],
     None, lambda o, _: (lambda r: (
         len(set(re.findall(r"DL-\d+", r))) >= 3
         and not any(w in r for w in ("찾지 못", "없습니다만", "존재하지 않"))
     ))(o.get("reply") or "")),

    ("WHO3", "우리 Jira 에 없는 사람 — 얼버무리지 말고 없다고", [
        "존재하지않는사람 담당 업무 알려줘"],
     None, lambda o, _: (lambda r: (
         # 없다고 분명히 말한다
         any(w in r for w in ("없", "확인되지 않", "찾지 못"))
         # ★ 다른 사람으로 바꿔 답하지 않았다 — 실재 사번·키를 끌어오면 실패
         and not re.search(r"skcc\.[a-z]\d+", r)
         and len(set(re.findall(r"DL-\d+", r))) == 0
     ))(o.get("reply") or "")),

    # ── 조건 일괄 코멘트 (사용자 요청) ─────────────────────────────────
    # "ETL 모듈 3개월 이상 업데이트 없는 티켓에 담당자를 멘션해서 상태 점검 요청" —
    # 대상 집합·티켓별 문구·범위(하위 유형 포함 여부)가 **전부 확인 대상**이다.
    # 이 유형의 실패는 조용하다: 대상이 안 잡히면 모델이 아무 티켓이나 고르고, 멘션이
    # 티켓별로 안 갈리면 남의 담당에게 알림이 간다.
    ("CMTB1", "조건 일괄 코멘트 — 대상·티켓별 문구·범위를 모두 확인받는다", [
        "ETL 모듈에서 3개월 이상 업데이트 없는 티켓 전부에 담당자를 멘션해서 "
        "상태 점검을 요청하는 코멘트를 남겨줘"],
     None, lambda o, _: (lambda p: (
         # ① 대상이 **여러 건으로 확정**됐다(단건 카드나 빈손이면 실패)
         len(p.get("keys") or []) >= 2
         # ② 티켓별 코멘트 미리보기가 있다 — 승인자가 무엇이 어디에 달리는지 봐야 한다
         and len(p.get("comments") or []) == len(p.get("keys") or [])
         # ③ ★ 멘션이 **티켓마다 다르다**(담당이 다르므로) — 한 문장을 N건에 붙이면 실패
         and len({c.get("assignee") for c in (p.get("comments") or []) if c.get("assignee")}) >= 2
         # ④ 담당이 없는 티켓에는 깨진 멘션을 남기지 않는다
         and not any("[~]" in (c.get("body") or "") for c in (p.get("comments") or []))
     ))(o.get("pending") or {})),

    # ── 추천 칩 5갈래 — 첫 화면의 유일한 행동 유도라 사용 빈도가 압도적이다.
    #    다섯 전부 **의도만 말하고 구체는 비어 있다** — 에이전트가 되물어 채워야 한다.
    #    "모르겠습니다"로 끝나거나 엉뚝한 걸 지어내면 첫 인상이 그것으로 굳는다.
    ("CHIP1", "추천 칩 — 테스크 생성(의도만 말했다)", [
        "업무 테스크를 생성하고 싶어"],
     "plan_work", lambda o, _: (bool(o.get("questions"))
                                # 무엇을 만드는지도 모르면서 초안을 지어내면 실패
                                and not _pending_items(o))),

    ("CHIP2", "추천 칩 — 버그 제보", [
        "버그를 제보하고 싶어"],
     # Bug is a Task-tier issue_type under plan_work, not a separate routing intent.
     "plan_work", lambda o, _: (lambda r: (
         bool(o.get("questions"))
         # 버그라는 것을 알아듣고 **재현·증상·범위** 같은 버그의 재료를 묻는다
         and any(w in (r + json.dumps(o.get("questions") or [], ensure_ascii=False))
                 for w in ("재현", "증상", "언제", "어디서", "로그", "영향"))
     ))(o.get("reply") or "")),

    ("CHIP3", "추천 칩 — 내 일 추천(즉답이어야 한다)", [
        "지금 무슨 업무를 시작해야 할까"],
     "my_day", lambda o, _: _has(o, "DL-")),

    ("CHIP4", "추천 칩 — 주제 조사(대상을 묻는다)", [
        "특정 주제를 조사하고 싶어 (히스토리, 지식 등)"],
     None, lambda o, _: (bool(o.get("questions"))
                         # 대상이 없는데 아무 티켓이나 긁어오면 실패
                         and len(set(re.findall(r"DL-\d+", o.get("reply") or ""))) <= 2)),

    ("CHIP5", "추천 칩 — 우리 모듈 최근 7일", [
        "우리 모듈의 최근 7일 업무 내역이 궁금해"],
     None, lambda o, _: (lambda r: (
         # 사람과 티켓이 함께 나오거나, 모듈을 모르면 되묻는다
         (("skcc." in r) and len(set(re.findall(r"DL-\d+", r))) >= 2)
         or bool(o.get("questions"))
     ))(o.get("reply") or "")),

    # ── 구조 합의 단계 (사용자 요청) ─────────────────────────────────
    # 복합 산출물은 **뼈대 먼저 합의하고 살은 나중**이다. 본문까지 다 써서 내밀면 구조가
    # 틀렸을 때 사용자가 고칠 것이 너무 많다 — 티켓 넷의 배경·범위·DoD 를 다 읽고 나서야
    # "2번은 1번에 합쳐야지"를 말하게 된다.
    ("STRUCT1", "복합 요청 — 본문 대신 **구조도부터**", [
        "우리 기존 etl 파이프라인에 iceberg puffin ndv 통계정보를 생성하는 기능을 추가구현하고 싶어",
        "1차 목표는 외부 feasibility test 후 일부 데이터 PoC, 검증 후 전체적용. "
        "많은 유저 클러스터가 SR Analyze 를 직접 수행해 콜드스타트 부하가 크다. "
        "완료 조건은 통계정보 생성 배치잡 개발·적용. 단계별 Sub-Task 로, Epic 은 DL-102. 알아서"],
     None, lambda o, _: (lambda r: (
         # ① 구조가 **눈에 보이는 나무**로 나왔다(관계가 보여야 고칠 수 있다)
         ("├─" in r or "└─" in r or re.search(r"\d+-\d+\.", r))
         # ② 아직 내용을 쓰지 않았다고 분명히 했다
         and any(w in r for w in ("구조", "뼈대"))
         # ③ ★ 본문을 미리 쓰지 않았다 — 이 단계에서 배경/완료 조건이 나오면 실패다
         and not any(w in r for w in ("### 배경", "**배경**", "완료 조건 (DoD)"))
     ))(o.get("reply") or "")),

    ("STRUCT2", "승인에 수정이 섞여 있으면 **승인이 아니다**", [
        "메타데이터 표준화를 하려고 해. 수집·검증·문서화 세 갈래로 나뉠 것 같아. 알아서",
        "좋아, 근데 문서화는 빼줘"],
     None, lambda o, _: (lambda r: (
         # 뺀 것이 실제로 빠졌다 — 승인으로 읽고 그대로 진행하면 실패
         "문서화" not in r
         # 그리고 아직 구조 단계에 머문다(다시 확인을 받는다)
         and ("├─" in r or "└─" in r or re.search(r"\d+\.\s", r))
     ))(o.get("reply") or "")),

    ("STRUCT3", "피드백은 **누적**된다 — 앞의 수정이 되돌아가면 실패", [
        "카탈로그 품질 점검 자동화. 룰 정의·배치 개발·리포트 세 갈래. 알아서",
        "리포트는 빼줘",
        "그리고 배치 개발을 개발과 검증 둘로 나눠줘"],
     None, lambda o, _: (lambda r: (
         "리포트" not in r                    # ★ 첫 턴의 수정이 살아 있어야 한다
         and ("검증" in r)                    # 둘째 턴의 수정도 반영
     ))(o.get("reply") or "")),

    ("WHO4", "담당 ≠ 최근 활동 — 활동 창으로 담당을 답하면 실패", [
        "이다은이 지금 들고 있는 미완료 티켓 몇 건이야?"],
     None, lambda o, _: (lambda r: (
         len(set(re.findall(r"DL-\d+", r))) >= 3 or re.search(r"\d+\s*건", r)
     ) and not any(w in r for w in ("최근 3일", "최근 7일", "활동 기록이 없"))
     )(o.get("reply") or "")),
]

# ── 케이스별 기대 계약 ────────────────────────────────────────────────────
# **이 배터리의 품질 하한은 여기 적힌 문장이다.** 결정적 체커는 낱말이 있는지만 보고,
# LLM judge 는 문장이 매끄러우면 후하다(실측: 다른 질문에 답한 것에 6축 전부 5점).
# 그래서 "무엇이 성립해야 이 답이 쓸 만한가"를 **미리 사람 말로 적어 두고** 그것과
# 대조한다 — 이 저장소의 평가 규율 ②(사전에 설계한 기대 결과와 대조)를 코드로 옮긴 것이다.
#
#   story    : 사용자가 무엇을 기대하고 이 대화를 시작했나(한 줄)
#   must     : 하나라도 빠지면 **실패**. 답에 그 사실·형태가 실제로 있어야 한다.
#   must_not : 하나라도 있으면 **실패**. 실측된 오답 패턴이다.
#
# 채점은 사람(또는 Claude)이 tools/agent_quality_gate.py 의 덤프를 읽고 한다.
# judge 는 보조 신호일 뿐 통과 권한이 없다.
EXPECT = {
    "EPIC1": {
        "story": "이니셔티브를 하나 세우려 한다 — 목표를 말했으니 Epic 한 건이 서야 한다",
        "must": ["Epic 1건만 초안에 있다(자식 Task 를 같은 배치에 섞지 않았다)",
                 "보드에 뜨는 짧은 Epic Name(10자 이내)이 붙어 있다",
                 "본문에 배경·목표·완료 기준이 있고 목표가 사용자가 말한 '등록률 100%' 를 담는다"],
        "must_not": ["Epic 과 하위 Task 를 한 승인 배치에 섞었다",
                     "이미 있는 유사 Epic 을 확인하지 않고 만들었다"],
    },
    "TECH2": {
        "story": "신규 개발 건을 착수하려 한다 — 규모 판단이 먼저다",
        "must": ["초안을 내거나, 범위·완료조건을 묻는 질문을 냈다(둘 중 하나는 해야 한다)",
                 "낸 것이 초안이면 모듈이 Observability 또는 DataOps 로 잡혀 있다(컨슈머 랙 감시)"],
        "must_not": ["방식이 안 정해졌는데 실행 단계를 미리 쪼갰다"],
    },
    "BULK3": {
        "story": "분담을 이미 정해 왔다 — 그대로 만들어 주기만 하면 된다",
        "must": ["Sub-Task 3건이 DL-9090 아래로 잡혔다",
                 "설계=x1103 · 구현=x1042 · 검증=i2011 이 **말한 그대로** 배정됐다"],
        "must_not": ["세 건을 한 사람에게 몰았다", "이미 말한 분담을 다시 물었다"],
    },
    "KNOW4": {
        "story": "개념과 우리 현황을 함께 알고 싶다",
        "must": ["개념 설명이 한두 문장으로 있다",
                 "우리 프로젝트 이력을 티켓 키와 함께 대거나, 없으면 '사내 이력 없음'이라 못 박았다"],
        "must_not": ["모듈이 같다는 이유로 무관한 티켓을 관련 이력이라 붙였다"],
    },
    "ACT5": {
        "story": "이 티켓 주변 사람들이 요즘 뭘 하는지 알고 싶다",
        "must": ["관련자를 사번으로 나열했다",
                 "사람마다 '주로 하는 일'을 문장으로 요약하고 근거 티켓을 붙였다"],
        "must_not": ["한 사람만 보고 끝냈다", "활동이 적은 것을 태만으로 단정했다"],
    },
    "GUIDE7": {
        "story": "이 도구 쓰는 법을 묻는다",
        "must": ["담당자 변경은 티켓 다이얼로그에서 값을 클릭하는 인라인 편집이라고 답했다",
                 "강제 새로고침의 위치(좌하단 ↻)를 답했다"],
        "must_not": ["가이드에 없는 화면·버튼을 지어냈다"],
    },
    "MOD8": {
        "story": "기존 티켓 두 필드를 바꿔 달라 — 승인 카드가 바로 서야 한다",
        "must": ["변경 계획에 라벨 추가와 컴포넌트 변경이 **둘 다** 있다",
                 "대상이 DL-101 이다"],
        "must_not": ["말하지 않은 필드까지 바꾸려 했다", "새 티켓을 만들려 했다"],
    },
    "REC9": {
        "story": "지금 집을 일을 고르고 싶고, 그다음 조건을 좁힌다",
        "must": ["티켓마다 **왜 목록에 있는지**(마감 D-n · n일째 정체 등)를 붙였다",
                 "후속 턴에서 '마감 안 지난 것'이라는 **사용자의 기준 그대로** 좁혔다"],
        "must_not": ["물은 기준을 다른 기준으로 바꿔치기했다", "이유 없는 키 목록만 나열했다"],
    },
    "JQL10": {
        "story": "조건 검색을 JQL 로 받아 재사용하고 싶다",
        "must": ["실행한 JQL 한 줄이 답에 그대로 실렸다",
                 "결과가 있으면 표로, 없으면 '없습니다' + 확인한 기준을 밝혔다"],
        "must_not": ["JQL 을 요구했는데 결과만 주고 쿼리를 뺐다"],
    },
    "FIT11": {
        "story": "이 사람에게 맡겨도 되는지 판단을 돕는 근거가 필요하다",
        "must": ["적합/부담 판단 문장이 있고 **숫자(진행중 건수)와 티켓 키**가 함께 있다",
                 "i2011 이 운영 인력(i 접두)이라는 점을 판단에 반영했다",
                 "부적합하면 대안 1명을 근거와 함께 냈다"],
        "must_not": ["'적합해 보입니다' 처럼 근거 없는 인상만 말했다",
                     "배정을 확정했다(결정은 사람 몫이다)"],
    },
    "CMT12": {
        "story": "댓글 하나만 남기려 한다",
        "must": ["코멘트 본문에 [~skcc.x1103] 멘션 표기가 그대로 들어갔다",
                 "코멘트만 계획했다"],
        "must_not": ["코멘트 외에 필드 변경·상태 전이를 함께 냈다",
                     "'댓글을 남길까요?' 하고 허락을 다시 물었다"],
    },
    "REL14": {
        "story": "신기술 도입 단계를 추가하려 한다 — 관련 없는 과거를 끌어오면 신뢰가 깎인다",
        "must": ["Iceberg/Puffin/NDV/통계 라는 원 요청의 고유어가 제목에 남았다",
                 "관련 이력이 없으면 '없음'이라고 말했다"],
        "must_not": ["모듈만 같은 무관한 티켓(DL-5487·DL-5876·DL-5122 류)을 근거로 붙였다"],
    },
    "EPICQ15": {
        "story": "새 일을 시작하는데 어느 Epic 에 달지는 내가 정해야 한다",
        "must": ["Epic 을 묻는다면 객관식(choice)이고 보기에 '없음(최상위)' 이 있다"],
        "must_not": ["Epic 키를 자유 입력(text)으로 물었다", "조용히 아무 Epic 에 붙였다"],
    },
    "EDGE13": {
        "story": "오타 섞인 모호한 복합 요청 — 상황 파악과 사람 추천을 함께 원한다",
        "must": ["상황 요약과 담당 후보가 **둘 다** 있다",
                 "후보마다 근거(워크로드 숫자 또는 관련 이력 키)가 붙었다"],
        "must_not": ["'기록을 찾지 못했다'로 끝내고 후보를 안 냈다"],
    },
    "DATA1": {
        "story": "이 테이블 지금 적재주기가 몇인지 하나만 알고 싶다",
        "must": ["현재 값이 30분이라고 단언했다", "근거로 DL-9044 를 댔다"],
        "must_not": ["변경 전 값(2시간)을 현재 값으로 말했다", "값 하나 물었는데 개념 강의를 붙였다"],
    },
    "DATA2": {
        "story": "스키마와 변경 내력을 함께 보고 싶다",
        "must": ["컬럼 8개를 실제로 나열했다(CHAMBER_ID 포함)",
                 "스키마 변경(DL-9045)과 주기 변경(DL-9044)을 날짜와 함께 짚었다"],
        "must_not": ["컬럼 수만 말하고 목록을 생략했다(문서 본문에 있는데 안 읽은 것이다)"],
    },
    "DATA3": {
        "story": "이 테이블을 적재하는 job 과 담당을 알고 싶다",
        "must": ["job 이름이 etl_fdc_trace_summary_ic_30m 이다", "담당이 skcc.x1042 다"],
        "must_not": ["주기가 바뀌기 전 job 이름(…_2h 류)을 현재로 말했다",
                     "코멘트 작성자를 담당자로 둔갑시켰다"],
    },
    "DATA4": {
        "story": "아는 것과 모르는 것을 구분해 듣고 싶다",
        "must": ["적재 방식이 실시간/스트리밍임을 답했다",
                 "스키마 컬럼은 **확인된 기록이 없다**고 명시했다"],
        "must_not": ["없는 컬럼 목록을 지어냈다"],
    },
    "DATA5": {
        "story": "기록에 없는 대상을 물었다 — 정직한 '없음'이 정답이다",
        "must": ["확인된 기록이 없다고 답했다",
                 "근거가 있다면 DL-9051 코멘트('우리 적재 대상이 아니다')를 댔다"],
        "must_not": ["다른 테이블(fdc)의 주기·담당을 끌어다 붙였다",
                     "코멘트 작성자를 이 대상의 담당자라고 했다"],
    },
    "DATA6": {
        "story": "두 테이블을 비교하고, 이어서 각각의 주기를 확인한다",
        "must": ["yms=4시간 · fdc=30분 을 각각 짚었다", "차이(시간축 불일치)를 한 문장으로 말했다"],
        "must_not": ["두 테이블의 값을 뒤바꿔 말했다"],
    },
    "DATA7": {
        "story": "이 기술을 우리가 어떻게 쓰고 정책이 뭔지 알고 싶다",
        "must": ["호환성 정책이 현재 FULL 이라고 답하고 DL-9071 을 댔다"],
        "must_not": ["초기 값(BACKWARD)을 현재 정책으로 말했다"],
    },
    "DATA8": {
        "story": "지금 담당이 누구인지가 알고 싶다 — 이관이 있었다",
        "must": ["현재 담당이 skcc.i2011 이라고 답했다"],
        "must_not": ["이관 전 담당(skcc.x1103)을 현재 담당으로 말했다"],
    },
    "PROG1": {
        "story": "이 티켓이 어디까지 왔는지, 남은 게 뭔지 알고 싶다",
        "must": ["하위 3건 중 2건 완료·1건 진행중을 짚었다",
                 "막고 있던 DL-9092 가 해소됐다는 사실을 코멘트 근거로 댔다",
                 "남은 일(성능 측정·가이드)을 말했다"],
        "must_not": ["상태 한 단어('진행중')로 끝냈다", "진척 숫자를 스스로 지어냈다"],
    },
    "PROG2": {
        "story": "마감 대비 위험을 알고 싶다",
        "must": ["그 티켓의 **실제 마감일**(세계에서 조회한 값) 대비로 남은 일을 말했다",
                 "리스크를 근거와 함께 짚었다"],
        "must_not": ["무관한 다른 티켓(DL-9008·9028·9029 류)을 리스크로 끌어왔다"],
    },
    "DATA9": {
        "story": "티켓이 하나도 없는 대상 — 문서에만 산다",
        "must": ["주기가 주 1회임을 답했다", "컬럼 DEFECT_CD 를 댔다", "출처 문서를 링크와 함께 댔다"],
        "must_not": ["'기록 없음'으로 끝냈다(문서를 안 읽은 것이다)"],
    },
    "DATA10": {
        "story": "표기를 틀리게 물었다 — 추측하지 말고 확인받아야 한다",
        "must": ["유사 표기 후보를 객관식으로 물었다", "보기에 '이 중에 없음' 탈출구가 있다"],
        "must_not": ["확인 전에 추정 대상의 실데이터(30분·DL-9044)를 쏟았다",
                     "'기록 없음'으로 끝냈다"],
    },
    "DATA11": {
        "story": "표기를 고른 뒤 — **처음에 물은 것은 히스토리다**",
        "must": ["관련 티켓 8건 중 6건 이상을 인용했다",
                 "시간순으로 전개했다(요청 → 구축 → 장애 → 변경 → 현재)",
                 "진행 중인 일(DL-9047 또는 DL-9062)을 현재 상태로 말했다"],
        "must_not": ["현재 값 표만 주고 이력을 생략했다",
                     "참조 줄에 티켓 키도 링크도 없는 출처가 있다"],
    },
    "DATA12": {
        "story": "이 데이터가 어떻게 여기까지 왔는지 통째로 알고 싶다",
        "must": ["탄생(VoC 요청 DL-9041 → 구축 DL-9042)을 짚었다",
                 "주기 단축의 계기가 된 지연 장애(DL-9043)를 짚었다",
                 "진행 중인 일을 현재 상태로 말했다"],
        "must_not": ["변경 2건(주기·스키마)만 말하고 끝냈다"],
    },
    "DATA13": {
        "story": "오타로 물었고, 확인 보기를 골랐다 — 원래 물은 것은 히스토리다",
        "must": ["첫 턴에서 확인 질문을 냈다",
                 "고른 뒤 답이 **연표**다(현재 값 요약이 아니다)"],
        "must_not": ["확인 턴을 지나며 원 요청(히스토리)이 값 조회로 축소됐다"],
    },
}

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
    ⑤ 티켓을 5건 이상 나열하면 표로 준다 — 불릿 벽은 읽히지 않는다(result_integrator.md 규칙)
    """
    from app.agent.workflow.grounding import _unlinked_refs
    if reply.count("확인된 기록 없음") >= 3:
        return False
    markers = len(set(re.findall(r"\[(\d{1,2})\](?!\()", reply)))
    if markers >= 3 and "참조" not in reply:
        return False
    if _unlinked_refs(reply):
        return False
    # ★ 마침표만 보고 자르면 **점 찍힌 식별자가 조각난다** — `yms.yms_lot_yield_daily 와
    #   fdc.fdc_trace_summary_ic` 가 "yms_lot_yield_daily 와 fdc" 같은 파편이 되고, 그 파편이
    #   답 안에서 두 번 나오면 **멀쩡한 답이 '중복 문장'으로 떨어진다**(실측 DATA11).
    #   문장 끝은 마침표 **뒤에 공백/줄바꿈**이 오는 자리다. 우리 도메인은 테이블 이름이
    #   본문에 늘 나오므로 이 구분이 필수다.
    sents = [s.strip() for s in re.split(r"(?<=[.?!])\s+|\n", reply) if len(s.strip()) >= 25]
    if len(sents) != len(set(sents)):
        return False
    if len(set(re.findall(r"DL-\d+", reply))) >= 5 and reply.count("|") < 6:
        return False
    return True

# 품질 하한 — 체커만 통과하고 답이 형편없으면 그건 통과가 아니다.
# 여태 passed 는 결정적 체커만 봤고 judge 점수는 **표시만** 했다. 그래서 2점짜리 답이
# green 으로 집계됐다(실측 DATA11). 환경변수로 낮출 수는 있게 두되 기본은 조인다.
QUALITY_MIN = float(os.environ.get("LAKE_SCENARIO_QUALITY_MIN", "3.5"))
JUDGE_AXES = ("visibility", "clarity", "completeness", "grounding", "interaction", "relevance")

JUDGE_SYS = (
    "너는 PMO 어시스턴트 답변의 채점자다. 아래를 채점하라. JSON 만 출력: "
    '{"answers_original":true|false,"why_not":"아니라면 무엇이 빠졌나 한 문장",'
    '"visibility":n,"clarity":n,"completeness":n,"grounding":n,"interaction":n,'
    '"relevance":n,"worst":"가장 아쉬운 점 한 문장"} (n 은 1~5, 5=흠잡을 데 없음)\n"'
    "★ **먼저 answers_original 을 판정하라.** '이 케이스가 보려는 것'과 '원래 요청'이 요구한 "
    "것에 답했는가 — 예/아니오로. 답의 성격은 원래 요청이 정한다: **히스토리를 물었는데 "
    "현재 값만 답했으면 그 값이 다 맞더라도 false 다**(다른 질문에 답한 것이다). 이력을 "
    "물었는데 시간순 전개와 '지금 어디까지 왔나'가 없으면 false. 목록을 물었는데 요약으로 "
    "뭉갰으면 false. false 면 completeness 는 반드시 2점 이하로 준다.\n"
    "이 판정을 점수보다 먼저 하는 이유: 문장이 매끄러우면 축 점수는 전부 올라가고, 정작 "
    "'다른 질문에 답했다'는 사실이 어디에도 안 남는다(실측: 현재 값만 답한 것에 6축 전부 "
    "5점을 준 적이 있다).\n"
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
    "★★ **이번 턴이 '확인 질문'이면 채점 잣대가 다르다.** 되묻는 것이 정답인 턴에서는 답이 "
    "짧고 근거가 없는 것이 **정상**이다(아직 답할 때가 아니다). 그 턴에는 "
    "grounding·completeness·visibility 를 **5점으로 두고**, interaction 과 clarity 만 "
    "실제로 채점하라 — 물어야 할 것을 정확히 물었나, 보기가 고르기 쉬운가. "
    "확인 전에 추정 데이터를 쏟았으면 그때만 grounding 을 깎는다. "
    "(실측: 되묻기가 정답인 케이스에 근거가 없다며 grounding 1점을 줘 평균 1.83 이 나왔다 — "
    "축을 잘못 댄 것이지 답이 나쁜 것이 아니었다.)\n"
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


# ★ **동적 색인을 비우고 시작한다 — 안 그러면 배터리가 재현되지 않는다.**
#   동적 색인(`CACHE_DIR/agent_index/dynamic`)은 에이전트가 본 것을 **누적**한다. 제품에서는
#   그게 기능이지만, 배터리에서는 앞 케이스가 훑은 티켓·문서가 뒤 케이스의 의미 검색에
#   섞여 들어와 **실행할 때마다 다른 케이스가 떨어진다.**
#   실측: 29케이스 한 실행을 두 번 돌렸더니 둘 다 26/29 였는데 **실패 집합이 서로 달랐고**
#   (MOD8·DATA5·PROG1 / REC9·PROG1·DATA11), 그 케이스들을 2~3건짜리 작은 배치로 돌리면
#   전부 통과했다. 케이스가 흔들린 게 아니라 **색인 상태가 실행 내내 자란 것**이다.
#   케이스마다 비우지는 않는다 — 멀티턴 케이스는 자기 턴 사이의 누적이 필요하다.
#   실행 단위로 같은 출발점을 주는 것이 목적이다.
def _reset_index():
    try:
        from app.agent.retrieval import dynamic_index
        dynamic_index.reset()
        return True
    except Exception:            # 색인이 없거나 임베딩 미설정이면 그냥 진행
        return False


print("(케이스마다 동적 색인을 비운다 — 앞 케이스의 재료가 뒤 케이스에 새지 않게)"
      if _reset_index() else "(동적 색인 초기화 생략)")

rows, total_cost = [], 0.0
for cid, desc, turns, want_intent, check in CASES:
    if ONLY and cid not in ONLY:
        continue
    # ★ **케이스마다 비운다.** 케이스 안의 여러 턴은 서로 이어져야 하지만(그건 이 안에서
    #   쌓인다), 케이스끼리는 남남이다. 실측(DATA4): 앞의 DATA1~3 이 'fdc 적재주기' 문서를
    #   잔뜩 색인해 두니, eqp 테이블을 물었을 때 **그 테이블의 사실 대신 일반 '적재주기 변경
    #   절차' 문서**가 올라와 "확인된 기록 없음"으로 답했다 — 인용한 근거가 그 증거다.
    #   실행 시작에 한 번만 비웠을 때는 이 누출이 실행 중반부터 다시 생겼다.
    _reset_index()
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
        _spec = EXPECT.get(cid) or {}
        # 기대 계약을 judge 에게도 그대로 준다 — desc 한 줄보다 must/must_not 이 훨씬 구체적이다.
        _exp_text = "\n".join(
            [_spec.get("story", desc)]
            + [f"- 반드시: {x}" for x in _spec.get("must", [])]
            + [f"- 있으면 실패: {x}" for x in _spec.get("must_not", [])]) or desc
        j = judge(turns[-1], last.get("reply") or "", original=turns[0], expect=_exp_text)
        # 6축 전부를 평균에 넣는다 — interaction 은 채점만 하고 **버리고 있었다**.
        score = round(sum(j.get(k, 0) for k in JUDGE_AXES) / len(JUDGE_AXES), 2) \
            if j and "error" not in j else 0
        # ★ judge 는 **게이트가 아니다.** 한때 품질 하한을 통과 조건에 넣었는데, 그러면
        #   "judge 는 보조 신호일 뿐 통과 권한이 없다"고 적어 놓고 정작 통과를 judge 가
        #   정하는 모순이 된다. 실측으로 judge 는 **양방향으로** 틀렸다:
        #     · 다른 질문에 답한 것에 6축 만점(DATA13)
        #     · 계약 3항목을 다 만족한 답에 answers_original=False + 틀린 worst(DATA9 —
        #       "DEFECT_CD 언급이 빠졌다"는데 표에 있었다)
        #   그래서 게이트는 **결정적 체커**가 쥔다. judge 점수와 answers_original 은
        #   "읽어 볼 곳"을 알려 주는 표식으로만 찍고, 최종 판정은 덤프를 읽고 한다.
        answered = j.get("answers_original", True) is not False
        low_quality = (score and score < QUALITY_MIN) or not answered
        passed = ok_intent and ok_check
        mark = "✓" if passed else "✗"
        print(f"{mark} {cid} {desc}: intent={last.get('intent')}"
              f"{'' if ok_intent else f'(기대 {want_intent})'} 체커={'ok' if ok_check else 'FAIL'}"
              f" 품질={score}{' ⚑읽어볼 것' if low_quality else ''} {time.time()-t0:.0f}s")
        if not passed or low_quality:
            print(f"   reply: {(last.get('reply') or '')[:200]}")
            if j and "error" not in j:
                print("   축별: " + " · ".join(f"{k}={j.get(k)}" for k in JUDGE_AXES))
        if j.get("worst"):
            print(f"   judge: {j['worst'][:110]}")
        rows.append({"id": cid, "desc": desc, "turns": turns, "passed": passed,
                     "intent": last.get("intent"), "score": score, "judge": j,
                     "ok_check": ok_check, "low_quality": bool(low_quality), "spec": _spec,
                     # 덤프는 **자르지 않는다** — 사람이 채점하려면 전문이 있어야 한다.
                     "replies": [o.get("reply") or "" for o in outs],
                     "questions": [o.get("questions") or [] for o in outs],
                     "pending": (last.get("pending") or {}),
                     "reply": (last.get("reply") or "")[:1500]})
    except Exception as e:
        print(f"✗ {cid}: 예외 {str(e)[:150]}")
        rows.append({"id": cid, "desc": desc, "passed": False, "error": str(e)[:300]})

n_ok = sum(1 for r in rows if r.get("passed"))
# 평균은 **전 케이스**로 낸다 — 통과분만 평균 내면 실패가 많을수록 평균이 좋아 보인다.
scored = [r for r in rows if r.get("score")]
avg = round(sum(r["score"] for r in scored) / max(1, len(scored)), 2)
# 체커는 통과했는데 judge 가 낮게 본 것 — **읽어 볼 곳**의 목록이지 실패가 아니다.
n_flag = sum(1 for r in rows if r.get("ok_check") and r.get("low_quality"))
print(f"\n{n_ok}/{len(rows)} 통과 · 품질 평균 {avg}/5 (전 케이스, 하한 {QUALITY_MIN})"
      f" · 읽어 볼 것 {n_flag}건 · 총비용 ${round(total_cost, 3)}")

if REPORT:
    lines = [f"# 에이전트 복합 시나리오 리포트 ({MODEL})", "",
             f"통과 {n_ok}/{len(rows)} · 품질 평균 {avg}/5 · 비용 ${round(total_cost, 3)}", "",
             "| ID | 시나리오 | 판정 | 품질 | 아쉬운 점 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['desc']} | {'통과' if r.get('passed') else '실패'} "
                     f"| {r.get('score', '-')} | {(r.get('judge') or {}).get('worst', '')[:80]} |")
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "research", "agent-improvement", "reports",
                     "agent-scenarios-report.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("리포트:", p)

if DUMP:
    # 사람이 읽고 채점하는 산출물 — 요약이 아니라 **대화 전문 + 기대 계약**을 나란히.
    # 자동 판정(체커·judge)은 참고로만 적는다. 최종 판정은 읽는 사람이 한다.
    d = [f"# 정성 판독용 전문 덤프 ({MODEL})", "",
         "각 케이스: **기대 계약**(무엇이 성립해야 쓸 만한가) → 대화 전문 → 자동 판정.",
         "자동 판정은 참고다 — judge 는 문장이 매끄러우면 후하고(실측: 다른 질문에 답한 것에",
         "6축 만점), 체커는 낱말만 본다. **판정은 읽고 하는 것이다.**", ""]
    for r in rows:
        d += [f"## {r['id']} — {r['desc']}", ""]
        sp = r.get("spec") or {}
        if sp:
            d += [f"> **사용자 스토리**: {sp.get('story', '')}", ""]
            if sp.get("must"):
                d += ["**반드시 성립 (하나라도 빠지면 실패)**"] + \
                     [f"- [ ] {x}" for x in sp["must"]] + [""]
            if sp.get("must_not"):
                d += ["**있으면 실패**"] + [f"- [ ] {x}" for x in sp["must_not"]] + [""]
        else:
            d += ["> (기대 계약 미작성 — EXPECT 에 추가할 것)", ""]
        for i, q in enumerate(r.get("turns") or []):
            d += [f"### 턴 {i + 1} · 사용자", "", "```", q, "```", "", f"### 턴 {i + 1} · 에이전트", ""]
            for qq in (r.get("questions") or [[]])[i] if i < len(r.get("questions") or []) else []:
                d += [f"- **[질문:{qq.get('kind')}]** {qq.get('question')}",
                      f"  - 보기: {' | '.join(str(x) for x in (qq.get('options') or [])) or '(자유 입력)'}"]
            d += [(r.get("replies") or [""])[i] if i < len(r.get("replies") or []) else "(없음)", ""]
        if r.get("pending", {}).get("items"):
            d += ["**승인 대기 초안**", "", "```json",
                  json.dumps(r["pending"]["items"], ensure_ascii=False, indent=1)[:4000], "```", ""]
        jj = r.get("judge") or {}
        d += [f"**자동 판정(참고)** — 체커={'ok' if r.get('ok_check') else 'FAIL'}"
              f" · judge 평균={r.get('score')}"
              f" · answers_original={jj.get('answers_original')}"
              f" · worst: {jj.get('worst', '')}", "", "---", ""]
    p2 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "research", "agent-improvement", "reports",
                      "agent-quality-run.md")
    os.makedirs(os.path.dirname(p2), exist_ok=True)
    with open(p2, "w", encoding="utf-8") as f:
        f.write("\n".join(d) + "\n")
    print("전문 덤프:", p2)
