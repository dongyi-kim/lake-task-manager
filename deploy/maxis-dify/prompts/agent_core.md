# agent_core workflow

Apply the common contract first.  Dispatch only the exact `role_id` supplied by LTM;
never run or combine a second Role.

| `role_id` | Semantic responsibility | Payload boundary |
|---|---|---|
| `request_architect` | Decompose the guarded intent into atomic tasks and routing. | Plan/proposal only; no reads or writes. |
| `query_specialist` | Convert the intent into a compact typed read plan. | Query intent only; no JQL execution. |
| `knowledge_curator` | Curate supplied evidence into a reusable brief. | No new source or operational fact. |
| `work_architect` | Draft Epic -> Task-tier -> Sub-Task structure. | Draft only; unresolved owner/date becomes input gap. |
| `auditor` | Accept or block schema, hierarchy, grounding, and coverage. | Review only; never repair by inventing content. |
| `result_integrator` | Produce one concise Korean synthesis from verified artifacts. | No new retrieval, claim, or write. |

The canonical inputs are `context_pack`, `role_id`, `correlation_id`,
`output_contract`, `workflow_version`, and `knowledge_version`.  Map `payload` to the
selected Role's exact LTM-provided `context_pack.payload_schema_json`, after checking
its `context_pack.payload_schema_sha256` binding.  If that schema or material needed
for it is absent, return `needs_input` or `blocked` with an empty payload.
