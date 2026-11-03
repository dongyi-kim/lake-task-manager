"""Generic evidence primitives; period and business reasoning belongs to the caller."""

from typing import Literal

from app.jira import evidence_service


def register_evidence(register, runtime):
    def get_evidence_page(key: str, kind: Literal["comments", "history"] = "comments",
                          start_at: int = 0, max_results: int = 100, record_offset: int = 0) -> dict:
        """Read original Jira comments or changelog records with IDs, full timestamps and authors.
        key is one ticket; kind=comments returns body/renderedBody/created/updated and original
        author; kind=history returns created/author/items including field IDs and from/to values.
        authorId is null when Jira supplies only a display name; never infer identity from names.
        Returns items, total, returned, hasMore, nextStartAt, complete/sourceComplete and partial.
        max_results is 1..100 and start_at is zero-based. Responses are bounded to 8KiB of data,
        so fewer records may be returned. Follow every nextStartAt; a complete page is not the collection.
        An oversized record is represented by id/sourceIndex/recordJson/recordOffset/nextRecordOffset/
        totalCharacters/recordSha256. Concatenate recordJson chunks and parse JSON to recover the record.
        While nextRecordOffset is non-null, repeat at the same sourceIndex with max_results=1 and
        record_offset=nextRecordOffset. Then follow nextStartAt with record_offset=0. Offsets count Unicode
        characters. Require identical recordSha256 across chunks; restart the record if it changes.
        This is lossless pagination, not a partial source. Comments use live Jira offset pagination,
        so deduplicate IDs and recheck totals if writes occur between pages. History pages cover
        the available expanded changelog (availableTotal). Jira 8.20 may truncate that expansion;
        sourceComplete=false explicitly means older history is unavailable even when hasMore=false.
        Preserve timestamps/timezones and distinguish current state from events when interpreting
        reporting periods. This tool does not aggregate periods, people, work or deliverables.
        Shares running LTM authentication/cache/invalidation. Permission/auth failures are errors,
        never successful empty evidence; incomplete source data is returned with partial=true.
        """
        runtime.check_key(key)
        return evidence_service.get_evidence_page(runtime.client, key, kind, start_at, max_results, record_offset)

    register(get_evidence_page, read_only=True)
