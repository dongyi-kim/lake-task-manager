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
