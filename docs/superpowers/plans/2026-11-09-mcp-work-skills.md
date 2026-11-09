# Issue 190: research, reporting and resumable investigations

Status: approved implementation plan; initial product release authorized before the full follow-up matrix.
Spec: https://github.com/dongyi-kim/lake-task-manager/issues/190 plus the user's
2026-11-09 checkpoint/resume requirement and approved conversation plan.

## Goal and constraints

User steering on2026-11-09 supersedes the original hold-to-full-qualification rollout:
ship the adequately verified product first, with both new skills enabled by default
(explicitly selected), then continue the remaining A/B and recovery qualification.
Use app repository main/tag/public GitHub Release, leaving deploy-repository pointers,
tags and releases unchanged. Preserve the separate draft evaluationPR192 and all raw
failures; publishing the initial product does not claim the full302-case matrix passed.

Provide `ltm-work-research` and `ltm-work-reporting` with shared Korean, evidence,
aggregation and client-local recovery guides. Preserve #187's body/comments-first
guidance. Keep existing four authoring skills and output contracts compatible.
Do not add production filesystem MCP tools, question-specific reporting tools,
new cache modes or JQL strategies. No paid/model run without separate approval.
Use one checkout, preserve unrelated editor work in Git, and use OS temporary
directories for development/test scratch with finally cleanup.

## Task 1: candidate packaged skills

- Add catalog opt-in `include_research=False`, with explicit shared-reference
  selection so Jira markup does not leak into research or Confluence guides.
- Add two guides, research-only shared references and reproducible checkpoint
  examples. Every packaged resource remains at most 8192 UTF-8 bytes.
- Extend fallback input enum and runtime skill discovery, conditionally describing
  only enabled skills. Keep resource/fallback bytes and digests identical.
- Test opt-in/default isolation, reference resolution, schemas, real MCP SDK reads,
  no Jira access and preservation of existing authoring resources.

## Task 2: work research/reporting evaluation

- Add a new versioned `work` battery with R01..R11 normal and boundary cases.
- Reuse reporting/identity mock foundations, with Confluence revisions, person
  ambiguity, activity, operations-event identity, cancellations and full evidence.
- Keep oracle data outside the model workspace and derive checks from successful
  actual MCP responses, not tool names/arguments or answer strings alone.
- A/B uses identical backend, fixture, dates and output schema; only candidate
  guide exposure changes. Record skill reads and content digests in manifests.
- Existing batteries remain backward-compatible. Freshness/JQL effects are not
  attributed to skills. Native extension and fallback transport are distinguished.

## Added requirement: fewer upstream requests

User steering on 2026-11-09 requires collecting necessary information in a few
multi-issue requests, reducing both MCP overhead and actual Jira requests/latency.
Jira 8.20 search supports selected fields including description. Existing MCP search
uses a light projection that omits it even with detail=full; do not pretend that
the current exposed search already reads complete bodies.

- User approved a separate `get_tickets(keys, include_description=false)` for
  known key sets, alongside single-ticket get_ticket and discovery search_issues.
  Bound batches at 100, validate configured scope before I/O, deduplicate keys,
  preserve requested order and report missing tickets/bodies explicitly. Never
  implement the batch as N single-ticket GETs. Follow actual smaller search pages.
- Expose optional `include_description=false` on existing search_issues,
  retaining existing defaults and query/project/pagination/cache isolation. Query
  the needed key set and opt into description plus existing metadata in one search.
  User explicitly declined general controls for other fields; do not expose a
  general fields selector or additional field toggles. The body switch is independent
  of the existing metadata detail projection and defaults off to avoid body cost.
  No per-key network fan-out or automatic full-field expansion for this path.
- Preserve raw description fidelity, distinguish omitted fields from empty values,
  retain returned keys/URLs and field-specific snapshot identity. A read projection
  must not poison canonical UI issue caches.
- Guide discovery/relationship/status collection in batches, reuse returned fields,
  and read extra bodies/comments/history only for actual evidence gaps. Do not load
  every skill reference or re-read identical URI/digest resources for every task.
- Verify actual provider endpoint counts/bytes/latency as well as MCP counts, using
  a cold multi-issue body-read regression and meaningful real-model comparisons.
  Completeness checks must accept genuinely observed bulk evidence rather than
  requiring duplicate single-ticket reads. Comment previews remain previews unless
  their returned pagination proves complete coverage.

