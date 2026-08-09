"""agent/workflow/grounding.py — 답변의 날조를 **코드가** 잡는다.

실측된 사고: 지도(실제 제목·참여자 사번)를 자료로 줬는데도 답변 단계에서 ① 존재하지 않는
티켓 키 ② 실제와 다른 제목("[ETL] Dashboard widget" → "데이터 처리 성능 개선") ③ 존재하지
않는 실명("PM: 김철수")을 만들어 냈다. 프롬프트로 세 번 막아 봤지만 재발했다 — 이 부류는
모델에게 부탁할 일이 아니라 **검증할 일**이다.

검사는 전부 결정적이다:
  · 티켓 키  — 답변에 등장한 모든 키를 `get_issue` 로 실재 확인(캐시를 타므로 싸다)
  · 제목     — "KEY (제목)" / "KEY: 제목" 꼴로 단정한 제목이 실제 summary 와 다르면 위반
  · 사람     — 역할 표기("PM: X", "담당: X", "X 님")의 한글 이름을 `search_users` 로 확인.
              화면 사번(skcc.xNNNN)은 항상 허용

위반이 나오면 호출자가 ① 위반 목록+실값을 주고 한 번 다시 쓰게 하고 ② 그래도 남으면
답변에 경고를 붙인다. 조용히 고치지 않는 이유: 무엇이 걸렀는지 사용자가 볼 수 있어야
시스템을 믿을 수 있다.
"""

from __future__ import annotations

import re

KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
# "PM: 김철수" / "담당자 - 김철수" / "김철수 님" — 역할·호칭 문맥의 2~4자 한글 이름만 본다.
# 아무 한글 3글자나 잡으면("중이며") 오탐 천지가 된다.
# ★ 답변은 마크다운이라 역할 낱말이 "**PM**:" 처럼 볼드로 감싸인다 — 구분자 앞의 *·_ 를
#   허용하지 않으면 정확히 그 꼴만 새어 나간다(실측: 첫 배포에서 전부 놓쳤다).
# 역할 낱말은 실측된 답변에서 계속 늘려 왔다 — "실무자: 이영희"가 목록에 없어 새어 나간 적 있다.
_ROLE = r"(?:PM|리더|담당자?|개발자|디자이너|리포터|작성자|검토자|매니저|QA|실무자|엔지니어|기획자|운영자)"
NAME_RE = re.compile(rf"{_ROLE}[\*_]*\s*[:\-–]\s*[\*_]*([가-힣]{{2,4}})\b|([가-힣]{{2,4}})\s*님\b")
UID_RE = re.compile(r"^[a-z]+\.[a-z]\d+$")
# 답변 속 사번 꼴 토큰 — 실재 검증 대상. NNNN 같은 자리표시자는 그 자체로 위반이다
# (재작성 지시문의 예시 표기를 답에 그대로 복사한 실측 사고).
UID_TOKEN_RE = re.compile(chr(92) + "b([a-z]{2,}" + chr(92) + ".[a-zA-Z]{1,2}[0-9N]{2,6})" + chr(92) + "b")
# "**DL-123**: 김철수" — 역할 낱말 없이 티켓키→사람 매핑으로 새는 변종(실측).
KEY_NAME_RE = re.compile(r"[A-Z][A-Z0-9]+-" + r"[0-9]+[*_]*\s*[:\-–]\s*[*_]*([가-힣]{2,4})(?=\s|$|[,.)—-])")
# 참조 인덱스 줄 — `[1] …`. responder.md 가 정한 형식이라 이 꼴만 본다.
REF_LINE_RE = re.compile(r"^\s*\[(\d{1,2})\]\s+(.+?)\s*$", re.M)
# 링크로 인정하는 것: 맨 URL 또는 마크다운 링크의 `](`.
LINKED_RE = re.compile(r"https?://|\]\(")


