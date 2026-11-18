# Optional ticket descriptions and comments — 2026-11-18

This record evaluates optional content acquisition through the real MCP SDK on `feat/bulk-comment-cache`. It uses controlled local REST sources and the installed Jira820 dependency. It does not record production deployment or a live LLM evaluation.

## Verified behavior

`search_issues` and `get_tickets` default both `include_description` and `include_comments` to false. `get_ticket` defaults `include_comments` to false; omitted or null `include_description` preserves its existing full/summary/read/edit behavior. Explicit false removes description representations and avoids requesting that heavy field.

Cold description and comment misses are fetched together in bounded Jira key searches. Each field has independent cache eligibility: when only descriptions are warm, the next request fetches comments alone, and the reverse also works. Cached content is shared across changed search queries and between `get_ticket`, `get_tickets`, and search.

The independent fixture implements actual JQL filtering, requested-field projection, stable `updated` revisions, ordering and physical page limits. Its request ledger counts calls to `provider.get_json("/rest/api/2/search", ...)`; unexpected scalar issue/comment GETs fail the fixture. Thus REST counts are measured at the transport boundary rather than inferred from MCP calls.

## Complete 100-ticket collection

Each of the 100 tickets has a complete 4,128-byte description and 25 raw comments. The cold and warm phases each call `get_tickets` once with all 100 keys. The changed-query phase collects the same 100 tickets through ten `search_issues` pages. All 100 keys, complete descriptions and all 2,500 comment rows are compared against the source.

| Phase | Metadata REST | Combined description/comment REST | Total REST | MCP tool calls | Structured data bytes | Serialized SDK result bytes | Local elapsed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cold | 1 | 10 | 11 | 1 | 926,571 | 1,971,739 | 1,248 ms |
| Identical warm request | 0 | 0 | 0 | 1 | 926,570 | 1,971,739 | 788 ms |
| Changed query, same keys/revisions | 1 | 0 | 1 | 10 | 929,522 | 1,984,672 | 728 ms |

Byte counts use compact UTF-8 JSON. Structured data bytes count the `data` object. SDK result bytes count `CallToolResult.model_dump(mode="json", by_alias=True, exclude_none=True)`, including its text and structured representations, but excluding JSON-RPC and HTTP framing. Cross-query byte counts sum all ten responses. Elapsed time includes local SDK handling, verification and byte accounting, and excludes environment/catalog construction.

The warm response still returns the requested content, so its response volume is essentially unchanged. **Actual LLM token consumption was not measured.** Fewer Jira requests do not by themselves establish fewer model tokens; clients must avoid repeatedly reading content they already hold.

For a **modeled comparator**, assume the former workflow used 11 description REST requests plus one separate comment request per ticket: `11 + 100 = 111`. Compared with 11 combined requests, that estimates a **90.1% REST request reduction** (`100 / 111`). This is not a measured run of the old branch. It assumes one comment request per ticket provides the required evidence; actual Jira comment windows and pagination can change the count.

## Ten-ticket page allocation check

This separate workload matches 100 tickets but returns only ten. Each source description is 512 KiB plus its key; every returned issue also carries 25 raw comments. The source dataset is created before `tracemalloc` starts.

| Phase | REST calls | Incremental traced Python peak | Instrumented local elapsed |
| --- | ---: | ---: | ---: |
| Cold page | 2: one metadata, one combined | 11,098,486 bytes | 1,208 ms |
| Identical warm page | 0 | 16,390,709 bytes | 396 ms |
| Changed query, same returned keys | 1 metadata | 16,372,126 bytes | 494 ms |

These are incremental Python allocation peaks during the measured MCP call, not total process RSS, total fixture memory, or a production latency forecast. The test requires less than 160 MiB of traced allocation and verifies that complete returned content matches the source. The complete-100-ticket ledger above is a different workload and does not use these memory figures.

## Coverage and safety checks

The 26 cases in [`test_optional_ticket_content.py`](../../tests/mcp/test_optional_ticket_content.py) cover:

- Explicit opt-out, combined cold acquisition, identical warm reuse, changed-query reuse, and independently warm description/comment fields.
- More than the existing 20-comment UI preview: all 25 source rows are preserved in bulk and single-ticket optional content.
- Complete envelopes, partial envelopes, explicit empty envelopes, bare arrays, bare empty arrays, null, omitted and malformed containers. Unknown containers never become an authoritative empty discussion.
- Raw bulk comments at `issue.fields.comment`, per-issue `commentsCoverage`, and top-level `commentsRequested`/`commentsMissingKeys`. Single-ticket comments are a raw list with the same coverage indicators.
- Partial embedded windows retain their reported total and rows but remain incomplete. Their follow-up `nextCall.name` is `get_evidence_page`, restarting at zero because embedded ordering does not prove a safe endpoint continuation offset.
- Changed `updated` revisions, explicit live validation, mutation fences, and preservation of the existing get-ticket detail defaults.
- Combined 16 MiB refusal: a 9 MiB description plus a 9 MiB comment fails explicitly. Either field alone remains complete. After both are individually cached, the oversized combined request fails with no additional REST request and no truncation.