## Task 3: local recovery

### Evidence-driven refinement after the first full candidate run

The frozen 76f7658e work0.3.1 candidate r1 completed22 cases, original12/22 pass.
The schedule stopped before baseline. Preserve this full failed attempt and its
4,630 identical pending-record reads; do not rescore it or mix versions in A/B.

- Return a ready-to-copy `nextCall` from bounded evidence pages, including both
  source index and record offset. Include its bytes in the8KiB bound and put this
  continuation rule first in tool/runtime guidance. Keep legacy arguments valid.
- Permit a chunk offset with a larger max_results; it applies to the first record.
  Preserve the caller's batch size through chunk completion, avoiding a forced
  series of one-record reads afterward. Test exact reconstruction and advance.
- Align resultKeys with actual evidence basis, including inspected exclusions and
  separately verified context/missing references. Keep unknown totals/progress null.
- Accept literal returned self with matching identity and verified reconstructed
  comment citations; reject invented/empty identities and URLs. Unknown citation
  URLs mean omit the entry, never synthesize a filler. Clarify R07's total unit.
- Validate numerical ratios/source coverage deterministically. Require direct
  semantic review of denominator labels and uncertainty disclosure; exact Korean
  word-pattern checks must not reject clear equivalent wording or replace review.
- Fix the CI test's missing external tmp_path fixture. Do not create repository
  test scratch locally to reproduce CI's legacy base path.
- Apply RED/GREEN tests, independent review, a new battery/prompt version and fresh
  boundary/recovery confirmations before a new frozen qualification schedule.
- Work1.0.0/promptv4 is a major contract revision. Audit each full five-repeat
  arm/scenario/mode against preregistered IDs/order/commit/model and retained raw,
  binding every authored review to case/source SHA and actual specialized spec.
  Protocol validation covers every common/specialized item. Any major denominator
  or uncertainty finding fails regardless of mean; incomplete cohorts never qualify.
  Keep original metadata/reviews intact, including producer string order indices
  and explicit material-fact finding arrays. A cohort decision is not whole-matrix
  completion or permission to roll out before all required gates.

### Further guide correction after the final fallback smoke

The frozen 6447aa28 candidate completed its first full A/B pair (both22/22,
direct means4.98/4.98), but final fallback smoke failed R10 (10/11). Retain all
original results. Complete parent JQL proves its permission-filtered query set,
not exhaustive Task membership. Root read metadata and two leaf parent fields
do not establish that no known/denied child was omitted. Keep the checker strict.

- Candidate guide0.1.4 requires complete hierarchy evidence for whole-progress
  denominators, while reusing a proven Task-to-leaf direct-child snapshot.
  Replace query-only membership discovery rather than adding duplicate key reads.
- Preserve the real failing pressure case and an actual MCP regression where
  parent REST knows two children but a fresh, exhausted parent search returns one.
- Do not mix previous candidate repetitions with the corrected guide. Freeze a
  new candidate and run the full11-case default/fallback smoke before its A/B,
  plus fresh recovery confirmation. Keep every failed schedule and attempt.

The subsequent c144c0bf Work1.0 default smoke was10/11, but its R03 failure was
an evaluator false positive: successful descendant raw metadata supplied the same
ticket's REST self, and actual comments supplied matching relative self paths.
The validator omitted that typed metadata path. Preserve the original score;
accept only direct, key-matched metadata through the existing endpoint guards,
including literal observed ticket self. Negative tests must reject nested body/
parent URLs, unknown/failed/mismatched routes and unsafe endpoints. Version this
checker meaning change as Work2.0/recovery3.0, keep guide0.1.4 unchanged, and run
fresh qualification without substituting the old model outputs.

The first Work2 pair at694296df ended candidate22/22/direct4.98 and baseline21/22/
direct4.95. BaselineR10's full-membership claim was genuinely unproven. A separate
whole-branch review identified two automatic-gate gaps: oracle-equal R04 progress
bypassed hierarchy proof, and recovery never affirmatively verified current complete
JQL membership. Preserve this entire pair and stop subsequent repetitions.

