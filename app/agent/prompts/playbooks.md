# Standard playbooks — predefined flows for recurring requests

When the Planner maps a request to a playbook, that section is **injected into every role's
prompt**. Purpose: remove pointless variance and mistakes on typical requests.
Each section = Trigger / Flow / Caution / **Check**.

**Check** is the list you verify before sending the answer. Whatever can be measured is also
measured by code (`workflow/postcheck.py`) — if you miss it, that fact is appended to the
answer. Being visible beats being hidden, for the user and for us.

## epic_create
Trigger: create a new Epic or initiative.
Flow: ① search similar Epics (duplicate check) ② interview for goal and related WBS/module
(multiple choice) ③ one draft with mode=epic (short epic_name, background/goal/done criteria)
④ overall owner candidates ⑤ approve → create → offer "Tasks next?".
Caution: do not mix child Tasks into the same batch (the Epic must exist first). Epic Name ≤10 chars.
Check: □ actually searched for duplicate Epics □ Epic Name ≤10 chars □ exactly one draft (no children) □ the approval card has items

## task_create
Trigger: start new work, a feature, or an improvement.
Flow: ① search internal history (+ web if the technology is new) ② if it is a duplicate, say so
first ③ interview only for what is missing (multiple choice, Epic candidates + none)
④ draft (background / scope / DoD) ⑤ owner candidates (similar history + workload) ⑥ review → approve.
Caution: if the approach is undecided, start with a single investigation Task. Only the user
knows deadlines and scope — do not invent them.
Check: □ searched similar history and stated the result (say "none" when there is none) □ the body has background, scope (including exclusions), and done criteria □ the done criteria are **decidable** (not "testing complete") □ the approval card has items

## bug_report
Trigger: something is broken, failing, or throwing errors.
Flow: ① search for Bugs with the same symptom ② obtain reproduction path / expected / actual
(ask if missing) ③ Bug draft ④ link suspected cause tickets and documents ⑤ owner candidates ⑥ approve.
Caution: nobody can fix a Bug without a reproduction path — always obtain it.
Check: □ reproduction path, expected behaviour and actual behaviour are **separate** in the body (leave a field blank and ask when it is unknown) □ searched for the same symptom □ did not split it into Sub-Tasks

## subtask_bulk
Trigger: several Sub-Tasks under one specific Task.
Flow: ① confirm the parent key exists ② decide the split axis (by content, or by batch volume)
③ confirm per-item attributes (owner / label / priority — they may differ) ④ bulk draft with
mode=subtask ⑤ approve.
Caution: if the user already named the split ("design to A, implementation to B"), that **is**
the plan — do not ask again.
Check: □ the parent key exists □ child titles say **what the work is** (not "design phase") □ the split the user named is reflected as-is

## find_people
Trigger: find people matching a condition (who has capacity, who has done this before, who is
involved in a ticket).
Flow: ① interpret the condition (module / experience / workload) ② query roster, workload,
similar history ③ 2–3 candidates with evidence (numbers, ticket keys) ④ offer to narrow further.
Caution: never use an id outside the roster. State "no history" explicitly and compensate with workload.
Check: □ every candidate carries evidence (numbers, ticket keys) □ no ids outside the roster □ when only a name was given, said on what grounds that person was chosen

## find_tickets
Trigger: find tickets matching conditions (priority, status, period, unassigned, stale).
Flow: ① condition → tool choice (unassigned = find_unassigned, stale = find_stale, otherwise
run_jql) ② result table (key + title + key attributes) ③ include the executed query when the
user mentions JQL ④ if zero, say "none" and state the criteria.
Caution: never silently replace the criteria the user asked for with different ones.
Check: □ results shown as a table (key + title + attributes) □ zero results stated **with the criteria** □ did not swap the requested criteria

## knowledge
Trigger: what is X / summarize what we know about X.
Flow: ① internal keyword + semantic search ② external (web / GitHub) reinforcement
③ Curator summary (concept / our situation / references / gaps) ④ cite evidence.
Caution: "no internal history" is also an answer. Do not pad it with unrelated tickets.
Check: □ concept, our situation and gaps are distinguishable □ evidence carries ticket keys or document links □ said "none" when there is no internal history (did not pad with unrelated tickets)

## history
Trigger: history, recent status, or how things got here for a specific topic or keyword.
Flow: ① search (lineage map when a key is given) ② open the 2–4 central tickets (comments =
decisions and reasons for stalling) ③ narrate in time order (start → progress → now → last update).
Caution: update order is the backbone of "recent status". Keep titles exactly as written.
Check: □ **current state** and **timeline** are both present □ current state rendered as a table (| item | value | evidence |) □ the timeline's "event" says **what changed**, not a copy of the ticket title □ a reference list exists and matches the [N] markers in the body □ when nothing was found, searched **once more with different wording** (abbreviations, English, spelling variants)

## workload
Trigger: analyze activity and workload of a person, a module, or the people involved.
Flow: ① fix the target roster (module or ticket participants) ② collect activity and workload
for everyone (code does this in parallel) ③ report in three layers: roster → group narrative →
per-person block (ticket, comment, document activity).
Caution: do not stop after one person. Low activity ≠ slacking (that judgement is the user's).
Check: □ looked at the whole roster (did not stop at one person) □ the three layers (roster → group → individual) are visible □ did not call low activity slacking

## assign_fit
Trigger: who should own this ticket, or is A a good fit for it.
Flow: ① understand the ticket (required skills) ② candidate profile, similar history, workload
③ a judgement sentence with evidence (fit / overloaded — why) + one alternative.
Caution: evidence must be written as human sentences (never as tool-call notation). The final
decision belongs to the user.
Check: □ stated which of the four signals (similar history, workload, participation, module) the judgement rests on □ stated unsuitability with its reason too

## asset_lookup
Trigger: a question about **the current fact** for one specific subject — a table's load
interval, schema, load job or owner; a technology's internal adoption status or policy; the
current state of a specific piece of work.
Flow: ① trace mentions of the exact name (find_mentions — **including raw comment text**)
② check field change history (current value = the most recent change) ③ read related document
bodies (read_document) ④ attach the source (ticket key, comment author, document title) to every value.
Caution: **if it does not exist, say it does not exist.** When the name appears nowhere, do not
borrow facts about a similar subject — that is the most common failure in this category. Never
report a pre-change value as the current one.
Check: □ the requested value is in the **first line** □ evidence (ticket, document) is attached to that value □ verified it is the latest value (no later change on record)
