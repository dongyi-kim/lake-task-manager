# Desktop design implementation record

Base: `7e8e9efc`; branch: `design/207-system-refresh`.

## Decisions

- Execute the approved desktop-only plan in the existing checkout. No worktree or source clone; preserve the deploy repository's unrelated changes.
- Root DESIGN.md is a standalone authority. The previous external prototype is reference material, not product implementation.
- First deliver existing-feature redesign; implement recent-item tabs as the separate follow-on feature branch/PR described in the plan.
- User workspace rules override the older app instructions on worktrees and repository-local pytest scratch: use OS temporary paths.

## Progress

- [x] Confirm clean application baseline and create design branch.
- [x] Write DESIGN.md and feature-preservation inventory.
- [x] Restore mock dependency and executable test environment.
- [x] Apply shared tokens and components.
- [x] Apply Task/WBS/VIT/Workload views.
- [x] Apply ticket/editor/Confluence/search/Bulk/management surfaces.
- [x] Verify desktop runtime and relevant regressions.
- [x] Review, commit and open design PR: [#211](https://github.com/dongyi-kim/lake-task-manager/pull/211), commit `424c92e5`.
- [x] Implement and validate recent-item tabs on follow-on branch.

## Validation

- 571 related static/Node/domain/Jira workload checks passed in 98.99s. Scope: `tests/frontend/static_assets`, compact/filter/find/fault Task models, WBS/VIT recovery, ticket model/recovery, `tests/jira/workload`, `tests/domain/test_rollup.py`. Browser checks are tracked separately.
- Actual mock application ran through `run.py --reload` on configured port 4457 with the Windows named launcher. Local jira820 was restored to the exact revision already pinned in requirements; dependency files were unchanged.
- Captured existing and redesigned CSS on actual product DOM, with baseline CSS read from Git `7e8e9efc` rather than a second source checkout. Confluence uses the existing upstream-shaped document test fixture. Evidence: `C:/Users/PC/research/ltm-ui-design-20261111/implementation/`.
- Task/WBS/Workload/VIT/Confluence matrix: 1280, 1440, 1720 px; light/dark; 30 rendered combinations with no document-level horizontal overflow or JavaScript page errors. Task two-line cards measured 56px, inline children 30px; reduced-motion token resolves to 0ms. This does not substitute for action/200% checks.
- Independent review found a dark VoC badge contrast defect; corrected using the existing type hue as a tint and primary text. Actual dark capture also exposed the no-Epic marker inheriting the filled-badge foreground; restricted that rule to filled badges.
- Tests use owned OS temporary runtime/cache/case folders, configured local browser port 4457, and the available Node/Chromium executables. Full suite is delegated to PR CI under `docs/TESTING.md`; no live Jira/Confluence writes or LLM calls are used.
- Existing browser regression selection: 63 current cases passed across the initial run and targeted reruns, with the six previously skipped management tests now executed through the shared browser launcher. Includes Task grouping/find/update acknowledgement, ticket updates/work time, rich description/comment two-publish round trips, document read/retry/authoring, Bulk draft/receipt recovery, workspace/cache/login. The initial run's 5 failures were resolved: 2 outdated styling assertions, dense document-tree row height, and 2 obsolete upstream-context failure cases replaced by one actual IndexedDB failure/retry case. The management rerun also corrected an outdated five-tab expectation to include existing Confluence navigation.
- Browser-plugin skill is absent; the repository's regular Playwright workflow was used. Related test runtime scratch was removed by `TemporaryDirectory` finally cleanup. Cleanup of the first, orphaned launcher scratch (`tmp79ep_1yi`) was separately denied by automatic command review; its process was stopped and the folder remains outside the repository.
- Design PR #211 full offline CI passed: [run 43367356893](https://github.com/dongyi-kim/lake-task-manager/actions/runs/43367356893), 20m43s. The workflow's browser skips remain separate from the local browser results. No production deployment is included.

## Recent-item tabs

Branch: `feat/207-recent-item-tabs`, stacked on `design/207-system-refresh`.

- One shared metadata store and tab strip; Task and Confluence maintain independent environment/server/project scopes. Persist IDs, titles, order, active item and scroll only. Drafts, attachments and mutation receipts remain in their original owners.
- Manual keyboard activation: arrows/Home/End move focus without body reads; Enter/Space select; Delete closes. Overflow remains local to the strip, including 200% CSS zoom. Failed navigation restores focus.
- Ticket restoration does not auto-open a modal or prefetch bodies. Document restoration reads the selected document only. Closing an inactive tab performs no body fetch and does not delete its draft.
- Active-item switches wait for durable editor saves and preserve pending uploads, field edits, nested forms and unresolved submission receipts. Same-document activation leaves the composer open. Hash navigation uses the same guard.
- Independent review identified two additional race windows: the editor's initial Jira-context await and publication-record cleanup. Shared submission state now starts before the first await, freezes the editor and guards duplicate submit/cancel/navigation; cleanup remains locked across every durable step. Both findings were independently re-reviewed after the fix.
- Local feature acceptance: **459 unique tests passed**, no remaining failures or skips in the selected scope. This combines the 436 existing static/browser regressions (two fixture corrections and targeted reruns) with 23 new tab/guard/density cases; it is not a single full-suite run. After the final editor guard changes, 399 relevant static/Node/rich publish/draft/Confluence checks passed again. JUnit evidence: `recent-tabs-regression.xml`, `recent-tabs.xml`, `tabs-final-targets.xml`, `targeted-fixture-rerun.xml`, `final-write-regression.xml` in the external evidence directory.
- The mention delay fixture now waits for initial ticket traffic to settle before measuring search: mock's 800ms applies to each upstream call through one serialized provider queue. The wheel test now settles Chromium's observed one-pixel clamp at the programmatic inner boundary before checking native chaining to the dialog. Product scroll handlers and search behavior were unchanged by these fixture corrections.
- Real 24/48/200-child cases passed at 1280/1440/1720 respectively, including every row's scroll/hit-test reachability, last-child search, full-result filtering and no additional Task stream request. These are three explicit combinations, not an all-size/all-theme cross product.
- Final rendered matrix repeated all 30 desktop width/theme/view combinations: no page errors or document overflow. Enlarged desktop layout used a 720×500 CSS viewport at DPR 2 (the layout equivalent of 1440×1000 at 200%, not a browser-menu zoom assertion). WBS/Task/Workload/Confluence all passed overflow and reachability of every navigation link by keyboard. The common navigation now owns local horizontal overflow and reveals focused links; Confluence's duplicate shrink/overflow rules were removed. The shared recent-tab strip separately passed actual 200% CSS zoom.
- Final shell syntax/static selection after the navigation change: 311 passed. Screenshot gallery includes nine actual product views plus separate Task-board, ticket-modal and document recent-tab captures; nine-view image switching and image decoding were checked in Chromium.
- Actual application comparison gallery: https://desktop-k634qao.tailff5611.ts.net/?root=research&path=ltm-ui-design-20261111%2Fimplementation%2Findex.html . The viewer uses the existing Tailscale setup; no public deployment is part of this work.
