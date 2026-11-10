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

## First full candidate attempt: preserved failure and direct review

Frozen `76f7658e3050c4358cebe8a661fc0e484f62cc64`, work0.3.1 / promptv3,
group `20261109-sol-high-main-candidate-v031-e4deee74`, repeat1/order1. The
predeclared driver completed all22 and stopped on exit1 before baseline. Original
automatic **12/22**, entrypoint reads **18/22**. The five-repeat A/B is incomplete.

| Metric | Observed |
|---|---:|
| Answer time | 1,002.10s; p50 42.73s / p95 99.48s |
| Input / output / total tokens | 4,891,442 / 31,759 / 4,923,201 |
| Cached input tokens | 4,041,472 |
| MCP calls | 4,861 |
| Actual Jira API requests | **108** |
| Confluence API requests | 21 |

Jira response-byte total is unknown because a denied request's body was not
observable; it is not zero-filled. Requests include failures/retries and are
separate from MCP calls. No production Jira latency/improvement is inferred.

A native Codex Sol/high reviewer directly read every answer and actual sources,
authored every common/specialized checklist, and validated all22 identities,
case/source SHA and literal excerpts. The root also read all22 answers and
reconciled the findings. Single-attempt human mean is **4.94/5**, with dimensions
request4.95 / facts4.91 / actionability4.86 / safety5.00 / communication4.98.
The panel is one formal reviewer, unblinded; there is no baseline or improvement
claim. Detailed authored reviews and validated aggregation are under external
`reviews/candidate-r01-part01.json` through part03 and `candidate-r01-summary.json`.
The original raw SHA is `d5a17ad23f5d7f33a74fbaf0f43642915f3d0497afe2ddbd80c5befe35be903d`.

Two actual defects remain in this attempt:

- R02-boundary read evidence4,653 times, including **4,630** identical start25/
  max100 calls with no record offset. It did not read a candidate entrypoint.
  Full51-record cancellation coverage and the final2-table answer were correct;
  the unchanged cursor loop is a major execution/performance failure. Jira made
  only8 requests because the repeated source page was cached.
- R05-boundary's visible candidate IDs and null count were correct, but one
  structured person source added an extra character to the ID and used an empty
  URL. This is a genuine identity/reference contract failure and is not accepted.

Other disagreements concern evidence-basis keys (inspected no-activity ticket,
observed WBS ancestor and known denied reference), literal returned person self,
reconstructed comment self, and natural Korean uncertainty/denominator wording.
R07-boundary has **31 MCP calls and no repeated evidence-page arguments**; its
business table/union counts were correct, while reportedTotal6 explicitly meant
completed tickets and its prompt had not fixed that scalar to union2. This is
metric-contract ambiguity, not an invented business count. Do not attribute R02's
loop to R07. Original automatic results remain unchanged.

Prospective product correction returns one ready-to-copy nextCall including record
offset and original batch size, inside the8KiB response budget. Legacy arguments
remain valid. Work1.0.0 / promptv4 makes the scalar/basis/citation contract explicit
and moves Korean meaning to mandatory direct-review elements, preserving numerical
coverage checks. A deterministic cohort audit now rejects missing repetitions,
unlinked/changed reviews, automatic failures and critical semantic majors even
above the mean threshold. It was also applied to the retained real attempt:22
linked reviews, mean4.94, Jira108, **qualityPassed=false**. No original raw/review
was edited; the failed/incomplete v2 schedule is retained and must be superseded.

The last offline CI had one failure (4,536 passed /120 skipped): the new metric
fixture used CI's repository-local default tmp_path, which WorkFixture correctly
rejects. It now imports the existing external scratch fixture; local test scratch
continues to use OS temp. Fresh boundary/recovery confirmation, full qualification
and any default activation remain pending.

Additional regression evidence for the prospective correction: with32 comments,
a17-record Jira server cap and3-record MCP slices, source-page reuse reduced
actual mock Jira requests from6 to2 without changing the records. Continuation
tests cover3/100 batches, Unicode/JSON/hash reconstruction, comments/history and
8KiB bounds. Cohort audit tests also cover actual producer order strings and
authored finding arrays, contradictory model identity, inappropriate NA variants,
and a rounded4.50 display whose true mean is below4.5. Latest focused verification
passed59 tests; broader offline CI is rerun after publishing the corrected commits.

### Prospective Work1.0.0 boundary confirmation and rejected recovery attempt

