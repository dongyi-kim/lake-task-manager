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
