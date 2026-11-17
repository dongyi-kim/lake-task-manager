# Ticket body cache validation — 2026-11-17

The new collection path discovers lightweight ticket metadata first, then fetches only missing descriptions for the returned page. Bodies are shared by authenticated session, ticket key and `updated` revision rather than by the search query. This validates the local implementation on `feat/ticket-body-cache`; it does not record a production deployment.

## Measured REST requests

The deterministic benchmark uses 100 tickets with distinct keys and 4,128-byte ASCII descriptions. Each MCP response contains ten tickets. The source implements Jira search filtering, field projection, `updated` revisions, ordering and server page caps. It records actual `provider.get_json("/rest/api/2/search", ...)` calls, rather than inferring REST work from MCP tool calls. Scalar issue GETs fail the fixture.

| Collection of all 100 tickets | Metadata REST calls | Body REST calls | Total REST calls | Local elapsed time |
| --- | ---: | ---: | ---: | ---: |
| Cold query | 1 | 10 | 11 | 303 ms |
| Identical warm query | 0 | 0 | 0 | 101 ms |
| Different query, same keys and revisions | 1 | 0 | 1 | 114 ms |

The cold metadata request asks for 100 lightweight rows. Body requests use explicit key predicates and request at most ten tickets. Both warm phases compare complete descriptions against the source and verify every expected key exactly once. The changed query adds a label predicate that retains the same 100 tickets.

These timings come from the real MCP SDK and local SQLite cache on Windows with Python 3.11 and a controlled provider. They exclude catalog discovery and environment construction. They are correctness evidence and a local measurement, not a forecast of Jira network latency or production throughput.

## Regression coverage

The independent cases in [`test_two_phase_body_collection.py`](../../tests/mcp/test_two_phase_body_collection.py) verify:

- Broad discovery never builds a whole-query body snapshot, and hydration is restricted to the returned ten keys.
- An identical warm query performs no REST work; a different query reuses unchanged per-ticket bodies.
- A cold three-key `get_tickets` request retains its single-REST-request shortcut. After those bodies are cached, a broader page hydrates only its seven missing keys.
- A new small `get_tickets` batch mixing cached and uncached keys discovers metadata first and fetches only the missing body. Identical warm batches still need zero REST calls. Small head records guide planning without loading bodies or contributing unrelated source timestamps.
- Authentication or mutation-generation changes between output pages reject the whole known-key batch, including batches larger than ten, instead of returning mixed-scope observations.
- Returned null and empty descriptions are known empty. An omitted description stays unknown and is requested again.
- A changed `updated` revision fetches only that ticket's body. A revision change between discovery and hydration produces `query_state_changed`, without a falsely consistent success, and a retry refreshes discovery.
- `cache_mode=live` revalidates both metadata and bodies and reports compliant upstream validation.
- A failed later body request leaves the first successful server-capped chunk reusable. Retrying fetches only the seven remaining keys, with no metadata refetch or scalar GET fan-out.
- A metadata server cap of four and body cap of three preserve all 13 tickets, using actual output cursors `4`, `8`, and `12`.

The migrated [`test_prefetch_body_pages.py`](../../tests/jira/query/test_prefetch_body_pages.py) retains six full-ticket prefetch checks: complete server-capped batches, source-age preservation, no false upstream validation on warm copies, retained successful rows after a later failure, and no scalar fan-out under queue or memory pressure. Full prefetch explicitly includes attachment/link fields, so it keeps bounded direct pages instead of asking for 100 heavy rows.

## Verification record

Before integration, all nine initial independent cases failed against the previous query-local body page cache; the added metadata-cap case also failed on its old `3, 6, 9, 12` cursors. Those failures established the behavior the new implementation needed to change.

The fresh measured run executed the six prefetch regressions plus the 100-ticket benchmark: **7 passed in 2.27 seconds**. The benchmark emitted the counts and timings above as `TWO_PHASE_BODY_REST_LEDGER` and a JUnit user property. The complete independent integration file also passed all ten cases during implementation verification.

A later combined run of all ten independent integration cases and six prefetch regressions passed **16 tests in 16.07 seconds**. Final related verification passed **573 tests in 224.11 seconds, with no failures or skips**: all Jira query tests, selected MCP body/cache/query/capacity cases, three runtime cache files, mock-world/local-parity checks, and frontend API retry/auth tests. An earlier broad harness had unusable output because of an encoding failure; it is not counted as a successful run.

All pytest temporary data, cache files and application runtime files were isolated under an OS temporary directory and removed when the process exited. No real Jira or LLM requests were made.

### Full-suite resource admission isolation

