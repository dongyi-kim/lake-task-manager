# tools/agent_create_suite.py — 티켓 **생성** 시나리오 배터리 (실 LLM, 수동 실행 전용).
#
# 실행: python -X utf8 tools/agent_create_suite.py [모델] [케이스ID ...]
#
# 생성 요청은 사용자가 말하는 방식이 제각각이다 — 한 줄로 던지기도 하고, 구조를 지정하기도
# 하고, 남이 쓴 글을 통째로 붙여넣기도 한다. 여기 모은 것은 **그 변주**다.
#
# 각 케이스는 **초안(pending/draft_items)** 을 본다. 답변 문장이 아니라 실제로 만들어질
# 것을 검사해야 한다 — 말은 그럴듯한데 초안이 비어 있는 실패가 실제로 있었다.
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
_args = [a for a in sys.argv[1:] if not a.startswith("-")]
MODEL = _args[0] if _args and not _args[0].isupper() else "gpt-4o-mini"
ONLY = {a for a in _args if a.isupper()}
os.environ["LAKE_AGENT_OPENAI_CHAT"] = MODEL
os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")

from app.agent.workflow import session  # noqa: E402


def items(o):
    """승인 대기 초안이 있으면 그것, 없으면 작성 중 초안(되묻는 턴)."""
    return ((o.get("pending") or {}).get("items")) or o.get("draft_items") or []


def kids(o):
    return ((o.get("pending") or {}).get("children")) or []


def pend(o, k, d=None):
    return (o.get("pending") or {}).get(k, d)


def _body(it):
    return str(it.get("description") or "")


def has_sections(it, *names):
    return all(n in _body(it) for n in names)


def _owners(rows):
    return [str(r.get("assignee") or "") for r in rows]


