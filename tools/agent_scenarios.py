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
]

JUDGE_SYS = (
    "너는 PMO 어시스턴트 답변의 채점자다. 사용자 질문과 답변을 보고 4축을 1~5로 채점하라. "
    "5=흠잡을 데 없음, 3=쓸 만하나 아쉬움, 1=실패. JSON 만 출력: "
    '{"visibility":n,"clarity":n,"completeness":n,"grounding":n,"worst":"가장 아쉬운 점 한 문장"} '
    "— visibility(구조·표·소제목으로 눈에 잘 들어오나), clarity(한 번 읽고 이해되나), "
    "completeness(질문이 요구한 정보를 다 담았나), grounding(주장마다 티켓 키·수치 근거가 있나)")


def judge(question, reply):
    from app.agent import config as C
    try:
        out = C.get_llm(temperature=0, tier="simple").invoke([
            ("system", JUDGE_SYS),
            ("user", f"### 질문\n{question}\n\n### 답변\n{reply[:4000]}")])
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
        passed = ok_intent and ok_check
        j = judge(turns[-1], last.get("reply") or "") if passed else {}
        score = round(sum(j.get(k, 0) for k in ("visibility", "clarity", "completeness",
                                                "grounding")) / 4, 2) if j and "error" not in j else 0
        mark = "✓" if passed else "✗"
        print(f"{mark} {cid} {desc}: intent={last.get('intent')}"
              f"{'' if ok_intent else f'(기대 {want_intent})'} 체커={'ok' if ok_check else 'FAIL'}"
              f" 품질={score} {time.time()-t0:.0f}s")
        if not passed:
            print(f"   reply: {(last.get('reply') or '')[:200]}")
        if j.get("worst"):
            print(f"   judge: {j['worst'][:110]}")
        rows.append({"id": cid, "desc": desc, "turns": turns, "passed": passed,
                     "intent": last.get("intent"), "score": score, "judge": j,
                     "reply": (last.get("reply") or "")[:1500]})
    except Exception as e:
        print(f"✗ {cid}: 예외 {str(e)[:150]}")
        rows.append({"id": cid, "desc": desc, "passed": False, "error": str(e)[:300]})

n_ok = sum(1 for r in rows if r.get("passed"))
avg = round(sum(r.get("score", 0) for r in rows if r.get("passed")) / max(1, n_ok), 2)
print(f"\n{n_ok}/{len(rows)} 통과 · 품질 평균 {avg} · 총비용 ${round(total_cost, 3)}")

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
