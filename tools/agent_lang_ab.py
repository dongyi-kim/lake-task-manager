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
# 실행: python -X utf8 -u tools/agent_lang_ab.py [모델] [시나리오 ID...] [--out .cache/...json]
#       raw 결과는 기본적으로 .cache/agent-evaluation/<runGroupId>/ 아래에 저장한다.
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
os.environ["LAKE_AGENT_SKIP_VERIFY"] = "1"      # 사람이 없는 실행 — 설정 확인 게이트 면제
_raw_args = list(sys.argv[1:])
REQUESTED_OUT = None
for i, arg in enumerate(_raw_args):
    if arg.startswith("--out="):
        REQUESTED_OUT = arg.split("=", 1)[1]
    elif arg == "--out" and i + 1 < len(_raw_args):
        REQUESTED_OUT = _raw_args[i + 1]
_args = [a for i, a in enumerate(_raw_args)
         if not a.startswith("-") and not (i and _raw_args[i - 1] == "--out")]
if _args and not _args[0].upper().startswith("S"):
    MODEL, _scenario_args = _args[0], _args[1:]
else:
    MODEL, _scenario_args = "gpt-4o-mini", _args
ONLY = {x.upper() for x in _scenario_args if x.upper().startswith("S")}
os.environ["LAKE_AGENT_OPENAI_CHAT"] = MODEL
# 언어/프롬프트 비교에서도 production routing을 유지한다. 모델을 하나로
# 평준화하면 프롬프트뿐 아니라 실행 환경까지 바뀌어 주 비교 결과가 무효가 된다.
os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")
SIMPLE_MODEL = os.environ["LAKE_AGENT_OPENAI_CHAT_SIMPLE"]

from tools.agent_eval_isolation import (begin_case, configure_process_isolation,
                                         finish_case)  # noqa: E402
configure_process_isolation("conversation")
from app.agent.workflow import session          # noqa: E402
from tools.agent_eval_protocol import (build_run_metadata, quantitative_metrics,
                                       raw_result_path, reserve_raw_result_path,
                                       write_raw_result)  # noqa: E402
from tools.agent_eval_review_specs import review_specs  # noqa: E402
try:  # 과거 prompt variant commit에도 같은 하네스를 적용한다.
    from app.agent.prompts.base import PROMPT_VERSION  # noqa: E402
except ImportError:  # legacy asset에는 version 상수가 없었다.
    PROMPT_VERSION = os.getenv("LAKE_AGENT_PROMPT_VERSION", "legacy")

BATTERY_VERSION = "3.2.0"
SUITE_REVIEW_ELEMENTS, CASE_REVIEW_SPECS = review_specs("conversation")

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
    ("S7-내외부조사", ["우리 프로젝트의 Iceberg Puffin NDV 적용 가능성을 내부 작업 이력과 "
                       "외부 공식 자료를 함께 조사해줘"]),
    ("S8-복합근거품질", [
        "우리 프로젝트의 Iceberg Puffin NDV 운영 적용 여부를 판단할 수 있도록 Jira 티켓·댓글, "
        "Confluence 설계 문서, 외부 공식 문서를 함께 조사해 근거 중심 의사결정 메모를 작성해줘. "
        "각 핵심 결론을 근거 marker로 연결하고, 같은 출처의 본문·댓글 등 여러 발견은 하나의 "
        "출처 번호 아래에서 구분해줘. 확인되지 않은 사항과 출처별 신뢰도·요청 적합성도 판단해줘."
    ]),
]

_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_TABLE = re.compile(r"^\s*\|.+\|\s*$", re.M)


