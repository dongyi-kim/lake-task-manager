# Local Qwen 35B r40 — full battery quality and efficiency

> Run date: 2026-08-19
> Candidate commit: `af34e4c66069208d7ef1eebc29b3ffb4b175d579`
> Model / simple model: `ltm-qwen3.6-35b-a3b` / same
> Runtime: `local-qwen35b-shared-v1`, provider `openai_compat`
> Comparison: EN v13 OpenAI primary full, commit `e68d2ea76c6cce75ca26c8612ad7123813b241d1`

## Verdict

The local candidate is **not quality- or latency-equivalent to the OpenAI v13 baseline**.
It passed 35/58 automatic contracts (60.3%) and received a direct Codex qualitative score
of 3.69/5, versus 54/58 (93.1%) and 4.66/5 for the OpenAI baseline. Total observed wall
time was 11,011.4 seconds including one bounded provider timeout, 9.48 times the baseline.

The result is useful as a diagnostic rather than a release qualification. Editor behavior and
simple exact updates are strong. The dominant failures are multi-item identity binding,
duplicate detection, structured-output recovery, meeting assignment/effect binding, and
Research routing loops.

## Method and evidence

- The five core suites were run once: conversation 8, create 28, editor 9, meeting 9,
  context-change 4. No candidate output was replaced by a focused closure result.
- The LTM model was not used as a judge. Codex directly reviewed reply, questions,
  approval/pending payload, automatic flaws, grounding checks, and execution traces.
- The run is exploratory: one repetition, non-blinded review, no statistical qualification.
- `ASKD2` did not return after 1,200 seconds. It was recorded as a timeout failure and was
  not retried. The other 27 create cases were each executed once; their artifacts are split
  between `create.json` and `create-rest.json`.
- Calls and tokens for the timed-out request are unavailable, so the reported 287 calls and
  2,027,927 tokens are lower bounds. The 1,200 seconds are included in wall time and latency.
- Raw artifacts are under
  `.cache/agent-evaluation/2026-08-19-local-qwen35b-full-r40/`.

## Full-run comparison

| Metric | Local r40 | OpenAI v13 | Change |
|---|---:|---:|---:|
| Automatic contracts | 35/58 (60.3%) | 54/58 (93.1%) | -32.8 pp |
| Direct qualitative quality | 3.69/5 | 4.66/5 | -0.97 |
| Wall time | 11,011.4s | 1,161.7s | 9.48x |
| Mean time / case | 189.9s | 20.0s | 9.48x |
| p50 case latency | 123.1s | 16.3s | 7.55x |
| p95 case latency | 758.4s | 43.9s | 17.28x |
| LLM calls | >=287 | 356 | <=-19.4% |
| Prompt tokens | >=1,852,047 | 1,888,114 | <=-1.9% |
| Completion tokens | >=175,880 | 93,638 | >=+87.8% |
| Total tokens | >=2,027,927 | 1,981,752 | >=+2.3% |
| Tokens / recorded call | 7,066 | 5,567 | +26.9% |

Fewer calls did not produce lower latency. Local completion volume nearly doubled, each call
carried more context, and long Research/Work loops dominated wall time.

## Suite results

| Suite | Local automatic | Local quality | Time | Calls | Tokens | OpenAI automatic | OpenAI quality |
|---|---:|---:|---:|---:|---:|---:|---:|
| conversation | 5/8 | 4.01 | 1,234.6s | 33 | 212,369 | 6/8 | 4.45 |
| editor | 9/9 | 4.62 | 106.1s | 6 | 27,163 | 9/9 | 4.69 |
| create | 17/28 | 3.66 | 4,218.3s | >=107 | >=521,627 | 27/28 | 4.66 |
| meeting | 2/9 | 2.67 | 4,839.6s | 115 | 1,116,774 | 8/9 | 4.70 |
| context-change | 2/4 | 3.53 | 612.8s | 26 | 149,994 | 4/4 | 4.85 |
| **overall** | **35/58** | **3.69** | **11,011.4s** | **>=287** | **>=2,027,927** | **54/58** | **4.66** |

Direct-review dimension averages were request fulfillment 3.70, factual grounding 3.75,
contract/actionability 3.54, safety/uncertainty 3.85, and communication/rendering 3.63.
Fail-closed behavior prevented unsafe writes in many bad cases, but it did not fulfill the
requested action and therefore does not count as a quality pass.

## Failure inventory

Automatic failures by suite:

- conversation: `S1`, `S7`, `S8`
- create: `ONE2`, `STR2`, `STR3`, `PAR1`, `SUB2`, `ASKD2`, `AMB1`, `DUP1`,
  `ASKD4`, `BUG3`, `RULE1`
