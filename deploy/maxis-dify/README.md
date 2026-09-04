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
   output.  The Answer node must return only that JSON object.
5. Before publishing, generate a different random release marker for each environment
   and add a dataset document containing exactly
   `LTM_RELEASE_SENTINEL_V1=<random-value>`.  Bind the Knowledge node output to the LLM;
   it must copy that exact line into `knowledge_proof`.  Store only the lowercase
   SHA-256 of the whole line in the LTM profile (or
   `LAKE_MAXIS_KNOWLEDGE_SENTINEL_SHA256`).  Do not put the marker or its digest in a
   Start input or prompt.
6. Run the three cases in `smoke-cases.yml`, then run
   `python deploy/maxis-dify/validate_bundle.py`.  LTM activation performs an
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

## Ownership boundary

- Dify/MAXIS: semantic decomposition, synthesis, drafting, and review proposals.
- LTM: scoped Jira/Confluence/Bitbucket reads, freshness and citation validation,
  deterministic calculations, schema checks, user approval, writes, and audit logs.
- Chatflows are stateless.  LTM sends a bounded pseudonymous evidence pack for every
  call; no prior Dify conversation is reused.
