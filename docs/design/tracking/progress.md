# Tracking implementation ledger

Plan: docs/design/tracking/plan.md; base 7e8e9efc; branch feat/tracking.

| Work pair | Shared contract | Ruling |
|---|---|---|
| Backend / UI | endpoints, camelCase run snapshot | agree wire examples before frontend binding; backend owns routes/main, UI owns static files |
| Backend / mock | Jira 8.20.8 search/comment/history semantics | mock implements upstream contracts; collector retains unsupported/partial fallback |
| Mock / performance | realistic 15 people, 10 Epic, 30 Task, 60 Sub-Task | persistent fixture source, OS-temp runtime, cold/warm separate |
| Backend itself | exact completion vs low call count | history required for completion/period assignee; other field display adds no collection |
| UI itself | dense hierarchy and complete functionality | distinct evidence region, square folding, local highlight/filter, no read-ack on mere listing |
| Tests / workspace | temp writes and branch isolation | user rules override stale AGENTS/skill worktree and temp paths |

## Status

- [x] Planning and generated preview prepared.
- [x] Backend + routes + semantic tests.
- [x] UI + integration + browser verification.
- [x] Mock parity + performance fixture and measurements.
- [x] Independent review + targeted regression validation.

## Final decisions and validation

- Date-only inclusive KST period. Module uses current component/WBS/ancestor membership; explicit people use historical responsibility or own activity.
- Raw evidence cache is shared across date ranges and projections, keyed by issue revision. Every new run refreshes lightweight membership/revision; completed source pages survive interruptions.
- Exact completion and assignment require history. Displaying other fields from that history adds no REST call. Truncation or missing identity remains partial, never zero.
- Mock Jira 8.20 search comment/changelog, historical JQL, creator and comment author metadata implemented in the app-owned compatibility layer. No installed package patch or invented Cloud endpoint.
- Independent review caught and verified fixes for DC username/key aliases, missing ancestor retry, inherited module projection, and stale analyzed rows after resumed scope resolution.
- Removed repeated full-checkpoint serialization per source request. Persist completed pages/raw sources; coalesce derived UI snapshots at the first result, batches, or elapsed intervals.
- Targeted integration/static/query/domain/parity suite: **723 passed**. After final scope fixes, Tracking backend/UI/mock subset: **49 passed**. These overlap; they are not a full repository suite or an additive total.
- Browser: light 1600px / dark 1360px, no JS errors or horizontal overflow, folding/highlighting/filter/feed switching made no Tracking API requests, existing TicketDialog opened.
- [Final local benchmark](performance.md): 15 people, 100 active + 9,900 archived tickets, 50ms injected/request. Cold/warm separated; all compared core events matched the fixture oracle.
- Production Jira/SSO latency and actual LLM calls remain unmeasured. No push, merge, or release performed.

## Report UX follow-up

- Default report view replaces event-count reading with Task-level outcomes, completed child titles, recent report/comment excerpts, current assignee/status, and evidence links. Existing tree/date views retained.
- Dashboard separates period completion history, newly created tickets, comments, field changes, and evidence-backed review items. Partial counts remain provisional; no inferred progress percentage.
- AI synthesis area remains visible in an unconfigured state. No automatic AI call. Pins persist locally; copying reports includes scope/time/partial status and ticket-specific reopen/recompletion evidence.
- Real VOC flag/configured component only: virtual `사용자 VOC` WBS → `[대분류 - 소분류]` virtual Epic → stripped display title. Original summaries, saved run, events and totals preserved. Virtual Epic highlight/filter supported; ordinary prefixed tasks remain unchanged.
- Otherwise unmapped VOC may use current assignee's configured module membership. Explicit module/WBS/parent mapping takes precedence; missing assignee proof remains uncertain. Lightweight discovery includes assignee without a separate REST call.
- Independent review fixes: synthetic missing-parent briefs open all child evidence; partial review counts do not show definitive zero; copied completion lines retain reopen state; assignee supplement does not override explicit other-module mapping.
- Final related tests: **441 passed** (Tracking backend/UI + static assets). Browser: real dev data at 1600/1360px, light/dark, pin persistence, copy, evidence pane, tree/feed switching, no JS errors or horizontal overflow. Interaction loop made **0 extra Tracking requests**.
- Separate synthetic browser scenario verifies two VOC categories, prefix removal, preserved ordinary prefix, virtual Epic filtering and tree rows. No server data modified by that scenario.
- Live mock collection for ETL, 2026-10-01 through 2026-11-15: complete, 137 context/target issues including 7 VOC. November-only fixture has no updated ETL VOC, so it correctly has none. Tailscale health returned 200.
- Browser plugin unavailable; used existing Python Playwright/Chromium. ui-skills CLI unavailable (`npx` absent); applied installed frontend-design guidance and existing LTM design tokens. Desktop only, as requested.

## Ticket-name correction

- Existing snapshots already contained titles; date feed, cross-ticket evidence, comment attribution and copied source lists were rendering only keys. Shared title/label helpers now render title first and key as secondary context. Unknown titles say `제목 미확인` rather than presenting the key as a title.
- Applied to report headings, child changes, tree rows, highlighting choices, AI evidence references, missing-source rows and all copied ticket references. Original evidence and VOC display grouping preserved; no new collection.
- Related frontend/static tests: **399 passed**. Tailscale browser verified all 330 date-feed references had titles, 8 child-comment source labels and 2 cross-ticket evidence labels included names, no JS errors/overflow, and no additional Tracking request.
