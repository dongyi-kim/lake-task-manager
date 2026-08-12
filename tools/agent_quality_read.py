# tools/agent_quality_read.py — **정성 판독용** 실 LLM 하네스 (수동 실행 전용).
# 실행: python -X utf8 tools/agent_quality_read.py [모델] [케이스…]
#
# agent_perf 는 시간·토큰만 준다. 프롬프트의 **의미**를 바꿨을 때 필요한 건 그게 아니라
# "답이 실제로 뭐라고 나왔는가"다 — 답변 전문·초안 본문·담당 근거·변경 계획을 통째로
# 찍어서 사람이 읽는다(배터리 green 은 품질의 근거가 아니다).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
# 사람이 없는 실행이다 — 설정 화면의 확인 게이트를 면제한다(config._env_supplied).
os.environ["LAKE_AGENT_SKIP_VERIFY"] = "1"
os.environ["LAKE_AGENT_OPENAI_CHAT"] = (
    sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].isupper() else "gpt-4o-mini")

from app.agent.workflow import session  # noqa: E402

CASES = {
    # 도구를 걷어낸 두 역할이 한 턴에 같이 도는 경로 — 이번 변경의 핵심 확인처.
    "CREATE": ["Workbench에 쿼리 결과 엑셀 내보내기 기능을 추가하자. 알아서 초안 잡아줘"],
    # 사전취합이 '미배정'이라는 **다른 기준**을 가로채던 회귀의 확인.
    "UNASSIGNED": ["내 모듈에서 담당자 없는 업무 하나 집고 싶은데"],
    # _change_plan 을 별도 함수로 뽑은 뒤의 변경 갈래.
    "MODIFY": ["DL-101 마감을 다음 주 금요일로 미뤄줘"],
}
picked = [a for a in sys.argv[1:] if a.isupper() and a in CASES] or list(CASES)


def _dump(tag, out):
    print("\n" + "=" * 78 + f"\n[{tag}]  질의: {out['_q']}\n" + "=" * 78)
    print("--- 답변 전문 ---")
    print(out.get("reply") or "(없음)")
    pend = out.get("pending") or {}
    if pend:
        print(f"\n--- 승인 카드: {pend.get('action')} "
              f"(구조 {pend.get('structure') or '-'} / {pend.get('structure_why') or ''}) ---")
        if pend.get("changes"):
            print(f"  변경: {pend['changes']}  코멘트: {pend.get('comment') or '없음'}")
        print(f"  근거: {pend.get('rationale') or '없음'}")
    for i, it in enumerate((pend.get("items") or []) + (out.get("draft_items") or [])):
        print(f"\n--- 초안[{i}] {it.get('type') or it.get('issueType')} · {it.get('summary')} ---")
        print(f"  모듈={it.get('components')} Epic={it.get('epic')} "
              f"라벨={it.get('labels')} 담당={it.get('assignee')} 마감={it.get('duedate')}")
        print("  본문:\n    " + str(it.get("description") or "").replace("\n", "\n    "))
    for c in pend.get("children") or []:
        print(f"  └ 하위: {c.get('summary')} (담당 {c.get('assignee')})")
    for a in out.get("assignments") or []:
        # 스키마상 `reasons` 는 **리스트**다 — `reason` 으로 읽어 None 이 찍혔다.
        print(f"\n--- 담당 제안: {a.get('user')} ---")
        for r in a.get("reasons") or ["(근거 없음)"]:
            print(f"  · {r}")
        for c in a.get("children") or []:
            print(f"  └ 하위[{c.get('index')}] → {c.get('user')} : {c.get('why')}")
        for alt in a.get("alternates") or []:
            print(f"  대안: {alt.get('user')} — {alt.get('why')}")
    if out.get("questions"):
        print("\n--- 질문 ---")
        for q in out["questions"]:
            print(f"  · {q.get('question')} [{q.get('kind')}] {q.get('options') or ''}")
    if out.get("error"):
        print(f"\n!! 오류: {out['error']}")


for name in picked:
    tid = ""
    for q in CASES[name]:
        out = session.ask(q, thread_id=tid) or {}
        tid = out.get("thread_id") or tid
        out["_q"] = q
        _dump(name, out)
    u = (out or {}).get("usage") or {}
    print(f"\n({name}: LLM {u.get('calls')}회 · {u.get('totalTokens', 0):,}tok "
          f"· ${u.get('costUsd', 0)})")