- Require observed complete membership for every non-null progress ratio, including
  exact-oracle R04 values. Reuse batch status metadata and a proven leaf-child snapshot;
  add required direct denominator review for R04 as well as R10.
- Require the last recovery phase's successful scoped canonical JQL, consistent search
  snapshot and complete/unique page coverage. Old saved search and known-key body batches
  cannot prove the current filtered population. Retain already verified body evidence.
  Do not add two-item restrictions to body batches or require duplicate body reads.
- Bind literal source URLs to typed record locations, rejecting cross-kind identity
  coincidences and objects quoted within bodies. Preserve valid literal person/comment/
  document URLs and verified ticket/comment self normalization.
- Record these prospective semantics as Work3.0.0/recovery4.0.0; product guide0.1.4
  and rendered questions are unchanged. Regressions must first expose the old false
  passes. Complete the independent code-review loop before a new freeze and fresh
  qualification. Old raw, scores, reviews and failed schedules stay unchanged.
- Enforce the existing empty operations array for non-operational cases in both the
  checker and the case-specific CLI schema, record each actual schema SHA, and keep
  operational/other batteries' schemas compatible. Same immutable search snapshot
  pages remain reusable after repeated first-page reads; conflicts and incomplete
  new snapshots still fail. Preserve unified search's typed source URLs/origins.

- Guide artifacts: manifest.json, progress.json, evidence/, findings.jsonl,
  report.md and resume.md. Save evidence before advancing progress.
- Resume checks task/source/scope identity, last valid generation, source age,
  snapshot validity and full-record digest; deduplicate by actual source identity.
- No file capability: report this and return a textual resume brief.
- Add evaluation-only rooted list/read/atomic-write tools. No shell or arbitrary
  filesystem/network access. Test traversal, symlinks, partial writes and bounds.
- Recovery sessions share only saved files and the same fixture world; fresh
  client/cache/session per restart. Six faults: between pages, response before
  checkpoint, record chunk, corrupt final progress, expired snapshot, source
  changes. Compare with uninterrupted answers against the correct source state.
- Evaluation output root is outside the repo; authentication/fixture scratch is
  removed on every exit and retained evidence is identified explicitly.

## Task 4: verification and handoff

- Run focused MCP/catalog and evaluation tests; preserve RED/GREEN evidence.
- Whole-branch independent review, fix substantive findings with regression tests.
- Commit by context; skills delivery and evaluation work use separate PR branches
  if needed, using dependent commits without a second checkout.
- Report initial release status and pending full-model validation honestly. The
  original no-merge/release hold is superseded by the explicit first-release request
  above; merge/release productPR191, retain evaluationPR192 for follow-up work.

## Real-model gate (authorized by continuation)

Model: gpt-6.1-sol/high, explicitly selected by the user on 2026-11-09.
The interrupted Astra exploratory attempt is retained separately and excluded
from Sol comparisons. No later experiment uses Astra.
Smoke: 11 B cases in default and fallback = 22. Main A/B: 22 x 2 x 5 = 220.
Recovery: 6 x uninterrupted/restarted x 5 = 60. Total 302 plus preflights/retries.
Record exact commit/manifests/model/clock/order, genuine tool evidence, token,
latency, upstream work and file overhead. The quantitative performance criteria
explicitly include actual Jira API request count, separate from MCP and Confluence
counts, per case and summed by arm/repeat. Count source search pages, individual
reads, failed requests and retries; a cache hit adds zero. Exclude preflight/fixture
setup and keep unobserved/unclassified service totals null, not zero. Also compare
Jira response bytes/request time and answer wall time, without equating fewer MCP
calls with reduced Jira load. Preserve first failures and new attempts.
Separate cold-start measurements from cache reuse. Fixed-transcript immediate
warm replays of completed paired runs are diagnostics with no experiment model
calls; they preserve recorded order but not original concurrency, thinking delays
or cache aging. Keep original model counts/timing and every replay response
difference. A cold-only request gap is not general Jira-load reduction evidence.
Require automatic gates, direct human review mean >=4.5/5 and zero material false
facts, unauthorized writes, failures represented as zero, or ungrounded counts.
The user explicitly authorized a first rollout with the new skills enabled after
the completed product checks/smokes/first candidate run; the full matrix remains
required follow-up evidence and must not be represented as already qualified.
