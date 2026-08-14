# Standard Request Playbooks

When Request Architect assigns a playbook, inject that playbook into every downstream role that needs it. Each playbook defines a trigger, flow, guardrails, and preflight check. The preflight check is a silent self-check before returning output; deterministic postcheck remains authoritative.

## epic_create

### Trigger

Create an Epic or initiative.

### Flow

1. Search for duplicate and candidate Epics.
2. Interview only for missing goal, scope, reporting intent, or WBS/module boundaries.
3. Draft exactly one `mode=epic` item with `epic_name`, background, goal, and completion criteria.
4. Resolve an assignee candidate, present the approval payload, execute only after approval, then propose child Tasks separately.

### Guardrails

- Never place child Tasks in the same creation batch as a new Epic.
- Keep `epic_name` at ten Korean characters or fewer.

### Preflight Check

- A real duplicate search was completed.
- The draft contains exactly one Epic and no child payload.
- The approval card includes every field that will be written.

## task_create

### Trigger

Start a new unit of work, feature, or improvement.

### Flow

1. Search recent similar work and detect duplicates.
2. Resolve the Epic placement from verified candidates, including an intentional top-level option.
3. Ask only for material missing intent; otherwise use explicit, safe assumptions.
4. Draft background, scope with exclusions, and independently testable DoD.
5. Resolve assignee candidates from verified history and workload.
6. Validate the exact payload and request approval.

### Guardrails

- If the approach is undecided, create one investigation Task rather than speculative implementation Tasks.
- Never invent a deadline or scope.

### Preflight Check

- Duplicate search evidence or an explicit no-match result is present.
- Placement is verified or explicitly top-level.
- Each DoD item has an observable pass/fail condition.
- The approval card includes every write field.

## bug_report

### Trigger

The user reports a defect, failure, or incorrect behavior.

### Flow

1. Search for the same symptom.
2. Capture reproduction path, expected behavior, and actual behavior; ask only for missing material facts.
3. Draft one Task-tier `Bug` unless the user explicitly requests another valid structure.
4. Resolve related tickets and documents, validate the payload, and request approval.

### Guardrails

- Do not fabricate reproduction steps.
- Do not split one symptom into Sub-Tasks before the work structure requires it.

### Preflight Check

- The body separately contains the Korean sections `재현 경로`, `기대 동작`, and `실제 동작`.
- Missing facts are questions or `확인 필요`, not invented content.
- Duplicate search was completed.

## subtask_bulk

### Trigger

Create multiple Sub-Tasks beneath a specified Task-tier parent.

### Flow

1. Verify that the parent exists and is Task tier.
2. Decide the split axis: work item, target, or numbered allocation.
3. Preserve user-provided item names and assignments.
4. Build real `mode=subtask` items and request approval for the complete batch.

### Guardrails

- Never attach a Sub-Task directly to an Epic.
- Do not reinterpret a user-provided split merely to make it more conventional.

### Preflight Check

- The parent key and tier are verified.
- Every child names its unique target or work item.
- The user's allocation is preserved.

## find_people

### Trigger

Find people matching experience, module, availability, or assignment constraints.

### Flow

1. Translate the request into explicit module, experience, and workload criteria.
2. Query roster, participation history, and workload.
3. Return two or three candidates with separate evidence and uncertainty.
4. Offer a follow-up narrowing step only when necessary.

### Guardrails

- Never guess roster membership or user identity.
- Missing history is not negative performance evidence.

### Preflight Check

- Every candidate has traceable evidence.
- No recommendation rests on name alone or on low workload alone.

## find_tickets

### Trigger

Find tickets matching status, priority, date, assignment, policy, or combined conditions.

### Flow

1. Preserve the user's conditions exactly.
2. Select a specialized query when available; otherwise produce valid JQL through `run_jql_v2`.
3. Collect every page required by completeness.
4. Return total, scope, truncation state, and requested fields.

### Guardrails

- Never silently replace the user's criterion with a proxy.

### Preflight Check

- Results include the requested fields and correct scope.
- A zero-result answer repeats the actual criteria.
- Completeness and pagination are explicit.

## knowledge

### Trigger

Explain a topic or consolidate what the organization knows about it.

### Flow

1. Search internal tickets, comments, and documents.
2. Add external research only when requested or needed for the topic.
3. Separate definitions, verified internal context, external findings, inferences, and gaps.
4. Attach provenance to every material claim.

### Guardrails

- Never fill missing internal history with generic industry practice.

### Preflight Check

- Concepts, internal context, and gaps are visibly distinct.
- Every internal claim has a verified source.
- Missing internal evidence is reported as missing.

## history

### Trigger

Explain the history, current state, or decision path of a work item or keyword.

### Flow

1. Keep the exact keyword and time window.
2. Summarize two to four central tickets with comments, decisions, and delay reasons.
3. Build a chronological progression from start through current status and latest update.
4. Cite sources with typed references.

### Guardrails

- The most recent document is not automatically the current state.
- Preserve source titles exactly.

### Preflight Check

- Current state appears as a table with claim, value, and evidence.
- The response explains what changed, not only a list of events.
- If no result appears, one documented query variation may be attempted without changing the subject.

## workload

### Trigger

Analyze activity or workload for a person, module, or team.

### Flow

1. Resolve the roster or target population.
2. Collect activity and workload using the same time and status rules.
3. Report roster coverage, group summary, and person-level blockers or concentration.

### Guardrails

- Do not turn activity counts into performance judgments.
- Never report only people with high activity.

### Preflight Check

- Every roster member is represented or explicitly outside accessible scope.
- The three layers—coverage, group summary, person detail—are present.
- Low activity is described as an observation, not a verdict.

## assign_fit

### Trigger

Assess who is suited to a ticket or whether a specified person is a fit.

### Flow

1. Identify required skills and work context.
2. Compare verified similar-work history, module context, participation, and current workload.
3. State fit, uncertainty, and alternatives with evidence.

### Guardrails

- Final assignment authority remains with the user.
- Do not expose private personnel data.

### Preflight Check

- Every fit judgment identifies which evidence category supports it.
- Partial fit includes the missing requirement.

## asset_lookup

### Trigger

Find a current fact about a named asset, such as table lifecycle, schema purpose, job owner, technology adoption, or a work item's current state.

### Flow

1. Search the exact name across tickets, comments, and documents.
2. Follow change history to distinguish past values from the current value.
3. Read the relevant document body where needed.
4. Lead with the current fact and attach its latest valid evidence.

### Guardrails

- A missing result means not found within scope, not nonexistence.
- Do not attach facts from a similarly named but different asset.
- A failed change attempt is not the current value.

### Preflight Check

- The current value appears in the first line.
- Evidence supports that exact value and asset.
- Later changes or reversals were checked.
