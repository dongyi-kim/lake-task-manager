# tools/agent_tier_eval.py — 티어별 모델 비교 (실 LLM, 수동 실행 전용).
#
# 실행: python -X utf8 tools/agent_tier_eval.py simple  [모델 ...]
#       python -X utf8 tools/agent_tier_eval.py embed   [모델 ...]
#
# 왜 따로 재나 — complex 티어만 바꿔 놓고 "이 모델이 낫다"고 말할 수 없다. simple 티어가
# 하는 일(의도 분류)과 임베딩이 하는 일(규칙 검색)은 성격이 완전히 달라서, **각자의 실패**를
# 각자의 잣대로 봐야 한다. 문장 품질 judge 로는 둘 다 안 보인다.
#
#   simple — Planner 의 의도 분류 정확도. 정답이 있으므로 judge 가 필요 없다(결정적 채점).
#   embed  — 규칙 질의가 맞는 문서를 끌어오는가(recall@k). 이것도 정답이 있다.
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"

# ── simple 티어: 의도 분류 골든셋 ───────────────────────────────────
# 배터리에서 실제로 갈렸던 경계들을 담았다 — 쉬운 것만 모으면 어떤 모델이든 만점이다.
INTENT_CASES = [
    ("실시간 수집 파이프라인에 CDC 방식을 도입해야 한다", "plan_work"),
    ("데이터 거버넌스 에픽 하나 새로 만들자", "plan_work"),
    ("DL-101 밑에 서브태스크 3개 만들어줘", "plan_work"),
    ("적재 배치가 어젯밤부터 계속 실패한다", "report_bug"),
    ("DL-101 어떻게 진행되고 있어?", "progress"),
    ("DL-9090 지금 어디까지 진행됐어?", "progress"),
    ("ETL 모듈 진척률 알려줘", "progress"),
    ("진행중인 티켓 중 2일 이상 업데이트 없는 것들 있니?", "progress"),
    ("나 오늘 뭐 해야 하지?", "my_day"),
    ("내 모듈에 담당자 없는 업무 있으면 하나 하고 싶네", "my_day"),
    ("skcc.x1042 최근 3일간 뭐 했어?", "activity"),
    ("DL-101 관련자들이 요즘 어떤 일들을 해?", "activity"),
    ("CDC 검토가 왜 멈췄었지?", "ask"),
    ("지난 분기에 성능 관련해서 어떤 논의가 있었어?", "ask"),
    ("DL-207을 x1103에게 맡기는 게 적절할까?", "ask"),
    ("DL-207 담당자를 x1103 으로 바꿔줘", "modify"),
    ("DL-207 마감을 다음 주로 미루고 사유도 코멘트로 남겨줘", "modify"),
    ("fdc.fdc_trace_summary_ic 데이터의 현재 적재주기는?", "ask"),
    ("fdc.fdc_trace_summary_ic 적재하는 job 이름이랑 작업자 누구야?", "ask"),
    ("Schema Registry 우리 어떻게 쓰고 있고 호환성 정책은 뭐야?", "ask"),
    ("고생 많으십니다", "chitchat"),
]

# 답변 깊이도 simple 티어가 정한다 — 값 질문에 개념 강의가 붙는 사고의 출발점이다.
DEPTH_CASES = [
    ("fdc.fdc_trace_summary_ic 적재주기는?", "brief"),
    ("DL-101 담당자 누구야?", "brief"),
    ("적재 지연이 왜 났고 어떻게 해결했어?", "explain"),
    ("CDC가 뭐고 우리는 어떻게 쓰고 있어?", "explain"),
]

# ── embedding: 규칙 검색 골든셋 ─────────────────────────────────────
# 질의 → 그 답이 실제로 들어 있는 knowledge 파일. 순위가 아니라 **맞는 문서를 끌어오는가**.
EMBED_CASES = [
    ("Story Point 는 어디에 매길 수 있나", "01-ticket-rules.md"),
    ("완료 판정은 무엇으로 하나", "01-ticket-rules.md"),
    ("진척률 분모에서 빠지는 티켓", "02-progress-formula.md"),
    ("ETL 모듈은 무슨 일을 하나", "03-modules-and-people.md"),
    ("담당자를 추천할 때 어떤 근거를 쓰나", "03-modules-and-people.md"),
    ("Epic 으로 격상해도 되는 기준", "04-work-breakdown.md"),
    ("티켓 본문에 무엇을 적어야 하나", "07-ticket-body-guide.md"),
    ("Sub-Task 담당을 어떻게 나누나", "07-ticket-body-guide.md"),
    ("LTM 에서 담당자는 어떻게 바꾸나", "05-ltm-guide.md"),
    ("적재주기 변경은 어디에 기록되나", "06-data-assets.md"),
]


