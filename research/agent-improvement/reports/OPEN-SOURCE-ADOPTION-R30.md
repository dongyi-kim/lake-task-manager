# Agent OSS adoption review — r30

Date: 2026-08-18

This is an engineering classification, not legal approval.  A package is eligible for a
proof of concept only after its exact version, transitive dependencies, NOTICE obligations,
security advisories, telemetry defaults, and the company's OSS allow-list have been checked.
The project is currently internal, is expected to be published as open source, and is not
planned for commercial sale.  Non-commercial use does not waive license obligations.

## Decision policy

1. Search established implementations before adding a new runtime policy or parser.
2. Prefer the framework already in the product over a second overlapping framework.
3. MIT, BSD, ISC, Apache-2.0, and Boost Software License 1.0 (`BSL-1.0`) code may enter an isolated PoC, subject to the checks
   above.  For Apache-2.0 distributions, inspect the exact artifact and preserve any NOTICE
   content it actually ships, together with required attribution.
4. Unmodified LGPL/MPL dependencies may be direct-use candidates when the distribution
   preserves their license/NOTICE, source link and replacement ability and satisfies the
   exact package's modification/disclosure obligations.  GPL requires an explicit project
   license-compatibility decision.  AGPL, SSPL, `BUSL-1.1`/Business Source, Elastic License, non-commercial,
   and unknown licenses remain reference-only by default.
5. No PoC may send Jira, Confluence, people, prompt, or trace data outside the configured
   corporate/local endpoints.  Telemetry and hosted validators must be explicitly off.
6. Adoption requires a measured deletion target.  A dependency that cannot remove or
   simplify existing code, reduce calls, or improve held-out quality is not adopted.
7. Compare on generic held-out cases.  STARR1 or any single named fixture may not be the
   optimizer, acceptance set, or prompt example.

## Current stack: reuse before replacement

| Component | License | Current use | r30 decision |
|---|---|---|---|
| LangGraph 0.3.2 | MIT | StateGraph, checkpoint memory, routing | Keep.  First PoC native `interrupt`/resume for one approval or required-input flow before maintaining more custom pause semantics.  Upgrade is a separate migration, not part of a quality fix. |
| OpenAI Python 1.65.2 | Apache-2.0 | Local OpenAI-compatible model transport | Keep.  The configured local endpoint and no-egress boundary remain authoritative. |
| Langfuse 2.59.7 | MIT core; enterprise directory/features excluded | Optional LangChain callback | Keep and use more fully.  Add stable case/version/node/repair/effect metadata through the existing callback before considering a major-version upgrade.  Self-host or approved endpoint only. |
| MCP Python 1.29.0 | MIT | Explicit read-only server/client allow-list | Keep, but run an exact-version advisory review and retain Host/Origin/auth protections.  Do not expose a general network transport merely because the SDK supports it. |
| Pydantic 2.13.4 | MIT | Typed state and strict role schemas | Keep as the canonical runtime contract. It is currently transitive rather than directly pinned, so the SBOM must still freeze it. |
| jsonschema 4.26.0 | MIT | Direct JSON Schema validation in `workflow/agents/base.py` | **Adopted in r31.** Added an exact direct runtime pin instead of relying on MCP to keep an incidental transitive dependency. No new installed package and no runtime behavior change. |
| Instructor 1.15.4 | MIT; exact wheel has `LICENSE`, no NOTICE | Pydantic response-model validation and one bounded format retry over existing LangChain calls | **Adopted in r32 after the OpenAI 2 migration.** Default with an explicit legacy gate; initialization can roll back only before any wire call. Full real-battery quality evidence remains a separate release gate. |
| Tenacity 9.1.4 in the target resolve | Apache-2.0 | Instructor's internal retry engine; LTM has no direct import | Record the resolved transitive version in the lock/SBOM. LTM supplies only the one-retry bound and does not add a second retry loop. |
| JSON Schema specification | N/A (specification, not the Pydantic package license) | Schema vocabulary consumed by local validators/providers | Keep the vocabulary separate from any implementation library's license finding. |

Discovery references below point at upstream project pages. They are not exact-version legal
evidence. Before promotion, archive the license/NOTICE files from the exact installed tag or
commit together with the SBOM and advisory scan date.

References:

