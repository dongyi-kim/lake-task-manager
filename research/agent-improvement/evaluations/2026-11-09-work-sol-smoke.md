# Work skills: Sol high exploratory smoke

Status: candidate remains disabled by default. These are exploratory smoke results,
not the five-repeat A/B or cold-recovery qualification. No quality-gate claim is made.

## Execution identity and retained evidence

- User selected **gpt-6.1-sol / high**. Codex CLI 0.167.0, existing ChatGPT login.
- Frozen commit: `3e716a05b91ae555a37fd10c798fbf603324c4b2`.
- Work battery 0.1.1, prompt `codex-mcp-work-v1`, protocol 3.0.0, rubric 2.0.0.
- Fixture date 2026-11-09, Asia/Seoul; isolated read-only Jira820/Confluence mock,
  private cold cache and HTTP MCP on port 4457 per case. No production Jira or LTM LLM.
- Same normal R01–R11 cases in default and fallback transports; no research file
  capability in these answer-only cases. Oracle and actual MCP responses are retained.
- Durable evidence root outside the repository: `C:\Users\PC\research\ltm-issue-190`.
  Each run retains its original raw JSON, claim, source snapshots, events and launcher log.
  The finished artifacts were moved from OS temp without changing their contents.
- Run groups: `20261109-sol-high-smoke-default-e4deee74` and
  `20261109-sol-high-smoke-fallback-e4deee74`.
- Before the user's model change, an Astra/ultra exploratory attempt completed five
  cases and was stopped during the sixth. Its original raw and interruption record
  are retained under `20261109-work-smoke-default-e4deee74`, excluded from every Sol
  comparison. No subsequent experiment uses Astra.

## Original quantitative results

These numbers preserve the original 0.1.1 checks, including their disagreements
with direct review. They have not been retroactively replaced with corrected scores.

| Transport | Completed | Automatic pass | Candidate entrypoint read | Answer time | p50 / p95 | MCP calls |
|---|---:|---:|---:|---:|---:|---:|
| default | 11 | 7 | 9 | 440.1s | 38.84 / 58.27s | 193 |
| fallback | 11 | 8 | 9 | 436.8s | 40.90 / 57.96s | 187 |

| Transport | Input tokens | Output tokens | Total tokens | Cached input tokens |
|---|---:|---:|---:|---:|
| default | 2,243,079 | 15,484 | 2,258,563 | 1,780,224 |
| fallback | 2,209,874 | 14,815 | 2,224,689 | 1,806,336 |

Preflights are retained separately and excluded from the answer-time/token table.
CLI model-call count, USD cost and time to first token were unavailable, recorded as
null. MCP calls are not model-call counts. All 22 case snapshots remained unchanged;
no forbidden tool event or unauthorized write was recorded.

## Direct review and checker disagreements

The root Codex work agent directly read all 22 final structured answers and relevant
actual source records. A separate `gpt-6.1-sol / high` reviewer independently examined
the disputed raw cases. This is not an LTM model judging its own answers. Complete
per-checklist qualification scoring remains pending; no 4.5/5 claim is inferred from
automatic passes or this smoke summary.

| Case | Observed answer behavior in both transports |
|---|---|
| R01 | Three-ticket LOAD-1 → CHANGE-1 → STOP-1 history, plan versus execution, report/resolution times distinguished from actual execution time. |
| R02 | Two unique tables from three tickets; same LOAD-1 re-report excluded from additional table/event count. |
| R03 | One today's activity ticket; unfinished validation separated from prior-day obstacle and workaround. |
| R04 | Child-only 1/2 = 50%, parent status preserved, three inspected tickets. |
| R05 | Actual test.ui01 work separated from current test.ui02 assignee; missing transfer/execution time disclosed. |
| R06 | Two people's contributions recognized without summing their participation into two unique LOAD-1 events. |
| R07 | New 3/1/2/2, improve 2/0/1/2, stop 1/0/1/1 across ticket/VoC/table/event columns; union tables = 2. |
| R08 | Assignee/reporter test.ui01 separated from developer test.ui02 using actual comment 61010. Citation representation needs clarification below. |
| R09 | Verified rename is one table; separate RENAME-1 original and exact execution time not claimed as read. |
| R10 | Revision 1 and current states compared in original two-column format. Explicitly labels 1/3 across all tickets and 1/2 across children, without claiming workload progress. |
| R11 | Original daily-report columns retained; yesterday's 61008 included, today's 61009 excluded; next work labeled a draft suggestion. |

No material false underlying fact was found in these normal-case final answers.
This observation does not establish boundary-case completeness, recovery success,
production accuracy, comparative improvement, or a qualifying average score.

