# Content-first ETL pilot A and source-fixture correction

The six-case baseline completed without an execution or infrastructure failure. Original automatic result: **3/6**. This is an exploratory A-only run, with no paired efficiency or release claim. Original responses, automatic grades and costs remain unchanged.

## Reproducible identity

- Run `pilot-a-e3000f7c-92025156`, cohort `cohort-920251564317498aac24454215895b41`.
- Clean H commit `e3000f7c512f0f5663748c4fee786452fe687136`; its app tree equals deployed `9b6fde102fb8420f2fd135d72b90b25a9af666c5`. Baseline deployed skills and common research-file capability.
- Actual Codex CLI 0.167.0, `gpt-6.1-sol`, high reasoning, existing ChatGPT login; one invocation, no fallback or model retry.
- Battery 2.1.1, world v4, seed `ltm-etl-year-v1`. Public-data SHA256 `64b2720223036977858959da391ac0ab7975e94b051eb6efe24908e0b838dd62`.
- Battery manifest `ffb2fc1bd1be6f3dddf9a2ec6c27c3a47243a4840d9deea0202c49e8b1a376b6`; review specification `1529336b5f43d560f22e0836f2f1049c96c3af5d9c2bcf6700b7ae96c36552e0`.
- Durable evidence root: `C:/Users/PC/research/ltm-188-189-190/pilot-a-e3000f7c-92025156`. Aggregate: `evidence/etl_pilot_A_pilot_a_e3000f7c_92025156/codex-mcp-etl-year-pilot-b2.1.1-r01.json`, byte SHA256 `df1f5c47718b5f8b0586b075f5c74709f429892963c764f8aa602fff6401c346`.

## Measured case cost

| Case | Seconds | Original auto | Jira REST | Confluence REST | LTM tools | File tools | Actual tokens |
|---|---:|---|---:|---:|---:|---:|---:|
| F03 monthly | 433.403 | FAIL | 457 | 12 | 462 | 10 | 1,348,784 |
| F04 WBS | 80.573 | FAIL | 15 | 4 | 13 | 0 | 357,342 |
| F08 loading | 184.670 | PASS | 23 | 0 | 123 | 231 | 877,820 |
| F09 modification/reload | 170.686 | PASS | 37 | 0 | 49 | 10 | 735,486 |
| F12 simple VoC | 121.036 | PASS | 59 | 0 | 65 | 59 | 790,669 |
| F14 report revisions | 191.637 | FAIL | 88 | 13 | 22 | 6 | 625,972 |
| Total | 1,182.005 | 3/6 | 679 | 29 | 734 | 316 | 4,736,073 |

Wrapper duration was 1,324.2 seconds. There were 1,050 tool attempts, plus 24 discovery and 12 other protocol requests. LTM attempts include 107 recovered local rate-limit rejections and one local permission rejection; all 708 case-scoped upstream records were HTTP 200. Cache priming and recovery observed within the cases remain counted. Fixture setup/conformance/preflight traffic is outside the case ledger: 679 is not an all-invocation Jira total.

Case input/output usage was 4,689,956/46,117. Preflight adds 10,712 tokens, yielding **4,746,785 cumulative measured tokens**. Cached input 4,159,872 and reasoning 9,783 are subsets, not additional usage. Monetary cost, model-call count and TTFT remain unknown. Session/reviewer tokens are excluded. F08 used 111 evidence-page tools but only 10 upstream comment requests: caching can reduce Jira traffic while leaving substantial MCP and context overhead.

All six attempts and their background settlements completed. Source, frozen files, approved dependencies and world/provider fingerprints remained stable; owned processes stopped, scratch was removed and port 4457 closed. These checks do not convert answer failures into passes.

## Direct review and subsequent diagnostic

Separate, nonblind native Sol/high direct reviews used the common five axes and found no material answer defect: F03 development/operations/VoC 4.7/4.8/5.0; F04 development 4.9; F08 operations 4.9; F09 operations 5.0; F12 VoC 4.9; F14 development 4.9. No pooled mean or independently verified reviewer-provider receipt is claimed. Exact evidence and rubric observations remain in the external `direct-review-A-e3000f7c-*` and `etl-direct-review-newA-*` files.

A separately saved post-hoc host diagnostic is **not qualification**, not a new model attempt and not a replacement grade. It verified that F04's actual fresh native aggregates can establish the requested current WBS percentage without 256 redundant child inputs. It also separated two kinds of F03/F14 residual:

- A real F03 structured temporal error remains: November 5 is the correction/publication date, not a proven occurrence date for the pending acceptance decision. Prose correctly identifies a correction; the canonical occurrence field must still remain unknown.
- Host overconstraints include eight unrequested cumulative lifecycle metrics, a mandatory machine row for a scope-only planned meeting already explained in prose, and rejection of a selected Task's otherwise grounded empty period-report context. Public source wording must not be strengthened into invented acceptance or occurrence dates. Optional context still requires its own evidence and does not establish zero actual work.

The initial diagnostic is `posthoc-old-a-host-diagnostic-3d79b2c4`; original raw hashes were checked unchanged. Its latest-world expectations differ from the original exposure, so its results cannot be pooled with that run.

A second separate diagnostic, `posthoc-old-a-host-diagnostic-final-8ac19e62`, retained the real F03 date failure and exposed one further host requirement: a finding excluding an unrelated Storage report asserted no Jira status but still demanded that Task's historical status. Finding-only proof now requires the actual report and candidate description; progress rows, including optional empty-period rows, still require their stated Jira status/history. No third diagnostic or replacement grade is claimed.

## Why the comparison must be resealed

The observed F03 `status WAS ... DURING` query silently returned 2,240 rows because the pinned fake Jira ignored historical predicates. The model recovered the correct development count by reading 109 complete histories. That artificial fallback cannot estimate optimal production Jira filtering, even though the answer was correct. No B or C arm was launched under this broken source contract.

The prospective common evaluation repair uses only public creation/current status/serialized changelog, with per-request isolation, strict unsupported-clause rejection and the same adapter for agent/UI/restart fixtures. The public actor and Asia/Seoul profile are now explicit and fingerprinted in world v5. Boundary behavior was checked against the official [Jira 8.20.8 core artifact](https://maven.artifacts.atlassian.com/com/atlassian/jira/jira-core/8.20.8/jira-core-8.20.8.jar), SHA256 `98bada68e8330daabd5890647e907793789276bccdba28e43bbc481c5a5070b0`: `created <= operand2.upper` and `nextChange >= operand1.lower + 1000ms`, including tied transitions. Finite grammar, ordinary date limits and the absence of live-server/plugin or indexed-latency validation remain explicit.

The repair does not change deployed H's app code. All comparison arms must receive identical common source/checker changes and new sealed identities before further paid runs. Older cohorts and this baseline remain intact. This report makes no savings, default-promotion or release-qualification claim.

Local validation: the related integration suite passed 291 tests before the final narrow optional-row/proof split; the final report/native suite passed 109 focused tests after that split. An additional full-world fixture test verified the historical monthly query's exact development membership of 108 and complete broader candidates without warming product caches. These are implementation checks, not substitute model runs or paired performance results.