The first RED run showed the old SDK input model rejecting `include_comments=false` as an extra argument. After integration, malformed-container coverage exposed a second issue: reported positions were being retained despite unreadable rows. The helper now treats those positions/totals as unknown, while preserving raw source data and explicit missing indicators.

The 24 non-ledger cases passed in **37.82 seconds** after that correction. Both ledger cases passed separately; the final byte-accounting 100-ticket ledger passed in **4.87 seconds**. Together these runs cover all 26 cases. Pytest data, runtime files and caches were isolated in an OS temporary directory and removed on process exit.

## Jira820 parity

The previously installed Jira820 returned a successful search response while omitting a requested `comment` field, even after comments were created. That gap was reported explicitly rather than hidden by modifying its response in the test fixture.

The dependency is now pinned to Jira820 0.13.1, commit `0b3a85f02f7bee462588e32bc4ca6bf91400c5a8`. The positive parity test creates a real local ticket and two comments, then compares the search-projected raw comment collection against its comment endpoint. Separate strict-source omission tests continue to exercise unsupported upstream behavior independently of Jira820's version.

## Regression and review

- Core description/comment hydration and existing large-description regressions: 54 passed; one additional warm combined-budget case subsequently passed with the 11-case hydration file.
- Comment coverage, single-ticket options, original source time, rich wiki/HTML/plain rendering, attachment-only image resolution, and existing ticket views: 54 passed.
- Broader MCP schemas, projections, cache modes, comment freshness/source, evidence, descriptions and authoring: all 65 cases passed across focused runs. The evidence fixture was upgraded from an incomplete namespace to a real client with a stable test cache namespace; source-age and invalidation assertions were preserved.
- Jira820 embedded-field and existing search tests: 15 passed.
- Packaged skill catalog/resource/transport tests: 21 passed, one existing Windows symlink-permission case skipped. All 76 Markdown links resolve; all 53 resources are within 8 KiB.
- Independent review caught and corrected wiki/HTML raw-cache rendering, plain comment line breaks, missing continuation cache policy, malformed coverage and legacy physical-read cache seeding. Filename images fetch bounded attachment metadata only when required; their extra REST request is outside the ordinary-text ledger above.

The existing full browser authoring regression completed all description/comment creation, two publish cycles, rendering, mention and image assertions in both wiki and HTML configurations. Its overall verdict is **not fully passing**: after accounting for observed resource recovery, wiki passed but HTML retained HTTP 429 observations for a background activity POST and avatar GETs. The fixture removes only a retryable, non-executed capacity refusal followed by a 2xx GET of the exact same URL; unresolved errors remain failures. This proves eventual URL-level recovery, not which refused request was retried. Resource admission was not disabled or loosened.

Read-only diagnosis traced those remaining requests to existing paths: activity uses POST and does not enter the safe GET/HEAD retry; native avatar images also bypass that retry and fall back to initials. Those middleware, activity and avatar production files are unchanged in this branch. New legacy-read cache seeding adds work and may affect request timing; no unchanged-baseline browser comparison establishes its contribution. The auxiliary-request failures remain a follow-up limitation, and the overall browser suite is not reported as passing.

Three existing comment-retention assertions also still expect physical deletion after invalidation, conflicting with retained-display policy; they were observed separately and not weakened. The complete repository suite was not run locally.

## MCP skill update

Research/reporting guides now identify themselves as `0.1.12-candidate`. A focused `heavy-content.md` reference explains independent inclusion/cache behavior, complete-discussion reuse, partial-window continuation, source age and size recovery. Relevant task/comment/bulk authoring guidance also reuses acquired data while retaining the editor-source contract.

The read-only baseline review found redundant per-ticket comment acquisition and unconditional evidence re-reads for complete monthly audits. After the update, a fresh native `gpt-6.1-sol` high reviewer passed all four scenarios: 40-key combined collection; reuse of complete, compliant monthly evidence; partial 25/100 continuation from the supplied start-zero call with the same cache mode; and current-group recovery after size refusal/disconnection during a long record. This is a native scenario review, not a full repeated external-model benchmark.

No real Jira service or external LLM was called. No production release is recorded by this task.
