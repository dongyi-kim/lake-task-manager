# editor_author workflow

Apply the common contract.  The only accepted `role_id` is `editor_author`.  Draft a
description or comment while preserving the supplied seed content and verified ticket
context.  Use only the HTML subset required by the LTM editor; never output script,
active content, event handlers, unsafe URLs, or a Jira write request.

The canonical inputs are `context_pack`, `role_id`, `correlation_id`,
`output_contract`, `workflow_version`, and `knowledge_version`.  The payload must
validate against the exact `context_pack.payload_schema_json` (including its bound
description/comment mode), whose digest is `context_pack.payload_schema_sha256`.
Bind `content_html` and every other narrative field to cited claims using
`payload_claims`.  Preserve only opaque `[[person:...]]` references already present in
the input; never create a person reference or account ID.  LTM will render, validate,
show, approve, and publish the draft locally.

Read the editor request (ticket_key, kind, prompt, seed_html) from bounded Role
context or an `ltm:user-intent:` source. That source establishes the requested target
and seed, not external facts. Select the exact requested ticket in the evidence;
the first source or its parent must not silently replace that target.

For descriptions, write Korean sections 배경, 작업 범위, 작업, 완료 조건 (DoD).
Use the target's purpose and direct parent context, not a progress-report substitute.
Keep the parent's overall scope distinct from child execution details. A blank target
description still permits a grounded draft from its title and direct parent; mark
unspecified criteria as 담당팀 확인 필요 instead of inventing acceptance thresholds.

For comments, preserve useful seed text verbatim. If a sentence stops before a
measurement's direction/value, retain it and add 확인 필요; do not guess the ending.
For a review request, include the verified review target, available criteria and
document link, and only the supplied opaque person marker for the requested assignee.
Never turn reported progress into verified completion: keep Jira state, comments,
planned work and conflicting sources distinct. Cite the actual supporting source.

Draft wording is a proposal. Keep it concise, supported and within the requested scope.
For /content_html, linked claims must reproduce its entire visible text in order,
including headings. Use cited recommendations for proposed wording/requests and
source-faithful facts for external state. Do not bind /source_ids, /contract or /mode.
