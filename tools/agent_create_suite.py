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
# 사람이 없는 실행이다 — 설정 화면의 확인 게이트를 면제한다(config._env_supplied).
os.environ["LAKE_AGENT_SKIP_VERIFY"] = "1"
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


# ── 본문 품질 게이트 (전 케이스 공통) ─────────────────────────────────────
# 여태 이 스위트는 **구조만** 봤다 — 몇 건인가, 자식이 붙었나, 부모가 맞나. 본문이
# 비어 있든 섹션이 세 벌이든 통과했다. 실사용 사고(STARR NDV)는 구조가 아니라 본문에서
# 났고, DRAFT-COMPARISON 의 갭 3종도 전부 본문 이야기다. 그래서 knowledge/07 의 규율을
# 결정적 검사로 내려 전 케이스에 건다 — judge(주관) 이전의 최소선이다.
# ★ 목록을 여기 다시 적지 않는다 — 코드가 지키는 규칙과 배터리가 재는 규칙이 갈리면
#   더 관대한 쪽이 사고를 낸다(§5-e). refiner 가 원본이고 여기는 그것을 가져다 쓴다.
from app.agent.workflow.agents.refiner import DOD_VAGUE as _DOD_VAGUE  # noqa: E402
from app.agent.workflow.agents.refiner import _bug_grade_body  # noqa: E402


