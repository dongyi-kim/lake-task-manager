# MCP Automatic Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task; use an independent Sol/high reviewer for the lifecycle and final diff.

**Goal:** LTM detects an exited or unresponsive MCP worker in the background and restores service without requiring a second client connection or replaying business operations.

**Architecture:** Keep health supervision on the app event loop, outside the MCP transport thread. A generation owns its SDK applications/session managers, thread, loop and in-flight transport requests. After an old generation has fully stopped, construct fresh SDK session managers and restore the existing URLs/token/catalog against the same live Jira client/cache. If an unresponsive thread cannot stop, expose failure to the existing owned-process watchdog instead of creating an overlapping worker.

**Tech Stack:** Python asyncio/threading, existing MCP SDK2, FastAPI/Starlette, existing owned-worker supervisor.

**Spec:** User reports LTM alive with dead MCP worker; requests MCP health/restart tools, then chooses LTM background health checking and automatic recovery instead of a separate recovery connection.

## Global constraints

- One existing checkout, no worktrees/source clones. Product branch separate from evaluation PR192 and CI budget PR195.
- Normal app port4457; unit ASGI/explicit port0 fixtures only. All pytest scratch/cache in OS temp with finally cleanup.
- No Jira/Confluence/auth/network requests for heartbeat. Busy business lanes/upstream failures alone are not dead MCP transport.
- Never replay a Jira/Confluence mutation, rotate credentials, wipe caches or discard persisted receipts during recovery.
- MCP SDK StreamableHTTPSessionManager.run is single-use: create new managers for replacement generations.
- Never start a second worker while an old worker can still execute requests. Preserve capacity ownership until real operations finish.
- A restart acknowledgement means successful final ASGI response-body delivery and SDK teardown, not merely return from the tool function. A disconnected/failed acknowledgement cannot activate its pending restart.
- Automatic recovery and explicit restart share one serialized lifecycle; no recovery after app shutdown; bounded restart attempts.
- Existing native/fallback skills, profiles, auth guard, input/output schemas and URL paths remain supported.
- All real experiments/reviewers use gpt-6.1-sol/high. V7 qualification is held before any model call until this feature is verified.

## Task1: generation lifecycle and background supervision

- [x] Add real SDK regression: stop only the MCP transport while a live app route remains200; observe a new generation and working tools at the same URL/token, with unchanged client/cache identity.
- [x] Add blocked-loop and busy-business tests: UI remains responsive, busy Jira work alone does not trigger replacement, and an old live worker cannot overlap a replacement.
- [x] Refactor transport_host lifecycle into serialized generation start/stop; allow runtime to supply freshly built SDK apps on replacement. Keep existing bridge cancellation/admission invariants.
- [x] Add periodic loop-pulse checks (no business APIs), consecutive-miss handling, bounded recovery3/600seconds, and stop/join on app shutdown. Defaults may reuse existing3second runtime-stall policy with test-injected timings.
- [x] Run focused transport/responsiveness/capacity tests outside repository scratch.

## Task2: runtime integration and observable recovery

- [x] Rebuild only SDK servers/session managers/profile apps from existing compiled tool descriptors and packaged skill catalog; retain Jira client, caches, receipts, credentials and business-lane ownership.
- [x] Resolve mounted profile endpoints to the current generation. New sessions rediscover the same catalog/skills without another connection setting.
- [x] Keep payload-free failure/recovery diagnostics and make runtime monitor resolve the current MCP loop after replacement.
- [x] Wire an unrecoverable MCP transport into app liveness so the existing owned-process watchdog can replace the backend. Inspect normal launcher modes and extend supervision where required; keep dev hot reload behavior explicit.
- [x] Apply owned-process supervision to normal tray, plain and non-tray window modes; current launcher only supervises the tray branch. A 503 alone is insufficient in other modes.
- [x] Tests cover fresh SDK managers, profiles, native/fallback guides, disabled MCP, repeated failure cap, monitor generation changes and shutdown without a spurious recovery.

## Task3: original health/restart tool request

- [x] Expose no-Jira health snapshot through all existing profiles; distinguish starting/healthy/recovering/stopped/unresponsive/recovery-failed and generation.
- [x] Expose a restart request using the same serialized background lifecycle. Bind it to the observed generation so retries cannot restart a later generation; coalesce duplicate requests and return before stopping its own response transport.
- [x] Register explicit output schemas, control-lane/auth exemptions, and tool descriptions explaining that business calls are never replayed. No separate MCP endpoint is required.
- [x] Real SDK tests validate output schemas, no Jira/auth calls, delivered restart acknowledgement, deduplication and eventual tool recovery.

## Task4: review and delivery

- [x] Add a system-tray "상태 확인" item showing app/MCP state, automatic recovery and recent recovery result without Jira/auth calls. Use the existing coalesced background tray-job path; the status dialog must not block Quit/Open or the tray message pump.
- [x] Unit-test health/action wiring and blocked app/MCP states. Native Windows dialog text verified via accessibility for recovering/failed states; Enter closes both. Screenshot failed with FrameArrived timeout; full menu/visual verification remains unproven.
- [x] Independent Sol/high lifecycle/code review; implement material findings and run affected tests.
- [x] Update runtime/MCP documentation and concise user-facing release-note draft as appropriate; do not publish an additional release without current authorization.
- [x] Split dependent product PRs: lifecycle197 (<500 lines), integration198 (~259 lines), operator/status follow-up (<500 lines).
- [ ] Full offline CI on final product heads. Merge finished candidate into eval192 and freeze a new source before resuming full302-case work. Prepared v7 remains held.

## Review focus

- A stopped SDK manager must not be reentered, including profile managers.
- Slow/hung writes must not be duplicated or freed early when transport cancels.
- Old-generation callbacks must not mark a replacement healthy or release its permits.
- Recovery must work when UI is healthy and MCP dead; a health tool inside a dead worker is insufficient.
- Pending manual recovery, startup failure, shutdown and automatic recovery must not race into two live workers.

## Additional observed client failure

The user reports Claude Desktop remains at "no such tool available server disconnected"
even after LTM restarts. Existing stdio.py terminates its proxy on a transport error,
which a backend-only restart cannot replace in the client's process. The production
PC/config is not accessible here (workspace PC has no4457 listener or Claude config).
This is a supported-code diagnosis, not proof of the remote incident's exact cause.

- [x] Add a real stdio/HTTP outage-and-return regression using the same client session.
- [x] Keep Claude's stdio process alive while HTTP LTM is down; the next caller opens
  a fresh HTTP connection with retained protocol state. No hidden retries or reconnect loop.
  Resolve every interrupted request explicitly;
  never resend a business call or invent successful/empty responses after uncertainty.
- [x] Preserve stdio cancellation/EOF shutdown, local URL/token boundaries, all profiles,
  current catalogs/resources and sanitized error reporting; no app auto-start/mock fallback.
- [x] Record that Claude Desktop may need one full restart to replace the already-dead
  older proxy; verify repaired proxy behavior rather than claiming the existing client is fixed.

Final review follow-up: delayed-owner startup cancellation and stale/finished health
were RED before fixes; lifecycle/capacity14 GREEN6.39s. Operator/core37 GREEN7.61s,
plus actual blocked MCP-loop/no-overlap1 GREEN1.85s. Sol/high focused re-review
confirms both Important findings resolved; no new Important/Critical. Local suites
overlap; these counts are not an aggregate. No real LLM or external Jira call.
Final combined relevant suite:92 passed105.05s, including auth/profile/stdio,
blocked loop, operator status and release-note shell contracts. PR196 full CI passed.
