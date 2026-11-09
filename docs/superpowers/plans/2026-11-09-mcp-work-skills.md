# Issue 190: research, reporting and resumable investigations

Status: initial product release with default-enabled skills authorized; full repeated qualification follows.
Spec: https://github.com/dongyi-kim/lake-task-manager/issues/190 plus the user's
2026-11-09 checkpoint/resume requirement and approved conversation plan.

## Goal and constraints

The user explicitly changed the rollout order on2026-11-09: release the reasonably
verified product first, with both research/reporting skills enabled by default,
then continue full A/B and cold-recovery validation in the separate evaluationPR.
Completed product CI, default/fallback22-case smoke, firstcandidate22-case automatic/
direct review and independent product code review support this initial delivery.
This supersedes the original default-off/no-merge/no-release hold below; it does
not assert complete302-case qualification. Release only the app main/tag/public
GitHub Release; do not change deploy-repository pointers, tags or releases.

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
- Report candidate status and pending real-model validation honestly. Do not merge
  or release as part of implementation.

## Later real-model gate (not authorized to run)

Model proposal: current harness gpt-6-astra/ultra, explicitly approved before run.
Smoke: 11 B cases in default and fallback = 22. Main A/B: 22 x 2 x 5 = 220.
Recovery: 6 x uninterrupted/restarted x 5 = 60. Total 302 plus preflights/retries.
Record exact commit/manifests/model/clock/order, genuine tool evidence, token,
latency, upstream work and file overhead. Preserve first failures and new attempts.
Require automatic gates, direct human review mean >=4.5/5 and zero material false
facts, unauthorized writes, failures represented as zero, or ungrounded counts.
Only a later qualified rollout enables the candidate skills by default.
