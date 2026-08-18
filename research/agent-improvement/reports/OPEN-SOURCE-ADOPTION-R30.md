# Agent OSS adoption review — r30

Date: 2026-08-18

This is a preliminary engineering classification, not a legal approval.  A package is
eligible for a proof of concept only after its exact version, transitive dependencies,
NOTICE obligations, security advisories, telemetry defaults, and the company's OSS
allow-list have been checked.  Code with an unknown, copyleft, source-available, or
commercial license is architecture-reference material only unless Legal explicitly
approves it.

## Decision policy

1. Search established implementations before adding a new runtime policy or parser.
2. Prefer the framework already in the product over a second overlapping framework.
3. MIT, BSD, ISC, and Apache-2.0 code may enter an isolated PoC, subject to the checks
   above.  For Apache-2.0 distributions, inspect the exact artifact and preserve any NOTICE
   content it actually ships, together with required attribution.
4. LGPL/MPL requires a packaging and modification-boundary review.  GPL, AGPL, SSPL,
   Elastic License, BUSL, non-commercial, and unknown licenses are reference-only by
   default.
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
| JSON Schema specification | N/A (specification, not the Pydantic package license) | Schema vocabulary consumed by local validators/providers | Keep the vocabulary separate from any implementation library's license finding. |

Discovery references below point at upstream project pages. They are not exact-version legal
evidence. Before promotion, archive the license/NOTICE files from the exact installed tag or
commit together with the SBOM and advisory scan date.

References:

- LangGraph license and durable/HITL model: <https://github.com/langchain-ai/langgraph/blob/main/LICENSE>, <https://github.com/langchain-ai/langgraph>
- Langfuse license boundary: <https://github.com/langfuse/langfuse/blob/main/CONTRIBUTING.md>, <https://github.com/langfuse/langfuse-docs/blob/main/content/self-hosting/license-key.mdx>
- MCP Python SDK and security status: <https://github.com/modelcontextprotocol/python-sdk>, <https://github.com/modelcontextprotocol/python-sdk/security>
- OpenAI Python SDK: <https://github.com/openai/openai-python>

## Candidate matrix

| Candidate | Exact license finding | Useful overlap | Data/security caveat | Decision |
|---|---|---|---|---|
| Instructor | MIT | Pydantic response models, validation feedback, bounded retries, failed-attempt usage | Must use only the configured local OpenAI-compatible endpoint; disable unneeded provider integrations | **Isolated PoC.** Replace only the structured transport/repair path of one low-risk role.  Keep semantic authority and fail-closed policy outside the library. |
| DeepEval | Apache-2.0 | Dataset/test runner, conversational/RAG metrics, DAG/custom metrics | Default examples may use hosted judges or Confident AI; no upload/login/sync in the PoC | **Offline evaluator PoC.** Shadow the existing evaluator; it never becomes the sole release gate. |
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

- Instructor license and retry behavior: <https://github.com/567-labs/instructor/blob/main/LICENSE>, <https://python.useinstructor.com/learning/validation/retry_mechanisms/>
- DeepEval license: <https://github.com/confident-ai/deepeval/blob/main/LICENSE.md>
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

### P0 — use what is already installed

1. Emit stable role, schema version, repair stage, defect signature, effect digest, source
   coverage, duration, and tokens through the existing usage callback and Langfuse hook.
2. Evaluate one LangGraph `interrupt`/resume flow against the current custom required-input
   path.  Accept only if it preserves the UI contract and deletes custom branching.
3. Run exact-version dependency/SBOM/advisory checks for the existing MCP, LangGraph,
   Langfuse, OpenAI, and transitive packages.

### P1 — isolated direct-use experiments

1. **Instructor transport adapter:** one role, same schema, same Qwen endpoint, zero external
   telemetry.  Compare valid-first-call rate, repairs, latency, tokens, failure diagnostics,
   and net deleted LOC.  Promotion gate: no authority regression, fewer calls or materially
   less transport code, and clean removal by feature flag.
2. **DeepEval offline adapter:** ingest existing battery JSON without login or upload and run
   only local/custom metrics.  Compare false-pass/false-fail against direct human review.
   It remains a shadow signal until agreement is demonstrated on held-out suites.
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