def _body_flaws(o) -> list:
    """상위 항목 본문의 규율 위반 목록. 빈 리스트면 통과.

    Sub-Task 본문은 대상이 아니다(배경을 쓰지 않는 것이 규칙이다 — knowledge/07).
    """
    flaws = []
    for i, it in enumerate(items(o)):
        if str(it.get("type") or "").lower().startswith("sub"):
            continue
        b = _body(it)
        if len(b.strip()) < 60:
            flaws.append(f"[{i}] 본문이 사실상 비었다")
            continue
        is_bug = str(it.get("type") or "").strip().lower() == "bug"
        # Bug는 Task의 배경/범위/DoD가 아니라 재현/기대/실제 세 칸이 계약이다. 전 케이스
        # 공통 Task 게이트를 걸어 PASTE2·BUG2가 올바른 Bug 본문인데도 항상 실패했다.
        if is_bug:
            if not _bug_grade_body(b):
                for sec in ("재현", "기대", "실제"):
                    if sec not in b:
                        flaws.append(f"[{i}] Bug '{sec}' 섹션 없음")
        else:
            for sec in ("배경", "작업 범위", "완료 조건"):
                if sec not in b:
                    flaws.append(f"[{i}] '{sec}' 섹션 없음")
        # 중복·영문 섹션 — 실측 사고(참고/Knowledge/References 3벌)
        if b.count("<h3>참고</h3>") > 1:
            flaws.append(f"[{i}] 참고 섹션이 두 벌")
        for bad in ("References", "<h3>Knowledge</h3>", "Acceptance Criteria"):
            if bad in b:
                flaws.append(f"[{i}] 영문 중복 섹션 '{bad}'")
        # 범위의 제외 — "하지 않는 것을 적는 게 절반"(knowledge/07)
        if not is_bug and "작업 범위" in b and not re.search(r"제외|하지\s*않", b):
            flaws.append(f"[{i}] 작업 범위에 제외가 없다")
        # DoD 판정 방법 — "테스트 완료"는 언제 끝인지 모른다
        dods = re.findall(r'data-checked="[^"]*"[^>]*>(.*?)</li>', b, re.S)
        dods = [re.sub(r"<[^>]+>", "", d).strip() for d in dods]
        dods = [d for d in dods if d]
        if not is_bug and dods:
            vague = [d for d in dods if any(v in d for v in _DOD_VAGUE) and len(d) < 24]
            if len(vague) * 2 > len(dods):
                flaws.append(f"[{i}] DoD 절반 이상이 판정 방법 없음: {vague[:2]}")
        # 링크도 키도 없는 **참고 섹션 안의** 불릿은 날조로 취급한다. 예전의 앞 400자
        # 탐색은 뒤의 '환경 및 추가 정보'까지 참고로 오인했다 — HTML 섹션 경계를 직접 본다.
        for sec in re.finditer(
                r"<h3>\s*참고(?:\s*(?:사항|자료|문서))?\s*</h3>\s*<ul[^>]*>(.*?)</ul>",
                b, re.S | re.I):
            for m in re.finditer(r"<li>(?!.*?(?:[A-Z]{2,}-\d+|<a href))(.{6,80}?)</li>",
                                 sec.group(1), re.S):
                flaws.append(f"[{i}] 참고에 출처 없는 불릿: {m.group(1)[:30]}")
                break
    return flaws


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

    # ★ 이 케이스는 이제 **두 턴**이다 — 최상위가 갈리는 복합 산출물은 본문 대신 구조도를
    #   먼저 내고 합의를 받는다(사용자 요청, §5-g). 예전 계약은 "한 턴에 본문까지"를
    #   기대했는데 그건 **옛 동작**이다. 케이스가 제품을 따라가야 한다.
    ("STR2", "기능 분화 — 구조 합의 후 모듈이 다르면 Task 를 나눈다", [
        "리니지 뷰어 성능 측정하고, 결과에 따라 쿼리 엔진 쪽 인덱스도 손봐야 해. "
        "그리고 사용 가이드도 써야 하고. 초안 잡아줘. 알아서",
        "이 구조로 진행한다"],
     lambda o, outs: (
         # ① 첫 턴은 **구조 확인**이었다(본문을 미리 쓰지 않았다)
         bool(outs[0].get("questions"))
         # ② 합의 뒤에는 본문까지 갖춘 초안이 나온다
         and len(items(o)) >= 2
         and len({(i.get("components") or [""])[0] for i in items(o)}) >= 2)),

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

    # ── Sub-Task 만들기 세 갈래 ──────────────────────────────────────
    ("SUB1", "기존 Task 를 여러 Sub-Task 로 분할 — 부모는 그대로 두고 쪼갠다", [
        "DL-9095 이거 혼자 하기엔 커. 단계별로 서브태스크로 쪼개줘. 알아서"],
     lambda o, _: (lambda rows: len(rows) >= 2
                   and all((r.get("parent") or "") == "DL-9095" for r in rows))
     (items(o) + kids(o))),

    ("SUB2", "기존 Task 하나에 Sub-Task 여러 개 추가 — 이미 있는 자식과 겹치지 않게", [
        "DL-9090 에 성능 측정이랑 사용 가이드 작성 서브태스크 추가해줘. 알아서"],
     lambda o, _: (lambda rows: len(rows) >= 2
                   and all((r.get("parent") or "") == "DL-9090" for r in rows)
                   # 이미 있는 자식(DL-9093~9095)의 제목을 그대로 다시 만들면 중복이다
                   and not any("다운스트림" in (r.get("summary") or "") for r in rows))
     (items(o) + kids(o))),

    ("SUB3", "여러 Task 에 비슷한 Sub-Task 를 각각 추가 — 부모별로 나뉘어야 한다", [
        "DL-9093 이랑 DL-9094 두 개 다 회귀 테스트 서브태스크 하나씩 붙여줘. 알아서"],
     lambda o, _: (lambda rows: len({(r.get("parent") or "") for r in rows}) >= 2
                   and {"DL-9093", "DL-9094"} <= {(r.get("parent") or "") for r in rows})
     (items(o) + kids(o))),

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
     lambda o, _: bool(o.get("questions")) and not items(o)),

    ("ASK2", "되물은 뒤 답을 반영해 초안으로", [
        "데이터 품질 개선 작업 하나 만들어줘",
        "널 비율 체크만 이번에 하고, 나머지는 다음에. 이번 주까지. 알아서"],
     lambda o, outs: bool(outs[0].get("questions")) and len(items(o)) >= 1),

    # ── 중복·기존 것 처리 ────────────────────────────────────────────
    ("DUP1", "이미 있는 일이면 새로 만들지 말고 알린다", [
        "프로듀서를 Avro 로 전환하는 작업을 새로 만들자"],
     lambda o, _: not items(o) and (bool(o.get("questions"))
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

    # ── 실사용 사고 재현: 주제 유지 + 구조 + 본문 규율 (STARR NDV 건) ──
    # 실측 실패: 제목이 Epic 본문("증분 적재")을 따라가 원 요청(StarRocks Puffin NDV)이
    # 사라졌고, 다단계 규모인데 단일 Task 로 뭉갰고, 본문에 참고/References/Knowledge 가
    # 3벌 중복 + 링크 없는 날조 문서 제목이 나열됐다. 전부 여기서 단언한다.
    ("STARR1", "주제 유지 — 원 요청 고유어가 제목에 남고, 다단계는 쪼개지고, 본문 규율", [
        "starrocks puffin ndv 통계정보를 생성하는 파이프라인을 개발해야해",
        "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지, 마감은 2026-09-30. 알아서 진행해"],
     lambda o, _: (lambda its: bool(its)
                   # ① 주제: 원 요청 고유어 2개 이상이 제목에 남아 있다
                   and sum(1 for w in ("starrocks", "puffin", "ndv", "통계")
                           if w in (its[0].get("summary") or "").lower()) >= 2
                   # ② 구조: 다단계 규모 — Sub-Task 로 나뉘었거나 최소한 구조 확인 질문
                   and (len(kids(o)) >= 2 or bool(o.get("questions")))
                   # ③ 본문 규율: 참고 1벌, 영문 중복 섹션 없음
                   and _body(its[0]).count("<h3>참고</h3>") <= 1
                   and "References" not in _body(its[0])
                   and "<h3>Knowledge</h3>" not in _body(its[0]))(items(o))),

    # ── 버그 신고 갈래 (report_bug) ──────────────────────────────────
    # 규율은 refiner 에 적혀 있는데 배터리가 CHIP2 하나뿐이었다. 이 갈래의 실패는 셋이다:
    #   ① 재현 경로 없이 티켓을 만들어 버린다(아무도 못 잡는 티켓이 생긴다)
    #   ② 기대/실제를 안 나눠 적어 무엇이 잘못인지 안 보인다
    #   ③ 버그를 Sub-Task 로 쪼개 관리 단위를 흩뜨린다
    ("BUG1", "재현 경로가 없으면 만들지 말고 묻는다", [
        "리니지 뷰어가 가끔 안 뜬다. 버그로 올려줘"],
     lambda o, _: (bool(o.get("questions"))
                   # 재현·조건을 묻는다(그냥 "더 알려주세요"가 아니라)
                   and any(w in json.dumps(o.get("questions") or [], ensure_ascii=False)
                           for w in ("재현", "언제", "어떤 경우", "조건", "빈도"))
                   # 재현 경로도 없이 초안을 지어내면 실패
                   and not items(o))),

    ("BUG2", "재현 경로를 주면 Bug 로 — 기대/실제가 본문에 나뉜다", [
        "리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다. 크롬에서 재현되고, "
        "기대는 그래프가 그려지는 것. 버그로 올려줘. 알아서"],
     lambda o, _: (lambda its: bool(its)
                   and any((i.get("type") or "") == "Bug" for i in its)
                   # ★ 기대/실제가 **나뉘어** 적혀야 한다 — 섞어 쓰면 무엇이 잘못인지 안 보인다
                   and all(w in _body(its[0]) for w in ("재현", "기대"))
                   # 버그는 쪼개지 않는다(관리 단위가 흩어진다)
                   and not kids(o))(items(o))),

    ("BUG3", "이미 같은 증상의 Bug 가 있으면 새로 만들지 않는다", [
        "야간 배치가 커넥션 타임아웃으로 실패한다. 버그로 등록해줘"],
     lambda o, _: not items(o) and (bool(o.get("questions"))
                                    or "DL-" in (o.get("reply") or ""))),

    # ── 규칙 위반을 요구 ─────────────────────────────────────────────
    ("RULE1", "Sub-Task 를 최상위로 만들어 달라 — 규칙대로 거절하거나 부모를 묻는다", [
        "서브태스크 하나만 딱 만들어줘. 부모는 없어도 돼"],
     lambda o, _: bool(o.get("questions")) and not items(o)),

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
        ok_struct = bool(check(last, outs))
        flaws = _body_flaws(last)           # 구조가 맞아도 본문이 규율을 어기면 통과가 아니다
        ok = ok_struct and not flaws
    except Exception as e:
        print(f"✗ {cid} {desc}: 예외 {str(e)[:160]}")
        return False, 0
    n = len(items(last))
    print(f"{'✓' if ok else '✗'} {cid} {desc}: 초안 {n}건"
          f"{' + 자식 ' + str(len(kids(last))) if kids(last) else ''}"
          f" · 질문 {len(last.get('questions') or [])}"
          f" · 구조 {pend(last, 'structure') or '-'}"
          f" · 본문 {'ok' if not flaws else f'{len(flaws)}건'} · {time.time() - t0:.0f}s")
    if flaws:
        print(f"    본문 결함: {' / '.join(flaws[:4])}")
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
