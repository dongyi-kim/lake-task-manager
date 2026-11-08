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

Regression tests reproduced the issues before the implementation changes. Targeted
real-model confirmation of source/progress cases, full 220-case A/B and 60-case cold
recovery evaluation, direct rubric scoring and any default activation remain pending.

Validation after the independent review fix pass: **125 focused/protocol tests
passed** in 90.19s. New negative cases reject unrelated scope words, workload
claims, ratios attributed to the wrong scope, conflicting displayed percentages,
missing hierarchy pages/status disagreement, failed source reads and missing IDs.
The denominator-label check is bounded arithmetic/text validation; direct semantic
review remains mandatory. Full offline CI is handled by the PR workflow.
