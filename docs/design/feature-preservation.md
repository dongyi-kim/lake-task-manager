# Desktop redesign: behavior preservation

Baseline: `7e8e9efc`. Authoritative design: root `DESIGN.md`. This inventory is a completion checklist, not a claim that implementation or tests have passed.

| Surface | Preserved behavior | Primary regression coverage |
|---|---|---|
| Shell/Home | routes, deep links, keep-alive, startup/auth, updates, search/create entrypoints | frontend/static_assets/test_shell_contracts.py; frontend/shell/test_ui_boot.py |
| Task | types/priority/owner/VoC/parent, 4 groups, both axes, filters/JQL/find, sorting/paging, drag/transition, compact groups, hydration and retries | frontend/mytasks; frontend/static_assets/test_task_ui_contracts.py |
| Ticket | hierarchy/body/history, modal/fullscreen/standalone, fields/comments/attachments/work time, independent panel recovery, correct unread acknowledgement | frontend/ticket_dialog |
| WBS | weighted SP rollup, shared Epic/weights, four levels, time units, timeline, display filters, retained rows and targeted retry | frontend/wbs; domain/test_rollup.py |
| VIT | module/briefing/done hide, descendants/comments, independent detail and failure recovery | frontend/vit |
| Workload | count/time meanings, both periods, group/type/Epic, people/buckets/activity, risk/bus factor/contribution/shares, partial recovery | frontend/static_assets/test_workload_ui_contracts.py; jira/workload |
| Confluence read | saved/recent/tree/search/navigation, resizable panes/outline/revision, auth/read/branch recovery | frontend/shell/test_confluence_* |
| Rich authoring | table/code/image/file/mention/badges/macros, clipboard/drop/resize/maximize, draft identity, immutable submission/receipt and retry | frontend/editor; frontend/shell/test_confluence_authoring* |
| Search/pickers | 3 sources/scope/recent/JQL, exact key, keyboard, user/link suggestions, clipboard entry | frontend/shell/test_clipboard_tickets*; static shell contracts |
| Create/transition | Epic/Task/Sub-task/VoC, metadata/required fields, attachments, dirty guard, parent cascade and duplicate write protection | frontend/editor/test_draft_lifecycle_ui.py; mcp/test_transition_recovery.py |
| Bulk | JSON/comment inputs, validation/selection/preview, per-row success/failure/pending/not-started, resume/receipts/drafts | frontend/shell/test_bulk_* |
| AI | conversation/evidence/edit/preview/approval/cancel/recovery, exact payload approval boundary | frontend/static_assets/test_agent_ui_contracts.py; routes/test_postcheck.py |
| Settings/admin | auth/theme/cache/AI/MCP/workspace/people/WBS/shortcut settings, errors and recovery | frontend/shell/test_workspace_admin_ui.py; test_cache_policy_ui.py; test_local_mcp_ui.py; test_login_recovery_ui.py |
| DevTools/native | API/method/params/results/probes, tray health/SSO/global shortcuts/startup/update/download | shell contracts/boot; launcher tests; explicit desktop smoke |

## Validation rules

1. Preserve both normal and failure paths: API query scope, stale response guards, successful rows, scroll, draft contents and mutation receipts.
2. Keep calculations and invisible behavior checks in addition to screenshots/control counts. Static source assertions do not establish browser behavior.
3. Validate 1280/1440/1720 desktop widths, light/dark, keyboard focus/IME and 200% zoom. Mobile redesign/verification is excluded.
4. Include long Korean titles, missing owner/date, updates combined with deadline urgency, done tasks and 48/24/200 child cases.
5. Use mock data for write round trips. Do not post real Jira/Confluence content or run live LLM tests for styling.
6. Compare request counts for the same flow; styling must not add queries. Inactive tabs must not fetch their bodies.

## Additions, separated from preservation

Recent ticket/document tabs are a new frontend state feature. They require a separate feature commit/PR: separate lists, deduplication, overflow/keyboard controls, metadata restoration, existing draft ownership and lazy active-item reads. They must not weaken any of the above behavior.

## Execution record

Implementation progress and actual validation results are recorded in `implementation.md` alongside this file. An unchecked or skipped validation is not a pass.
