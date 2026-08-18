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
# 역할과 이름은 **같은 줄**이어야 한다. `\s*`는 줄바꿈까지 먹어서
# `유사 업무 1건 담당\n- **대안**:`의 '담당'을 역할로, '대안'을 사람 이름으로 오인했다.
NAME_RE = re.compile(
    rf"{_ROLE}[\*_]*[ \t]*[:\-–][ \t]*[\*_]*([가-힣]{{2,4}})\b|"
    rf"([가-힣]{{2,4}})[ \t]*님\b")
# Natural prose also names people without punctuation: ``담당자는 안하준``. A bare
# ``담당 성능`` is a responsibility noun phrase, not a person assertion; bare ``담당``
# therefore requires a subject particle while ``담당자`` remains an explicit role.
_ROLE_SENTENCE = (
    r"(?:(?:PM|리더|담당자|개발자|디자이너|리포터|작성자|검토자|매니저|QA|"
    r"실무자|엔지니어|기획자|운영자)(?:은|는|이|가)?|담당(?:은|는|이|가))"
)
ROLE_NAME_SENTENCE_RE = re.compile(
    rf"{_ROLE_SENTENCE}[ \t]+[\*_]*([가-힣]{{2,4}})\b")
BARE_NAME_ACTION_RE = re.compile(
    r"(?<![가-힣])([가-힣]{2,4})(?:은|는|이|가)[ \t]+"
    r"(?:담당|진행|작성|보고|검토|확인|수행|맡)")
UID_RE = re.compile(r"^[a-z]+\.[a-z]\d+$")
# 답변 속 사번 꼴 토큰 — 실재 검증 대상. NNNN 같은 자리표시자는 그 자체로 위반이다
# (재작성 지시문의 예시 표기를 답에 그대로 복사한 실측 사고).
UID_TOKEN_RE = re.compile(chr(92) + "b([a-z]{2,}" + chr(92) + ".[a-zA-Z]{1,2}[0-9N]{2,6})" + chr(92) + "b")
# "**DL-123**: 김철수" — 역할 낱말 없이 티켓키→사람 매핑으로 새는 변종(실측).
KEY_NAME_RE = re.compile(r"[A-Z][A-Z0-9]+-" + r"[0-9]+[*_]*\s*[:\-–]\s*[*_]*([가-힣]{2,4})(?=\s|$|[,.)—-])")
# "| 담당자 | 한예준 |" — **표 칸**으로 새는 변종(실측 EDGE13). 역할 낱말과 이름 사이가
# 콜론이 아니라 파이프라 위 두 패턴이 전부 놓쳤다. 답변이 표를 많이 쓰는 화면이라 이 꼴이
# 흔하다. common.md: "People appear as ids (skcc.x1042)" — 실명은 자료에 없는 표기다.
TABLE_NAME_RE = re.compile(
    r"\|[^|\n]*(?:담당|작성자|보고자|리더|사람|인력)[^|\n]*\|\s*\**\s*([가-힣]{2,4})\s*\**\s*\|")
# 참조 인덱스 줄 — `[1] …` 이 우리 형식이지만, 모델이 `1. …` 로 쓰는 일이 잦다.
# **금지한 형식이라고 검사에서 빼면 그 형식으로 새어 나간다**(실측: `1.` 로 쓴 참조 줄이
# 통째로 검사 밖이었고, 그 줄들이 티켓 키에 엉뚱한 문서 URL 을 달고 있었다). 둘 다 본다.
REF_LINE_RE = re.compile(r"^\s*(?:\[(\d{1,2})\]|(\d{1,2})[.)])\s+(.+?)\s*$", re.M)
# ★ 링크로 인정하는 것은 **진짜 URL 뿐**이다. 예전엔 `](` 만 봐서 괄호 안에 아무 말이나
# 넣어도 통과했다 — 실측(가드가 만든 회피 3번째): 재작성 지시문의 문구를 URL 자리에 그대로
# 복사했다: `1. [DL-9044 — 적재주기 변경](확인할 방법이 없음)`.
# "지시문을 답에 복사"는 이 저장소가 이미 겪은 부류다(사번 자리표시자 NNNN).
LINKED_RE = re.compile(r"https?://")
URL_RE = re.compile(r"https?://[^\s)\]]+")
# 링크 괄호는 열었는데 안에 URL 이 없는 것 — `](확인할 방법이 없음)` `](URL)` `]()`
FAKE_LINK_RE = re.compile(r"\]\(\s*(?!https?://)[^)]*\)")
# 티켓으로 가는 링크인가 — Jira 는 /browse/KEY 다. Confluence 문서 URL 은 /pages/ 를 가진다.
BROWSE_RE = re.compile(r"/browse/([A-Z][A-Z0-9]+-\d+)")


