# tools/agent_compose_eval.py — Editor AI(compose) 검증 배터리 (실 LLM, 수동 실행).
#
# 실행: python -X utf8 tools/agent_eval_launcher.py compose [모델] [케이스ID ...] [--out 결과.json]
#
# 검증 축(사용자 지시):
#   ① 문맥 적합성 — 티켓 컨텍스트(제목·코멘트·문서)에 맞는 Description/Comment 인가
#   ② 모호 피드백 — 정보가 부족하면 지어내지 말고 NEED_INFO(보완 요청)로 답하는가
#   ③ 시드 활용 — 쓰던 글의 말투·내용을 이어 쓰는가 / 형식(멘션·뱃지·4섹션)을 지키는가
#
# 체커는 최소선이고, 출력 전문을 사람이 읽는 것이 최종 게이트다(battery green ≠ 품질).
import json
import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.agent_scenario_eval import validate_eval_argv  # noqa: E402

# 사람이 없는 실행이다 — 설정 화면의 확인 게이트를 면제한다(config._env_supplied).
_raw_args = list(sys.argv[1:])
if __name__ == "__main__":
    validate_eval_argv(_raw_args)
OUT = None
for i, arg in enumerate(_raw_args):
    if arg.startswith("--out="):
        OUT = arg.split("=", 1)[1]
    elif arg == "--out" and i + 1 < len(_raw_args):
        OUT = _raw_args[i + 1]
_args = [a for i, a in enumerate(_raw_args)
         if not a.startswith("-") and not (i and _raw_args[i - 1] == "--out")]
MODEL = _args[0] if _args and not _args[0].isupper() else "gpt-4o-mini"
ONLY = {a for a in _args if a.isupper()}
SIMPLE_MODEL = os.environ.get("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")

from tools.agent_eval_protocol import (build_run_metadata, quantitative_metrics,
                                       raw_result_path, reserve_raw_result_path,
                                       write_raw_result)  # noqa: E402
from tools.agent_eval_contracts import (  # noqa: E402
    EDITOR_RENDERER_CONTRACT_DEPENDENCIES,
    editor_renderer_contract_flaws,
)
from tools.agent_eval_isolation import (begin_case, configure_process_isolation,
                                         finish_case,
                                         preflight_evaluation_provider)  # noqa: E402
from tools.agent_eval_review_specs import review_specs  # noqa: E402
try:  # 과거 prompt variant commit에도 같은 하네스를 적용한다.
    from app.agent.prompts.base import PROMPT_VERSION  # noqa: E402
except ImportError:  # legacy asset에는 version 상수가 없었다.
    PROMPT_VERSION = os.getenv("LAKE_AGENT_PROMPT_VERSION", "legacy")

# v4.0.0 inherits the common hidden-draft/user-visible approval boundary.
BATTERY_VERSION = "4.0.0"
SUITE_REVIEW_ELEMENTS, CASE_REVIEW_SPECS = review_specs("editor")
CP = None


def _prepare_runtime():
    """Configure the live battery only when executed, never when imported by tests."""
    global CP
    configure_process_isolation("editor")
    os.environ.setdefault("JIRA_ENV", "mock")
    os.environ.setdefault("LAKE_AGENT_PROVIDER", "openai")
    os.environ["LAKE_AGENT_SKIP_VERIFY"] = "1"
    os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")
    from tools.agent_scenario_eval import configure_model_routing
    configure_model_routing(MODEL, SIMPLE_MODEL)
    preflight_evaluation_provider()
    from app.agent import editor_author
    CP = editor_author


def _txt(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _seed_preserved(result, seed_html):
    """이어쓰기 결과에는 사용자가 이미 쓴 visible text가 그대로 남아야 한다."""
    seed = _txt(seed_html)
    return bool(result.get("ok") and seed and seed in _txt(result.get("html")))


def _editor_contract_flaws(result):
    """사람 판독에서 발견된 reference resolution·renderer 계약 회귀."""
    return editor_renderer_contract_flaws(result)


def _redact_diagnostic_text(value):
    """Remove common credential forms before an exception reaches raw evidence."""
    text = str(value or "")
    text = re.sub(
        r"(?i)\b(https?://)[^/@\s:]+(?::[^/@\s]*)?@",
        r"\1<redacted>@",
        text,
    )
    text = re.sub(
        r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|password|secret)=)[^&\s]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}",
        r"\1 <redacted>",
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-<redacted>", text)
    text = re.sub(
        r'''(?ix)\b(authorization|api[-_ ]?key|access[-_ ]?token|password|secret)\b
            (\s*[=:]\s*|["']\s*:\s*["'])
            (?!<redacted>)[^\s,;"'}]+''',
        lambda match: match.group(1) + match.group(2) + "<redacted>",
        text,
    )
    return text