| Original failed case | Original flag | Direct-review decision and prospective correction |
|---|---|---|
| default R01, default R09, fallback R08, fallback R09 | `candidate_skill_not_read` | Non-adoption/routing noncompliance. Task facts remain correct. Report exposure versus observed reads separately and retain nonadopters in primary intention-to-treat A/B. Never claim actual guide execution for them. |
| default R08 | `source_not_observed` | Same actual comment ID 61010 and returned relative `self`; same ticket's canonical origin was observed. Exact bounded normalization is legitimate. The old three-kind schema did not explicitly represent comments. |
| fallback R08 | `source_not_observed` | Actual comment facts exist, but source ID `DL-94010/comment/61010` and focus-link form were synthesized. Preserve as an ambiguous representation-contract defect, not invented underlying evidence. Define actual IDs and separate comment citations prospectively. |
| both R10 | `progress_mismatch` | Hard-null oracle was unjustified by the request for current progress. Permit optional, explicitly labeled ticket-count ratios only after complete hierarchy/status evidence and valid arithmetic. Keep incomplete cases null and explicit child-only requests child-only. |

## Next battery contract

Work battery **0.2.0 / prompt v2** records this metric-contract change prospectively.
It does not overwrite 0.1.1 attempts or convert automatic results into qualitative scores.

- `skillApplication` and summary adoption counts distinguish offered guides from
  successful entrypoint/resource reads. Read-only answer quality and adoption are
  separate. Recovery's explicitly requested guide/file protocol remains mandatory.
- Work-only source schema supports `kind=comment` with the actual comment ID.
  Parent-ticket citation plus comment ID in the reply remains valid. Relative `self`
  normalization requires the same observed ticket origin, ID and comment path;
  foreign hosts, mismatched tickets, failed reads, synthetic IDs and focus links fail.
- R10 count progress requires complete paginated hierarchy, directly observed
  matching statuses, a named denominator and correct arithmetic. It does not accept
  an inferred workload percentage or relax other cases' progress contracts.
- Qualification records explicit fixed planned repetitions and candidate-order
  index; treatment belongs in candidate identity, not the shared benchmark identity.
  Invalid/dirty qualification is rejected before model calls. Historical exploratory
  retry-index usage remains supported.

Regression tests reproduced the issues before the implementation changes. The full
220-case A/B and 60-case cold recovery evaluation, direct rubric scoring and any
default activation remain pending. Focused confirmations follow below.

Validation after the independent review fix pass: **125 focused/protocol tests
passed** in 90.19s. New negative cases reject unrelated scope words, workload
claims, ratios attributed to the wrong scope, conflicting displayed percentages,
missing hierarchy pages/status disagreement, failed source reads and missing IDs.
The denominator-label check is bounded arithmetic/text validation; direct semantic
review remains mandatory. Full offline CI is handled by the PR workflow.

## Focused confirmation and first cold diagnostic

Frozen commit `0d754a9b3b077456d356b453421fab4c32d62c11`, work 0.2.0 / prompt v2,
same Sol/high model and fixture. Full offline GitHub CI for this commit succeeded.

| Run group suffix | Cases | Original automatic result | Answer time | MCP calls | Total tokens |
|---|---|---:|---:|---:|---:|
| `v02-confirm-default-e4deee74` | R08, R10 normal | 2/2 | 92.2s | 37 | 436,042 |
| `v02-confirm-fallback-e4deee74` | R08, R10 normal | 2/2 | 101.0s | 37 | 489,094 |
| `v02-recovery-diagnostic-e4deee74` | between-pages, restarted | 0/1 | 248.1s | 85 LTM + 133 file | null |

All suffixes have prefix `20261109-sol-high-` under the durable evidence root.
The root agent directly read all four confirmation answers: both transports used
actual comment ID 61010 with `kind=comment`; R10 preserved revision 1 and current
states with correctly labeled child-only or all-ticket count ratios. All four read
candidate entrypoints. These focused passes do not replace original failed attempts.

The cold diagnostic intentionally stopped phase 0 after a real saved page and
checkpoint (34.29s). Phase 1 read the investigation files in a fresh session/cache
and finished in 213.83s. Full-comment/chunk/document coverage checks passed and the
answer correctly excluded cancellation, duplicate LOAD-1 and out-of-period work:
two unique tables. Both phases preserved source snapshots and recorded no forbidden
tools. One first-phase response was eligible persisted evidence; this is not an
observed count of avoided calls.

The original failure is **`progress_mismatch`**: the model put collection completion
`5/5 = 100%` into the answer's business-progress field. Recovery lacked the normal
prompt's explicit field semantics. The output schema/request is clarified in work
**0.2.1 / prompt v3**, recovery prompt v2: collection progress belongs in files and
answer progress stays null when business progress was not requested. The strict
null oracle remains; the failed raw is not rescored as a pass.

Investigation files: 62, retained size 755,278 bytes. File calls wrote 2,329,224 bytes
including rewrites, read 13,946 bytes and took 3.38s inside the file service; overall
model/MCP time includes copying and orchestration as well. Total token usage is null
because interrupted phase 0 emitted no completed-turn usage. Do not compare this
missing total against a complete uninterrupted token total or treat it as zero.

