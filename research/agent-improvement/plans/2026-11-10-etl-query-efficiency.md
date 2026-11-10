# ETL query efficiency: #188, #189 and #190

Status: approved for implementation on 2026-11-10; user added cool (7 days) to the goal and plan during implementation. This is a specification, not a claim of measured improvement.

## Goal and constraints

Preserve factual correctness, freshness and interruption recovery while reducing actual Jira REST attempts, MCP calls and measured model tokens. Prioritize correctness/recovery, Jira REST, tokens, then MCP calls; report tradeoffs rather than inventing a weighted score.

- Baseline: deployed v2026.11.09.2, commit 9b6fde102fb8420f2fd135d72b90b25a9af666c5.
- All model experiments and delegated reviews: gpt-6.1-sol, reasoning high.
- One existing checkout; no worktrees, source copies or production load tests.
- Preserve the user's editor changes in Git; initial state and stash SHA are recorded outside the repository.
- Test scratch uses a unique OS temporary directory, cleaned in finally. Durable research artifacts use C:/Users/PC/research/ltm-188-189-190; only lightweight specifications/reports are tracked here.
- Existing omission defaults, permission/write validation, query meaning and snapshot completeness remain compatible.

## Work packages

1. Common evaluation infrastructure and behavior-equivalent refactoring (H): actual transport/MCP/usage records, equal research_files access, public year world with independent oracle and neutral dead-code cleanup. Prove H equivalent to the deployed behavior using existing regression and HTTP/MCP/fallback/snapshot contracts. Source-age and cache-policy fixes discovered during row-resolution consolidation belong to #188, not H.
2. #188: explicit request freshness, provenance, retention, propagation and invalidation.
3. #189: request execution strategy, bounded physical queries, proven cross-decomposition reuse and scheduling.
4. #190: concise three-stream guidance, economical evidence collection and durable recovery.
5. Deployed reference plus the conditional four-cell deterministic comparison defined below, real-agent qualification, independent direct review and staged rollout.

Independent purposes receive separate commits/branches/PRs. Shared source is edited in the one checkout with explicit file ownership; Git branch pointers, not physical clones, preserve variants.

## Year world

Fixed KST interval [2025-11-01, 2026-11-01), observation tail through 2026-11-10. Stable seed and manifest; per-entity randomness must not reshuffle the world when one template changes.

Synthetic defaults (not asserted production statistics):

| Stream | Count | Meaning |
|---|---:|---|
| Development epics | 5 | Year-round ETL context |
| Development Task-tier/Subtask | 800 unique | About 100 distinct active per month, including carryover |
| Data operations | 864 | 72 new requests/month: load, modify, reload, suspend, delete, federation |
| Simple VoC | 576 | 48 new requests/month: questions, explanations, data checks |
| Other project issues | 7,755 | Meaningful other-module histories, similar names and cross references |
| Total | 10,000 | ETL subset 2,245 |

Generate a private event ledger and independently rendered public Jira descriptions, comments, changelog, links and Confluence revisions. The oracle reducer must not reuse product aggregation/parser code. Every critical expected assertion maps to accessible public evidence; inaccessible facts are unknown. No oracle IDs, answer labels or scope shortcuts leak into model-visible sources.

Include same-current-state/different-history pairs; occurredAt versus recordedAt; late corrections; reopen, cancel, transfer, hierarchy and assignee changes; multi-table partial completion; duplicate reporting; verified rename and environment distinctions. Hot issues have 200+ comments and 100+ histories with decisive evidence at different pages. All three streams cover normal/boundary, current/past and sufficient/insufficient evidence.

Keep current-reconstructed past, knowledge available then and actual published report revisions distinct. Historical WBS mapping is public dated evidence, not an invented Jira hierarchy. Preserve the product's SP-weighted WBS contract; separately label count-based child completion and parent status.

Development lifecycles must reflect realistic work, not only regular start/finish schedules. Author and verify at least these patterns: long dormancy followed by recent actual start; partial deliverables verified only in comments; sparse or absent comments with insufficient completion evidence; months of incremental progress; dependency/blocker waits; ownership handoff; reopen/rework; and a recent administrative edit without business progress. Mix these patterns within monthly and Task/Epic investigations.

