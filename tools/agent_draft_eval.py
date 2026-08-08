# tools/agent_draft_eval.py — 복합 생성 시나리오 평가 (실 LLM, 수동 실행 전용).
#
# 실행: python -X utf8 tools/agent_draft_eval.py [모델]
#
# 시나리오: 기술 현황 질의 → 새 작업 필요 발견 → 태스크 초안 → 기존 Epic 선정 →
#           Sub-Task 분할(기능 분화 + 단순 반복 분량 분할) → 담당 배분
#
# 평가하는 것은 **답변 문장이 아니라 초안(draft) 자체**다. 사용자가 승인 카드에서 보는 것이
# 곧 티켓이 되므로, 본문·역할분리·참조가 거기서 결정된다.
#   정량(코드) — 구조/에픽 실재/본문 섹션/참조 키 실재/배분 규율
#   정성(judge) — ① 본문 작성 가이드 준수 ② Task↔Sub-Task 역할분리 ③ 참조 선정 적절성
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-4o-mini"
os.environ["LAKE_AGENT_OPENAI_CHAT"] = MODEL
os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")

from app.agent.tools._ctx import client            # noqa: E402
from app.agent.workflow import session             # noqa: E402

TURNS = [
    # ① 특정 기술의 현황 질의
    "Schema Registry 지금 우리 어떻게 쓰고 있고 아직 안 끝난 건 뭐야?",
    # ② 현황에서 새 작업 필요가 드러남 — 기능 분화 + 단순 반복이 섞인 규모
    "그럼 남은 토픽 3개 Avro 전환을 마무리하는 작업을 만들자. 전환 전에 스키마 호환성 "
    "검증 스크립트도 하나 필요하고, 전환 후 컨슈머 모니터링도 붙여야 해. "
    "토픽별 전환은 단순 반복이라 사람 나눠서 하면 될 것 같아. 초안 잡아줘",
    # ③ 되묻기에 답 — 에픽은 기존 것 중에서 고르게 한다
    "기한은 2주 뒤까지고, 기존 에픽 중에 맞는 걸로 붙여줘. 나머지는 알아서 진행해",
]


def _items(o):
    return ((o.get("pending") or {}).get("items")) or []


# ── 정량: 코드가 판정한다 ──────────────────────────────────────────
def measure(items: list, outs: list) -> dict:
    c = client()
    m = {}
    m["n_items"] = len(items)
    subs = [i for i in items if (i.get("type") or "") == "Sub-Task"]
    tasks = [i for i in items if (i.get("type") or "") not in ("Sub-Task", "Epic")]
    m["n_task"], m["n_sub"] = len(tasks), len(subs)

    # ① Epic — 기존 것을 골랐고 실재하는가
    epics = {str(i.get("epic") or "") for i in items if i.get("epic")}
    m["epic"] = sorted(epics)
    m["epic_real"] = all(((c.get_issue(e) or {}).get("fields") or {}).get("issuetype", {})
                         .get("name") == "Epic" for e in epics) if epics else False

    # ② 본문 — 섹션 구성(배경/완료조건/참고)과 분량
    def body(i):
        return str(i.get("description") or "")
    m["empty_body"] = sum(1 for i in items if len(body(i).strip()) < 40)
    m["avg_body"] = round(sum(len(body(i)) for i in items) / max(1, len(items)))
    def has(i, *words):
        b = body(i)
        return any(w in b for w in words)
    m["with_why"] = sum(1 for i in items if has(i, "배경", "왜", "이유", "계기"))
    m["with_dod"] = sum(1 for i in items if has(i, "완료 조건", "완료조건", "Acceptance", "DoD",
                                                "완료 기준"))
    m["with_scope"] = sum(1 for i in items if has(i, "범위", "대상", "작업 내용", "할 일"))

    # ③ 참조 — 본문에 실재하는 티켓 키·문서 링크가 있는가
    keys = {k for i in items for k in re.findall(r"DL-\d+", body(i))}
    m["ref_keys"] = sorted(keys)
    m["ref_keys_real"] = [k for k in keys if (c.get_issue(k) or {}).get("key")]
    m["ref_keys_fake"] = sorted(keys - set(m["ref_keys_real"]))
    m["ref_docs"] = sum(1 for i in items if re.search(r"https?://|「|Confluence", body(i)))

    # ④ 배분 — 단순 반복(제목이 비슷한 묶음)은 골고루, 기능 분화는 모듈 적합자
    owners = [str(i.get("assignee") or "") for i in subs]
    m["sub_assigned"] = sum(1 for o in owners if o)
    m["distinct_owners"] = len({o for o in owners if o})
    top = max([owners.count(o) for o in set(owners) if o] or [0])
    m["max_per_owner"] = top
    from app.infra.settings import load_people
    roster = load_people() or {}
    known = {u for ids in roster.values() for u in ids or []}
    m["unknown_owners"] = sorted({o for o in owners if o and o not in known})

    m["questions_turns"] = sum(1 for o in outs if o.get("questions"))

    # ⑤ 구조 판단이 드러나 있는가 — 숨은 판단은 검토할 수 없다
    pend = outs[-1].get("pending") or {}
    m["structure"] = pend.get("structure") or ""
    m["structure_why"] = (pend.get("structure_why") or "")[:80]
    m["new_labels"] = pend.get("new_labels") or []

    # ⑥ 배치 — Epic 이 실재하는 Epic 인가, 담당의 모듈과 컴포넌트가 맞는가
    from app.infra.settings import load_people
    mod_of = {u: mod for mod, ids in (load_people() or {}).items() for u in (ids or [])}
    ok_epic, bad_epic, mod_ok, mod_bad = 0, [], 0, []
    for it in items:
        ek = str(it.get("epic") or "").strip()
        if ek:
            f = (c.get_issue(ek) or {}).get("fields") or {}
            if str((f.get("issuetype") or {}).get("name") or "") == "Epic":
                ok_epic += 1
            else:
                bad_epic.append(ek)
        comps = [str(x) for x in (it.get("components") or []) if str(x)]
        who = str(it.get("assignee") or "")
        if comps and who:
            if mod_of.get(who) == comps[0]:
                mod_ok += 1
            else:
                mod_bad.append(f"{who}@{comps[0]}")
    m["epic_ok"], m["epic_bad"] = ok_epic, bad_epic
    m["module_match"], m["module_mismatch"] = mod_ok, mod_bad
    m["multi_component"] = [i.get("summary", "")[:30] for i in items
                            if len(i.get("components") or []) > 1]
    return m


