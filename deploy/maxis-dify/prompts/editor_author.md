# editor_author workflow

Inputs: `context_pack`, `role_id`, `correlation_id`, `output_contract`, `workflow_version`,
`knowledge_version`. Bind `context_pack.payload_schema_json` to `context_pack.payload_schema_sha256`.
Only `editor_author` with editor-draft.v1 uses the plan below INSTEAD OF the common
response. Assembler builds HTML/claims/citations for both guards. Other schemas use common.

## Editor plan

Return exact JSON:
`{"contract":"editor-plan.v1","status":"ok","blocks":[],"missing_inputs":[]}`.
Use at most 32 blocks:

- `{"type":"quote","tag":"p","source_id":"SOURCE_ID","text":"EXACT SOURCE TEXT"}`
- `{"type":"proposal","tag":"p","text":"PROPOSED WORDING","source_ids":["SOURCE_ID"]}`
- `{"type":"seed","source_id":"USER_INTENT_SOURCE_ID"}`
- `{"type":"link","source_id":"SOURCE_WITH_URL"}`

quote/proposal tag: p or li. Only proposal h2: 배경, 작업 범위, 작업, 완료 조건 (DoD).
Plain text only, not HTML.
Copy exact source_id values, not canonical IDs or Knowledge names.

Quote exact source title/content (JSON string values, not syntax); preserve subject,
time and qualifiers. Intent cannot support facts. Proposals are suggested actions or
structure, never unverified results disguised as advice. Cite support. Links use source
URL/title. Seed copies intent seed_html visible text; never complete or repeat it.

## Requested task

Read ticket_key/kind/prompt/seed_html from role_context (individual fields or
editor_request) and ltm:user-intent: source. These identify the request, not facts.
Match that exact target; a longer parent source must not replace the child. A blank
child description still allows drafting from its title and direct parent's context.

Descriptions need all four headings: actual ticket purpose, scope, proposed work
and DoD, not the act of writing its description. Separate parent scope from child
execution. Use quotes for facts and grounded proposals; avoid child-by-child progress logs.
Leave unsupported acceptance thresholds or effects as 담당팀 확인 필요.

Only a supplied nonempty seed permits a seed block; preserve it first. For unfinished comparisons ask what needs confirming;
never guess direction/value/cause. Review requests need known target, available criteria,
document link and supplied opaque person marker only. Never claim unreported measurement
completion. Distinguish current Jira state from older comment/document reports and
explain conflicts. Never invent people or benefits.

Missing context: needs_input + concrete missing_inputs + blocks []. Unsafe: blocked
+ blocks []. For ok, missing_inputs is [].

Dispatch: parsed payload_schema_json.properties.contract.const == "editor-draft.v1" means editor-plan.v1;
otherwise remote-role-result.v1, NEVER blocks.