def _checks(out: dict, user_text: str = "", evaluation_evidence: dict | None = None) -> dict:
    """코드가 **잴 수 있는 것**만. 문장의 좋고 나쁨은 사람이 본다."""
    text = out.get("reply") or ""
    pending = out.get("pending") or {}
    items = (pending.get("items") or (out.get("draft") or {}).get("items") or [])
    # API 승인 shape는 부모 `items`와 자식 `children`을 분리한다. 예전 하네스는 items 안의
    # nested children만 세서 실제 트리도 전부 0건으로 기록했다 — 보고서의 S1 오판 원인.
    flat_kids = pending.get("children") or []
    child_count = (len(flat_kids) if flat_kids else
                   sum(len(i.get("children") or []) for i in items))
    c = {
        "글자수": len(text),
        "표": len(_TABLE.findall(text)) >= 2,
        "참조절": "참조" in text and bool(_KEY.search(text)),
        "초안항목": len(items),
        "자식합계": child_count,
        "질문수": len(out.get("questions") or []),
        # 어투 — 정보 전달 줄의 종결어미(사용자 지시: 종결어미 생략·개조식)
        "종결어미줄": len(re.findall(r"(?:입니다|습니다|합니다)[.\s]*$", text, re.M)),
        "맺음상투구": bool(re.search(r"(?:궁금|필요).{0,20}(?:말씀|알려)", text)),
    }
    wants_kids = bool(re.search(
        r"단계별\s*(?:sub-?task|서브\s*태스크)|하위\s*작업으로\s*나눠|단계별로\s*쪼개",
        user_text or "", re.I))
    claims_kids = bool(re.search(
        r"(?:^|\n)#{1,4}\s*하위\s*작업|(?:sub-?task|서브\s*태스크)로\s*나누",
        text, re.I))
    c["요구구조불일치"] = bool(wants_kids and items and not out.get("questions")
                             and c["자식합계"] < 2)
    c["응답카드불일치"] = bool(claims_kids and items and c["자식합계"] == 0)
    if "외부 공식" in (user_text or ""):
        c["내외부조사완결"] = bool(
            re.search(r"https?://", text)
            and re.search(r"내부|Jira|Confluence|티켓|문서", text, re.I)
            and re.search(r"외부|공식", text)
        )
    if "근거 중심 의사결정" in (user_text or ""):
        # Human review owns truth/quality, but the harness records the minimum persisted
        # grammar that the real UI renderer consumes.  Visual usability is reviewed from
        # an actual local-UI screenshot under the S8 specialized contract.
        roots = re.findall(r"^\[(\d+)\]\s+.+$", text, re.M)
        evidence_match = re.search(r"(?m)^#{1,4}\s+근거\s*$", text)
        body = text[:evidence_match.start()] if evidence_match else text
        body_citations = re.findall(r"\[(\d+)(?:-[a-z])?\](?!\()", body, re.I)
        source_sections = re.split(r"(?m)^\[(\d+)\]\s+.+$", text.split("### 근거", 1)[-1])
        grouped_ok = True
        for pos in range(1, len(source_sections), 2):
            number = source_sections[pos]
            observations = [line for line in source_sections[pos + 1].splitlines()
                            if line.strip().startswith("-")]
            if len(observations) > 1 and not all(
                    re.match(rf"^\s*-\s*\[{number}-[a-z]\]", line, re.I)
                    for line in observations):
                grouped_ok = False
        source_rows = re.findall(r"^\[(\d+)\]\s+(.+)$", text.split("### 근거", 1)[-1], re.M)
        linked_sources = all("{{ticket-detail:" in row or re.search(r"\]\(https?://", row)
                             for _number, row in source_rows)
        planned_sources = {
            str(query.get("source") or "") for query in
            (((evaluation_evidence or {}).get("queryPlan") or {}).get("queries") or [])
            if isinstance(query, dict)
        }
        c["복합근거단일인덱스"] = bool(
            len(re.findall(r"^#{1,4}\s+근거\s*$", text, re.M)) == 1
            and not re.search(r"^#{1,4}\s+(?:참조|관련 문서)\s*$", text, re.M)
            and len(roots) >= 3
            and len(roots) == len(set(roots))
            and grouped_ok and linked_sources
        )
        c["본문근거연결"] = bool(body_citations and set(body_citations) <= set(roots))
        c["복합자료조회"] = {"jira", "comments", "confluence", "web"} <= planned_sources
        c["출처평가완결"] = bool(
            re.search(r"(?m)^###\s+출처 평가\s*$", text)
            and "| 출처 | 신뢰도 | 요청 적합성 | 한계 |" in text
            and "명시된 한계 없음" not in text
        )
    if re.search(r"지금\s*맡|현재\s*맡", user_text or ""):
        c["현재업무범위"] = bool(
            re.search(r"mention:|\[~|data-(?:uid|id)", text)
            and not re.search(r"(?<!미)완료(?:된|한|\s*작업|\s*티켓)", text)
        )
    try:
        from app.agent.workflow import grounding
        g = grounding.check(text) or {}
        c["근거위반"] = (len(g.get("fake_keys") or []) + len(g.get("wrong_titles") or {})
                       + len(g.get("fake_people") or []) + len(g.get("name_as_id") or {}))
    except Exception:
        c["근거위반"] = None
    try:
        from app.agent.workflow import postcheck
        c["후검증위반"] = postcheck.check(out, text)
    except Exception:
        c["후검증위반"] = []
    return c