Clean commit40673f69, Sol6.1/high, real MCP over mock Jira at port4457:
`20261109-sol-high-v100-boundary-confirm-e4deee74` completed R02/R03/R05/R07 boundary
with automatic4/4. The retained raw SHA is
`3ebd622c9fc61400ec2c91ff575e77dfc5fbd5dc66a313cfb375c309f64d1e84`.
Total259.20s,68 MCP calls,20 actual Jira requests,0 Confluence,259,680 source bytes,
1,100,419 total tokens. R02 andR07 each used23 evidence calls with zero repeated
arguments and fully reconstructed the cancellation comment. Candidate entrypoint
adoption was3/4; non-adopter R05 remains included. These are diagnostic cases,
not an A/B or performance improvement estimate.

All four final answers and their source calls were directly reviewed by the same
single unblinded native Codex Sol/high panel: mean4.97/5, zero major/caps/material
false facts. Exact case/snapshot SHA, excerpts and the full32 common plus specialized
checklist were validated in external `reviews/v100-boundary-confirm.json`. R02 has
one minor output-contract issue: correct operational rows despite the non-operational
question's empty-array instruction. This diagnostic does not replace any prior failure.

The sibling recovery diagnostic is **failed**, original raw SHA
`396146aa459487272a9ba48983e460aa89f2cc0a965de3b4072ad907af9de155`:
196.83s,34 LTM MCP+60 file calls, actual Jira8/Confluence3,124,674 Jira response
bytes,489,320 file-write bytes and0 file-read bytes. It completed only one phase;
the fault never fired, so it provides no cold-session recovery evidence. The model
saved actual search pages and legitimate `nextArguments` progress, which the
controller did not recognize. It also interpreted the JQL's candidate key18 as a
required unread member and returned null/unknown after a missing-key lookup;
the intended population was the complete returned JQL membership. This is an
ambiguous input contract, not an invented business fact. The result remains0/1.

Prospective correction is confined to the evaluation harness: recognize source-
linked direct/nested argument cursors and reject mismatching query/snapshot;
clarify search candidate vs returned population without relaxing failed or
incomplete query disclosure. Recovery battery becomes2.0.0 / promptv3, while main
Work stays1.0.0. Four behavioral regressions first failed, then recovery/integrity
checks passed80 tests. Four additional missing-identity regressions first failed;
the corrected source binding then passed31 recovery tests. Read-only review found
no remaining important issue. Product PR191 full offline CI passed at2633bd3c and
PR192's full offline CI passed at40673f69. Fresh
same-commit boundary/recovery diagnostics and the full qualification remain pending;
no v3 matrix has been executed and no candidate is enabled by default.

### Subsequent v3 first pair, cache diagnostic and final fallback failure

At clean commit `6447aa28`, guide candidate `0.1.3`, Work `1.0.0` and recovery
`2.0.0`, fresh boundary confirmation passed4/4 and between-pages restart passed1/1
(direct4.9/5). The seed190 v3 schedule then executed its first candidate→baseline
pair. Original source worlds/stores and every attempt remain unchanged.

| First repeat, all22 cases | Candidate | Baseline |
|---|---:|---:|
| Automatic pass |22/22|22/22|
| Direct-review mean |4.98/5|4.98/5|
| Actual Jira requests |106|116|
| Confluence requests |20|22|
| MCP calls |234|167|
| Answer wall seconds, summed |1058.3|878.1|
| Total tokens |4516887|3404244|
| Cached tokens |3650176|2667008|
| Observed candidate entrypoint reads |19/22|0/22|

Jira response bytes remain unknown/null because failed-response bodies were not
observed. Failed requests and retries count; cache hits add no upstream request.
Each real-model case starts with a fresh client/private cache. The same single,
unblinded native Codex Sol/high panel directly reviewed all44 answers,704 common
and52 specialized items per arm, with exact case/snapshot SHA and excerpts. No
material false facts or major semantic failures; local minors remain recorded.
Both consuming cohort audits reject qualification because four repeats are missing.

Candidate raw SHA:
`34c11423f35af0135b1703b9aa3cae4c3d6db201aa920d6e4d723ace6cf37361`.
Baseline raw SHA:
`14cf046bffdad875430f12714e6f8ec170af8ee0bfda3325dc139063a2e52a48`.
The retained groups end in `main-candidate-v200-e4deee74` and
`main-baseline-v200-e4deee74`. Authored parts and compact summaries are under
external `reviews/v3-main-...`; no score was copied across arms.