Before full qualification, the recovery treatment was also removed from shared
battery/review identity: each scenario now declares both mode-specific review paths
in one common manifest, while actual mode stays in candidate identity. Six scenarios
and actual runner metadata regressions reproduced eight failures before the fix;
**86 focused tests passed** afterward (87.18s). Invocation/preflight timeout limits
are recorded in benchmark context. New cold confirmation and full qualification
remain pending.

## Second cold diagnostic: stalled nested continuation

Frozen `f263ce7d7cbaea7750b13824107c2af90ffd9d45`, work 0.2.1 / prompt v3,
group `20261109-sol-high-v021-recovery-confirm-e4deee74`, between-pages/restarted.
The original result failed `comment_collection_incomplete` and
`required_evidence_not_observed`; original raw remains unchanged. The answer's count
was two and business progress correctly null. Total time was 318.31s (35.46s interrupted
phase + 282.84s resumed). Interrupted usage is unknown, not zero.

Direct trace inspection found a real operational defect: the resumed model repeated
the same `start_at=25` call without the returned `nextRecordOffset` 560 times while
reading DL-94006's long comment. It later advanced and obtained the full cancellation
record. The phase made 596 evidence-page calls, 588 for that ticket. Across the run,
1,316 file calls wrote 51,356,607 bytes and read 140,803 bytes; file-service time was
26.45s. This is not an acceptable recovery success despite the correct final count.

A separate checker defect rejected valid reuse: the first membership traversal had
all 51 records and complete comment 62100 (20,556 characters). A second membership
scan revalidated the same ID and SHA, reusing verified middle chunks. The checker
incorrectly erased all chunk evidence at the new first page. Prospective 0.3.0 keeps
chunk evidence by record ID+digest while replacing membership; missing, changed or
foreign-source chunks still fail. Running the new logic diagnostically on the old raw
finds complete coverage **and** a stalled continuation. It does not turn that failed
attempt into a pass.

Candidate guides now map `sourceIndex` and `nextRecordOffset` to the exact next
arguments and require diagnosing two identical nonadvancing successful responses
before a third. The prospective automatic gate fails three identical continuing reads.
Real confirmation is required before full qualification. Both phases preserved source
snapshots and recorded zero forbidden calls. The unused f263 qualification schedule
is retained and must be superseded for the next frozen commit.

## Batched collection refinement before qualification

The user requested fewer MCP/Jira requests, description-only opt-in, and approved
a distinct `get_tickets(keys, include_description=false)` for known key sets.
`search_issues` remains discovery and `get_ticket` supplies one detailed/rendered/edit
view. The batch validates configured scope, deduplicates at most 100 keys, preserves
requested order and discloses missing tickets/bodies. Jira search retrieves multiple
bodies per request; there is no per-key GET fan-out. Descriptions remain off by default
and there is no arbitrary field selector. Server page caps still require real pages.

SDK regression tests verify three bodies in one actual Jira search with zero individual
issue GETs, on/off projection isolation, missing versus empty bodies, scope and snapshot
constraints. Typed status fields and same-ticket source origins from successful batch
responses now count as evidence, avoiding forced duplicate individual reads. Changed-source
recovery excludes old persisted batches. Actual provider requests, response bytes and
summed request time are reported separately from MCP counts; unobserved totals stay null.
These are implementation checks, not a measured real-model efficiency improvement.

## Batched-path real confirmation and two checker disagreements

Frozen `3514abe792211e7f199c10c19a853632e2f9a280`, work 0.3.0, group
`20261109-sol-high-v03-batch-confirm-e4deee74`. R01/R04/R08/R10 normal completed;
original automatic **2/4**, entrypoint reads **3/4**, 159.81s, 791,427 tokens,
40 MCP calls, **23 actual Jira API requests** and 10 Confluence API requests.
Jira responses totaled 53,994 bytes / 0.250742s summed request time; these local mock
times are not production Jira latency. All source fingerprints remained unchanged.
The wrapper stopped on failure before starting the planned cold confirmation.

The root directly read all four final answers and an independent reviewer checked
both disputes against actual responses. R01 used one get_tickets for three bodies;
its relative comment self was resolved against the same returned ticket's absolute
REST self at testserver. The checker had indexed only its canonical browse URL.
R10 read fresh complete direct membership, both leaf SubTasks and their parent/status
fields, reporting a correctly labeled 1/2 child count. The checker demanded another
get_descendants traversal despite equivalent complete membership evidence.

Prospective 0.3.1 bounds alternate origins to validated same-ticket REST self and
recognizes verified fresh Task-to-leaf membership below the display cap. Negative
tests reject unrelated paths, changed IDs/parents, stale/partial/capped/duplicate
memberships, unknown leaf types and conflicting statuses. The initial focused set
passed 47 tests. A separate diagnostic report records the resulting checker ruling
and the original raw SHA; it does not overwrite original 2/4 or replace a fresh run.
Final qualification and default activation remain pending.