Distinguish created, scheduled, actually started, actively worked, blocked, verified partial deliverables and final acceptance. Current status, elapsed time, updated timestamp, silence and comment count are signals, not standalone proof. A partial completion statement may establish named deliverables while leaving overall completion or a numerical percentage unknown when the denominator is missing. Contradictory sources require chronology and explicit uncertainty. The oracle must respect public evidence availability, and the manifest/scenario coverage matrix records which lifecycles are tested. Preserve the 10,000-issue/800-unique-development population; about 100 monthly active tasks is a realistic target rather than a reason to erase long-running work.

Confluence daily, weekly and monthly development reports are independent progress evidence. Include reports without Jira URLs/keys, renamed or paraphrased features, several Tasks aggregated in one report item and one Task split across multiple reports. Public project/module, functional scope, named deliverable, participant and time evidence must support the correspondence; a title similarity alone cannot establish it. Some mappings must remain ambiguous. Keep possible correspondence separate from verified completion, and preserve a many-to-many evidence ledger rather than forcing one report item to one Task.

Include a linked report with decisive proof, an unlinked but publicly resolvable report, an aggregated multi-Task item, a split Task report, same-name work in another module/environment, a genuinely ambiguous item, repeated daily→weekly→monthly reporting, late publication/revision and Jira/report contradiction. Separate occurrence date, reporting period and publication/revision time, planned work, reported implementation and accepted deliverables. Repeated reporting must not multiply completed work; superseded claims retain their original revision and correction trail. Private authoring associations never make a publicly ambiguous mapping certain.

Operations distinguish request tickets, completed tickets, requested targets, completed unique tables, work events and period-end active assets. A Done ticket is not proof every target completed. Query changes, reloads, stop and delete are separate events. Polaris views, external catalog tables and temporal many-to-many federation edges are separate entities. Simple VoC table mentions are not operational execution. VoC→development→operations links do not duplicate the same execution event.

## Public behavior

### #188

- cache_mode: live/hot/warm/cool/cold; explicit max ages 0/600/86400/604800/2592000 seconds. Omitted mode preserves existing resource policies.
- Existing force-refresh means live; conflicting explicit non-live options are rejected.
- Reuse one shared cache, never one copy per mode. Preserve original source collection times through row hydration, list reuse and snapshot assembly.
- Membership, fields, bodies, comments/history and decorative metadata have explicit provenance. A fresh component cannot launder an old one.
- Explicit valid hits do not schedule unnecessary revalidation. Expired, partial or invalidated data cannot become a compliant successful hit.
- live requires upstream validation and may share a safe current-generation in-flight request; completed warm/cold data alone cannot satisfy it.
- Separate retention from allowable age. Structured cache retention supports 30 days with an initial 512 MiB payload LRU budget; dependency indexes and generation fences survive consistently. This is not a physical SQLite file size guarantee.
- Propagate policy safely across async/thread fan-out. Recheck freshness and generation for joined flights.
- Keep frozen snapshot continuation distinct from a new live search. Preserve option/context identity.
- Confluence latest-revision validation is separate from immutable revision-body reuse. Do not relax write validation or authorization.

### #189

- Workspace discovery uses a bounded first page, including the server's smaller page cap. A preview is never published as exhaustive membership. It may reuse already complete whole-query or decomposed memberships only when all required cached rows, ordering fields, source ages and generation proofs are available; a cache miss does not trigger full hydration. Exact ticket-number candidates across configured projects are batched separately while retaining priority and visibility filtering.
- strategy: auto/off/people/full; max_parts integer 1..64, default 64. No redundant decomposition boolean.
- Distinguish logical leaves, unique physical JQL groups, HTTP pages and concurrency. The physical-query budget includes hydration and fallback, excluding repeated pages/retries of the same query.
- Plan once and pass the execution plan through search/snapshot creation. Reuse proven cache coverage, deduplicate missing keys and batch the remainder without extra cost-estimation probes.
- Sharing across strategies requires exact membership or a proven union/subset selector. No arbitrary local JQL evaluator or false empty leaf cache.
- off is one logical upstream search, possibly many HTTP pages. All strategies preserve complete keys/order/total and safe fallback behavior.
- Preserve the 64 logical DNF safety limit separately from physical-query and pagination limits.
- Serial providers remain serial; parallel-capable providers use shared bounded scheduling with cancellation/yield between pages. Initially retain the deployed concurrency ceiling of 8; compare candidate 4 in the performance experiment before promoting a default change through the full qualification gate. Shared admission must prevent independent request pools from multiplying that ceiling.
- Existing get_tickets max 100 and include_description=false remain. Fetch needed bodies in batches; full→light can reuse, light→full must prove coverage or fetch.
- Return requested/effective strategy and policy, fallback reason, leaf/group/page counts, cache and provenance diagnostics.