The user's cache concern was checked with an additional **zero experiment-model
call** diagnostic across all44 recorded cases. Immediate serial cold→warm replay
used the same fixture/cache for the second pass and retained all responses.

| Jira requests | Candidate | Baseline |
|---|---:|---:|
| Original real-model cold-start |106|116|
| Serial cold replay |104|115|
| Immediate warm replay |10|10|

The original concurrency, thinking delays and cache aging are not replayed.
Twelve original not-executed rate limits and one query-state-guard failure succeed
when serialized; three cases have a total of three fewer cold-replay requests.
Original106/116 is not overwritten. All401 cold/warm result pairs have equal
business data after explicitly scoped cache/check/generated-time metadata and
opaque pagination-ID comparison; worlds/stores remain unchanged. Raw differences,
all802 replay calls and analysis scripts are retained under `warm-replay-v3/`.
Its manifest SHA is
`446b2324c975e5a1338091067e7c09a1b61a44d20191095d86cad1fb395775b6`.
The first cold request gap disappears with warm cache; candidate MCP, token and
wall-time costs were higher. No general performance or causal gain is claimed.

Final same-commit smoke completed11 cases in each transport: default11/11,
direct4.98/5; fallback10/11, direct4.91/5. Fallback R10's1/2=50% and actual three
visible ticket facts are correct, but only a completed `parent = DL-94010` search
establishes membership. The root read exposes no child keys/count. Child parent and
SubTask fields prove inclusion/leafness, not exhaustiveness. Permission-filtered
search can hide known denied children. The reviewer records major whole-coverage
and confidence defects, with no fabricated factual count or false denominator unit.
The original `progress_mismatch` remains; no checker relaxation or rescore.

Default smoke SHA:
`8240a2f5a2c9ca51fd88db5926ee99fc744f96aea39f0f17eda172d782bd2456`.
Fallback smoke SHA:
`ff594978ef0c9a62415d9954cf7069049fc1ae1230791ce852e8b9bded3a2501`.
The v3 schedule stops after its first A/B pair. Candidate guide0.1.4 now directs
whole-progress questions to complete hierarchy evidence and distinguishes cache
reuse from cold API-load claims. An actual MCP regression proves parent REST can
know two children while an exhausted, fresh, nonpartial search returns only one;
query-only100% is correctly rejected. Related asset/leaf/prompt-integrity checks
passed **68 tests,1 skipped** (existing Windows symlink limitation), and narrow
independent review found no important issue. New candidate confirmation and full
qualification remain pending. Default activation remains off; no merge/release.

### Guide 0.1.4 confirmation: grounded URI rejected by Work1.0 checker

Clean commit `c144c0bf` (product guide commit `3b4ebed4`) started full default/
fallback smoke before any new A/B, then planned one fresh recovery confirmation.
Default smoke completed10/11; the helper stopped before fallback and recovery.
The sole failure is R03 `source_not_observed`, with original raw SHA
`56da16a88de17d47bcef723714ecdcb3bffc77cc881cd6e03d8ecdaa36bb10da`.
The same formal native Sol/high panel directly reviewed all11 outputs and sources:
mean4.99/5,352 common and23 specialized items, no material facts/semantic defects.
R02 operations[] remains a local minor. R10 actually used a fresh, complete2/2
descendants collection with no missing/partial and verified child fields, so the
whole-hierarchy guide correction is supported in this pressure case.

R03's successful `get_descendants` contained a wrapper/raw key-matched DL-94008
issue with numeric ID1974554802 and literal REST self on `http://testserver`.
Actual comments61008/61009 contained their matching relative REST self paths.
The final URLs exactly normalize those observed values. The checker had collected
ticket origins only from get_ticket/search_issues/get_tickets, overlooking the
typed descendant issue. This is a checker false positive, unlike the earlier
unsupported whole-hierarchy R10 claim. Original10/11 remains unchanged.

Prospective Work2.0.0 / recovery3.0.0 adds only direct typed descendant metadata
where wrapper key equals raw key, and explicit own URLs on child badges. Literal
ticket REST self also uses the same actual-address/key/numeric-ID/endpoint checks.
No arbitrary nested description/parent origin is accepted, and missing URLs are
not synthesized. Existing credentials, query/fragment, foreign host, mismatched
key/ID and failed-response rejection remains. A partial returned citation still
cannot prove whole-progress coverage. The product guide remains0.1.4.

