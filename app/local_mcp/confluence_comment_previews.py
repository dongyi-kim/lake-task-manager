"""Bound comment discovery independently from version-pinned full-body reads."""
import json
import re
from html.parser import HTMLParser

from app.auth.base import UpstreamUnavailable


COMMENT_PAGE_BYTES = 8000


class _PreviewText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)


def _preview(row):
    cid = str(row.get("id") or "")
    source = ((row.get("body") or {}).get("storage") or {}).get("value")
    if not re.fullmatch(r"[0-9]{1,30}", cid) or not isinstance(source, str):
        raise UpstreamUnavailable("Confluence 댓글 ID 또는 본문이 응답에 없습니다.")
    parser = _PreviewText()
    parser.feed(source[:8192])
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:400]
    omitted = text != source
    version = row.get("version") or {}
    author = version.get("by") or {}
    by = {key: value[:120] for key in ("username", "userKey", "displayName")
          if isinstance(value := author.get(key), str)} if isinstance(author, dict) else {}
    when = version.get("when")
    number = version.get("number")
    if type(number) is not int or number < 1:
        raise UpstreamUnavailable("Confluence 댓글 버전을 확인하지 못했습니다.")
    return {"id": cid, "version": {"number": number,
                                   "when": when if isinstance(when, str) and len(when) <= 64 else None,
                                   "by": by},
            "preview": text, "format": "text-preview", "bodyLength": len(source),
            "bodyTruncated": omitted}


def comment_page(cid, data, start, limit):
    rows = data.get("results") or []
    result = {"pageId": cid, "kind": "comment", "results": [], "startAt": start,
              "returned": 0, "hasMore": False, "nextStartAt": None, "coverage": "accessible_only",
              "bodyReadHint": "Read the selected comment's full storage source with its ID and version; this list contains text previews only."}
    for row in rows:
        preview = _preview(row)
        candidate = {**result, "results": result["results"] + [preview]}
        # Reserve room for the final pagination fields without depending on upstream page size.
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > COMMENT_PAGE_BYTES - 128:
            break
        result["results"].append(preview)
    count = len(result["results"])
    if rows and not count:
        raise UpstreamUnavailable("Confluence 댓글 미리보기 한도를 초과했습니다.")
    more = count < len(rows) or bool(rows) and (bool((data.get("_links") or {}).get("next")) or len(rows) >= limit)
    result.update(returned=count, hasMore=more, nextStartAt=start + count if more else None)
    return result