### #190 and refactoring

- Shared _research references are a single source remapped into two skill packages; retain this structure.
- Add concise development/operations/simple-VoC decision guidance and optional domain references, avoiding duplicated universal rules.
- Development guidance distinguishes long wait/recent start, incremental or partial completion, blocked work, sparse evidence and reopening; do not infer no work from silence or completion from status/recency alone.
- Search Confluence reports by the requested period/module and reuse the discovered report set across Tasks. Read relevant report sections/revisions and record correspondence evidence once. Expand ambiguous matches selectively; avoid one independent report search or full document reread per Task. Direct Jira linkage is useful evidence but not a requirement for finding relevant progress reports.
- Reuse returned fields and evidence; collect history only where required. A historical period alone does not authorize cool/cold freshness. cool is appropriate only when a maximum source age of 7 days is acceptable, not merely because a report covers the previous week.
- Large investigations persist scope, per-group state, immutable evidence chunks, source age and cursor/snapshot identity. Avoid repeated wrappers and whole-file rewrites/rereads.
- Finish the failed group's missing collection before starting the next group. On unrecoverable failure persist incomplete state and keep final totals unknown. Revalidate invalidated snapshots after restart; file existence does not bypass freshness.
- Keep investigation progress separate from the user's business progress.
- Extract a cached-row resolver shared by readiness/hydration; readiness never fetches or publishes business data or resets source age. Ordinary LRU access bookkeeping remains allowed.
- Extend the existing JQL planner as sole owner of grouping/proof/plan selection. Keep transport, fencing and snapshot publication in the client.
- Share metadata reducers while preserving explicit HTTP/MCP source selection. Generic freshness policy and JQL dependency policy stay distinct.
- Remove only proven unused definitions/obsolete fixture hooks after reference and compatibility checks; retain legacy fallback and old snapshot compatibility.
- Reuse and consolidate existing normal/recovery measurement and file-provider code.

## Metrics and validation

Actual Jira REST attempts include auth probes, retries, pages, hydration and attributable background work; Confluence is separate. Distinguish dispatch attempts from server-received requests where relevant. Record all MCP attempts (including file tools) and discovery protocol requests separately. Preserve unknown usage as null; cached-input/reasoning counters are subsets, not extra tokens added to total. Never report replay-only savings as actual agent token savings.

Cache priming, failed attempts and recovery phases remain in cumulative cost records. Report empty-cache separately from the cold 30-day policy. Freeze clock, data, commit, versions, model, skill, cache initial state and run order. Background work has an explicit completion/timeout record; an unsettled run cannot silently omit its tail.

All five cache levels share storage and provenance. Include cool boundary tests just before/at/after 604800 seconds, cross-mode reuse (warm expired/cool valid; cool expired/cold valid), mutation invalidation and restart retention. Adding cool does not change omitted policy defaults or automatically relax current progress/report freshness.

Deterministic comparison retains H (deployed product plus measurement infrastructure) as a separate reference. Implementation review established that #189 requires #188-owned atomic value/provenance reads, persistent row identity, captured source age and validation propagation. These corrections change behavior and must not be silently moved into H. The reviewed shared foundation F also includes the inseparable cache core, retention/LRU and dependency invalidation, internal request-policy validation, snapshot/MyTasks corrections and client comment/timeline policy handling. A is the remaining public HTTP/MCP freshness interfaces and Confluence/evidence/hierarchy propagation. Compare four cells F, F+A, F+B (#189 query execution), F+A+B using common omitted freshness semantics. Report H→F separately; full #188 is H→F+A, not F→F+A. Common direct-backend workloads may have identical F and F+A costs because their cache core is shared. F+B is not an independently applicable H+#189; report only conditional #189 effects and the complete H→F+A+B change. Exact prerequisite commits and identities must be recorded before runs. Use one checkout with Git commits/stash/cherry-picks; no source copies or production feature switches for the experiment.

