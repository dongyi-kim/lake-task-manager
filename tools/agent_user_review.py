# tools/agent_user_review.py — **사용자 관점 평가자**(실 LLM, 수동 실행 전용).
#
# 왜 만들었나 (사용자 지적, 2026-08-10):
#   "직접 답변 품질을 검토하라고 한 게 몇 번째인데 이런 것들이 필터링이 안 되고 내 테스트에서
#    발견되는지 모르겠다. user agent 를 만들어서 진짜 사람 이용자의 관점에서 평가해야 한다."
#
# 정확한 진단이다. 기존 배터리(agent_scenarios·agent_create_suite)는 **계약**을 검사한다 —
# 계약에는 **내가 이미 예상한 실패**만 들어 있다. 그래서 41/41 초록인 채로
#   · Sub-Task 이름이 "설계 단계 / 구현 단계 / 검증 단계"
#   · 미리보기 상위에 "DL-102" 만 (무슨 Epic 인지 모름)
#   · 코멘트 남기는 요청에 "완료 조건이 무엇인가요"
# 같은 것들이 통과했다. 전부 사용자가 먼저 발견했다.
#
# 이 도구가 다른 점 넷:
#   ① **계약을 안 준다** — 케이스가 무엇을 재려 했는지 모른 채 결과만 본다
#   ② **산출물 전체**를 준다 — 답변 문장 + 초안 항목·자식·카드 필드·질문 보기
#   ③ **사실 대조와 렌더 진단을 코드가 계산해** 함께 준다 — 텍스트만 보고는 "참조가 진짜인가",
#      "화면에서 깨지지 않는가"를 판정할 수 없다(사용자 지적 축 ③⑤)
#   ④ 점수를 안 매긴다. **불평을 받는다** — 점수는 다음 행동을 안 바꾸지만 불평은 고칠 것을
#      바로 가리킨다
#
# 실행: python -X utf8 tools/agent_user_review.py [모델] [흐름ID …]
#       결과: docs/agent-user-review.md
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")
os.environ["LAKE_AGENT_PROVIDER"] = "openai"
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
MODEL = _args[0] if _args and not _args[0].isupper() else "gpt-4o-mini"
ONLY = {a for a in _args if a.isupper()}
os.environ["LAKE_AGENT_OPENAI_CHAT"] = MODEL
os.environ.setdefault("LAKE_AGENT_OPENAI_CHAT_SIMPLE", MODEL)

from app.agent.workflow import session          # noqa: E402

# ── 실사용 흐름 — 배터리 케이스가 아니라 **사람이 실제로 하는 일**의 묶음이다.
#    여러 턴이 이어지고 초안·카드까지 간다(거기가 사람이 판단하는 자리다).
FLOWS = [
    ("F1", "새 기능을 티켓으로 (인터뷰 → 구조 → 초안)", "실무", [
        "우리 기존 etl 파이프라인에 iceberg puffin ndv 통계정보를 생성하는 기능을 추가구현하고 싶어",
        "1차 목표는 PoC. 배경은 StarRocks 4.1.1 QueryQueueV2 의 Estimation 성능 개선. "
        "완료 조건은 Lake 내 Iceberg 배치적재 테이블에 통계 생성 Batch Job 구현. "
        "단계별 Sub-Task 로. Epic 은 DL-102. 알아서",
    ]),
    ("F2", "데이터 자산 히스토리 파악", "PMO", [
        "fdc_flat_summary_ic 데이터에 대해 히스토리 정리",
        "fdc.fdc_trace_summary_ic",
    ]),
    ("F3", "사람이 무슨 일을 들고 있는지", "PMO", [
        "이다은 책임이 지금 맡고 있는 일 알려줘",
    ]),
    ("F4", "정체 티켓에 일괄 코멘트", "PMO", [
        "ETL 모듈에서 3개월 이상 업데이트 없는 티켓 전부에 담당자를 멘션해서 "
        "상태 점검을 요청하는 코멘트를 남겨줘",
    ]),
    ("F5", "버그 신고", "실무", [
        "리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다. 크롬에서 재현되고 기대는 그래프가 "
        "그려지는 것. 버그로 올려줘. 알아서",
    ]),
    ("F6", "내 일 추천", "실무", [
        "지금 무슨 업무를 시작해야 할까",
    ]),
    ("F7", "우리 모듈 최근 7일", "PMO", [
        "우리 모듈의 최근 7일 업무 내역이 궁금해",
    ]),
    ("F8", "티켓 진척 파악", "PMO", [
        "DL-9090 지금 어디까지 진행됐어?",
    ]),
]