def _exception_diagnostic(stage, exc):
    """Keep a stage-labelled traceback without frame locals in ignored raw evidence."""
    rendered = "".join(traceback.TracebackException.from_exception(
        exc, capture_locals=False).format())
    return {
        "stage": str(stage or "unknown"),
        "exceptionType": type(exc).__name__,
        "message": _redact_diagnostic_text(str(exc))[:500],
        "traceback": _redact_diagnostic_text(rendered)[-8000:],
    }


EDITOR_CHECKER_DEPENDENCIES = (
    *EDITOR_RENDERER_CONTRACT_DEPENDENCIES,
    _txt, _seed_preserved, _editor_contract_flaws,
)


# (ID, 설명, kwargs, 체커(result))
CASES = [
    ("CMP1", "코멘트 — 진행 중 티켓의 진행 보고(맥락: DL-9090 최근 코멘트를 이어받아야)", dict(
        ticket_key="DL-9090", kind="comment", prompt="진행 상황 공유 코멘트 써줘"),
     lambda r: r.get("ok") and "DL-9090" not in _txt(r["html"])[:0]  # 항상 참 — 아래 실질 체크
     and any(w in _txt(r["html"]) for w in ("진행", "완료", "예정"))
     and len(_txt(r["html"]).strip()) > 40),

    ("CMP2", "본문 — 기존 티켓의 본문 보강(4섹션 구조 준수)", dict(
        ticket_key="DL-9095", kind="description",
        prompt="이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘"),
     lambda r: r.get("ok") and all(w in r["html"] for w in ("배경", "완료 조건"))
     and "Knowledge" not in r["html"] and "References" not in r["html"]),

    ("CMP3", "시드 이어쓰기 — 쓰던 글의 내용을 버리지 않는다", dict(
        ticket_key="DL-9090", kind="comment", prompt="이어서 완성해줘",
        seed_html="<p>오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다</p>"),
     lambda r: (_seed_preserved(
         r, "<p>오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다</p>")
         and "확인 필요" in _txt(r.get("html") or "")
         and not re.search(r"생각보다\s*(?:높|낮)", _txt(r.get("html") or "")))),

    ("CMP4", "모호 — 새 티켓·빈 시드·내용 없는 지시어는 보완 요청(NEED_INFO)이 정답", dict(
        ticket_key="", kind="description", prompt="잘 좀 써줘"),
     lambda r: (not r.get("ok")) and r.get("needsInfo")
     and any(w in (r.get("error") or "") for w in ("무엇", "어떤", "대상", "목적", "알려", "적어"))),

    ("CMP5", "상태 공유 — 짧은 프롬프트로 쓰되 명시적 미완료를 완료로 뒤집지 않는다", dict(
        ticket_key="DL-9090", kind="comment", prompt="상태 공유"),
     lambda r: r.get("ok")
     and not re.search(r"성능\s*측정.{0,12}완료(?:되|됐|했|함|됨)", _txt(r["html"]))),

    ("CMP6", "멘션·키 — 담당 멘션은 뱃지 마크업, 키는 앵커(뱃지 렌더)로", dict(
        ticket_key="DL-9090", kind="comment",
        prompt="담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘"),
     lambda r: (r.get("ok") and 'data-type="mention"' in r["html"]
                and all(value in _txt(r["html"]) for value in ("2홉", "100 노드"))
                and "confluence" in r["html"].lower())),

    ("CMP7", "무관 요청 — 티켓과 무관한 글은 짓지 말고 보완 요청 또는 맥락 확인", dict(
        ticket_key="DL-9090", kind="comment", prompt="김치찌개 레시피 알려줘"),
     lambda r: (not r.get("ok") and r.get("needsInfo"))
     or (r.get("ok") and "레시피" not in _txt(r["html"]))),

    # 댓글/본문 비대칭(사용자 지적): 댓글은 문맥 없으면 못 쓰고, 본문은 계보로 쓴다
    ("CMP8", "자식 있는 부모 본문 — '무엇을 왜'를 맡고 자식 제목을 반복하지 않는다", dict(
        ticket_key="DL-9090", kind="description",
        prompt="본문 정리해줘"),
     lambda r: r.get("ok") and "배경" in r["html"] and "작업 범위" in r["html"]
     # 자식 실행 세부(예: '그래프 렌더 컴포넌트' 자식 제목)를 DoD 로 그대로 나열하면 실패
     and _txt(r["html"]).count("컴포넌트") <= 2),

    ("CMP9", "본문은 짧은 프롬프트여도 티켓 맥락으로 쓴다(코멘트보다 관대)", dict(
        ticket_key="DL-9095", kind="description", prompt="본문 써줘"),
     lambda r: r.get("ok") and "배경" in r["html"]),
]