Explicit new modes get separate conformance tests. Eight journeys cover bulk initial search, person filters, Task/workload/WBS navigation, whole/split reuse, projection upgrade, TTL/mutation/20-day retention, interactive cancellation/scheduling, and interrupted pagination/restart. Empty cache 5 repeats, valid-cache 20; representative actual UI journeys 10. Functional latency 0; performance 0/250/800 ms and seeded jitter. No full Cartesian product of every user case and technical state. Compare cost only at equivalent correctness; an incorrect cheap H result is a defect, not an efficiency target.

Real-agent A=current backend/current deployed #190; B=both backend changes/same skill; C=both/backend plus enhanced skill. Every arm receives equal research_files capability. Pilot: 6 representative cases×3 arms=18. Formal main: 28 cases×A/C×5=280; B on the same 6 representative cases×5=30; 6 recovery cases×A/C×5=60. Formal total 370, first round 74. B pairs use the same case/seed/repeat as A/C. Pilot is separate.

Pilot design is frozen at battery 2.0.1 after independent public-evidence correspondence review and the corrected equivalent-history/comment proof gate. The six cases are F03-boundary, F04-boundary, F08-normal, F09-boundary, F12-boundary and F14-boundary. This freezes representative coverage and the schedule only; factual, performance, direct-review and rollout gates still require actual runs.

Fourteen families, each normal/boundary: daily, weekly, monthly; WBS, Epic, Task, Subtask progress; new load; modification/reload; stop/delete; federation; simple VoC; linked request→development→operations; historical knowledge/correction. Recovery cuts search/comments/history, group boundary, client restart, backend restart.

Hard gates: required automatic contracts 100%; material false completion/count/contribution/freshness and unauthorized writes 0; direct review mean >=4.5/5 per stream, no averaging away major defects. All strategy keys/order/total agree; no incomplete-success cache entries. Fully covered valid hits have zero business-data REST requests. Equivalent-quality deterministic empty/expired/mutated/recovery requests do not increase versus H; predefined overlap/reuse cases strictly improve. UI p95 regression exceeding max(100ms,10%) prevents default promotion. Efficiency claims require observed paired data; disclose missing usage and per-stream results.

## Rollout and recovery record

Post-pilot repairs preserve the original results and create a new prospective condition: ETL main/recovery contracts are 3.0.0; the shared work checker is 4.0.0. The host accepts only bounded equivalent evidence, clarifies module/environment scope independently of period relevance, and retains the actual undated-plan failures. Candidate skills are 0.1.9. The versioned evidence wire format retains exact content/history/non-actor metadata and supplied actor identity fields while excluding actor profile presentation fields; raw caches remain unchanged. This transport optimization belongs to the remaining public/resource A package and must be disclosed when attributing H-to-F+A effects. Unknown wire formats fail closed; supported-format chunk changes require restarting the affected record.

Prospective formal deadline amendment after the retained A/B/C pilots: use 1,200 seconds per model invocation uniformly across A/B/C, including each recovery phase, with the existing 90-second preflight. Outer limits are 36,000 seconds for A/C 28-case batches, 9,000 for B's six cases, and 3,000 for a two-phase recovery case. This is headroom for necessary evidence reading and final delivery, not a measured minimum or an efficiency claim. Preserve every original 600-second attempt, its failures and unknown usage; do not extend an attempt in flight or automatically retry until passing. The separate application 35-second inactivity responsiveness requirement remains unchanged. Update and verify the actual launcher arguments and recorded conditions before formal execution.

Order: baseline/metrics → world/oracle/battery → equivalent refactor → #188/#189 → #190 and pilot → first complete round/direct review → staged initial release → five-repeat qualification.

The initial release exposes verified explicit options and skill improvements; new automatic cache/JQL selection/default changes wait for five repeats. A first round counts toward five only when commit/config/manifest remain identical. Fixes create new groups; focused successes never replace primary failed runs. Preserve original raw attempts and publish lightweight reports with limitations. Follow the user's existing staged-release authorization, and release the application repository only.