# ── 페르소나 — **한 사람이 아니다**(사용자 지적). 보는 눈이 다르면 다르게 걸린다.
PERSONAS = {
    "PMO": "너는 Lake 데이터플랫폼 팀의 **PMO 담당자**다. 진척·배치·집계로 본다. "
           "티켓이 보고 단위로 말이 되는가, 승인해도 되는가를 판단한다. 개발자가 아니다.",
    "실무": "너는 Lake 데이터플랫폼의 **실무 엔지니어**다. 이 결과를 받아 **오늘 당장** "
            "무엇을 하는지로 본다. 티켓을 열자마자 손을 댈 수 있어야 쓸모가 있다.",
}

AXES = """평가 축은 일곱이다. 각 축을 **실제 출력에 비추어** 본다.

1. **요청 컨텍스트에 맞는가** — 내가 말한 것에 맞는 결과인가. 필요한 것을 **적절히 물어서**
   좁혔는가. 안 물어서 엉뚱하지 않은가, **이미 답한 걸 또 묻지** 않는가, 이 갈래에 무의미한
   것을 묻지 않는가(예: 코멘트 남기는 일에 "완료 조건이 무엇인가요").
2. **생략·누락·할루시네이션** — 빠진 것이 없나. 근거와 참조가 **충분한가**. 자료에 없는
   말을 지어내지 않았나. 알 수 있는 것을 "확인된 기록 없음"으로 넘기지 않았나.
3. **참조·근거가 사실인가** — 아래 [사실 대조]가 코드로 맞춰 본 결과다. 날조 키·틀린
   제목이 하나라도 있으면 **blocker**.
4. **사람이 이해할 수 있는가** — 읽고 뜻이 통하는가. 키만 있고 이름이 없지 않은가
   (DL-102 만으로는 무슨 Epic 인지 모른다).
5. **렌더링 문제** — 아래 [렌더 진단]을 보라. 링크 안 걸린 참조, 깨진 표, 닫히지 않은
   괄호·마커는 화면에서 그대로 깨져 보인다.
6. **어체와 형식** — 간결·효율적인가. 정보 전달인데 "…입니다/…합니다"로 늘어지지 않는가
   (**종결어미 생략·두괄식·개조식**이 이 도구의 규칙이다). 나열할 것을 **불릿·표**로
   냈는가, 줄글로 뭉갰는가. 같은 말을 두 번 하지 않는가.
7. **실질적이고 의미 있는가** — 티켓 제목이 무슨 일인지 말해 주는가("설계 단계"는 아무것도
   안 알려준다). 권고가 구체적인가("점검하는 것이 좋습니다"는 다음 행동이 아니다).

**점수를 매기지 마라.** 불평을 항목으로:
  axis(1~7) · severity(blocker|annoying|nit) · what(화면에 나온 그대로 인용) ·
  why(그래서 내가 무엇을 못 하는지) · want(한 줄)
불평이 없으면 빈 배열. **실제 출력에 있는 것만** 짚어라 — 없는 문제를 지어내지 마라."""