Four source-route regressions first failed, including actual MCP. Two further
literal-ticket-self regressions first failed, again including actual MCP. Final
related source/boundary checks passed56 tests; recovery/metadata/cohort checks
passed72 tests (overlapping earlier evidence is not summed). Narrow independent
reviews found no important issue. Both product/eval full offline CI passed at
3b4ebed4/c144c0bf before this checker-only correction. New full smoke and recovery
confirmation must use the new frozen checker; no old scores are reclassified or
substituted into its qualification.

## Work2 fresh prequalification and retained first pair

Frozen694296df, guide0.1.4, Work2/recovery3, Sol6.1/high. Fresh default/fallback
smoke each11/11, direct means4.98/4.99. Between-pages/restarted confirmation1/1,
direct4.8, actual Jira7/Confluence4/MCP43/file251,279.2s; aggregate token counts
remainnull. All29 saved response payloads match actual MCP results,53comments
and the7-chunk long cancellation record were verified. Consuming prequalification
audit bound all23 cases/specs/snapshots/authored reviews/source counts, with no
material/major finding. It did not claim full qualification.

| First paired repeat | Candidate | Baseline |
| --- | ---: | ---: |
| Original automatic | 22/22 | 21/22 |
| Direct human mean | 4.98 | 4.95 |
| Actual Jira requests | 104 | 116 |
| Confluence requests | 21 | 21 |
| MCP calls | 244 | 174 |
| Answer duration sum | 988.4s | 879.6s |
| Answer p50/p95 | 39.35/75.08s | 36.11/79.62s |
| Total tokens | 4,661,517 | 3,604,137 |
| Cached input tokens | 3,876,864 | 2,957,824 |

Both22-case outputs were directly reviewed with704common/52specialized items each.
Candidate19/22 observed guide adoption remains separate from primaryITT22/22.
BaselineR10's exact1/2=50% ratio had only exhausted permission-filtered parent JQL,
not exhaustive root membership. Native review confirmed a genuine unsupported
whole-coverage claim (common/case-specific majors); arithmetic/denominator wording
is clear and the oracle numbers are correct, so materialFalseFacts remains0.
Human mean does not remove the majors. BaselineR07 also reread7 complete cached
record chunks once, increasing MCP work without additional Jira source requests.
Jira response-byte totals remainnull on both arms; this mock first repeat establishes
neither production latency/CPU improvement nor a general cold/warm load reduction.
The earlier guide0.1.3 warm10/10 replay belongs to its own separate pair.

Original raw SHA256candidate:
`f4187c45b47e854c8a1e319f6f153e2e9d1ff30a3059bd877cdc16fa668fe7d3`;
baseline:`e8b501e60067489f7833cc17cb7adf2d448741ccdf872417f3fd2de59d101782`.
Qualification-schedule-v4 stopped after this44-case pair for the independent
code-review findings below, before any other repeats or recovery matrix cases.

## Prospective completeness-gate corrections

Whole-branch Sol/high code review found no productPR191 blocker, but two important
evaluator gaps. Oracle-equal R04 progress skipped the complete-hierarchy helper,
and recovery accepted correct known-key body/count evidence without proving the
last session's complete current filtered JQL population. A no-HTTP diagnostic
reproduced exact50% passing while its hierarchy-proof helper returnedfalse.

New regressions first produced16 failures/4passes, including actualMCP known-key
metadata with no hierarchy. The corrected progress gate accepts the same already
observed batch statuses after actual fresh leaf-child membership is obtained.
Recovery now consumes actual canonicalJql/snapshot/page/end/uniqueness evidence
from the last phase, rejecting missing/partial/wrong-query/mixed/stale membership.
It preserves body reuse and accepts complete larger batches. The focused gate,
batch-status and12 actualMCP recovery-mode/scenario regressions passed56checks.

The review also found literal document IDs could be labeled comment/person and
body-quoted records could pass as actual comments. Five new negative tests failed
first; literal matching now uses typed result paths. The related source suite
passed70checks. Product guide0.1.4 is unchanged. Prospective Work3/recovery4 has
new direct R04 denominator semantics; rendered question text is unchanged.
No prior scores or model outputs are reclassified into the new qualification.