- LangGraph license and durable/HITL model: <https://github.com/langchain-ai/langgraph/blob/main/LICENSE>, <https://github.com/langchain-ai/langgraph>
- Langfuse license boundary: <https://github.com/langfuse/langfuse/blob/main/CONTRIBUTING.md>, <https://github.com/langfuse/langfuse-docs/blob/main/content/self-hosting/license-key.mdx>
- MCP Python SDK and security status: <https://github.com/modelcontextprotocol/python-sdk>, <https://github.com/modelcontextprotocol/python-sdk/security>
- OpenAI Python SDK: <https://github.com/openai/openai-python>
- jsonschema package license: <https://github.com/python-jsonschema/jsonschema/blob/v4.26.0/COPYING>
- Tenacity package license: <https://github.com/jd/tenacity/blob/9.1.4/LICENSE>

## Candidate matrix

| Candidate | Exact license finding | Useful overlap | Data/security caveat | Decision |
|---|---|---|---|---|
| Instructor | 1.15.4 MIT; exact wheel includes `LICENSE`, no NOTICE | Pydantic response models, validation feedback, bounded retries | Requires `openai>=2,<3` and `jiter<0.15`; the adapter must keep calls on the existing LangChain/local-endpoint path | **Adopted in r32.** Exact pins are Instructor 1.15.4, OpenAI 2.54.0 and jiter 0.14.0. One shared adapter now replaces both manual prompt-JSON ladders while preserving usage/tracing and a legacy capability gate. |
| DeepEval | 4.1.8 Apache-2.0; exact wheel includes `LICENSE.md`, no NOTICE | Dataset/test runner, conversational/RAG metrics, DAG/custom metrics | Telemetry defaults on to PostHog; OTel, gRPC, PostHog, pytest and plugins are mandatory runtime dependencies. Hosted sync must remain off | **Do not promote.** The fully offline PoC worked, but it adds a second heavy evaluator, no current harness deletion, MPL transitive distribution work, and dependency-version pressure. Keep as architecture reference. |
| DSPy | MIT | Typed signatures and prompt optimization against a metric | Optimizers can overfit a small battery and consume many local-model calls | **Research PoC only.** Train/validation/test split by capability family, with named cases excluded from optimization.  Adopt only if held-out quality improves without runtime coupling. |
| Outlines | Apache-2.0 | Constrained JSON/regex/grammar generation | Primary constrained backends target Transformers, llama.cpp, or MLX; the current remote OpenAI-compatible Qwen service already exposes schema modes | **Defer.** Revisit only if the model server exposes a supported constrained-decoding backend and it removes repair calls. |
| PydanticAI core | MIT | Typed agents/tools, durable integrations, evaluation/dataset patterns | It substantially overlaps LangGraph and would create two state runtimes | **Architecture reference; isolated runner PoC at most.** No wholesale migration. |
| Pydantic AI Gateway | AGPL-3.0, archived | Gateway/routing patterns | Copyleft network service and archived project | **Reference-only; do not install or copy.** |
| OpenAI Agents SDK | MIT | Sessions, handoffs, guardrails, tracing, MCP, HITL | Overlaps LangGraph; a local OpenAI-compatible comparison must explicitly disable the SDK's default trace export or install an approved local processor | **Architecture reference; optional isolated comparison.** No second production orchestrator without a deletion/migration case. |
| Temporal Python SDK | MIT | Process-level durable execution and idempotent activities | Requires additional service/operations and duplicates LangGraph checkpointing for current scope | **Defer.** Consider only when crash recovery must span processes/hosts beyond LangGraph persistence. |
| OpenTelemetry Python | Apache-2.0 | Vendor-neutral traces/metrics and exporter separation | Exporters can cause unapproved egress | **Small PoC candidate.** Local/no-op exporter first; compare with extending existing Langfuse callback. |
| Arize Phoenix | Elastic License 2.0 | Self-hosted observability and eval UI | Source-available license requires Legal review; not treated as permissive OSS here | **Reference-only pending approval.** |
| Guardrails AI | Apache-2.0 | Composable validators and structured-output reasks | A malicious PyPI release (`0.10.1`) was reported in May 2026; validators may add models or hosted inference/telemetry | **Do not install in r30.** Reference validator interfaces only until supply-chain and package-pin review clears it. |

### Famous alternatives screened, but not added

- **Microsoft AutoGen** has permissively licensed code, but the official project now says it
  is in maintenance mode and directs new users to Microsoft Agent Framework.  It is not a
  sensible new production dependency.
- **Microsoft Agent Framework** and **Semantic Kernel** are MIT-licensed and offer typed
  agents/orchestration, but both duplicate the LangGraph layer.  Their layered runtime,
  effect middleware, and provider-boundary patterns are reference material; a direct PoC
  requires an explicit module-deletion plan.
- **CrewAI** is MIT-licensed and supports local models, but its role/crew abstraction would
  add another orchestration and telemetry policy.  It is reference-only; `share_crew`-style
  data sharing must never be enabled for internal data.
