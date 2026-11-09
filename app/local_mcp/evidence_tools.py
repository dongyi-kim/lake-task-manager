"""Generic evidence primitives; period and business reasoning belongs to the caller."""

from typing import Literal

from app.jira import evidence_service


def register_evidence(register, runtime):
    def get_evidence_page(key: str, kind: Literal["comments", "history"] = "comments",
                          start_at: int = 0, max_results: int = 100, record_offset: int = 0) -> dict:
        """Page original comments or field history for one ticket when the question needs more evidence.
        To continue, copy data.nextCall.arguments into this tool's next call. Do not follow
        nextStartAt alone: an oversized record keeps that index while its record_offset advances.
        For progress summaries, reuse batched metadata/needed bodies and relevant get_comments. Use kind=comments
        for older discussion or a complete review of decisions, exclusions and revised scope.
        Use kind=history only for change chronology, exact transition times, historical ownership or
        unresolved discrepancies; do not fetch every ticket's history as a routine progress-summary step.
        key is exactly one ticket: no child lookup, recursive traversal or descendant history is included.
        kind defaults to comments; it returns body/renderedBody/created/updated and original
        author; kind=history returns created/author/items including field IDs and from/to values.
        authorId is null when Jira supplies only a display name; never infer identity from names.
        Returns items, total, returned, hasMore, nextStartAt, nextCall, complete/sourceComplete and partial.
        max_results is 1..100 and start_at is zero-based. Responses are bounded to 8KiB of data,
        so fewer records may be returned. Follow every nextCall when complete coverage of the selected
        source is required; a complete page is not the collection. Otherwise stop when the question is
        supported and report the inspected scope, never claim an exhaustive review from a preview.
        An oversized record is represented by id/sourceIndex/recordJson/recordOffset/nextRecordOffset/
        totalCharacters/recordSha256. Concatenate recordJson chunks and parse JSON to recover the record.
        nextCall already combines sourceIndex and nextRecordOffset, preserving the requested batch size.
        Legacy callers can use record_offset=nextRecordOffset at sourceIndex, then nextStartAt with
        record_offset=0 after the record finishes. The offset applies to the first record; max_results=1
        remains supported but is not required. Offsets count Unicode
        characters. Require identical recordSha256 across chunks; restart the record if it changes.
        This is lossless pagination, not a partial source. Comments use live Jira offset pagination,
        so deduplicate IDs and recheck totals if writes occur between pages. History pages cover
        the available expanded changelog (availableTotal). Jira 8.20 may truncate that expansion;
        sourceComplete=false explicitly means older history is unavailable even when hasMore=false.
        nextCall=null also occurs for an unproven source; check complete/sourceComplete/partial before
        claiming a complete collection. Stop and diagnose identical nonadvancing continuation arguments.
        Preserve timestamps/timezones and distinguish current state from events when interpreting
        reporting periods. This tool does not aggregate periods, people, work or deliverables.
        Shares running LTM authentication/cache/invalidation. Permission/auth failures are errors,
        never successful empty evidence; incomplete source data is returned with partial=true.
        """
        runtime.check_key(key)
        return evidence_service.get_evidence_page(runtime.client, key, kind, start_at, max_results, record_offset)

    register(get_evidence_page, read_only=True)