def _unlinked_refs(text: str) -> list:
    """참조 줄인데 **티켓 키도 링크도 없는** 것 — 검증할 방법이 없는 출처다.

    common.md: "NEVER drop a bare document title with no URL: an unlinked title cannot be
    verified and looks fabricated." 프롬프트가 두 곳(common·responder)에서 말하는데도
    실측으로 샜다: `[4] [데이터카탈로그] fdc_trace_summary_ic 테이블 특성 분석 — 적재 Job
    정보`. 재료에는 그 문서의 URL 이 실려 있었으므로 **쓸 수 있었는데 안 쓴 것**이다.

    본문 참고 불릿에는 이미 같은 가드가 있다(`refiner._drop_unlinked_refs`) — 답변 텍스트
    쪽에만 없었다. 같은 규칙이 두 산출물에 다 걸려야 한다.
    """
    out = []
    for m in REF_LINE_RE.finditer(text or ""):
        body = m.group(2)
        if KEY_RE.search(body) or LINKED_RE.search(body):
            continue
        out.append(f"[{m.group(1)}] {body[:60]}")
    return out


def check(reply: str) -> dict:
    """답변을 실물과 대조한다. 반환:
    {"fake_keys": [...], "wrong_titles": {key: 실제제목}, "fake_people": [...], "ok": bool}
    """
    from app.agent.tools._ctx import client, settings
    c = client()
    text = reply or ""

    import re as _re0
    fake_keys, real_titles = [], {}
    for key in dict.fromkeys(KEY_RE.findall(text)):        # 등장 순서 유지 + 중복 제거
        raw = c.get_issue(key) or {}
        if not raw.get("key"):
            # 답변이 그 키를 **미존재라고 말하는 중**이면 날조가 아니다 — "DL-90933 은
            # 존재하지 않습니다"에 '존재하지 않는 티켓' 경고를 붙이면 헛소리가 된다(실측).
            near = "".join(m.group(0) for m in
                           _re0.finditer(_re0.escape(key) + r"[^\n]{0,40}", text))
            if _re0.search(r"존재하지\s*않|없는\s*티켓|찾을\s*수\s*없|미존재", near):
                continue
            fake_keys.append(key)
        else:
            real_titles[key] = ((raw.get("fields") or {}).get("summary") or "").strip()

    # 제목 단정 검사 — "KEY: 제목" / "KEY (제목)" / "**KEY**: 제목" / **KEY "제목"** 꼴.
    # 따옴표 꼴이 우리 권장 표기인데 정작 검사에서 빠져 있었다(실측: modify 답변이
    # DL-9062 "리샘플링 기준 논의" 로 제목을 지어냈는데 통과).
    wrong_titles = {}
    for key, real in real_titles.items():
        if not real:
            continue
        claims = [mm.group(1).strip().rstrip(")").strip() for mm in
                  re.finditer(rf"{re.escape(key)}\**\s*[:(]\s*([^)\n**]{{4,80}})", text)]
        claims += [mm.group(1).strip() for mm in
                   re.finditer(rf"{re.escape(key)}\**\s*[\"“'']([^\"”'\n]{{4,80}})[\"”'']", text)]
        claims = [c for c in claims if c]
        if not claims:
            continue
        # 실제 제목의 핵심 토큰이 하나도 안 겹치면 다른 제목을 단정한 것으로 본다.
        # (요약·의역은 허용해야 하므로 완전 일치를 요구하지 않는다.)
        # 후보 표기 중 **하나라도** 겹치면 제목을 안 것이다 — 콜론 뒤 상태 서술
        # ("DL-9090: 현재 2/3 완료")이 첫 후보로 잡혀 정확한 따옴표 제목이 무시되던 오탐 방지.
        real_tokens = {t for t in re.split(r"[\s\[\]()\-—·/]+", real) if len(t) >= 2}
        claim_sets = [{t for t in re.split(r"[\s\[\]()\-—·/]+", c) if len(t) >= 2}
                      for c in claims]
        claim_sets = [cs for cs in claim_sets if cs]
        if real_tokens and claim_sets and not any(cs & real_tokens for cs in claim_sets):
            wrong_titles[key] = real

    fake_people = []
    try:
        from app.domain.search import search_users
        s = settings()
        seen = set()
        # 사번 꼴 토큰 검증 — 자리표시자(NNNN)는 즉시 위반, 그 외에는 실재 확인
        for m in UID_TOKEN_RE.finditer(text):
            uid = m.group(1)
            if uid in seen:
                continue
            seen.add(uid)
            if "N" in uid.split(".")[-1].upper() and any(ch == "N" for ch in uid):
                if "NN" in uid.upper():
                    fake_people.append(uid + " (자리표시자)")
                    continue
            # ★ 전체 id 가 **정확히** 실재해야 한다. 접미(x1001)만 검색하면 다른 실존
            #   사번(skcc.x1001)의 접미와 겹치는 날조(etl.x1001)가 통과한다(실측).
            hits = (search_users(c, s, uid, 5) or []) + \
                   (search_users(c, s, uid.split(".")[-1], 8) or [])
            if not any(str(u.get("id") or "") == uid for u in hits):
                fake_people.append(uid)
        # 역할 문맥 + 키→사람 매핑("**DL-123**: 김철수") 두 꼴 모두 본다 —
        # 후자는 역할 낱말이 제목 줄에만 있고 항목 줄엔 없어서 NAME_RE 가 놓쳤다(실측).
        names = [(m.group(1) or m.group(2) or "").strip() for m in NAME_RE.finditer(text)]
        names += [m.group(1).strip() for m in KEY_NAME_RE.finditer(text)]
        # 상태·시간 낱말은 사람이 아니다 — "DL-9090: 현재 2/3 완료" 의 '현재'가 인물로
        # 걸렸다(실측 오탐). 이 목록은 오탐이 관측될 때마다 늘린다.
        _NOT_NAMES = {"현재", "이번", "오늘", "내일", "진행", "완료", "지연", "마감",
                      "상태", "예정", "검토", "확인", "미정", "없음", "전체"}
        for name in names:
            if not name or name in seen or UID_RE.match(name) or name in _NOT_NAMES:
                continue
            seen.add(name)
            if not (search_users(c, s, name) or []):
                fake_people.append(name)
    except Exception:
        pass          # 사람 검증이 안 되는 환경이면 키 검사만으로 간다

    unlinked_refs = _unlinked_refs(text)
    return {"fake_keys": fake_keys, "wrong_titles": wrong_titles,
            "fake_people": fake_people, "real_titles": real_titles,
            "unlinked_refs": unlinked_refs,
            "ok": not (fake_keys or wrong_titles or fake_people or unlinked_refs)}


