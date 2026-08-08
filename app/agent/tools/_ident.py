"""agent/tools/_ident.py — 데이터 자산 식별자(테이블·Job 이름) 인식과 변형.

사내 질문의 상당수는 **테이블 하나**를 가리킨다 — "fdc.fdc_trace_summary_ic 적재주기가?".
그런데 이 이름은 검색·라우팅·사전 취합 세 군데에서 각각 필요하고, 정규식을 세 군데에
복붙하면 반드시 갈라진다. 그래서 여기 한 곳에만 둔다.

**왜 변형(variants)이 필요한가**: 같은 테이블을 어떤 티켓은 `fdc.fdc_trace_summary_ic`,
어떤 코멘트는 스키마를 떼고 `fdc_trace_summary_ic` 로 적는다. mock/Jira 의 `text ~` 는
부분문자열 매칭이라 **접미형(스키마 없는 쪽)으로 찾으면 상위집합**이 잡힌다 — 반대는 안 된다.
그래서 접미형을 먼저 시도한다.

**하지 않는 것**: 단어 단위 재분해(`fdc`/`trace`/`summary`). 노이즈가 폭발하고, 그건
이미 완화 사다리가 하는 일이다.
"""

from __future__ import annotations

import re

# schema.table — 소문자·숫자·밑줄. 앞뒤가 단어 문자면 제외(URL·패키지 경로 오인 방지).
_QUALIFIED = re.compile(r"(?<![\w.])([a-z][a-z0-9_]{2,})\.([a-z][a-z0-9_]{2,})(?![\w.])")
# 스키마 없이 밑줄만 있는 이름 — etl_fdc_trace_summary_ic, fdc_trace_summary_ic
_BARE = re.compile(r"(?<![\w.])([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})(?![\w.])")
# 공백으로 풀어 쓴 이름 — "fdc trace summary ic는?" 처럼 밑줄을 빼고 말한다(실측:
# 이 표기를 식별자로 못 봐서 '기록 없음'으로 답했다). 소문자 ASCII 짧은 낱말이
# **3개 이상 연달아** 나올 때만(2개면 평범한 영어구까지 잡는다) 밑줄로 이어 후보로 삼는다.
# 경계는 ASCII 만 본다 — 한국어 조사("ic는")는 식별자의 일부가 아니라 경계다.
_SPACED = re.compile(r"(?<![A-Za-z0-9_.])([a-z][a-z0-9]{1,11}(?:\s+[a-z][a-z0-9]{1,11}){2,})"
                     r"(?![A-Za-z0-9_.])")
# 평범한 영어 문장 오인 방지 — 기능어가 하나라도 끼면 식별자가 아니다.
_EN_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
            "are", "was", "be", "this", "that", "it", "as", "at", "by", "from", "we",
            "you", "my", "our", "your", "please", "review", "check", "make", "fix",
            "add", "update", "remove", "delete", "create", "build", "test", "run",
            "use", "do", "not", "no", "yes", "can", "should", "will", "would",
            "how", "what", "why", "when", "where", "new", "old", "all", "any"}

# 확장자·모듈 경로로 흔한 것 — schema.table 로 오인하면 엉뚱한 조사가 돈다.
_NOT_TABLE_SUFFIX = {"py", "md", "json", "yaml", "yml", "sql", "csv", "txt", "png", "com",
                     "net", "org", "log", "html", "js", "css", "xml", "gz", "parquet"}


def find_identifiers(*texts: str) -> list[str]:
    """텍스트에서 데이터 자산 식별자를 뽑는다. 수식형(schema.table)을 먼저, 없으면 밑줄형.

    수식형이 하나라도 있으면 밑줄형은 무시한다 — `fdc.fdc_trace_summary_ic` 한 덩어리를
    `fdc_trace_summary_ic` 와 별개 자산으로 세면 조사가 두 번 돈다.
    """
    blob = " ".join(t for t in texts if t)
    out: list[str] = []
    for m in _QUALIFIED.finditer(blob):
        if m.group(2).lower() in _NOT_TABLE_SUFFIX:
            continue
        v = m.group(0)
        if v not in out:
            out.append(v)
    if out:
        return out
    for m in _BARE.finditer(blob):
        v = m.group(0)
        if v not in out:
            out.append(v)
    if out:
        return out
    # 공백형 폴백 — "fdc trace summary ic" → "fdc_trace_summary_ic". 검색은 variants 가
    # 밑줄형으로 하므로 실물 표기와 만난다.
    for m in _SPACED.finditer(blob):
        words = m.group(1).split()
        if any(w in _EN_STOP for w in words):
            continue
        v = "_".join(words)
        if v not in out:
            out.append(v)
    return out


def variants(term: str) -> list[str]:
    """검색에 쓸 표기 변형 — 접미형(스키마 제거)이 먼저다(부분문자열 매칭의 상위집합).

    `fdc.fdc_trace_summary_ic` → ["fdc_trace_summary_ic", "fdc.fdc_trace_summary_ic"]
    """
    t = (term or "").strip()
    if not t:
        return []
    out = []
    if "." in t:
        tail = t.split(".", 1)[1]
        if len(tail) >= 4:
            out.append(tail)
    out.append(t)
    return out


def looks_like_asset_question(text: str) -> bool:
    """식별자를 담은 데이터 자산 질문인가 — 라우팅 가드가 이걸 본다."""
    return bool(find_identifiers(text or ""))


# 주제어가 될 수 없는 일반어 — 이걸로 조사를 시작하면 온 프로젝트가 걸린다.
_GENERIC = {"테스크", "태스크", "티켓", "업무", "작업", "관련", "정리", "확인", "현황", "이력",
            "히스토리", "근황", "최근", "내용", "정보", "상황", "진행", "결과", "방법", "지식",
            "프로젝트", "모듈", "데이터", "시스템", "우리", "사내", "설명", "요청", "사용"}


def subject_term(text: str, keywords=None) -> str:
    """질문이 **무엇 하나에 대한 것인지** 고른다 — 사전 취합의 씨앗.

    테이블 이름만이 아니다. 특정 기술("Schema Registry"), 특정 주제("호환성 정책"),
    특정 업무 어느 것이든 조각은 똑같이 흩어져 있다. 고르는 순서:
      ① 데이터 자산 식별자(schema.table, job 이름) — 가장 확실하다
      ② 영문 기술어를 포함한 핵심어(약어·제품명) — 두 낱말까지 붙여 쓴다
      ③ 3자 이상 한국어 핵심어 중 일반어가 아닌 것
    아무것도 없으면 빈 문자열 — 그때는 취합하지 않는다(일반어로 훑으면 노이즈만 는다).
    """
    idents = find_identifiers(text or "", " ".join(keywords or []))
    if idents:
        return idents[0]
    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    ascii_kws = [k for k in kws if re.search(r"[A-Za-z]{3,}", k) and k.lower() not in ("etl", "voc")]
    if ascii_kws:
        return max(ascii_kws, key=len)
    ko = [k for k in kws if len(k) >= 3 and k not in _GENERIC]
    return max(ko, key=len) if ko else ""