- **LlamaIndex** core is MIT-licensed and **Haystack** is primarily Apache-2.0.  Both are
  strong document/RAG frameworks, but the application already has Jira/Confluence-specific
  retrieval, FAISS, provenance, and LangGraph routing.  Adopt a standalone retriever or
  evaluator only if it outperforms the current component and removes code; do not add a
  second end-to-end RAG stack.

Primary references:

- Instructor exact metadata and license: <https://pypi.org/pypi/instructor/1.15.4/json>, <https://pypi.org/pypi/instructor/1.8.2/json>, <https://github.com/567-labs/instructor/blob/v1.15.4/LICENSE>
- DeepEval exact metadata, license and telemetry settings: <https://pypi.org/pypi/deepeval/4.1.8/json>, <https://github.com/confident-ai/deepeval/blob/v4.1.8/LICENSE.md>, <https://github.com/confident-ai/deepeval/blob/v4.1.8/deepeval/telemetry/client.py>, <https://github.com/confident-ai/deepeval/blob/v4.1.8/deepeval/config/settings.py>
- DSPy license and program/optimizer model: <https://github.com/stanfordnlp/dspy/blob/main/LICENSE>, <https://dspy.ai/>
- Outlines license and backends: <https://github.com/dottxt-ai/outlines/blob/main/LICENSE>, <https://dottxt-ai.github.io/outlines/latest/features/advanced/backends/>
- PydanticAI core and gateway licenses: <https://github.com/pydantic/pydantic-ai/blob/main/LICENSE>, <https://github.com/pydantic/pydantic-ai-gateway/blob/main/LICENSE>
- OpenAI Agents SDK: <https://developers.openai.com/api/docs/guides/agents>, <https://github.com/openai/openai-agents-python/blob/main/LICENSE>
- Temporal Python SDK: <https://github.com/temporalio/sdk-python>
- OpenTelemetry Python: <https://github.com/open-telemetry/opentelemetry-python>
- Phoenix license: <https://github.com/Arize-ai/phoenix/blob/main/docs/phoenix/self-hosting/license.mdx>
- Guardrails license and security advisory: <https://github.com/guardrails-ai/guardrails>, <https://github.com/guardrails-ai/guardrails/blob/main/SECURITY_ADVISORY.md>
- AutoGen/Microsoft Agent Framework/Semantic Kernel: <https://github.com/microsoft/autogen>, <https://github.com/microsoft/agent-framework>, <https://github.com/microsoft/semantic-kernel>
- CrewAI, LlamaIndex, and Haystack: <https://github.com/crewAIInc/crewAI>, <https://github.com/run-llama/llama_index>, <https://github.com/deepset-ai/haystack>

## What should become library code vs product code

The following are commodity mechanisms and should be delegated or consolidated where a
PoC proves compatibility:

- JSON extraction, schema validation feedback, retry accounting, and raw failed-attempt
  capture;
- graph persistence, pause/resume, and human approval interruption;
- standard trace/span propagation and local export;
- evaluation dataset execution, result storage, and generic metric plumbing;
- MCP transport and schema validation.

The following remain Lake Task Manager product authority and must not be delegated to an
LLM framework or validator package:

- Jira effect envelope, exact target/field/value locks, approval fingerprints, and
  idempotent side-effect execution;
- canonical Jira/Confluence/person provenance and temporal fact authority;
- internal data-egress policy and public-query sanitization;
- meeting role/assignee semantics and company-specific workflow constraints;
- the human qualitative rubric and release decision.

## Measured PoC sequence

Measured starting point on the r29 tree: `agents/base.py` is 1,120 physical lines and its
structured transport, validation/repair, semantic projection, and post-projection correction
paths span roughly 400 lines.  `work_architect.py` is 9,798 lines.  The graph already compiles
with a LangGraph checkpointer and `interrupt_before` for the executor, but required-input
interviews still use substantial custom state/routing.  Consequently, an OSS adapter is not a
success merely because it wraps these paths: the PoC must delete a meaningful portion of them
or prevent equivalent new policy code.

### r31 review-first execution result

Review date: 2026-08-18.  Base commit: `afb0d5d541a5d914a3b05fdeb87390b9edd29b30`;
the PoCs ran from the active r31 working tree.  They used Python 3.11, no real LLM/API, no
project secret, and an ignored workspace target under `.cache/poc/oss-gate`.  Socket
`connect`/`connect_ex` was blocked and recorded by each process.  These are deterministic
compatibility measurements, not held-out agent-quality evidence.