# (ID, 설명, [질의…], 체커(마지막 out, 전체 outs))
CASES = [
    # ── 한 줄 요청: 되묻지 않고 기본값으로 끝내야 하는 것들 ──────────
    ("ONE1", "한 줄 + 알아서 — 되묻지 말고 초안까지", [
        "Workbench 쿼리 편집기에 단축키 도움말 팝업 추가해줘. 알아서 초안 잡아줘"],
     lambda o, _: len(items(o)) == 1 and not o.get("questions")
     and has_sections(items(o)[0], "배경", "완료 조건")),

    ("ONE2", "작은 수정 요청 — 단일 Task 로 끝난다(과잉 분해 금지)", [
        "카탈로그 화면 상단 필터에 '내 모듈만' 체크박스 하나 추가. 알아서"],
     lambda o, _: len(items(o)) == 1 and not kids(o)
     and pend(o, "structure") in (None, "", "single_task")),

    # ── 구조를 사용자가 지정 ─────────────────────────────────────────
    ("STR1", "분량 분할을 명시 — Sub-Task 로 나누고 담당을 골고루", [
        "메타데이터 미등록 테이블 30개를 등록해야 해. 사람 나눠서 진행하게 만들어줘. 알아서"],
     lambda o, _: len(kids(o)) >= 2 and len({x for x in _owners(kids(o)) if x}) >= 2),

    ("STR2", "기능 분화 — 모듈이 다르면 Task 를 나눈다", [
        "리니지 뷰어 성능 측정하고, 결과에 따라 쿼리 엔진 쪽 인덱스도 손봐야 해. "
        "그리고 사용 가이드도 써야 하고. 초안 잡아줘. 알아서"],
     lambda o, _: len(items(o)) >= 2
     and len({(i.get("components") or [""])[0] for i in items(o)}) >= 2),

    ("STR3", "Epic 격상 요구를 보수적으로 — 근거 없으면 기존 Epic 아래로", [
        "쿼리 성능 개선을 대대적으로 해보자. 에픽으로 크게 잡아줘",
        "기간은 2주 정도고 ETL 쪽만 손볼 거야. 알아서 진행해"],
     lambda o, _: pend(o, "structure") != "new_epic"
     or "보류" in str(pend(o, "rationale") or "")),

    # ── 대상·부모를 명시 ─────────────────────────────────────────────
    ("PAR1", "기존 Task 밑에 Sub-Task 를 개별 담당으로", [
        "DL-9090 밑에 서브태스크 3개 만들어줘: 성능 측정은 x1402, 가이드 작성은 x1450, "
        "회귀 테스트는 x1042. 알아서"],
     lambda o, _: (lambda rows: len(rows) >= 3
                   and len({r.get("assignee") for r in rows}) >= 3)(items(o) + kids(o))),

    ("PAR2", "Epic 을 사용자가 지목 — 초안에 그대로 실린다", [
        "DL-101 에픽 아래에 CDC 재처리 배치 개선 Task 하나 만들어줘. 알아서"],
     lambda o, _: any((i.get("epic") or "") == "DL-101" for i in items(o))),

    # ── 남이 쓴 글을 통째로 붙여넣기 ─────────────────────────────────
    ("PASTE1", "VoC 원문 붙여넣기 — 요구를 티켓 언어로 옮긴다", [
        "아래 VoC 그대로 티켓으로 만들어줘. 알아서\n\n"
        "---\n안녕하세요 운영팀입니다. 데이터 조회할 때 컬럼 설명이 안 보여서 매번 "
        "담당자한테 물어보고 있습니다. 카탈로그에 설명이 있다는데 화면에서는 안 보이네요. "
        "조회 화면에서 바로 봤으면 좋겠습니다. 급하진 않습니다."],
     lambda o, _: len(items(o)) >= 1
     and any(w in _body(items(o)[0]) for w in ("VoC", "운영팀", "컬럼 설명"))),

    ("PASTE2", "장애 대화록 붙여넣기 → Bug 로", [
        "이거 버그로 등록해줘. 알아서\n\n"
        "[10:12] 김운영: 야간 배치 또 실패했어요\n"
        "[10:13] 이개발: 로그 보니 커넥션 타임아웃이네요. 어제도 같은 시간대\n"
        "[10:15] 김운영: 재실행하면 되긴 하는데 매일 이러면 곤란해요"],
     lambda o, _: any((i.get("type") or "") == "Bug" for i in items(o))),

    # ── 정보가 모자란 요청: 물어야 한다 ──────────────────────────────
    ("ASK1", "범위가 없으면 되묻는다(무턱대고 만들지 않는다)", [
        "데이터 품질 개선 작업 하나 만들어줘"],
     lambda o, _: bool(o.get("questions"))),

    ("ASK2", "되물은 뒤 답을 반영해 초안으로", [
        "데이터 품질 개선 작업 하나 만들어줘",
        "널 비율 체크만 이번에 하고, 나머지는 다음에. 이번 주까지. 알아서"],
     lambda o, outs: bool(outs[0].get("questions")) and len(items(o)) >= 1),

    # ── 중복·기존 것 처리 ────────────────────────────────────────────
    ("DUP1", "이미 있는 일이면 새로 만들지 말고 알린다", [
        "프로듀서를 Avro 로 전환하는 작업을 새로 만들자"],
     lambda o, _: (bool(o.get("questions"))
                   or "DL-9072" in (o.get("reply") or ""))),

    # ── 속성 지정이 섞인 요청 ────────────────────────────────────────
    ("ATTR1", "우선순위·마감·라벨을 말로 지정", [
        "적재 지연 알림 임계값 조정 Task 만들어줘. 우선순위 P1, 이번 주 금요일까지, "
        "라벨은 hotfix. 알아서"],
     lambda o, _: (lambda i: str(i.get("priority") or "").startswith("P1")
                   and bool(i.get("duedate"))
                   and "hotfix" in [str(x) for x in (i.get("labels") or [])])(items(o)[0])),

    ("ATTR2", "없는 라벨을 요구 — 막지 말고 신규로 표시", [
        "카탈로그 품질 룰 점검 Task 만들고 라벨은 quality-gate 로. 알아서"],
     lambda o, _: "quality-gate" in json.dumps(items(o), ensure_ascii=False)
     and (not pend(o, "new_labels") or "quality-gate" in pend(o, "new_labels"))),

    # ── 규칙 위반을 요구 ─────────────────────────────────────────────
    ("RULE1", "Sub-Task 를 최상위로 만들어 달라 — 규칙대로 거절하거나 부모를 묻는다", [
        "서브태스크 하나만 딱 만들어줘. 부모는 없어도 돼"],
     lambda o, _: bool(o.get("questions"))
     or all((i.get("type") or "") != "Sub-Task" for i in items(o))),

    ("RULE2", "Story Point 를 넣어 달라 — 생성 시에는 넣지 않는다", [
        "리니지 3홉 확장 Story 만들고 스토리포인트 5로 넣어줘. 알아서"],
     lambda o, _: "storyPoint" not in json.dumps(items(o))
     and "sp" not in {k.lower() for i in items(o) for k in i}),
]


def run(cid, desc, turns, check):
    t0, tid, outs = time.time(), "", []
    try:
        for q in turns:
            o = session.ask(q, thread_id=tid)
            tid = o["thread_id"]
            outs.append(o)
        last = outs[-1]
        ok = bool(check(last, outs))
    except Exception as e:
        print(f"✗ {cid} {desc}: 예외 {str(e)[:160]}")
        return False, 0
    n = len(items(last))
    print(f"{'✓' if ok else '✗'} {cid} {desc}: 초안 {n}건"
          f"{' + 자식 ' + str(len(kids(last))) if kids(last) else ''}"
          f" · 질문 {len(last.get('questions') or [])}"
          f" · 구조 {pend(last, 'structure') or '-'} · {time.time() - t0:.0f}s")
    if not ok:
        print(f"    reply: {(last.get('reply') or '')[:200]}")
        print(f"    items: {json.dumps(items(last), ensure_ascii=False)[:300]}")
    return ok, (last.get("usage") or {}).get("costUsd", 0) or 0


if __name__ == "__main__":
    hits, cost = 0, 0.0
    run_cases = [c for c in CASES if not ONLY or c[0] in ONLY]
    for cid, desc, turns, check in run_cases:
        ok, c = run(cid, desc, turns, check)
        hits += 1 if ok else 0
        cost += c
    print(f"\n{hits}/{len(run_cases)} 통과 · ${round(cost, 3)}")