Two final review negatives exposed loss of legitimate unified-search source URLs
and loss of completed same-snapshot membership after a first-page reread. Three
regressions failed first; typed Jira/Confluence workspace paths now also provide
comment origins, and frozen snapshot coverage is reused while conflicts/new
incomplete snapshots fail. Related regression65passed. Non-operational arrays
also have a hard empty-array check and case-specific CLI maxItems0 constraints;
actual case schema SHA is retained in runtime metadata. The earlier two negative
schema/contract tests failed first; launcher/recovery/metadata/cohort suite102passed
(overlaps earlier sets, not summed). Rendered questions and guide0.1.4 are unchanged.

On2026-11-09 the user explicitly chose an initial product release with the new
skills enabled after reasonable product validation, with full repeated A/B and
recovery validation afterward. This supersedes the original default-off rollout
hold, not the evidence requirements or the retained failures. The release is not
a claim that the complete302-case follow-up has passed. Product and evaluation
changes remain separate, and deploy-repository tags/pointers/releases are excluded.

## Released product and post-release Work3 evidence

PR191 merged to9ad95f81424faa718a7f89524aac39431698d3ff and public
v2026.11.09 was published2026-11-09T06:41:56Z. Both research skills are enabled by
default; explicit false keeps the controlled four-authoring-skill baseline. Tested
product1c79789c and merged main had identical trees. Product/main full CI passed,
as did evaluation5059e816 CI42574938814. No deploy-repository pointer/tag/release
was changed. This release was explicitly authorized before full qualification.

Fresh Work3.0.0 at5059e816, guide0.1.4, Sol/high, protocol3.0.0/rubric2.0.0:

| Exploratory smoke | Default | Fallback |
| --- | ---: | ---: |
| Automatic / directly reviewed | 11/11 | 11/11 |
| Direct mean | 5.00 | 4.990909… |
| Jira / Confluence / MCP | 58 / 10 / 112 | 54 / 9 / 115 |
| Duration sum | 599.1s | 579.2s |
| Total / cached-input tokens | 2330606 / 1991168 | 2254940 / 1918336 |
| Observed adoption / ITT | 9/11 / 11 | 10/11 / 11 |

Each arm has352common/24actual specialized items, no material/major finding and
all case/source/spec/schema/review hashes checked. Fallback R03 has one temporal
minor: comment-created clock time is stronger than the event time in its body.
Raw groups are20261109-sol-high-v014w3-smoke-{default,fallback}-e4deee74;
SHA256respectively5d8ef1040e67645a64fb668e3b590dd1a54a295748d44fa8f1b1ad99c7ae268e
andc8258d29d8c7ed928d3b571200ca44a65e2b902a993a86032989e58d3eaccda5.
Reviews remain under the external durable root in reviews/v5-smoke-*-part*.json.

The subsequent between-pages/restarted confirmation FAILED0/1: phase0 intentional
stop73.391s, phase1codex_timeout600.061s. The saved report/count2 was supported by
complete JQL5/53comments/7long-record chunks/document v1, but no final structured
answer was returned. Original8f48b85819e18fb6c5ef0e82307a61d9884b191aa81fde0c6a9cdd8cf63d6bf9
and its failed verdict are unchanged. Jira11/Confluence3/MCP48/file113; token/cost
unknown. No positive prequalification audit or v5 matrix was started. Direct
failure reviewa0a91858afa68e74c4b25865f9c317e7a7da36d3beea19d06de2bc1e9fe3dcd9
distinguishes missing final output from actual incorrect scope/operations rows.

## Compact-checkpoint diagnostic and production disconnect feedback

Separate productPR193 clarifies storing one application response, retaining all
source/cache/completeness/cursor/recordJson metadata and unique warnings/errors.
Static guides use manifest URI/digest/version; progress stays compact. Guide0.1.5
at evaluation95e83140 had one prospectively registered exploratory confirmation,
group20261109-sol-high-v015w3-recovery-overhead-e4deee74, same600s/phase/Sol/high.
It completed1/1 in366.7s: Jira7/Confluence3/MCP42/file70. Aggregate tokens/cost
remainnull. Rawdcf76034fd7861d6c4c62656d9f352d83ee0e5d7dfcd992f82f4c65d2e42a313.

The same single unblinded native Sol/high reviewer directly checked32common and
both actual Recovery4 specialized elements. Mean4.9: fulfillment5, grounding4.5,
actionability5, safety5, communication5; no material/major finding. Saved findings
attach precise event/decision clocks to comment-created timestamps, a temporal
minor. Count2, complete fresh JQL membership,53comments, long cancellation-record
reconstruction/SHA, document v1, actual new session/saved-file reads, retained
expired-snapshot error and new full search all matched sources. Reviewpart SHA
48f3dec859824256caf5aa71972f7e56209551c084f4e5468e696a74a14284bb; summary SHA
239f8c03cef2eaed671d611d80ece12920c142b4165277a8fd64e5c8f98422f8.

