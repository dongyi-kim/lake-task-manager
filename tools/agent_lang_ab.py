# tools/agent_lang_ab.py — **프롬프트 언어 A/B 측정 하네스**(실 LLM, 수동 실행).
#
# 왜 따로 만들었나: 기존 배터리는 "통과/실패"만 남긴다. 언어를 비교하려면 그것으로 부족하다 —
# 같은 질문에 **실제로 어떤 답이 나왔는지**를 나란히 놓고 봐야 하고(정성), 그 답이 몇 토큰·
# 몇 초를 썼는지도 알아야 한다(정량). 그래서 한 실행에서 셋을 다 남긴다:
#
#   ① 정량 — 턴당 지연 · LLM 호출 수 · 프롬프트/완성 토큰 · 캐시 히트 · 역할별 분해
#   ② 계약 — 코드가 잴 수 있는 최소선(초안 항목·표·참조·근거 위반·후검증)
#   ③ 정성 — **답변 전문**과 승인 카드. 보고서에 그대로 실어 사람이 비교한다.
#
# 실행: python -X utf8 -u tools/agent_lang_ab.py <출력.json> [모델]
#       (브랜치별 워크트리에서 각각 돌리고 두 json 을 비교한다)
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
os.environ["LAKE_AGENT_SKIP_VERIFY"] = "1"      # 사람이 없는 실행 — 설정 확인 게이트 면제
OUT = sys.argv[1] if len(sys.argv) > 1 else "lang-ab.json"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "gpt-4o-mini"
os.environ["LAKE_AGENT_OPENAI_CHAT"] = MODEL
os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", MODEL)

from app.agent.workflow import session          # noqa: E402

# ── 시나리오 — 실사용에서 가장 자주 오는 것들. 여러 턴짜리도 그대로 둔다
#    (인터뷰 → 초안이 이 도구의 핵심 갈래다).
SCENARIOS = [
    ("S1-생성", ["우리 기존 etl 파이프라인에 iceberg puffin ndv 통계정보를 생성하는 "
                 "기능을 추가구현하고 싶어",
                 "1차 목표는 PoC. 완료 조건은 Lake 내 Iceberg 배치적재 테이블에 통계 생성 "
                 "Batch Job 구현. 단계별 Sub-Task 로. 알아서"]),
    ("S2-버그", ["리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다. 크롬에서 재현되고 "
                 "기대는 그래프가 그려지는 것. 버그로 올려줘. 알아서"]),
    ("S3-이력", ["fdc.fdc_trace_summary_ic 데이터에 대해 히스토리 정리"]),
    ("S4-사람", ["이다은 책임이 지금 맡고 있는 일 알려줘"]),
    ("S5-내일", ["지금 무슨 업무를 시작해야 할까"]),
    ("S6-진척", ["DL-9090 지금 어디까지 진행됐어?"]),
]

_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_TABLE = re.compile(r"^\s*\|.+\|\s*$", re.M)


def _checks(out: dict) -> dict:
    """코드가 **잴 수 있는 것**만. 문장의 좋고 나쁨은 사람이 본다."""
    text = out.get("reply") or ""
    items = ((out.get("pending") or {}).get("items")
             or (out.get("draft") or {}).get("items") or [])
    c = {
        "글자수": len(text),
        "표": len(_TABLE.findall(text)) >= 2,
        "참조절": "참조" in text and bool(_KEY.search(text)),
        "초안항목": len(items),
        "자식합계": sum(len(i.get("children") or []) for i in items),
        "질문수": len(out.get("questions") or []),
        # 어투 — 정보 전달 줄의 종결어미(사용자 지시: 종결어미 생략·개조식)
        "종결어미줄": len(re.findall(r"(?:입니다|습니다|합니다)[.\s]*$", text, re.M)),
        "맺음상투구": bool(re.search(r"(?:궁금|필요).{0,20}(?:말씀|알려)", text)),
    }
    try:
        from app.agent.workflow import grounding
        g = grounding.check(text) or {}
        c["근거위반"] = (len(g.get("fake_keys") or []) + len(g.get("wrong_titles") or {})
                       + len(g.get("fake_people") or []))
    except Exception:
        c["근거위반"] = None
    try:
        from app.agent.workflow import postcheck
        c["후검증위반"] = postcheck.check(out, text)
    except Exception:
        c["후검증위반"] = []
    return c


def run():
    rows = []
    for sid, turns in SCENARIOS:
        tid, per = "", []
        for q in turns:
            t0 = time.time()
            try:
                out = session.ask(q, thread_id=tid)
            except Exception as e:                # noqa: BLE001 — 한 케이스가 죽어도 계속
                per.append({"질문": q, "오류": str(e)[:200]})
                continue
            tid = out.get("thread_id") or tid
            u = out.get("usage") or {}
            per.append({
                "질문": q,
                "초": round(time.time() - t0, 1),
                "LLM호출": u.get("calls"), "프롬프트토큰": u.get("promptTokens"),
                "완성토큰": u.get("completionTokens"), "총토큰": u.get("totalTokens"),
                "캐시토큰": u.get("cachedTokens", 0),
                "역할별": u.get("byNode") or {},
                "검사": _checks(out),
                "답변": out.get("reply") or "",
                "카드": [{"타입": i.get("type"), "제목": i.get("summary"),
                          "본문": (i.get("description") or "")[:900],
                          "자식": [c.get("summary") for c in (i.get("children") or [])]}
                         for i in (((out.get("pending") or {}).get("items")
                                    or (out.get("draft") or {}).get("items") or [])[:4])],
                "질문폼": [q2.get("question") for q2 in (out.get("questions") or [])],
            })
            print(f"  {sid} · {per[-1].get('초')}s · {per[-1].get('총토큰')}tok", flush=True)
        rows.append({"시나리오": sid, "턴": per})
        print(f"✔ {sid} 완료", flush=True)

    tot = {"턴수": 0, "초": 0.0, "총토큰": 0, "프롬프트토큰": 0, "완성토큰": 0,
           "캐시토큰": 0, "LLM호출": 0, "근거위반": 0, "후검증위반": 0,
           "종결어미줄": 0, "맺음상투구": 0}
    for r in rows:
        for t in r["턴"]:
            if "오류" in t:
                continue
            tot["턴수"] += 1
            for k in ("초", "총토큰", "프롬프트토큰", "완성토큰", "캐시토큰", "LLM호출"):
                tot[k] += (t.get(k) or 0)
            ck = t.get("검사") or {}
            tot["근거위반"] += (ck.get("근거위반") or 0)
            tot["후검증위반"] += len(ck.get("후검증위반") or [])
            tot["종결어미줄"] += ck.get("종결어미줄") or 0
            tot["맺음상투구"] += 1 if ck.get("맺음상투구") else 0
    tot["초"] = round(tot["초"], 1)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(
        json.dumps({"model": MODEL, "합계": tot, "시나리오": rows},
                   ensure_ascii=False, indent=1))
    print(json.dumps(tot, ensure_ascii=False), flush=True)
    print(f"→ {OUT}", flush=True)


if __name__ == "__main__":
    run()
