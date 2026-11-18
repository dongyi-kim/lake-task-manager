# Optional ticket content and shared caches

User-approved implementation and evaluation, in the existing checkout on `feat/bulk-comment-cache`.

## Contract

- `search_issues` / `get_tickets`: `include_description=false`, `include_comments=false`.
- `get_ticket`: omitted `include_description` preserves existing `detail` behavior; explicit false excludes acquisition, decoding and rendering of the body. `include_comments=false`.
- Inclusion and presentation are separate. Explicit description with summary returns display HTML; read/edit choose one representation. Full keeps both representations when requested.
- Search/batch retain raw `fields.description` and `fields.comment`. Single ticket returns raw `comments` plus `commentsCoverage`. Search/batch attach the same coverage to each issue.
- Coverage reports returned rows, source total/start position when known, completeness and follow-up evidence call. Unknown/null/missing comments are not an empty discussion. A bare array has unknown completeness. No artificial 20-comment truncation of embedded Jira responses.
- Requested comments may be a partial source window. `complete` on a ticket batch measures ticket membership only; comment coverage is separate. Partial discussion remains visible in the common read envelope.

## Acquisition and cache

- Cache raw description and raw comment field independently per authenticated session, mutation generation, ticket key and observed version. Preserve original fetch time.
- Batch only missing fields. Both cold: one combined request where the bounded page allows. One warm: request the other field only. Never scalar comment-request fan-out for search.
- Reuse complete, validated raw discussion in existing preview/full/evidence readers. Partial or unknown data must not replace complete coverage. Preview still shows the existing latest 20 comments.
- Keep live/fresh behavior, session reset and local write invalidation. An unchanged `updated` value is not proof that a comment cache never expires.
- Keep 100-ticket light discovery, 10-ticket heavy pages, incremental 16 MiB aggregate limits, pre-decode cache sizing and resource admission. Reject oversize; do not silently clip text.

## Work and verification

1. Extend per-ticket field cache and bounded search; preserve description regressions.
2. Add single-ticket include controls and safe bridges to existing comment readers.
3. Add MCP schemas, projections, tool guidance and independent strict-provider integration tests.
4. Measure actual REST calls for cold/warm/cross-query and mixed-field cache scenarios. Check source pagination, empty/unknown/partial, mutation/session races, source age and multi-megabyte payloads.
5. Check UI comment reads and MCP transport/schema regressions, review the diff, and record evidence and limits in `docs/validation/`.

Use OS temporary directories with guaranteed cleanup for test artifacts. No source copies/worktrees, production Jira tests or release in this task.

## Completed scope and evaluation

Implemented steps 1–5; detailed measurements and remaining browser/retention limitations are in
[`docs/validation/2026-11-18-optional-ticket-content.md`](../../validation/2026-11-18-optional-ticket-content.md).
The user's follow-up request also updated MCP research/reporting and relevant authoring skills.
Jira820's missing embedded comment projection was implemented in its existing checkout and pinned
by immutable commit, with a package version increment so installations actually upgrade.