Observed writes676414→215974B, phase1 progress203248→9617B, static-guide copies5→0.
These runs interrupted at different actual boundaries (eligible saved calls6vs1),
so neither latency/Jira differences nor byte reductions establish causal speedup
or production load improvement. The failed0.1.4 case remains failed; this single
0.1.5 diagnostic cannot qualify the complete repeated matrix.

The user then reported Claude+production LTM advancing to the next query group
after disconnecting, saying the monthly total was unconfirmed. Separate product
PR194/guide0.1.6 makes the current failed group a completion barrier: retain exact
arguments, retry transient reads, revalidate expired snapshots, finish missing
pages/fragments, or stop at that group for a concrete required action. Explicit
partial-investigation requests are the exception. Successful nonadvancing pages
and transport-failure retries are distinct. No new runtime retry API was added.
Native Sol/high code review and four bounded pressure scenarios found no important
issue; this was not a Claude runtime experiment. Packaged resource/SDK/prompt
checks52passed/1OS-symlinkskip; the final narrow wording/resource check1passed.

Evaluation95e83140 full CI42581858250 did not pass: the job hit15minutes and its
log exposed the continuation SDK fixture assuming posting order equals newest-first
source order when all four writes happen in one second. An external offline clock
probe reproduced1failure. Pinning this fixture's source timestamps, without
changing order/body/fragment/hash/single-GET assertions, made the same probe pass;
the complete related payload file passed18checks. No CI timeout or product
pagination contract was widened. Current guide0.1.6 needs its own frozen source
and fresh checks before the still-uncompleted220A/B+60recovery matrix. Prior raw
scores, cache replay and all failed attempts remain separate and immutable.

### Desktop disconnect and supervised evaluation transport follow-up

The later CI-only PR195 raises the total offline job budget from15 to20minutes;
model phase timeouts remain600seconds. The exact prior source d6dc passed full CI,
but its prepared v7 model prerequisites were held before execution when the user
reported persistent Claude Desktop disconnects and requested background recovery
and tray health. No v7 model result exists or is substituted for an older attempt.

Product PR196 keeps the same stdio process/session alive through an HTTP outage,
without replaying an uncertain write. PR197/198/199 add serialized worker generation
ownership, real SDK/profile replacement, bounded health/recovery and tray status.
Final related product checks92passed; independent Sol/high findings were fixed.
The actual user's production logs/config remain unavailable. These are code/SDK
regressions, not a new real-Claude quality evaluation.

The private evaluation fixture now uses the product TransportHost instead of
bypassing it. Rebuilding retains the default/fallback extension flag and reinstalls
tool/resource/recovery observers on fresh SDK instances. Baseline/candidate guide
selection remains independent; write handlers remain absent. Four combinations
were RED on stopped health before integration and GREEN after it. Related private
fixture/work/recovery checks48passed117.42s; no real model or external Jira call.
An independent Sol/high static review found no Important issue. New exact-source
CI and a new external freeze are required before fresh23 prerequisites or the still
unfinished220A/B+60recovery matrix. Old raw attempts and scores remain immutable.

The first product integration CIs failed:197 exposed the already reproduced comment
clock/order fixture issue (now separate PR200);198 and199 saw generic HTTP errors
during ordinary SDK calls. Exact eval b72 CI42600226203 also failed4cases with4687
passed/120skipped645.61s. Those original logs remain failures. Their generic errors
do not prove which HTTP status/body occurred. A deterministic new regression did
prove that one stale2.1s pulse immediately rejected a responsive worker with503,
before the configured three-miss policy. Two cases were RED then GREEN after bounded
business-admission grace; health/diagnostic predicates remain strict. Actual guarded
dispatch advances the pulse; old/stopping/fatal generations stay fenced. Nine fence
cases passed; source-integrated product-related60passed80.41s. Independent Sol/high
review found no Important issue; its test-isolation Minor was fixed. After merge,
worker/qualification-controls21passed13.93s. No real model or external Jira call.
V8 was prepared only and is held without attempts; a new exact-source CI/freeze is
required after these fixes. No previous score, timeout or failed CI is replaced.
