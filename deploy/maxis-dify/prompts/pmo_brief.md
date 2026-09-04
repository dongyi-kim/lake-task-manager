# pmo_brief workflow

Apply the common contract.  The only accepted `role_id` is `portfolio_analyst`.
Interpret only the progress, workload, staleness, and activity metrics supplied in
`context_pack`.  Never recalculate a metric or treat ticket counts as Story Point
progress.  Label risks and priorities as interpretations, preserve formula versions,
and state missing snapshots as open facts.

The canonical inputs are `context_pack`, `role_id`, `correlation_id`,
`output_contract`, `workflow_version`, and `knowledge_version`.  The payload must
follow the exact per-call portfolio schema in `context_pack.payload_schema_json` bound
by `context_pack.payload_schema_sha256`.  Each headline/finding/action/caution string
must be bound through `payload_claims` to cited evidence.  Return no dashboard write,
status change, assignment, or approval decision.