RUBRIC = (
    "너는 Jira 티켓 초안의 심사자다. 아래 초안(JSON)을 사내 규율에 비추어 4축으로 1~5점 채점하라.\n"
    "5=흠잡을 데 없음, 3=쓸 만하나 아쉬움, 1=실패. JSON 만 출력:\n"
    '{"body":n,"roles":n,"refs":n,"placement":n,'
    '"worst":"가장 큰 문제 한 문장","fix":"가장 먼저 고칠 것 한 문장"}\n'
    "— body(본문 작성 가이드: 첫 문단에 배경·왜, 작업 범위, 완료 조건이 있고, 제목은 무엇을 "
    "하는지 동사로 끝나며 제목만으로 구분되는가. 빈 본문·한 줄 본문은 1점)\n"
    "— roles(역할분리: 상위 Task 는 '무엇을 왜'를 담고 Sub-Task 는 '한 사람이 며칠 안에 끝낼 "
    "실행 단위'인가. Task 본문을 Sub-Task 가 그대로 베끼거나, Sub-Task 가 다시 쪼개야 할 만큼 "
    "크거나, 같은 일이 중복되면 감점)\n"
    "— refs(참조: 본문에 근거가 되는 실제 티켓 키·문서가 붙어 있고 그것이 이 일과 실제로 "
    "관련 있는가. 근거 없는 서술, 무관한 티켓 나열, 여러 항목에 같은 목록을 복붙하면 감점)\n"
    "— placement(배치: 각 티켓의 Epic·모듈(컴포넌트)·라벨이 그 일에 맞는가. 모듈이 다른 일을 "
    "한 티켓에 몰았거나, Epic 을 아무 데나 붙였거나, 같은 뜻의 라벨을 새로 만들었으면 감점. "
    "여러 Task 가 서로 다른 Epic 에 붙는 것은 정상이다)")


def judge(items, context):
    from app.agent import config as C
    try:
        out = C.get_llm(temperature=0, tier="simple").invoke([
            ("system", RUBRIC),
            ("user", f"### 사용자 요청\n{context}\n\n### 초안\n"
                     f"{json.dumps(items, ensure_ascii=False, indent=1)[:6000]}")])
        txt = str(getattr(out, "content", "") or "")
        mm = re.search(r"\{.*\}", txt, re.S)
        return json.loads(mm.group(0)) if mm else {}
    except Exception as e:
        return {"error": str(e)[:150]}


if __name__ == "__main__":
    t0, tid, outs = time.time(), "", []
    for q in TURNS:
        o = session.ask(q, thread_id=tid)
        tid = o["thread_id"]
        outs.append(o)
        print(f"[턴] {q[:40]}… → intent={o.get('intent')} "
              f"질문={len(o.get('questions') or [])} 초안={len(_items(o))}건")
    items = _items(outs[-1]) or next((_items(o) for o in reversed(outs) if _items(o)), [])
    m = measure(items, outs)
    j = judge(items, TURNS[1])
    print("\n── 정량 ──")
    for k, v in m.items():
        print(f"  {k}: {v}")
    print("\n── 정성(3축) ──")
    print(f"  본문 {j.get('body')} · 역할분리 {j.get('roles')} · 참조 {j.get('refs')}"
          f" · 배치 {j.get('placement')}")
    print(f"  worst: {j.get('worst')}")
    print(f"  fix:   {j.get('fix')}")
    usage = outs[-1].get("usage") or {}
    print(f"\n{time.time()-t0:.0f}s · ${round(sum((o.get('usage') or {}).get('costUsd', 0) or 0 for o in outs), 4)}")
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "draft_eval_last.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"model": MODEL, "turns": TURNS, "items": items, "measure": m, "judge": j,
                   "replies": [o.get("reply") for o in outs]}, fh, ensure_ascii=False, indent=1)
    print(f"저장: {out_path}")
