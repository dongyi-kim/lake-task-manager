# Draft persistence verification (2026-11-03)

Base: `7799e39`. Mock Jira only; normal scenarios use 0 ms latency.

## Implemented behavior

- Cancel, close, folding and navigation preserve drafts. Explicit draft deletion is separate.
- Task/Epic/SubTask forms, description edits, new/existing comments and transition forms use a
  shared IndexedDB store with server/environment/account/surface scopes and seven-day retention.
- Save success means transaction commit. Writes/deletes are ordered per key; failures are visible.
- Image tokens do not depend on filenames; same-named files remain distinct after restoration.
- Confirmed creation keys and uncertain mutation receipts survive close/reload and follow-up failures.
- Description/comment edit restoration asks before replacing changed server content.
- Local creation candidates remain available while draft identity and stored state are loading.

## Browser evidence and environment

Tests use real Chromium, Vue, IndexedDB and TipTap against the isolated mock Jira world. No
production writes were performed. Ordinary cases run at 0 ms; only delayed recommendation and
draft close/reload cases use 800 ms. Console/page/API response errors are checked.

`test_draft_lifecycle_ui.py` covers Task/Epic/SubTask cancellation, comment close/navigation and
same-filename images, description server/draft conflicts, transition fields/comments, seven-day
expiry, server/account isolation, ordered commits, transaction abort, synchronous storage-open
failure recovery, creation follow-up recovery with exactly one POST and uncertain transition
receipts. Failed storage deletion preserves the visible document. Pending parent mutation receipts
also prevent deletion through an embedded editor's own draft-delete button.

`test_editor_controls_ui.py` inventories and executes all 26 toolbar controls, plus 15 description
and 14 comment slash commands. It uses plain paragraph paste only to prepare text: formatting,
tables, file picker, mentions, badges, dialogs and slash actions use real controls. DOM Range selects
text; it does not inject rich structure or call editor formatting commands. The resulting document
survives close/reopen, publish, edit, per-group modification, republish and final render. Existing
images are retained and another is added during edit. Computed colors, background, font, alignment,
avatar fit, badge semantics and opaque table borders are checked, not just text presence.

The control suite uses the production HTML/JEditor serialization settings. The test subprocess
adapts **only jira820's upstream renderer** to return stored HTML for HTML fields; ordinary wiki
fixtures retain jira820's renderer. Jira endpoints, storage, attachments and all LTM sanitizing,
serialization and caching remain real. This is necessary because jira820 models native wiki Jira,
which cannot preserve background/alignment and renders color macros differently. It is not a
production server test. Existing wiki-mode rich-content regressions also pass independently.

### Runtime control inventory

- Toolbar: bold, italic, strike, inline code, foreground/background palettes, styles (paragraph,
  headings 1–3, quote, code, clear), bullet/ordered/task list, quote, code/language, info/note/tip/warning,
  left/center/right alignment, both fonts, external link dialog, image, table grid, markdown table,
  AI dialog, maximize/restore.
- Contextual toolbar: image 25/50/75/100/original size, column/row before/after/remove, header toggle,
  table deletion, code language selection. Image selection is repeated after each resize.
- Slash: `code`, `table`, `markdown_table`, `checkbox`, `quote`, `info`, `note`, `tip`, `success`,
  `warning`, `error`, `jira`, `confluence`, `file`, plus description-only `divider`.
- AI opens and closes without invoking a provider. Native document-link title/URL guidance is
  available; the shared custom hover popup is implemented only for ticket/person references.

### Two-surface matrix

P = asserted pass. N/A means a feature is absent on that surface. Timing-independent content
roundtrips intentionally do not add delay. Delayed mention search remains the baseline failure
described below; it must not be presented as a pass.