REF_HEAD_RE = re.compile(r"^\s*[*_#\s]*(?:근거|참조)[*_\s]*$|^\s*[*_#\s]*references[*_\s]*$",
                         re.M | re.I)


def _ref_lines(text: str):
    """참조 줄을 (번호, 내용) 으로 돌려준다 — `[1] …` 과 `1. …` 둘 다.

    `1. …` 은 **참조 섹션 뒤에서만** 참조로 본다. 본문의 평범한 번호 목록
    ("1. 먼저 설계한다")까지 출처로 오인하면 멀쩡한 답에 경고가 붙는다.
    """
    t = text or ""
    m0 = REF_HEAD_RE.search(t)
    ref_from = m0.end() if m0 else len(t) + 1
    for m in REF_LINE_RE.finditer(t):
        bracketed = m.group(1) is not None
        if not bracketed and m.start() < ref_from:
            continue                      # 본문의 번호 목록이다
        yield (m.group(1) or m.group(2) or "?"), m.group(3)


def _unlinked_refs(text: str) -> list:
    """참조 줄의 **검증 불가** 항목 — 두 부류를 잡는다.

    ① 티켓 키도 링크도 없는 줄. common.md 가 "NEVER drop a bare document title with no
       URL" 이라 못 박는데도 샜다(실측: `[4] [데이터카탈로그] … — 적재 Job 정보`).
       재료에는 그 문서의 URL 이 실려 있었으므로 **쓸 수 있었는데 안 쓴 것**이다.

    ② ★ 티켓 키를 단 참조에 **그 티켓이 아닌 URL**이 붙은 줄. 실측(가드가 만든 회피 경로):
       ①을 막았더니 모델이 아무 URL 이나 붙여 통과했다 —
         `1. [DL-9044 — 적재주기 변경](http://…/pages/…/[데이터카탈로그]+…+특성+분석)`
       클릭하면 전혀 다른 것이 열린다. 링크가 없는 것보다 **나쁘다**(있는 척한다).
       티켓으로 가는 링크는 /browse/KEY 여야 한다 — 다른 키를 가리켜도 위반이다.

    본문 참고 불릿에는 ①의 가드가 이미 있다(`work_architect._drop_unlinked_refs`) — 답변 텍스트
    쪽에만 없었다. 같은 규칙이 두 산출물에 다 걸려야 한다.
    """
    out = []
    for num, body in _ref_lines(text or ""):
        keys = KEY_RE.findall(body)
        urls = URL_RE.findall(body)
        # ③ 링크인 척하는 괄호 — 안에 URL 이 없다. 있는 척하는 것이 없는 것보다 나쁘다.
        if FAKE_LINK_RE.search(body):
            out.append(f"[{num}] 링크 자리에 URL 이 아닌 것이 들어갔다: {body[:60]}")
            continue
        if not keys and not LINKED_RE.search(body):
            out.append(f"[{num}] {body[:60]}")
            continue
        if keys and urls:
            # 키를 말해 놓고 붙인 링크가 그 티켓이 아니면 위조다.
            linked = {m for u in urls for m in BROWSE_RE.findall(u)}
            if not (linked & set(keys)):
                out.append(f"[{num}] {keys[0]} 인데 링크는 그 티켓이 아니다: {urls[0][:60]}")
    return out