def run():
    selected_ids = [
        sid for sid, _ in SCENARIOS
        if not ONLY or sid.split("-", 1)[0].upper() in ONLY or sid.upper() in ONLY
    ]
    evaluation = build_run_metadata(
        suite="conversation",
        battery_version=BATTERY_VERSION,
        cases=SCENARIOS,
        selected_case_ids=selected_ids,
        model=MODEL,
        simple_model=SIMPLE_MODEL,
        prompt_version=PROMPT_VERSION,
        suite_review_elements=SUITE_REVIEW_ELEMENTS,
        case_review_specs=CASE_REVIEW_SPECS,
    )
    out_path = reserve_raw_result_path(
        raw_result_path("conversation", evaluation, requested=REQUESTED_OUT),
    )
    rows = []
    for sid, turns in SCENARIOS:
        if ONLY and sid.split("-", 1)[0].upper() not in ONLY and sid.upper() not in ONLY:
            continue
        isolation_start = begin_case(sid)
        tid, per = "", []
        for q in turns:
            t0 = time.time()
            try:
                out = session.ask(q, thread_id=tid)
            except Exception as e:                # noqa: BLE001 — 한 케이스가 죽어도 계속
                per.append({"질문": q, "오류": str(e)[:200]})
                continue
            tid = out.get("thread_id") or tid
            evaluation_evidence = session.evaluation_snapshot(tid)
            u = out.get("usage") or {}
            per.append({
                "질문": q,
                "초": round(time.time() - t0, 1),
                "LLM호출": u.get("calls"), "프롬프트토큰": u.get("promptTokens"),
                "완성토큰": u.get("completionTokens"), "총토큰": u.get("totalTokens"),
                "캐시토큰": u.get("cachedTokens", 0),
                "비용USD": u.get("costUsd"),
                "역할별": u.get("byNode") or {},
                "검사": _checks(out, q, evaluation_evidence),
                "답변": out.get("reply") or "",
                "카드": [{"타입": i.get("type"), "제목": i.get("summary"),
                          "본문": (i.get("description") or "")[:900],
                          "자식": ([c.get("summary") for c in (i.get("children") or [])]
                                   or [c.get("summary") for c in
                                       ((out.get("pending") or {}).get("children") or [])
                                       if c.get("parent_index") == idx])}
                         for idx, i in enumerate(
                             ((out.get("pending") or {}).get("items")
                              or (out.get("draft") or {}).get("items") or [])[:4])],
                "질문폼": [q2.get("question") for q2 in (out.get("questions") or [])],
                "평가근거": evaluation_evidence,
            })
            print(f"  {sid} · {per[-1].get('초')}s · {per[-1].get('총토큰')}tok", flush=True)
        rows.append({"시나리오": sid, "턴": per,
                     "격리": finish_case(isolation_start)})
        print(f"✔ {sid} 완료", flush=True)

    tot = {"턴수": 0, "초": 0.0, "총토큰": 0, "프롬프트토큰": 0, "완성토큰": 0,
           "캐시토큰": 0, "LLM호출": 0, "근거위반": 0, "후검증위반": 0,
           "종결어미줄": 0, "맺음상투구": 0, "요구구조불일치": 0,
           "응답카드불일치": 0, "비용USD": 0.0}
    for r in rows:
        for t in r["턴"]:
            if "오류" in t:
                continue
            tot["턴수"] += 1
            for k in ("초", "총토큰", "프롬프트토큰", "완성토큰", "캐시토큰", "LLM호출"):
                tot[k] += (t.get(k) or 0)
            tot["비용USD"] += (t.get("비용USD") or 0)
            ck = t.get("검사") or {}
            tot["근거위반"] += (ck.get("근거위반") or 0)
            tot["후검증위반"] += len(ck.get("후검증위반") or [])
            tot["종결어미줄"] += ck.get("종결어미줄") or 0
            tot["맺음상투구"] += 1 if ck.get("맺음상투구") else 0
            tot["요구구조불일치"] += 1 if ck.get("요구구조불일치") else 0
            tot["응답카드불일치"] += 1 if ck.get("응답카드불일치") else 0
    tot["초"] = round(tot["초"], 1)
    tot["비용USD"] = round(tot["비용USD"], 6)
    metrics = quantitative_metrics(
        attempts=tot["턴수"], duration_seconds=tot["초"], calls=tot["LLM호출"],
        prompt_tokens=tot["프롬프트토큰"], completion_tokens=tot["완성토큰"],
        total_tokens=tot["총토큰"], cached_tokens=tot["캐시토큰"],
        cost_usd=tot["비용USD"],
    )
    write_raw_result(out_path, {"model": MODEL, "simpleModel": SIMPLE_MODEL,
                                "promptVersion": PROMPT_VERSION, "evaluation": evaluation,
                                "metrics": metrics, "합계": tot, "시나리오": rows})
    print(json.dumps(tot, ensure_ascii=False), flush=True)
    print(f"→ {out_path}", flush=True)


if __name__ == "__main__":
    run()
