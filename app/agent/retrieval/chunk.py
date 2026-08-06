"""agent/retrieval/chunk.py — 문서를 검색 단위로 자른다.

**제목 경계로 자른다.** 고정 길이로 자르면 표 한가운데, 문장 한가운데가 끊긴다. 우리 지식
문서는 `##` 절 하나가 곧 하나의 주제라("담당자 추천 정책", "분모에서 빠지는 것") 그 경계가
가장 좋은 자르는 자리다.

**각 조각에 제목 경로를 붙인다.** 조각만 떼어 놓으면 "무엇에 대한 규칙인지"가 사라진다.
`01-ticket-rules.md > Story Point` 라는 머리를 달아 두면 임베딩에도 그 맥락이 실리고,
모델이 결과를 읽을 때 출처도 안다.

절이 너무 길면 그때만 길이로 한 번 더 쪼갠다 — 순서가 반대면(길이 먼저) 경계가 이미 깨진다.
"""

from __future__ import annotations

import re

MAX_CHARS = 1200        # 한 조각 상한. 임베딩 품질과 컨텍스트 비용의 절충
OVERLAP = 120           # 길이로 쪼갤 때만. 경계 문장이 양쪽에 다 남게

_H = re.compile(r"^(#{1,3})\s+(.+?)\s*#*$", re.M)


def split_markdown(text: str, source: str = "") -> list[dict]:
    """마크다운 → [{"text","title","source","anchor"}]. 제목 경로가 본문 머리에 박혀 나온다."""
    text = (text or "").replace("\r\n", "\n")
    marks = [(m.start(), len(m.group(1)), m.group(2).strip()) for m in _H.finditer(text)]
    if not marks:
        return _by_length(text, source, source, "")

    out, doc_title = [], ""
    for i, (pos, level, title) in enumerate(marks):
        if level == 1 and not doc_title:
            doc_title = title
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[pos:end].strip()
        # 제목만 있고 본문이 없는 절(상위 제목)은 건너뛴다 — 다음 절이 그 내용을 갖는다.
        if len(body.split("\n", 1)) < 2 or not body.split("\n", 1)[1].strip():
            continue
        path = " > ".join(x for x in (source, doc_title if level > 1 else "", title) if x)
        out.extend(_by_length(body, source, path, title))
    return out or _by_length(text, source, source, "")


def _by_length(body: str, source: str, path: str, anchor: str) -> list[dict]:
    head = f"[{path}]\n" if path else ""
    if len(body) <= MAX_CHARS:
        return [{"text": head + body, "title": path, "source": source, "anchor": anchor}]

    parts, buf = [], ""
    for para in body.split("\n\n"):
        if buf and len(buf) + len(para) + 2 > MAX_CHARS:
            parts.append(buf)
            buf = buf[-OVERLAP:] + "\n\n" + para       # 경계 문장을 양쪽에 남긴다
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf.strip():
        parts.append(buf)
    return [{"text": head + p.strip(), "title": path, "source": source, "anchor": anchor}
            for p in parts if p.strip()]


def split_text(text: str, source: str = "", title: str = "") -> list[dict]:
    """마크다운이 아닌 것(티켓 본문·코멘트·Confluence 평문)용. 문단 단위로만 자른다."""
    return _by_length((text or "").strip(), source, title or source, "")
