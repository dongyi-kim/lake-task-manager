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
