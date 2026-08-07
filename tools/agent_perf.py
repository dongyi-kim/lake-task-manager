# tools/agent_perf.py — 성능 기준선 하네스 (실 LLM, 수동 실행 전용).
# 실행: python -X utf8 tools/agent_perf.py [모델]   (기본 gpt-4o-mini)
# 대표 시나리오의 턴 시간·호출 수·토큰을 **역할별로** 표로 낸다 — 최적화 전/후 비교 기준.
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
os.environ["LAKE_AGENT_OPENAI_CHAT"] = sys.argv[1] if len(sys.argv) > 1 else "gpt-4o-mini"

from app.agent.workflow import session  # noqa: E402

SCENARIOS = [
    ("조회", ["나 오늘 뭐 해야 할까"]),
    ("지식", ["데이터 리니지가 뭐고 우리가 뭘 했는지 정리해줘"]),
    ("생성(첫턴)", ["Workbench에 쿼리 결과 엑셀 내보내기를 추가하자. 알아서 초안 잡아줘"]),
    ("수정", ["DL-101 우선순위를 P2-Major로 바꿔줘"]),
]

grand = {"sec": 0.0, "calls": 0, "tok": 0, "cost": 0.0}
for name, turns in SCENARIOS:
    t0, tid, u = time.time(), "", {}
    for q in turns:
        out = session.ask(q, thread_id=tid)
        tid = out["thread_id"]
        u = out.get("usage") or {}
    sec = time.time() - t0
    grand["sec"] += sec
    grand["calls"] += u.get("calls", 0)
    grand["tok"] += u.get("totalTokens", 0)
    grand["cost"] += u.get("costUsd", 0) or 0
    rows = " · ".join(f"{k} {v['calls']}회/{v['seconds']}s/{v['tokens']//1000}k"
                      for k, v in sorted((u.get("byNode") or {}).items(),
                                         key=lambda kv: -kv[1]["seconds"]))
    print(f"[{name}] {sec:.0f}s · LLM {u.get('calls')}회 · {u.get('totalTokens', 0):,}tok "
          f"· ${u.get('costUsd', 0)}\n    역할별: {rows}")
print(f"\n합계 {grand['sec']:.0f}s · {grand['calls']}회 · {grand['tok']:,}tok · ${round(grand['cost'], 3)}")
