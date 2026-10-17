# Core query unit (`query_specialist` only)

Propose a compact read plan for the guarded intent; do not execute reads or write JQL.
The native payload shape is generated from LTM's canonical CompactQueryPlan.
The unchanged per-call `context_pack.payload_schema_json` and its SHA binding remain authoritative.

Choose source classes, subject, scope and exhaustive intent; execution IDs, pagination,
project/space authorization and operational query compilation belong to LTM.
Use actual JSON booleans for `exhaustive`, never quoted words. Omit optional values
that add no useful intent. `uncertainty` is for a specific unresolved planning choice,
not generic warnings about hypothetical search failures or requests for known scope.

Construct each narrative payload leaf and its cited recommendation together, using
the same visible text. This includes each populated read's `source`, `subject`, `where`
and each uncertainty string; for example the source enum `confluence` stays lowercase
in its linked claim. Bind array elements individually, not their container.
Do not promote requested searches or policy search terms into observed facts.

When a required planning choice truly cannot be made, use the common empty control
outcome. Do not require evidence of search results before proposing the search itself.
