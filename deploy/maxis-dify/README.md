# LTM MAXIS/Dify workflow bundle

This directory is the version-controlled **build specification** for the three Dify
Chatflows used by Lake Task Manager.  It is intentionally not advertised as an
importable Dify DSL: model-provider IDs, tenant dataset bindings, and published app
IDs belong to each MAXIS environment and must be selected in the Dify builder.

## Build in Dify

For each entry in `bundle.yml`:

1. Create a Chatflow with `Start -> Knowledge Retrieval -> LLM -> Answer` nodes.
   Use two retrieval nodes bound only to the approved LTM dataset: one release-sentinel
   node with fixed query `LTM_RELEASE_SENTINEL_V1`, and one Role-knowledge node whose
   query is `sys.query` (the bounded query sent by LTM). Feed both results to the LLM.
2. Configure **exactly** the six required Start variables in `bundle.yml`, all as
   required text/paragraph inputs.  `query` and `user` are Dify Chat API fields, not
   Start variables.  Match both `start_variable_control` and
   `start_variable_min_capacity`: `context_pack` is a Paragraph of at least 20,000
   characters and the five control values are Text Input fields of at least 128
   characters.  A missing/unbounded `max_length` is also accepted by the live probe.
3. Assemble the LLM instruction as `prompts/common.md` followed by the workflow's
   prompt file.  Bind the six values from the Start node; do not expose tools, HTTP
   nodes, Jira credentials, approval tokens, or conversation memory.  The per-call
   payload JSON Schema travels inside `context_pack` as canonical
   `payload_schema_json` plus `payload_schema_sha256`, so it does not add a seventh
   Start variable.
4. Enable JSON output and use `contracts/remote-role-result.v1.schema.json` as the
   response contract where the selected MAXIS model supports schema-constrained
   output.  The Answer node must return only that JSON object. Apply
   `execution_policy` as builder settings: critical Knowledge/LLM/Answer failures
   terminate the run; disable automatic node retries, default-value recovery,
   failure-branch answers, and model fallback. A failure must return to LTM as a
   failure, not as a plausible-looking answer.
5. Before publishing, generate a different random release marker for each environment
   and add a dataset document containing exactly
   `LTM_RELEASE_SENTINEL_V1=<random-value>`.  Bind the Knowledge node output to the LLM;
   it must copy that exact line into `knowledge_proof`.  Store only the lowercase
   SHA-256 of the whole line in the LTM profile (or
   `LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256`).  Do not put the marker or its digest in a
   Start input or prompt.
6. Run the three cases in `smoke-cases.yml`, then run
   `python deploy/maxis-dify/validate_bundle.py`. This command validates only checked-in
   contracts and golden-eval coverage; it does **not** claim that a remote MAXIS/Dify
   retrieval test passed. Execute every case in `knowledge-evals.yml` against the
   candidate remote dataset and retain the environment/release-bound result as an
   external release artifact before authoritative prod use. Register a
   `ltm-maxis-knowledge-eval-attestation.v1` metadata record containing the evidence
   ID, `outcome: passed`, `case_count: 25`, pass time, artifact/suite SHA-256 values,
   and the exact dataset, manifest, workflow-version, and profile-material bindings.
   LTM activation performs an
   additional live `/parameters` plus synthetic `/chat-messages` probe and refuses
   activation unless the retrieved sentinel hashes to the locally pinned digest.
7. Publish the Chatflow and record its immutable published workflow UUID and
   Knowledge version in the environment-specific LTM profile.  Store its app API key
   only through the LTM credential store/environment variable.

The tracked Knowledge manifest digest received inside `context_pack` must be echoed
as top-level `knowledge_manifest_sha256`.  LTM rejects a missing or different digest;
copying an arbitrary digest into a workflow does not prove dataset contents.  The
deployment-specific Knowledge sentinel therefore verifies that the published app is
actually wired to the intended dataset.  It detects a missing or wrong binding; it is
an operational canary, not protection against a deliberately malicious workflow that
hard-codes the marker.
Likewise, every response payload is checked locally against the exact schema/digest
pair carried by that request; a generic Role-shaped object is not accepted.

## Governed Knowledge release

`config/maxis-knowledge.yml` is the source inventory. Every row has a stable document
ID and path, owner, classification, audience, Role allow-list, revision, and explicit
`reviewed_for_release`, `reviewed_for_version`, `reviewed_at`, and non-identifying
`reviewer_digest` evidence. The builder accepts a profile `knowledge_version` only when
every document was reviewed for that exact version and the UTC checkpoint remains
inside `review_cycle_days`; it never manufactures a reviewed version from runtime input.
A content, revision, review checkpoint, policy, Role registry, dataset UUID, or version
change therefore creates a different digest. The release reviewer must re-check every
document whenever a new release is cut; the global `review_cycle_days` is the maximum
governance interval, not permission to skip release review. Governance also pins the
raw SHA-256 of `knowledge-evals.yml`, and the bundle validator rejects suite drift.

The `markdown-headings-then-length.v1` builder also commits a deterministic segment
ledger into the digest. Each ledger row contains only document ID, heading path,
one-based ordinal, normalized-text SHA-256, and a half-open normalized character range.
Segment body text is never exposed through manifest metadata.

Knowledge releases are immutable and promoted blue-green: build and evaluate a new
dataset, switch the candidate workflow binding after the gate passes, and retain the
previous dataset for rollback. Do not edit the active dataset in place.

The current single-dataset runtime has no audience-entitlement or metadata-filter
enforcement. Consequently the validator permits only `classification: internal` and
`audience: all_ltm_users`; restricted documents must not be added until a separate
dataset or enforced retrieval filter exists. `allowed_roles` records the intended
retrieval policy and drives the ACL-negative golden cases, but is **not** itself a live
authorization boundary today. A prod release cannot claim this gate complete until the
remote ACL-negative cases prove that the deployed retrieval layer enforces it.

`knowledge-evals.yml` covers every document with positive, prompt-injection, and
ACL-negative cases plus a no-hit abstention case. The repository intentionally records
these as `specification_only_not_executed`; only the separately captured remote result
artifact can satisfy the prod release gate.

The attestation can be stored in the prod profile as
`knowledge_eval_attestation` (API: `knowledgeEvalAttestation`) or supplied as JSON in
`LAKE_MAXIS_KNOWLEDGE_EVAL_ATTESTATION`. Its workflow map must cover all three published
apps. A prod profile may still be activated for `shadow` without it. Transitioning a
route to authoritative `maxis`, or activating prod while a `maxis` route already
exists, requires a current exact binding. Any profile, dataset, manifest, suite,
workflow-version, or attestation change invalidates the active fingerprint and blocks
the old authoritative runtime.

This is deliberately an **attestation**, not artifact verification: LTM validates the
metadata shape and current binding but does not fetch, parse, signature-check, or replay
the external result artifact. The artifact and its independent integrity/signature
evidence remain release-governance responsibilities; public status reports
`metadata_binding_only_external_artifact_not_read` so it cannot be mistaken for a live
remote test result.

## Ownership boundary

- Dify/MAXIS: semantic decomposition, synthesis, drafting, and review proposals.
- LTM: scoped Jira/Confluence/Bitbucket reads, freshness and citation validation,
  deterministic calculations, schema checks, user approval, writes, and audit logs.
- Chatflows are stateless.  LTM sends a bounded pseudonymous evidence pack for every
  call; no prior Dify conversation is reused. The client explicitly sends an empty
  `conversation_id` and `auto_generate_name: false` on every request.