# ── 이미 지적받은 것들 — **재발 감시 목록**(사용자 요청).
#    같은 지적을 두 번 받는 것이 가장 나쁘다. 사용자가 실제로 보고한 문장을 그대로 둔다.
REGRESSIONS = """아래는 **이 도구가 예전에 지적받아 고친 것들**이다. 하나라도 다시 보이면
반드시 불평으로 적어라(재발은 severity 를 한 단계 올린다).

[답변 구조]
- 이력을 물으면 **연표만** 내고 "그래서 지금 어떤가"가 없었다 → 현재 상태 + 연표 둘 다.
- 현재 상태를 **줄글**로 냈다 → 표로(`| 항목 | 값 | 근거 |`).
- 진행 중 Task 를 표 꼬리에 흘렸다 → **자기 제목**(`현재 진행 중인 Task`)을 달 것.
- 연표 '사건' 칸에 **티켓 제목만** 옮겼다 → 실제로 무슨 변동이 있었는지 적을 것.
- 진척을 물었는데 **완료된 것만** 말하고 진행 중인 자식을 빼먹었다. 심지어 그 일을
  "완료됐다"고 거꾸로 말했다.

[근거·참조]
- 참조가 **하이퍼링크가 아니라 평문**으로 나왔다.
- 참조 목록에 **같은 출처가 두 번** 나왔다.
- 값 옆 괄호에 티켓 키를 박아 넣어 참조 체계가 둘로 갈렸다 → 근거는 `[N]` 마커로.
- 참조는 **접히고**, 첫 줄 출처 / 둘째 줄 설명(작게 회색) 두 층이어야 한다.

[티켓 설계]
- Sub-Task 이름이 "설계 단계 / 구현 단계 / 검증 단계" — 무슨 일인지 알 수 없다.
- 시킨 일의 절반을 본문 '제외'에 적고 **티켓을 안 만들었다**.
- 초안이 **0건**인데 답변은 "카드에서 확인 후 승인해 주세요"라고 했다.
- 본문에 배경·작업 범위(포함/제외)·완료 조건이 빠졌다. 완료 조건이 "테스트 완료" 수준이라
  언제 끝나는지 알 수 없었다.
- 복합 산출물(여러 Task)을 본문까지 다 써서 한 번에 내밀었다 → **구조 먼저 합의**.

[사람·모듈]
- 사람 이름을 물었는데 "최근 3일 활동 기록이 없습니다"/"그 모듈 로스터에 없습니다"로
  답했다 — 실제로는 미완료 티켓 21건을 들고 있었다.
- 호칭이 붙은 이름("이다은 책임"·"김동이 M")을 못 찾았다.
- 동명이인인데 임의로 한 명을 골랐다 → 표시 이름+이메일로 확인받을 것.
- 개발용 UI 픽스처 티켓(`[UI]` 접두)이 실제 답변에 섞여 나왔다.

[화면]
- 승인 카드 미리보기에 **Sub-Task 목록이 없었다**(무엇이 함께 생기는지 모른 채 승인).
- 미리보기 상위에 **티켓 번호만** 있고 이름이 없었다.
- 뱃지에 상태가 이미 있는데 텍스트로 **또** 상태를 적었다.
- 담당자·일정을 지금 정하라고 막아섰다 → "나중에 직접 선택"이 있어야 한다."""

SCHEMA = {
    "title": "user_review",
    "type": "object",
    "properties": {
        "good": {"type": "string", "description": "잘된 점 한 줄(없으면 빈 문자열)"},
        "complaints": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "axis": {"type": "integer", "description": "평가 축 1~7"},
                "severity": {"type": "string", "enum": ["blocker", "annoying", "nit"]},
                "regression": {"type": "boolean",
                               "description": "예전에 지적받아 고친 것이 다시 보이는가"},
                "what": {"type": "string"},
                "why": {"type": "string"},
                "want": {"type": "string"}},
                "required": ["axis", "severity", "what", "why", "want"]},
        },
    },
    "required": ["complaints"],
}

_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\b")