| Measurement | Instructor 1.8.2 | DeepEval 4.1.8 |
|---|---:|---:|
| Exact direct wheel SHA-256 | `06e143fb467135f0a572d9e1f255ab2fedd722bbac6edcd42def18ac597bee8f` | `998c1e424a6a8a11c919b894a0c08dc6e2c4a6331863ed79e323b8f0bf2b4ccf` |
| Exact direct license/NOTICE | MIT / none | Apache-2.0 / none |
| Resolved wheel set | 40 files, 7,190,646 compressed bytes | 66 files, 18,742,518 compressed bytes |
| Isolated installed tree | 41,138,856 bytes | 99,490,096 bytes |
| Cold import, one exploratory run | 0.776 s | 1.490 s |
| Deterministic operation | 0.052 s; invalid date then valid date | 0.022 s; three custom exact-match cases |
| Result | valid typed object after two fake wire calls | pass vector `[true, false, true]` |
| Observed socket attempts | 0 | 0 |

The size figures are complete isolated environments, not incremental production size; they
make the candidates comparable while avoiding an understated dependency footprint.  The
resolved artifacts were scanned from their wheel metadata and embedded license/NOTICE files.
Instructor's direct set is permissive except existing common MPL-licensed certificate/progress
dependencies; its shipped NOTICE-bearing transitives include Requests, propcache, and yarl.
DeepEval additionally pulls MPL-licensed pytest plugins.  If any such dependency ships with an
OSS distribution, preserve its exact license/source/replacement obligations and all NOTICE files.
The exact PyPI JSON `vulnerabilities` arrays for Instructor 1.15.4, Instructor 1.8.2, and
DeepEval 4.1.8 were empty on the review date.  That feed observation is not a guarantee that the
whole resolved graph is clean; the exact wheel set still belongs in the repository's SBOM/OSV
audit before any later promotion.

At the r31 baseline, Instructor 1.15.4's exact metadata requirement `openai>=2.0,<3.0`
conflicted with the then-product pin `openai==1.65.2`; its 252,522-byte wheel SHA-256 is
`00e0ecda80fd9746fb6d082d3f9641e193adb1d8849f0775f91519a82aeff968`.  Version 1.8.2 is the
latest release whose exact metadata accepts that SDK
(`openai>=1.52,<2.0`), but resolves `jiter==0.8.2` rather than the current 0.16.0 and adds
`docstring-parser`, Jinja2, Rich and Typer families.  Its wheel contains no telemetry/PostHog/
Sentry/OpenTelemetry code.  The fake OpenAI transport proved schema feedback was added to the
second request, but the completion error hook recorded no validation diagnostic.  The result
still took two wire calls, the same one-initial/one-repair ceiling already enforced by LTM.
The compatibility boundary was verified across every published wheel after 1.8.2: 1.8.3 through
1.12.0 require `openai>=1.70`, and 1.13.0 through 1.15.4 require `openai>=2.0`.

DeepEval 4.1.8 defaults analytics **on** and its exact code points PostHog at
`https://us.i.posthog.com`.  The PoC set `DEEPEVAL_TELEMETRY_OPT_OUT=1`,
`DEEPEVAL_DISABLE_DOTENV=1`, `DEEPEVAL_FILE_SYSTEM=READ_ONLY`,
`DEEPEVAL_NO_INSPECT_PROMPT=1`, disabled the update check, and provided no
`CONFIDENT_API_KEY`.  The global OpenTelemetry tracer provider remained the same object and no
socket attempt occurred.  Exact 4.1.8 source no longer contains the Sentry, New Relic, or public
IP lookup strings reported against older 3.7.7 code.  It still printed a Confident AI call to
action, and an unconstrained isolated resolve selected `packaging==26.3` and `pytest==9.1.1`,
which conflict with the product's Langfuse/LangChain packaging bounds and test `pytest<9` policy
unless the complete application constraint set is resolved together.

Older telemetry risk report inspected during review:
<https://github.com/confident-ai/deepeval/issues/2497>.  The r31 decision is based on exact 4.1.8
wheel source rather than assuming that the older report still describes the current package.

Net production deletion was zero for both r31 PoCs.  Instructor overlaps the 109-line
`invoke_schema` and 157-line `_invoke_structured_transport` routines, but those routines also
own provider capability negotiation, unsupported-capability caching, non-retryable transport
classification, projection routing, safe usage metadata, Langfuse callbacks, and truncation
fail-close policy.  Replacing only commodity validation/retry would add an adapter while leaving
those authorities in place.  DeepEval required a custom metric adapter and did not replace the
versioned manifests, raw evidence capture, deterministic contract checks, or direct human rubric.