- meeting: `MTG1`, `MTG2`, `MTG4`, `MTG5`, `MTG7`, `MTG8`, `MTG9`
- context-change: `CTX3`, `CTX4`

Important direct-review findings:

1. Multi-item write identity remains unstable. `STR2`, `PAR1`, and `SUB2` produced duplicate
   `item_id` values and were correctly blocked by the reviewer, but the user received no usable
   approval draft.
2. Duplicate work was not recognized. `DUP1` and `BUG3` created new approval drafts instead of
   confirming the existing work or asking for the missing identity/reproduction facts.
3. Structured-output failure recovery can contradict state. `ASKD4` said a Task was ready for
   approval while pending payload was empty after RequestArchitect validation exhaustion.
4. Meeting mapping is functionally fail-closed but not usable. Five cases ended in typed
   assignment/effect/review rejection; `MTG1` also presented contradictory writer-PoC status.
5. Context target rebinding was safely rejected in `CTX3` and `CTX4`, but the desired updates
   were not produced.
6. The editor suite was the strongest area: 9/9, including deterministic zero-call state
   summaries and bounded semantic rewriting.

## Call-path diagnosis

| Role | Calls | Tokens | Model seconds |
|---|---:|---:|---:|
| ResearchAnalyst | 62 | 834,864 | 3,058.1 |
| WorkArchitect | 73 | 443,275 | 2,970.9 |
| RequestArchitect | 69 | 332,826 | 1,919.9 |
| Auditor | 24 | 143,138 | 414.2 |
| ResultIntegrator | 10 | 93,400 | 441.0 |
| PeopleAdvisor | 25 | 89,206 | 410.0 |
| QuerySpecialist | 14 | 38,380 | 211.9 |
| EditorAuthor | 6 | 27,163 | 102.1 |
| KnowledgeCurator | 4 | 25,675 | 136.7 |

There were 26 structured repair calls (9.1% of recorded calls): 15 general structured repairs,
7 typed-projection repairs, and 4 tool-decision repairs. The typed fast-path telemetry recorded
five committed skips (two exact updates and three portfolio projections), but also 45 invalid
events; those invalid events need reconciliation before the telemetry can be treated as a
reliable optimization denominator.

## Follow-up: bounded required-field repair (r42)

Nine of the 15 general repairs were the same Research schema failure: the initial object had
valid evidence but omitted the single required root field `situation`. The common structured
adapter now proves that this is the only JSON Schema violation, preserves the validated object,
requests a strict one-field patch, merges it server-side, and validates the complete original
schema again. Any second violation keeps the existing full-repair path.

The same real Qwen CTX4 turn produced the following isolated repair measurements:

| Metric | r40 full repair | r42 required patch | Change |
|---|---:|---:|---:|
| Prompt tokens | 1,721 | 1,115 | -35.2% |
| Completion tokens | 960 | 192 | -80.0% |
| Total tokens | 2,681 | 1,307 | -51.2% |
| Model time | 32.406s | 7.971s | -75.4% |

The end-to-end CTX4 result is not used as a latency A/B because r42 made one extra stochastic
Research tool-decision call. Its existing final target-authority failure also remained unchanged.
The table measures only the matched `structured_repair` opportunity; the full regression suite
after the change was 3,182 passed and 2 skipped.

The prompt-only tool fallback also repeated the same nine-tool Research catalog with JSON
whitespace on every decision. The catalog now uses the same fail-closed compact JSON transport as
schema prompts: parsed value, property order, descriptions, input schemas, and validation remain
identical. The catalog itself changed from 11,138 to 10,815 characters and from 2,597 to 2,303
o200k tokens. In the same CTX4 fixture, the first provider-counted Research decision prompt changed
from 9,236 tokens in r42 to 8,913 in r43 (`-323`, `-3.5%`). Later decision count and latency are not
used as an A/B because tool selection and generation length varied between runs.

## Recommended next work

1. Fix producer identity, not downstream title matching: unique server-owned `item_id` and exact
   source/outcome relation for multi-item create and meeting assignments.
2. Add a typed duplicate-candidate receipt that binds requested work to existing tickets before
   WorkArchitect can issue a create effect.
3. Make structured failure terminal state truthful: no approval language unless a validated
   pending payload exists; expose one bounded recovery question or a clear failure.
4. Reduce Research loops by constraining tool-decision state and repairing once outside the
   repeated full-context loop. `MTG9` alone used 11 Research calls and 178,589 total tokens.
5. Preserve the strong lanes: deterministic editor paths, exact single-ticket updates, and
   portfolio projection. Changes to the shared architecture must keep these regressions green.

The candidate should remain behind the local-model opt-in gate until a new full run reaches the
OpenAI baseline on both direct quality and latency, not merely on call count.