def check(reply: str, allowed_people: set[str] | None = None) -> dict:
    """답변을 실물과 대조한다. 반환:
    {"fake_keys": [...], "wrong_titles": {key: 실제제목}, "fake_people": [...], "ok": bool}
    """
    from app.agent.tools._ctx import client, settings
    c = client()
    text = reply or ""
    allowed_people = {str(x).strip() for x in (allowed_people or set()) if str(x).strip()}

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
        # 두 꼴을 **따로** 모은다 — 단정의 세기가 다르기 때문이다(아래 판정에서 갈린다).
        loose = [mm.group(1).strip().rstrip(")").strip() for mm in
                 re.finditer(rf"{re.escape(key)}\**\s*[:(]\s*([^)\n**]{{4,80}})", text)]
        quoted = [mm.group(1).strip() for mm in
                  re.finditer(rf"{re.escape(key)}\**\s*[\"“''](?=\S)([^\"”'\n]{{4,80}})"
                              rf"[\"”'']", text)]
        loose = [c for c in loose if c]
        quoted = [c for c in quoted if c]
        claims = loose + quoted
        if not claims:
            continue
        # 요약·의역은 허용해야 하므로 완전 일치를 요구하지 않는다. 후보 표기 중
        # **하나라도** 제목을 안 것으로 보이면 통과 — 콜론 뒤 상태 서술("DL-9090: 현재
        # 2/3 완료")이 첫 후보로 잡혀 정확한 따옴표 제목이 무시되던 오탐 방지.
        #
        # ★ **따옴표 꼴에만** 엄격한 잣대를 댄다(사용자 관점 리뷰 F6, blocker).
        #   `DL-9008 '[내Task] Epic 없는 내 Task — 마감 초과'` 가 통과했는데 실물은
        #   `[UI] 마감 초과(D+) — 기한 붉은 강조` 였다 — 겹친 것은 '마감'·'초과' 둘뿐,
        #   **둘 다 아무 티켓에나 있는 말**이다. 흔한 낱말 하나로 제목을 안 척할 수 있으면
        #   이 가드는 있으나 마나다.
        #   그렇다고 모든 꼴에 엄격하게 대면 오탐이 난다 — 콜론 뒤는 대개 제목이 아니라
        #   **상태 서술**("DL-101: 성능 관련 작업이 진행 중")이라 원래 안 겹친다. 오탐은
        #   공짜가 아니다: 재작성 LLM 이 한 번 더 돌고, 못 고치면 답에 경고가 붙는다.
        #   그래서 세기를 나눈다 — **따옴표는 제목을 단정한 것**, 콜론은 서술.
        #     · 따옴표: 실제 제목의 부분집합(줄여 부르기)이거나 토큰 40% 이상을 덮을 것
        #       — F6 은 없던 말 5개를 더했고(부분집합 아님) 겹침도 2/7=29% 라 걸린다
        #     · 콜론·괄호: 예전대로 한 토큰이라도 겹치면 통과
        def _tok(s: str) -> set:
            return {t for t in re.split(r"[\s\[\]()\-—·/]+", s) if len(t) >= 2}

        real_tokens = _tok(real)
        if not real_tokens:
            continue
        need = max(1, round(len(real_tokens) * 0.4))
        ok_loose = any(_tok(c) & real_tokens for c in loose)
        ok_quoted = any(_tok(c) <= real_tokens or len(_tok(c) & real_tokens) >= need
                        for c in quoted if _tok(c))
        # 따옴표로 제목을 단정했으면 **그 단정이 맞아야** 한다 — 옆줄의 느슨한 표기가
        # 통과했다고 해서 틀린 단정이 사면되지 않는다.
        if quoted:
            if not ok_quoted:
                wrong_titles[key] = real
        elif loose and not ok_loose:
            wrong_titles[key] = real

    fake_people, name_as_id, person_findings = [], {}, []
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
                    person_findings.append({
                        "candidate": uid, "context_kind": "user_id",
                        "verdict": "placeholder",
                    })
                    continue
            # ★ 전체 id 가 **정확히** 실재해야 한다. 접미(x1001)만 검색하면 다른 실존
            #   사번(skcc.x1001)의 접미와 겹치는 날조(etl.x1001)가 통과한다(실측).
            hits = (search_users(c, s, uid, 5) or []) + \
                   (search_users(c, s, uid.split(".")[-1], 8) or [])
            if not any(str(u.get("id") or "") == uid for u in hits):
                fake_people.append(uid)
                person_findings.append({
                    "candidate": uid, "context_kind": "user_id",
                    "verdict": "unverified_person",
                })
        # 역할 문맥 + 키→사람 매핑("**DL-123**: 김철수") 두 꼴 모두 본다 —
        # 후자는 역할 낱말이 제목 줄에만 있고 항목 줄엔 없어서 NAME_RE 가 놓쳤다(실측).
        candidates = [
            ((m.group(1) or m.group(2) or "").strip(),
             "role_delimiter" if m.group(1) else "honorific")
            for m in NAME_RE.finditer(text)
        ]
        candidates += [(m.group(1).strip(), "role_sentence")
                       for m in ROLE_NAME_SENTENCE_RE.finditer(text)]
        candidates += [(m.group(1).strip(), "ticket_mapping")
                       for m in KEY_NAME_RE.finditer(text)]
        candidates += [(m.group(1).strip(), "role_table")
                       for m in TABLE_NAME_RE.finditer(text)]
        candidates += [(m.group(1).strip(), "named_actor_action")
                       for m in BARE_NAME_ACTION_RE.finditer(text)]
        # 상태·시간 낱말은 사람이 아니다 — "DL-9090: 현재 2/3 완료" 의 '현재'가 인물로
        # 걸렸다(실측 오탐). 이 목록은 오탐이 관측될 때마다 늘린다.
        _NOT_NAMES = {"현재", "이번", "오늘", "내일", "진행", "완료", "지연", "마감", "기한",
                      "상태", "예정", "검토", "확인", "미정", "없음", "전체", "작업", "근거",
                      "후보", "후보는", "업무", "내용", "분야", "조직", "모듈", "티켓", "범위",
                      "확인되지"}
        _NOT_NAME_SUFFIXES = ("에는", "에서는", "으로는", "부터는", "까지는", "보다도")
        for name, context_kind in candidates:
            if (not name or name in seen or UID_RE.match(name) or name in _NOT_NAMES
                    or name.endswith(_NOT_NAME_SUFFIXES)
                    or name in allowed_people):
                continue
            seen.add(name)
            hits = search_users(c, s, name) or []
            if not hits:
                fake_people.append(name)
                person_findings.append({
                    "candidate": name, "context_kind": context_kind,
                    "verdict": "unverified_person",
                })
            else:
                # ★ **실재하는 실명도 위반이다.** result_integrator.md: "never translate ids into
                #   names". 여태 이 검사는 **날조만** 봤기 때문에 실명이 그냥 통과했다
                #   (실측 EDGE13: "담당자 한예준"). 화면은 사번을 뱃지·프로필로 렌더하고,
                #   실명은 동명이인·표기 흔들림에 취약해 검증도 안 된다.
                #   실값을 알고 있으니 고칠 값까지 쥐여 준다.
                uid = str((hits[0] or {}).get("id") or "")
                if uid:
                    name_as_id[name] = uid
                    person_findings.append({
                        "candidate": name, "context_kind": context_kind,
                        "verdict": "verified_name_requires_id",
                    })
    except Exception:
        pass          # 사람 검증이 안 되는 환경이면 키 검사만으로 간다

    unlinked_refs = _unlinked_refs(text)
    return {"fake_keys": fake_keys, "wrong_titles": wrong_titles,
            "fake_people": fake_people, "real_titles": real_titles,
            "unlinked_refs": unlinked_refs, "name_as_id": name_as_id,
            "person_findings": person_findings,
            "ok": not (fake_keys or wrong_titles or fake_people
                       or unlinked_refs or name_as_id)}


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
    for n, uid in (result.get("name_as_id") or {}).items():
        lines.append(f"- '{n}': 사람은 **사번으로** 쓴다. '{n}' 을 `{uid}` 로 바꿔라 — "
                     "화면이 사번을 뱃지·프로필로 렌더하고, 실명은 동명이인·표기 흔들림에 "
                     "취약해 검증이 안 된다. 이름을 지우지 말고 **사번으로 바꿔** 쓸 것.")
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
    items += [f"'{n}' (사번 `{u}` 로 써야 한다)" for n, u in (result.get("name_as_id") or {}).items()]
    items += [f"`{r}` (링크·키가 없어 확인 불가한 출처)" for r in result.get("unlinked_refs") or []]
    if not items:
        return ""
    return ("\n\n---\n⚠️ **자동 검증 경고** — 아래 항목은 실제 데이터와 대조되지 않았습니다. "
            "이 상태로 승인하지 말고 실제 값을 확인하세요:\n"
            + "\n".join(f"- {x}" for x in items))