def _facts(text: str) -> dict:
    """축 ③ — **참조가 사실인가**는 텍스트만 보고 판정할 수 없다. 코드가 실물과 맞춘다.

    grounding.check 가 날조 키·틀린 제목·없는 사람을 잡는다. 여기에 답변이 언급한 키의
    **실제 제목·상태**를 붙여 준다 — 평가자가 "이 키가 정말 그 티켓인가"를 눈으로 대조한다.
    """
    out = {}
    try:
        from app.agent.workflow.grounding import check
        r = check(text) or {}
        # ★ **깨끗하면 '이상 없음' 한 줄로 말한다.** 예전엔 빈 배열을 그대로 실었는데,
        #   평가자가 `"날조된 키": []` 를 보고 "검증이 안 됐다"며 blocker 를 매겼다
        #   (첫 실행 blocker 6건 중 3건이 이 오탐이었다). 빈 그릇은 사람에게도 모델에게도
        #   "없음"이 아니라 "모름"으로 읽힌다 — 판정 결과는 **문장으로** 줘야 한다.
        bad = {k: v for k, v in (("날조된 키", r.get("fake_keys") or []),
                                 ("제목이 틀린 키", r.get("wrong_titles") or {}),
                                 ("없는 사람", r.get("fake_people") or [])) if v}
        out["코드 검증"] = bad or "이상 없음 — 답변의 티켓 키·제목·인명이 실물과 일치한다"
    except Exception as e:
        out["코드 검증"] = {"오류": str(e)[:120]}
    try:
        from app.agent.tools._ctx import client
        c = client()
        real = {}
        for k in list(dict.fromkeys(_KEY_RE.findall(text or "")))[:12]:
            f = (c.get_issue(k) or {}).get("fields") or {}
            real[k] = {"실제 제목": f.get("summary"),
                       "상태": (f.get("status") or {}).get("name")} if f else "존재하지 않음"
        # 대조용 참고 자료지 결함 목록이 아니다 — 이름으로 그것을 분명히 한다.
        out["[참고] 언급된 키의 실제 제목·상태 (대조용)"] = real or "언급된 티켓 키 없음"
    except Exception as e:
        out["[참고] 언급된 키의 실제 제목·상태 (대조용)"] = {"오류": str(e)[:120]}
    return out


def _render_check(text: str) -> dict:
    """축 ⑤ — **화면에서 깨지는가**. 평가자는 JSON 을 보므로 렌더 문제를 못 본다.

    화면이 실제로 하는 일(마크다운 렌더)의 실패 조건을 코드가 대신 본다:
    링크 안 걸린 참조 · 열만 어긋난 표 · 닫히지 않은 대괄호 · 가리키는 곳 없는 [N] 마커.
    """
    t = text or ""
    diag = {}
    try:
        from app.agent.workflow.grounding import _unlinked_refs
        diag["출처를 확인할 수 없는 참조 줄"] = _unlinked_refs(t)
    except Exception:
        pass
    # 표 — 헤더와 본문 열 수가 다르면 화면에서 칸이 밀린다
    bad_rows = []
    for blk in re.findall(r"(?:^\|.*\|\s*$\n?)+", t, re.M):
        rows = [r for r in blk.strip().splitlines() if r.strip().startswith("|")]
        if len(rows) < 2:
            continue
        w = rows[0].count("|")
        bad_rows += [r[:60] for r in rows[2:] if r.count("|") != w]
    diag["열 수가 어긋난 표 줄"] = bad_rows[:5]
    # [N] 마커가 가리키는 참조가 실제로 있는가
    marks = sorted({m for m in re.findall(r"\[(\d{1,2})\](?!\()", t)})
    listed = sorted({m for m in re.findall(r"^\s*(?:\[(\d{1,2})\]|(\d{1,2})[.)])\s+", t, re.M)
                     for m in m if m})
    diag["가리키는 참조가 없는 [N] 마커"] = [m for m in marks if m not in listed]
    diag["닫히지 않은 대괄호가 있는 줄"] = [
        ln[:60] for ln in t.splitlines() if ln.count("[") > ln.count("]")][:5]
    return {k: v for k, v in diag.items() if v}


def _seen(out):
    """사용자가 **화면에서 실제로 보는 것** 전부 — 답변 문장만 주면 반쪽이다.

    초안 항목·자식·카드 필드·질문 보기가 다 화면에 있고 사람은 그걸 보고 판단한다.
    (Sub-Task 이름이 '설계 단계'인 것도, 상위에 키만 있는 것도 여기서 보인다.)
    """
    p = out.get("pending") or {}
    items = p.get("items") or out.get("draft_items") or []
    reply = out.get("reply") or ""
    view = {
        "화면에 보인 답변": reply,
        "질문 폼": [{"질문": q.get("question"), "보기": q.get("options") or []}
                     for q in (out.get("questions") or [])],
    }
    if items:
        view["승인 카드 — 만들 티켓"] = [
            {"타입": i.get("type"), "제목": i.get("summary"), "상위": i.get("epic"),
             "모듈": (i.get("components") or [None])[0], "담당": i.get("assignee"),
             "마감": i.get("duedate"), "본문": (i.get("description") or "")[:700],
             "Sub-Task": [{"제목": c.get("summary"), "담당": c.get("assignee")}
                          for c in (i.get("children") or [])]}
            for i in items[:6]]
    if p.get("keys"):
        view["승인 카드 — 일괄 대상"] = p["keys"]
        view["티켓별 코멘트"] = p.get("comments") or p.get("comment")
    if reply.strip():
        view["[사실 대조] (코드가 실물과 맞춰 본 것)"] = _facts(reply)
        rc = _render_check(reply)
        if rc:
            view["[렌더 진단] (화면에서 깨지는 자리)"] = rc
    return view