def _summary(records, hits):
    usage = {"calls": 0, "promptTokens": 0, "completionTokens": 0,
             "totalTokens": 0, "cachedTokens": 0, "costUsd": 0.0}
    for record in records:
        current = ((record.get("결과") or {}).get("usage") or {})
        for key in ("calls", "promptTokens", "completionTokens", "totalTokens",
                    "cachedTokens"):
            usage[key] += current.get(key) or 0
        usage["costUsd"] += current.get("costUsd") or 0
    usage["costUsd"] = round(usage["costUsd"], 6)
    total = {"통과": hits, "전체": len(records),
             "초": round(sum(record["초"] for record in records), 1), **usage}
    metrics = quantitative_metrics(
        attempts=len(records), duration_seconds=total["초"], calls=usage["calls"],
        prompt_tokens=usage["promptTokens"], completion_tokens=usage["completionTokens"],
        total_tokens=usage["totalTokens"], cached_tokens=usage["cachedTokens"],
        cost_usd=usage["costUsd"],
    )
    return total, metrics


if __name__ == "__main__":
    _prepare_runtime()
    hits, records = 0, []
    run = [c for c in CASES if not ONLY or c[0] in ONLY]
    evaluation = build_run_metadata(
        suite="editor",
        battery_version=BATTERY_VERSION,
        cases=CASES,
        selected_case_ids=[case[0] for case in run],
        model=MODEL,
        simple_model=SIMPLE_MODEL,
        prompt_version=PROMPT_VERSION,
        suite_review_elements=SUITE_REVIEW_ELEMENTS,
        case_review_specs=CASE_REVIEW_SPECS,
        checker_dependencies=EDITOR_CHECKER_DEPENDENCIES,
    )
    OUT = str(reserve_raw_result_path(
        raw_result_path("editor", evaluation, requested=OUT),
    ))
    for cid, desc, kw, check in run:
        isolation_start = begin_case(cid)
        t0 = time.time()
        isolation = {}
        stage = "compose"
        try:
            r = CP.compose(**{("ticket_key" if k == "ticket_key" else k): v for k, v in kw.items()})
            stage = "renderer_contract"
            flaws = _editor_contract_flaws(r)
            stage = "case_contract"
            ok = bool(check(r) and not flaws)
            elapsed = round(time.time() - t0, 1)
            stage = "isolation_finish"
            isolation = finish_case(isolation_start)
        except Exception as e:
            diagnostics = [_exception_diagnostic(stage, e)]
            safe_message = diagnostics[0]["message"]
            try:
                isolation = finish_case(isolation_start)
            except Exception as isolation_error:
                diagnostics.append(_exception_diagnostic("isolation_finish", isolation_error))
            print(f"✗ {cid} {desc}: {stage} {type(e).__name__}: {safe_message[:120]}")
            records.append({"id": cid, "설명": desc, "입력": kw, "통과": False,
                            "초": round(time.time() - t0, 1), "오류": safe_message,
                            "진단": diagnostics, "격리": isolation})
            total, metrics = _summary(records, hits)
            write_raw_result(OUT, {"model": MODEL, "simpleModel": SIMPLE_MODEL,
                                   "promptVersion": PROMPT_VERSION, "evaluation": evaluation,
                                   "metrics": metrics, "합계": total, "케이스": records,
                                   "checkpoint": {"complete": False,
                                                  "completedCases": len(records),
                                                  "selectedCases": len(run)}})
            continue
        print(f"{'✓' if ok else '✗'} {cid} {desc}: {elapsed:.0f}s")
        body = (r.get("html") or r.get("error") or "")
        print(f"    {'html' if r.get('ok') else 'resp'}: {body[:260]}")
        if flaws:
            print(f"    계약 결함: {' / '.join(flaws)}")
        records.append({"id": cid, "설명": desc, "입력": kw, "통과": ok,
                        "계약결함": flaws, "초": elapsed, "결과": r, "격리": isolation})
        hits += 1 if ok else 0
        total, metrics = _summary(records, hits)
        write_raw_result(OUT, {"model": MODEL, "simpleModel": SIMPLE_MODEL,
                               "promptVersion": PROMPT_VERSION, "evaluation": evaluation,
                               "metrics": metrics, "합계": total, "케이스": records,
                               "checkpoint": {"complete": False,
                                              "completedCases": len(records),
                                              "selectedCases": len(run)}})
    print(f"\n{hits}/{len(run)} 통과")
    total, metrics = _summary(records, hits)
    write_raw_result(OUT, {"model": MODEL, "simpleModel": SIMPLE_MODEL,
                           "promptVersion": PROMPT_VERSION, "evaluation": evaluation,
                           "metrics": metrics, "합계": total, "케이스": records,
                           "checkpoint": {"complete": True,
                                          "completedCases": len(records),
                                          "selectedCases": len(run)}})
    print(f"→ {OUT}")
