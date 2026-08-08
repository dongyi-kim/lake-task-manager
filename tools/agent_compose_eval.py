# tools/agent_compose_eval.py — Editor AI(compose) 검증 배터리 (실 LLM, 수동 실행).
#
# 실행: python -X utf8 tools/agent_compose_eval.py [모델] [케이스ID ...]
#
# 검증 축(사용자 지시):
#   ① 문맥 적합성 — 티켓 컨텍스트(제목·코멘트·문서)에 맞는 Description/Comment 인가
#   ② 모호 피드백 — 정보가 부족하면 지어내지 말고 NEED_INFO(보완 요청)로 답하는가
#   ③ 시드 활용 — 쓰던 글의 말투·내용을 이어 쓰는가 / 형식(멘션·뱃지·4섹션)을 지키는가
#
# 체커는 최소선이고, 출력 전문을 사람이 읽는 것이 최종 게이트다(battery green ≠ 품질).
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

from app.agent import compose as CP  # noqa: E402


def _txt(html):
    return re.sub(r"<[^>]+>", " ", html or "")


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
     lambda r: r.get("ok") and ("p95" in r["html"] or "성능 측정" in _txt(r["html"]))),

    ("CMP4", "모호 — 새 티켓·빈 시드·내용 없는 지시어는 보완 요청(NEED_INFO)이 정답", dict(
        ticket_key="", kind="description", prompt="잘 좀 써줘"),
     lambda r: (not r.get("ok")) and r.get("needsInfo")
     and any(w in (r.get("error") or "") for w in ("무엇", "어떤", "대상", "목적", "알려", "적어"))),

    ("CMP5", "모호하지 않음 — 티켓 맥락이 있으면 짧은 프롬프트로도 쓴다(과잉 NEED_INFO 금지)", dict(
        ticket_key="DL-9090", kind="comment", prompt="상태 공유"),
     lambda r: r.get("ok"), ),

    ("CMP6", "멘션·키 — 담당 멘션은 뱃지 마크업, 키는 앵커(뱃지 렌더)로", dict(
        ticket_key="DL-9090", kind="comment",
        prompt="담당자를 멘션해서 성능 측정 결과 검토 요청 코멘트 써줘"),
     lambda r: r.get("ok") and ('data-type="mention"' in r["html"] or "@" in _txt(r["html"]))),

    ("CMP7", "무관 요청 — 티켓과 무관한 글은 짓지 말고 보완 요청 또는 맥락 확인", dict(
        ticket_key="DL-9090", kind="comment", prompt="김치찌개 레시피 알려줘"),
     lambda r: (not r.get("ok") and r.get("needsInfo"))
     or (r.get("ok") and "레시피" not in _txt(r["html"]))),
]


if __name__ == "__main__":
    hits, run = 0, [c for c in CASES if not ONLY or c[0] in ONLY]
    for cid, desc, kw, check in run:
        t0 = time.time()
        try:
            r = CP.compose(**{("ticket_key" if k == "ticket_key" else k): v for k, v in kw.items()})
            ok = bool(check(r))
        except Exception as e:
            print(f"✗ {cid} {desc}: 예외 {str(e)[:140]}")
            continue
        print(f"{'✓' if ok else '✗'} {cid} {desc}: {time.time()-t0:.0f}s")
        body = (r.get("html") or r.get("error") or "")
        print(f"    {'html' if r.get('ok') else 'resp'}: {body[:260]}")
        hits += 1 if ok else 0
    print(f"\n{hits}/{len(run)} 통과")