def violation_note(result: dict) -> str:
    """다시 쓰기 지시문 — 무엇이 왜 틀렸고 실값이 무엇인지."""
    lines = []
    for k in result.get("fake_keys") or []:
        lines.append(f"- {k}: 존재하지 않는 티켓이다. 언급을 지워라.")
    for k, real in (result.get("wrong_titles") or {}).items():
        lines.append(f"- {k}: 제목이 틀렸다. 실제 제목은 \"{real}\" 이다 — 글자 그대로 써라.")
    for n in result.get("fake_people") or []:
        lines.append(f"- '{n}': 존재하지 않거나 확인되지 않는 사람이다. **자료의 참여자 목록에 "
                     "실제로 있는 사번만** 쓰고, 자료에 없으면 그 역할 줄을 통째로 지워라. "
                     "예시·자리표시자 표기를 만들어 넣지 마라.")
    for r in result.get("unlinked_refs") or []:
        lines.append(f"- 참조 `{r}`: 티켓 키도 링크도 없어 **확인할 방법이 없다**. 자료에 그 "
                     "문서의 URL 이 있으면 `[제목](URL)` 마크다운 링크로 고쳐라. URL 이 자료에 "
                     "없으면 그 참조 줄을 지우고 본문의 [n] 마커도 함께 지워라.")
    return "\n".join(lines)


def warning_block(result: dict) -> str:
    """재작성으로도 못 고쳤을 때 답변에 붙이는 경고 — 조용히 넘어가지 않는다."""
    items = []
    items += [f"`{k}` (존재하지 않는 티켓)" for k in result.get("fake_keys") or []]
    items += [f"`{k}` (실제 제목: {v})" for k, v in (result.get("wrong_titles") or {}).items()]
    items += [f"'{n}' (확인되지 않는 인물)" for n in result.get("fake_people") or []]
    items += [f"`{r}` (링크·키가 없어 확인 불가한 출처)" for r in result.get("unlinked_refs") or []]
    if not items:
        return ""
    return ("\n\n---\n⚠️ **자동 검증 경고** — 아래 항목은 실제 데이터와 대조되지 않았습니다. "
            "무시하고 읽으세요:\n" + "\n".join(f"- {x}" for x in items))