def review(persona_key, turns):
    from app.agent import config as C
    tid, outs = "", []
    t0 = time.time()
    for q in turns:
        o = session.ask(q, thread_id=tid)
        tid = o["thread_id"]
        outs.append(o)
    convo = [{"내가 한 말": q, "도구가 보여 준 것": _seen(o)} for q, o in zip(turns, outs)]
    llm = C.get_llm(temperature=0.3).with_structured_output(SCHEMA)
    system = PERSONAS[persona_key] + "\n\n" + AXES + "\n\n" + REGRESSIONS
    r = llm.invoke([("system", system),
                    ("user", "방금 이 도구를 이렇게 써 봤다:\n\n"
                             + json.dumps(convo, ensure_ascii=False, indent=1)[:16000])]) or {}
    return r, convo, time.time() - t0


if __name__ == "__main__":
    rows, md = [], ["# 사용자 관점 리뷰 — 실사용 흐름별 불평 목록", ""]
    md += ["> 계약을 주지 않고 화면에 보인 것만 보고 **사람처럼** 평가한 결과.",
           "> 배터리(계약)는 내가 예상한 실패만 잡는다 — 여기는 **예상 못 한 것**을 잡는 자리다.",
           "> 축 ③(사실)·⑤(렌더)는 코드가 실물과 맞춰 본 결과를 함께 준다.", ""]
    for fid, desc, persona, turns in FLOWS:
        if ONLY and fid not in ONLY:
            continue
        try:
            r, convo, secs = review(persona, turns)
        except Exception as e:
            print(f"✗ {fid} {desc}: 예외 {str(e)[:140]}")
            continue
        cs = r.get("complaints") or []
        blk = [c for c in cs if c.get("severity") == "blocker"]
        reg = [c for c in cs if c.get("regression")]
        print(f"{'✗' if blk else '·'} {fid} [{persona}] {desc}: 불평 {len(cs)}건"
              f"(blocker {len(blk)} · 재발 {len(reg)}) · {secs:.0f}s")
        for c in cs:
            print(f"    축{c.get('axis')} [{c.get('severity')}]"
                  f"{' ★재발' if c.get('regression') else ''} {str(c.get('what'))[:100]}")
        rows.append((fid, cs))
        md += [f"## {fid} [{persona}] — {desc}", ""]
        if r.get("good"):
            md += [f"잘된 점: {r['good']}", ""]
        for c in cs:
            md += [f"### 축{c.get('axis')} [{c.get('severity')}]"
                   + (" ★재발" if c.get("regression") else "") + f" {c.get('what')}",
                   f"- **왜 문제인가**: {c.get('why')}",
                   f"- **이렇게 나왔으면**: {c.get('want')}", ""]
        md += ["<details><summary>대화 전문 · 사실 대조 · 렌더 진단</summary>", "",
               "```json", json.dumps(convo, ensure_ascii=False, indent=1)[:10000], "```",
               "</details>", ""]
    tot = sum(len(c) for _f, c in rows)
    blk = sum(1 for _f, cs in rows for c in cs if c.get("severity") == "blocker")
    reg = sum(1 for _f, cs in rows for c in cs if c.get("regression"))
    print(f"\n불평 {tot}건 · blocker {blk} · 재발 {reg} — docs/agent-user-review.md 를 읽을 것")
    io.open("docs/agent-user-review.md", "w", encoding="utf-8", newline="\n").write("\n".join(md))