The first PR CI run ([44084066644](https://github.com/dongyi-kim/lake-task-manager/actions/runs/44084066644)) finished with 726 failures and 85 setup errors. Of those 811 failure/error records, 551 explicitly reported resource pressure or admission refusal, including all 85 setup errors. The full pytest process shared the production memory sampler and cached pressure state; its accumulated fixture/module memory and runner headroom could therefore refuse unrelated functional tests. The report did not capture the triggering memory counters, so it does not establish which threshold was reached.

The suite now supplies controlled memory counters to the shared monitor and isolates its cached state per test. Production code, thresholds, admission checks and hysteresis remain unchanged. A real HTTP regression exercises healthy admission, refusal without executing a write, continued refusal within the recovery margin, and successful recovery. Native OS counter tests continue to use `sample_memory()` directly. Independent verification of this regression, pressure policy and CI policy passed **9 tests in 0.12 seconds**.

## Limits retained

- The output page remains advisory at at most ten body-bearing tickets; metadata discovery is at most 100 rows, with actual server caps respected.
- Complete body responses remain limited to 16 MiB. Oversized pages fail explicitly; descriptions are never truncated to satisfy the limit.
- The limit is checked cumulatively while hydrating server-capped pages. Ten 15 MiB bodies with a one-ticket server cap stop after the second body response; only the first validated body is published. Smaller requested pages also narrow metadata discovery.
- Missing versions cannot establish cross-query body reuse. Missing bodies and missing tickets remain explicit, separate states.
- Results are current observations with a null snapshot cursor. Concurrent upstream edits can change page membership; the cache is not a frozen historical report.
- Session changes, mutation fences, source-age policies and resource admission remain part of cache eligibility. Reuse does not turn an older body into a new observation.
- Body payloads are content-addressed so unchanged revalidations do not accumulate duplicate copies. Provider/session resets conservatively partition reuse; older canonical issue caches without that session proof are not imported.

## Rendered UI verification

The actual running development app was exercised at `https://desktop-k634qao.tailff5611.ts.net:8443/`, with `http://127.0.0.1:4457/` used for comparison. Python Playwright 1.62.0 drove the installed Google Chrome 157.0.8103.39 at 1440 × 1000. The Browser/CUA host reported `The browser access host is unavailable`; the authorized regular-Playwright fallback reused the installed browser without downloading dependencies. The development worker hot-reloaded during verification; the later live-health probe identified PID 16336 and returned `status: ok`.

| Actual interaction | Result |
| --- | --- |
| Task tab and ticket list | 26 rendered cards; meaningful content and no framework error overlay. |
| Open DL-9020, read description, close and reopen | Identical rendered description; SHA-256 `cbc26551ed775ed74aba943ada99397445fcbc08bf912b1a2930370258347035`. |
| Localhost DL-9001 rich description | Table and code rendered; no failed API responses, console errors or page errors in that comparison flow. |
| Tracking saved result → DL-10024 evidence | Native status timeline and activity summary rendered; seven rich comment tables and seven code blocks; comment expansion exposed the rendered table/code without displaying raw HTML tags. |
| Fresh Tracking 조회 on Tailscale, ETL, 2026-11-10 through 2026-11-17 | Visible collection returned HTTP 202, then completed with 184 issues, 769 events, `coverage.complete: true`, and four visible WBS summary rows. No failed API response or JavaScript page error occurred during this fresh collection. |

Cold ticket-dialog fan-out exposed a real UI issue: local admission returned HTTP 429 for auxiliary panels, and the original browser transport left a partial-information warning until the dialog was reopened. The transport now retries only safe GET/HEAD reads with explicit `retryable: true` and `executed: false` admission proof, at most twice, while honoring `Retry-After` and the original cancellation/deadline. POST/PUT/DELETE and mutation-marked reads are never replayed.

The actual Tailscale UI was then tested with two deliberately injected 429 responses for DL-9002's children endpoint before allowing the real backend request. It made exactly three attempts, waited 1.05 and 1.02 seconds between attempts, and completed without the partial-information warning or busy error. Description content remained identical after closing/reopening. Other naturally refused auxiliary GETs also recovered. Screenshots of the initial warning, recovered ticket, rich Tracking evidence and fresh completed collection were inspected.

The focused API retry/auth regression run passed **30 tests in 6.25 seconds**, using the Node 24.18.1 binary already bundled with Playwright. The new cases were first observed failing before the retry change. Existing rendered browser regressions for rich/legacy Tracking comments on summary, feed and detail surfaces plus the TicketDialog read/close/reopen lifecycle passed **7 tests in 15.58 seconds** with installed Chrome.

The Tailscale console was **not globally error-free**: `/api/tickets/activity` returned HTTP 403 because its existing local-request Host/Origin guard accepts the local app origin, and deliberate/transient 429 responses produced expected browser resource-error logs. That activity guard was left unchanged; the localhost comparison had no such 403. There were no JavaScript `pageerror` events in the tested actual-app flows. No Jira mutation or LLM summary generation was submitted; the fresh Tracking action created only the authorized local collection. Temporary browser scripts and screenshots were kept outside the repository and removed after inspection.
