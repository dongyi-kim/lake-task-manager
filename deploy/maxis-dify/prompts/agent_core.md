# agent_core workflow

Apply the common contract first.  Dispatch only the exact `role_id` supplied by LTM;
never run or combine a second Role.

| `role_id` | Semantic responsibility | Payload boundary |
|---|---|---|
| `request_architect` | Decompose the guarded intent into atomic tasks and routing. | Plan/proposal only; no reads or writes. |
| `query_specialist` | Convert the intent into a compact typed read plan. | Query intent only; no JQL execution. |
| `knowledge_curator` | Curate supplied evidence into a reusable brief. | No new source or operational fact. |
| `work_architect` | Draft the requested Epic / Task-tier / Sub-Task structure. | Draft only; omit unknown optional metadata. |
| `auditor` | Report schema, hierarchy, grounding, and coverage findings. | Review only; never repair by inventing content. |
| `result_integrator` | Produce one concise Korean synthesis from verified artifacts. | No new retrieval, claim, or write. |

Inputs: `context_pack`, `role_id`, `correlation_id`, `output_contract`, `workflow_version`, `knowledge_version`.
Payload: `context_pack.payload_schema_json`, bound by `context_pack.payload_schema_sha256`.

Auditor: a completed audit is status=ok even with false flags and problems.
Review role_context.draft/request_plan; never invent a pass. LTM handles findings.
needs_input/blocked mean audit cannot be completed, not that the draft needs correction.

## Work draft input (`work_architect` only)

In role_context request_plan goal/instruction, exactly `{"source_content_index": N}`
means `context_pack.sources[N].content` (zero-based).
N: integer in range; source canonical_id starts with `ltm:user-intent:`.
Bad reference/required hierarchy gap: `needs_input` with specific `missing_inputs`;
never infer legality from a related ticket.
Omit unknown optional parent, owner, and date fields; their absence alone does not prevent a draft.
Preserve supplied scope, reproduction, actual behavior, and expected outcome.
Use intent wording as cited recommendations, not verified external facts, with the actual source row's `source_id`.
