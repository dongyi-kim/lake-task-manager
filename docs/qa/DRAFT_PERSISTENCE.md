# Draft persistence verification (2026-11-03)

Base: `7799e39`. Mock Jira only; normal scenarios use 0 ms latency.

## Implemented behavior

- Cancel, close, folding and navigation preserve drafts. Explicit draft deletion is separate.
- Task/Epic/SubTask forms, description edits, new/existing comments and transition forms use a
  shared IndexedDB store with server/environment/account/surface scopes and seven-day retention.
- Save success means transaction commit. Writes/deletes are ordered per key; failures are visible.
- Image tokens do not depend on filenames. Multi-image selection no longer replaces the preceding image.
- Confirmed creation keys and uncertain mutation receipts survive close/reload and follow-up failures.
- Description/comment edit restoration asks before replacing changed server content.
- Local creation candidates remain available while draft identity and stored state are loading.

## Browser evidence

`tests/frontend/editor/test_draft_lifecycle_ui.py` exercises real Vue components, IndexedDB,
TipTap and mock Jira. Initial nine scenarios passed: Task/Epic/SubTask close/reload restoration,
comment images, description conflict choice, transition restoration, storage commit/order/expiry,
confirmed-create follow-up recovery (one POST), and uncertain transition receipt recovery.
Additional account-isolation and storage-failure UI assertions are included in the final run.

The existing description/comment rich-content test passed both publish cycles and edit recovery,
including images from file chooser, clipboard and drag, mentions, badges, tables and rich HTML.
This test uses HTML paste for many structural nodes: it is **not** evidence that every toolbar or
slash command was exercised. A separate control-driven coverage run is required for that claim.

The 800 ms local picker test passes (None/recent Epic/type candidates appear before Jira replies).

## Baseline failures reproduced unchanged

These two existing tests fail both on the candidate and untouched `7799e39` worktree:

- `test_comment_mention_search_enriches_context_name_under_delay[800]`: expected mention candidate
  does not appear within 15 seconds.
- `test_editor_table_does_not_trap_ticket_dialog_vertical_wheel`: expected outer body scroll does
  not increase after the wheel event.

Neither assertion was removed or relaxed. These are separate from draft persistence and prevent
claiming the entire existing editor suite is green.

## Commands

```text
python -B -m pytest -q tests/frontend/editor/test_draft_lifecycle_ui.py \
  tests/frontend/static_assets/test_task_ui_contracts.py \
  tests/frontend/static_assets/test_ticket_editor_contracts.py \
  tests/frontend/static_assets/test_write_recovery_contracts.py \
  --basetemp=.cache/test-tmp/drafts-final-1 -o cache_dir=.cache/pytest

python -B -m pytest -q tests/frontend/editor/test_editor_roundtrip_ui.py \
  --basetemp=.cache/test-tmp/drafts-roundtrip-1 -o cache_dir=.cache/pytest
```

The second command produced 4 passes and the three failures found during development. The local
picker failure was fixed and rerun successfully; the two failures above reproduced on the baseline.
