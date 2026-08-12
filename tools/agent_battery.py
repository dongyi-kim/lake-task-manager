# tools/agent_battery.py — 실사용형 난이도 배터리 (실 LLM, 수동 실행 전용).
# 실행: python -X utf8 tools/agent_battery.py [모델]   (기본 gpt-4o-mini)
# pytest 에 넣지 않는 이유: 실 키·비용이 든다. 릴리스 전 손으로 돌려 회귀를 본다.
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
# 사람이 없는 실행이다 — 설정 화면의 확인 게이트를 면제한다(config._env_supplied).
os.environ["LAKE_AGENT_SKIP_VERIFY"] = "1"
os.environ["LAKE_AGENT_OPENAI_CHAT"] = sys.argv[1] if len(sys.argv) > 1 else "gpt-4o-mini"

from app.agent.workflow import session  # noqa: E402

# (질의, 기대 intent, 응답에서 확인할 것)
CASES = [
    ("지식 조사", "CDC가 뭐고 우리 프로젝트에서 CDC 관련해 지금까지 뭘 했는지 정리해줘",
     "ask", lambda o: "DL-" in o["reply"]),
    ("히스토리", "ETL 파이프라인 마이그레이션 히스토리와 지금 상태 정리해줘",
     "ask", lambda o: "DL-" in o["reply"]),
    ("업무 파악", "나 오늘 뭐 해야 할까", "my_day", lambda o: "DL-" in o["reply"]),
    ("처리할 업무", "내 모듈에서 담당자 없는 업무 하나 집고 싶은데", "my_day",
     lambda o: "DL-" in o["reply"] or "없습니다" in o["reply"]),
    ("댓글 작성", "DL-101에 '스테이징 검증 완료, 내일 운영 반영 예정' 이라고 댓글 남겨줘",
     "modify", lambda o: bool(o.get("pending"))),
    ("태스크 생성", "Workbench에 쿼리 결과 엑셀 내보내기 기능 추가하자. 알아서 초안 잡아줘",
     "plan_work", lambda o: bool(o.get("pending")) or bool(o.get("questions"))),
    ("구조적 생성", "Catalog 메타데이터 전 테이블(120개) 설명 보강 작업을 해야 해. "
     "분업 가능하게 나눠서 계획 잡아줘", "plan_work",
     lambda o: bool(o.get("pending")) or bool(o.get("questions"))),
    ("사람 문의", "skcc.x1042는 어떤 사람이야? 최근에 뭘 했고 지금 여유가 있어?",
     "activity", lambda o: "x1042" in o["reply"] or "확인" in o["reply"]),
    ("복합 조건 검색", "진행중이면서 마감이 지났는데 5일 이상 업데이트도 없는 티켓 있어?",
     "progress", lambda o: "DL-" in o["reply"] or "없습니다" in o["reply"]),
    ("문서 검색", "ETL 관련해서 참고할 만한 컨플루언스 문서 뭐가 있어?",
     "ask", lambda o: "문서" in o["reply"] or (o.get("related_docs") or [])),
]

ok = 0
for name, q, want_intent, check in CASES:
    t0 = time.time()
    try:
        out = session.ask(q)
    except Exception as e:
        print(f"✗ {name}: 예외 {str(e)[:120]}")
        continue
    got = out.get("intent")
    passed = (got == want_intent) and bool(check(out))
    ok += passed
    cost = (out.get("usage") or {}).get("costUsd", 0)
    mark = "✓" if passed else "✗"
    print(f"{mark} {name}: intent={got}(기대 {want_intent}) {time.time()-t0:.0f}s ${cost}")
    if not passed:
        print(f"   reply: {(out.get('reply') or '')[:220]}")
print(f"\n{ok}/{len(CASES)} 통과")