The one immediate production adoption is therefore `jsonschema==4.26.0`: LTM already imports
and executes it, so making it a direct exact pin adds zero packages, zero runtime code, zero calls,
and removes the hidden assumption that MCP will retain it.  A dependency test locks the direct pin
to the installed version.  Verification: 63 related tests passed and `pip check` reported no broken
requirements.  Rollback is removal of the pin and its dependency test; no stored state changes.

### r32 Instructor adoption update

The r31 rejection above remains the historical result for the 1.8.2 wrapper PoC. It was
superseded after explicit approval to move the application to current dependencies and adopt
Instructor. The production boundary now pins `instructor==1.15.4`, `openai==2.54.0`, and
`jiter==0.14.0`. Instructor receives a Pydantic response model, owns validation retry accounting,
and sees the exact role JSON Schema. Actual model calls still run through the existing LangChain
closures, so local OpenAI-compatible endpoints, callbacks, usage metering, trace metadata, and
wire budgets are unchanged. `LTM_AGENT_STRUCTURED_OUTPUT_BACKEND=legacy` is the explicit rollback;
an Instructor initialization failure also falls back only when no wire call has been spent.
The exact 1.15.4 wheel is 252,522 bytes with SHA-256
`00e0ecda80fd9746fb6d082d3f9641e193adb1d8849f0775f91519a82aeff968`. In the target resolve,
reverse dependency metadata attributes eight additional transitives only to this adoption:
`docstring-parser`, Jinja2, MarkupSafe, Typer, Shellingham, Rich, markdown-it-py, and mdurl;
OpenAI, jiter, Pydantic, Requests, aiohttp, and Tenacity are shared with the modernized stack.

This is no longer a wrapper-only addition. The shared `base.py` change is +97/-168 lines (net
-71), removing two manual prompt-JSON parse/repair ladders, the duplicate repair prompt, the
private manual parser, and an unused structured fallback. The isolated adapter is 135 lines, so
the measured total production delta is +64 lines with 168 old base lines deleted. Product-owned
strict terminal framing, role `pre_validate`, local JSON Schema validation, truncation fail-close,
and trace labels remain explicit; Instructor owns the commodity Pydantic parsing/retry boundary.

On the exact target stack, deterministic fake traces were equivalent for the default and legacy
backends: strict-valid and valid-with-`finish_reason=length` used one wire call; invalid syntax or
schema used exactly two; initial transport, empty output, and partial length-truncation used one
and failed closed. Verification was 22 adapter/dependency tests, 57 structured-capability tests,
and 237 config/usage/model-profile/graph/prompt/dependency-modernization tests; `pip check` was
clean. These tests used no real API and are compatibility evidence, not a claim of held-out
quality or latency improvement. The complete real battery and human review remain release gates.

### P0 — use what is already installed

1. Emit stable role, schema version, repair stage, defect signature, effect digest, source
   coverage, duration, and tokens through the existing usage callback and Langfuse hook.
2. Evaluate one LangGraph `interrupt`/resume flow against the current custom required-input
   path.  Accept only if it preserves the UI contract and deletes custom branching.
3. Run exact-version dependency/SBOM/advisory checks for the existing MCP, LangGraph,
   Langfuse, OpenAI, and transitive packages.

### P1 — isolated direct-use experiments

1. **Instructor transport adapter: adopted on the OpenAI 2 baseline.** Keep the legacy gate until
   the complete real battery and human review confirm no quality or latency regression.
2. **DeepEval offline adapter: completed and not promoted.** Do not add it merely as a wrapper
   around existing battery JSON; reconsider only if a bounded replacement deletes evaluator code
   and agrees with direct human review on held-out suites.
3. **OpenTelemetry or extended Langfuse:** choose one after a small trace prototype; do not
   maintain two canonical trace pipelines.

### P2 — research, not production dependencies

1. DSPy optimization on generic training cases, with whole capability families held out.
2. PydanticAI/OpenAI Agents SDK architecture spike only if a bounded role can be isolated
   and the spike states which current modules it would delete.
3. Temporal only after a documented cross-process durability requirement.

## Adoption scorecard

Every PoC report must record:

- exact package version, commit, direct license URL, dependency licenses, NOTICE, and
  advisory scan date;
- whether any prompt, trace, metric, or document can leave the configured environment;
- new dependency size and startup/runtime cost;
- old LOC deleted, new adapter LOC, and new public abstractions;
- focused held-out quality, full-battery quality when eligible, calls, prompt/completion
  tokens, wall time, and repair count;
- rollback procedure and whether stored state remains readable without the package.

No result from a single named case is sufficient to adopt or reject a component.