def eval_simple(models):
    from app.agent import config as C
    from app.agent.workflow.agents.planner import Planner
    rows = []
    for m in models:
        os.environ["LAKE_AGENT_OPENAI_CHAT_SIMPLE"] = m
        os.environ["LAKE_AGENT_OPENAI_CHAT"] = m      # complex 는 이 시험에 안 쓰인다
        from app.agent import usage as _usage
        meter = _usage.Meter()
        cb = _usage.callback(meter)
        agent, hit, dhit, t0, wrong = Planner(), 0, 0, time.time(), []
        for text, want in INTENT_CASES:
            got = _classify(agent, text, cb)
            hit += 1 if got.get("intent") == want else 0
            if got.get("intent") != want:
                wrong.append(f"{text[:24]}… → {got.get('intent')}(기대 {want})")
        for text, want in DEPTH_CASES:
            got = _classify(agent, text, cb)
            dhit += 1 if (got.get("answer_depth") or "brief") == want else 0
        snap = meter.snapshot()
        rows.append({
            "model": m, "intent": f"{hit}/{len(INTENT_CASES)}",
            "depth": f"{dhit}/{len(DEPTH_CASES)}",
            "sec": round(time.time() - t0),
            "tok": snap.get("totalTokens", 0),
            "usd": round(snap.get("costUsd", 0) or 0, 4),
            "wrong": wrong,
        })
        print(f"[{m}] 의도 {rows[-1]['intent']} · 깊이 {rows[-1]['depth']} · "
              f"{rows[-1]['sec']}s · {rows[-1]['tok']:,}tok · ${rows[-1]['usd']}")
        for w in wrong:
            print(f"    ✗ {w}")
    return rows


def _classify(agent, text, cb=None):
    """Planner 한 건만 돌린다 — 그래프를 태우면 다른 역할의 비용이 섞인다.

    ★ 호출은 **에이전트 자신의 경로**(`structured()`)로 한다. 여기서 LLM 호출을 다시 짜면
    스키마에 이름을 붙이는 처리가 빠져 `Unsupported function` 으로 전건 실패한다(실측) —
    운영과 다른 경로로 잰 숫자는 비교에 쓸 수 없다.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.agent.workflow.agents.base import _as_dict
    state = {"messages": [HumanMessage(content=text)], "thread_id": "tier-eval"}
    try:
        out = agent.structured().invoke(
            [SystemMessage(content=agent.system(state)),
             HumanMessage(content=agent.task(state))],
            config={"callbacks": [cb]} if cb else None)
        return agent.apply(state, _as_dict(out))
    except Exception as e:
        return {"intent": f"(오류 {str(e)[:60]})"}


def eval_embed(models):
    from app.agent.retrieval import static_index
    rows = []
    for m in models:
        os.environ["LAKE_AGENT_OPENAI_EMBED"] = m
        static_index._cached["store"] = None
        t0 = time.time()
        static_index.build(force=True)
        build_s = round(time.time() - t0, 1)
        hit1 = hit3 = 0
        miss = []
        for q, want in EMBED_CASES:
            hits = static_index.search(q, k=3)
            srcs = [h.get("source") for h in hits]
            hit1 += 1 if srcs[:1] == [want] else 0
            if want in srcs:
                hit3 += 1
            else:
                miss.append(f"{q[:22]}… → {srcs}")
        rows.append({"model": m, "top1": f"{hit1}/{len(EMBED_CASES)}",
                     "top3": f"{hit3}/{len(EMBED_CASES)}", "build_s": build_s, "miss": miss})
        print(f"[{m}] top1 {rows[-1]['top1']} · top3 {rows[-1]['top3']} · 색인 {build_s}s")
        for x in miss:
            print(f"    ✗ {x}")
    return rows


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "simple"
    models = sys.argv[2:] or (["gpt-4o-mini", "gpt-5.4-nano", "gpt-5.4-mini"] if what == "simple"
                              else ["text-embedding-3-small", "text-embedding-3-large"])
    print(f"── {what} 티어 비교: {', '.join(models)}\n")
    (eval_simple if what == "simple" else eval_embed)(models)