| Surface | Group | Compose | First render | Edit recovery | Second render | 800 ms |
|---|---|---|---|---|---|---|
| Description | Plain/Korean text and breaks | P | P | P | P | Not timing-related |
| Description | Inline marks/colors/background/font | P | P | P | P | Not timing-related |
| Description | Heading/style/alignment controls | P | P | P | P | Not timing-related |
| Description | Bullet/ordered/task lists | P | P | P | P | Not timing-related |
| Description | Quote/code/language/divider | P | P | P | P | Not timing-related |
| Description | Six callout types | P | P | P | P | Not timing-related |
| Description | Grid/markdown tables and borders | P | P | P | P | Not timing-related |
| Description | External link and edited href/label | P | P | P | P | Not timing-related |
| Description | Jira badge and hover | P | P | P | P | Not timing-related |
| Description | Document badge/href | P | P | P | P | Not timing-related |
| Description | Mentions/avatar/fallback/geometry | P | P | P | P | Search baseline failure |
| Description | Picker/clipboard/drag images | P | P | P | P | Not timing-related |
| Description | Maximize/restore/AI dialog | P | N/A | P | N/A | Not timing-related |
| Description | Every available slash command | P | P | P | P | Not timing-related |
| Comment | Plain/Korean text and breaks | P | P | P | P | Not timing-related |
| Comment | Inline marks/colors/background/font | P | P | P | P | Not timing-related |
| Comment | Heading/style/alignment controls | P | P | P | P | Not timing-related |
| Comment | Bullet/ordered/task lists | P | P | P | P | Not timing-related |
| Comment | Quote/code/language | P | P | P | P | Not timing-related |
| Comment | Six callout types | P | P | P | P | Not timing-related |
| Comment | Grid/markdown tables and borders | P | P | P | P | Not timing-related |
| Comment | External link and edited href/label | P | P | P | P | Not timing-related |
| Comment | Jira badge and hover | P | P | P | P | Not timing-related |
| Comment | Document badge/href | P | P | P | P | Not timing-related |
| Comment | Mentions/avatar/fallback/geometry | P | P | P | P | Search baseline failure |
| Comment | Picker/clipboard/drag images | P | P | P | P | Draft restore covered |
| Comment | Fold/preview/resize/cancel/maximize | P | N/A | P | N/A | Draft restore covered |
| Comment | Every available slash command | P | P | P | P | Not timing-related |

The rich fixture sweep opens DL-9001, DL-9002, DL-9004, DL-9005, DL-9006, DL-9007, DL-9017 and
DL-9036. Separate HTML-mode backend gates confirm explicit Jira-visible borders on table/th/td
and idempotent serialization. The 800 ms local-picker test proves None/recent Epic/type choices
appear before server replies.

## Baseline failures reproduced unchanged

These two existing tests fail both on the candidate and untouched `7799e39` worktree:

- `test_comment_mention_search_enriches_context_name_under_delay[800]`: expected mention candidate
  does not appear within 15 seconds.
- `test_editor_table_does_not_trap_ticket_dialog_vertical_wheel`: expected outer body scroll does
  not increase after the wheel event.

Neither assertion was removed or relaxed. These are separate from draft persistence and prevent
claiming the entire existing editor suite is green.

## Commands and results

```text
python -B -m pytest -q tests/frontend/editor/test_draft_lifecycle_ui.py \
  tests/frontend/static_assets/test_task_ui_contracts.py \
  tests/frontend/static_assets/test_ticket_editor_contracts.py \
  tests/frontend/static_assets/test_write_recovery_contracts.py \
  --basetemp=.cache/test-tmp/drafts-final-3 -o cache_dir=.cache/pytest

python -B -m pytest -q tests/frontend/editor/test_editor_roundtrip_ui.py \
  -k 'not mention_search_enriches and not editor_table_does' \
  --basetemp=.cache/test-tmp/drafts-rich-final -o cache_dir=.cache/pytest

python -B -m pytest -q tests/frontend/editor/test_editor_controls_ui.py \
  --basetemp=.cache/test-tmp/draft-controls-final-2 -o cache_dir=.cache/pytest
```

The existing rich suite subset passed **5 tests** (69.87 seconds), including both-surface rich
clipboard/file/drop roundtrips, fixture sweep, section-table scrolling, composer resizing and delayed
local candidates. Its two excluded failures were run separately on both branches and are documented
above, not silently skipped. Storage fault/recovery and production table serialization targeted
checks passed independently. Final lifecycle/contracts: **43 passed** (98.26 seconds; 11 browser
scenarios and 32 contracts). Final control-driven HTML/JEditor suite: **2 passed** (47.66 seconds).
Both final commands completed without browser page/API errors.

## Combined five-PR verification

The combined validation worktree exercised 11 draft lifecycle scenarios, two control-driven editor
roundtrips, three cache settings/memo scenarios and four MCP settings scenarios (20 total).
The first run passed 19 and exposed a browser harness issue in the second comment edit: after a
toolbar click, native Home ran before editor focus was restored and replacement typing removed
`UI-PLAIN`. The persisted comment matched the submitted HTML; publication/cache invalidation was
not the cause. The test now explicitly focuses the editor and asserts the sentinel before publishing.

After that fix, both integrated description/comment roundtrips passed in **50.27 seconds** on
`5f8d62b82174c2222e13818f22a953a130001b4d`; the prior 19 other passes remain valid (the description
roundtrip overlaps the rerun). Every one of the 20 scenarios therefore passed across those runs.
The editor fixture also isolates `LAKE_CACHE_DIR` per run so preferences, authentication state and
MCP credentials cannot leak between test servers. The shared test Chromium installation was retained;
only this task's temporary run directories/logs and the preferences file it created were removed.

Run-specific temporary uploads, logs and databases are removed after recording this report. The
committed tests and this report remain as reproducible evidence.
