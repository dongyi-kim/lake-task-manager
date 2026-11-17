# Ticket body cache implementation plan

> **For agentic workers:** Use executing-plans and test-driven-development. The user approved implementation; proceed in the existing checkout.

**Goal:** Discover light ticket metadata first and fetch only missing, expired or changed descriptions in bounded key batches, sharing bodies across different searches.

**Architecture:** Cache small discovery pages independently from ticket bodies. A session-scoped ticket/version index points to a separately stored immutable body. Read only the returned page's bodies, with bounded batch and output sizes. Known-key small cold reads keep their single-request shortcut.

**Tech Stack:** Python, Jira REST search, existing SQLite Cache/read policies, pytest, MCP.

**Spec:** User-approved two-phase bulk description collection and per-ticket body caching (this document records that design).

## Global constraints

- Existing checkout only; do not modify the deploy repository or port 4457.
- Light discovery up to 100 rows; body requests at most 10 rows; complete response at most 16 MiB.
- No scalar GET fan-out, body truncation, whole-query body snapshot, or promotion of old body timestamps.
- Keep live/hot/warm/cool/cold policies, auth-session boundaries, mutation fences and resource admission.
- Null/empty returned description is known empty; absent description is unknown.
- Discovery pages are current observations, not a frozen historical report. Cursor remains null.
- Use OS temporary directories for tests; no live LLM calls.

## Review focus

- Auth changes and mutations during fetch must prevent publication/return under the wrong session.
- Version mismatch between discovery and hydration must never silently combine inconsistent fields.
- Server page caps and missing fields must not skip keys or create false completeness.
- Large warm bodies must be bounded before deserializing a whole batch.
- Cold small known-key reads must not double Jira calls; fresh cross-query bodies must be reused.

### Task 1: Per-ticket cache

Files: `app/jira/ticket_body_cache.py`, `tests/jira/query/test_ticket_body_cache.py`.

Interface: `TicketBodyCache(cache, scope, ttl, fence)`, `read(issues, max_bytes) -> dict[key, description]`, `store(issues, fetched_at)`. Issues include key and fields.updated; missing versions are not shared. Index and immutable payload commit atomically. Read policies/source age come from existing Cache.

- [x] Write/run failing tests for empty/unknown, changed version, read modes/source age, immutable index and oversized warm bodies.
- [x] Implement bounded index/payload cache without scanning canonical issue bodies.
- [x] Run focused tests and commit with Task 2 once integration passes.

### Task 2: Two-phase collection

Files: `app/jira/body_search.py`, `app/jira/jira_client.py`, `app/local_mcp/read_tools.py`, related description tests.

Interface: existing search page schema unchanged. Internal optional known-key hint only, never added to MCP inputs. Body cache consumes discovery rows and fresh body-fetch timestamps.

- [x] Add failing tests for light discovery + missing-only hydration, query-independent reuse, cold known-key shortcut, body/version races and interrupted retry.
- [x] Replace body page copies with small cached discovery pages; hydrate returned rows with key searches of at most 10. Keep physical page validation and resource guards.
- [x] Preserve all requested metadata. Reject a changed source version explicitly rather than returning a falsely consistent snapshot.
- [x] Update existing expectations for the deliberate two-phase request pattern; preserve correctness assertions.
- [x] Run related MCP/Jira/cache suites; record actual REST call counts and commit.

### Task 3: Independent review and evidence

Files: tool descriptions and `docs/validation/2026-11-17-ticket-body-cache.md`.

- [x] Review auth/cache/size/race behavior independently; fix material findings with regression tests.
- [x] Record cold, warm, cross-query, mixed and interrupted REST request counts and limitations.
- [x] Check diff/status and development health; report implementation and validation, without claiming production deployment.

## Decisions

- Bounded light pages rather than exhausting a 10,000-ticket query for one requested body.
- Session identity and mutation generation conservatively partition body reuse; no unproven import from older canonical issue caches.
- Test/ledger artifacts live outside the repository per workspace rules; durable evidence is this plan and validation document.

## Verification and review decisions

- Core focused run: 98 passed in 62.68 seconds. Related expanded verification is recorded in the validation document.
- Independent review found missing aggregate enforcement across server-capped body pages; fixed with a 15 MiB-per-body regression that stops after the second physical body response.
- Small mixed known-key requests now use small per-ticket head hints, light discovery and missing-only body fetches. All-cold small batches retain one combined request and a pinned plan across server-capped pages.
- All known-key body batches pin authentication scope and mutation fence across pages. Both between-page regressions failed before the fix and passed afterward.
- Reduced max_results also narrows light discovery; heavy attachment/link projections retain direct bounded pages.
- Rendered UI verification exposed persistent partial panels after admission rejection. Safe GET/HEAD requests now retry at most twice, respecting Retry-After and the original timeout/abort. Writes are never replayed.
- Broader all-MCP exploration produced no usable verdict because the test runner failed decoding mixed-encoding stdout. Final related verification uses forced UTF-8 and loss-tolerant capture.
