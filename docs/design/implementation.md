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
- [ ] Review, commit and open design PR.
- [ ] Implement and validate recent-item tabs on follow-on branch.

## Validation

- 571 related static/Node/domain/Jira workload checks passed in 98.99s. Scope: `tests/frontend/static_assets`, compact/filter/find/fault Task models, WBS/VIT recovery, ticket model/recovery, `tests/jira/workload`, `tests/domain/test_rollup.py`. Browser checks are tracked separately.
- Actual mock application ran through `run.py --reload` on configured port 4457 with the Windows named launcher. Local jira820 was restored to the exact revision already pinned in requirements; dependency files were unchanged.
- Captured existing and redesigned CSS on actual product DOM, with baseline CSS read from Git `7e8e9efc` rather than a second source checkout. Confluence uses the existing upstream-shaped document test fixture. Evidence: `C:/Users/PC/research/ltm-ui-design-20261111/implementation/`.
- Task/WBS/Workload/VIT/Confluence matrix: 1280, 1440, 1720 px; light/dark; 30 rendered combinations with no document-level horizontal overflow or JavaScript page errors. Task two-line cards measured 56px, inline children 30px; reduced-motion token resolves to 0ms. This does not substitute for action/200% checks.
- Independent review found a dark VoC badge contrast defect; corrected using the existing type hue as a tint and primary text. Actual dark capture also exposed the no-Epic marker inheriting the filled-badge foreground; restricted that rule to filled badges.
- Tests use owned OS temporary runtime/cache/case folders, configured local browser port 4457, and the available Node/Chromium executables. Full suite is delegated to PR CI under `docs/TESTING.md`; no live Jira/Confluence writes or LLM calls are used.
- Existing browser regression selection: 63 current cases passed across the initial run and targeted reruns, with the six previously skipped management tests now executed through the shared browser launcher. Includes Task grouping/find/update acknowledgement, ticket updates/work time, rich description/comment two-publish round trips, document read/retry/authoring, Bulk draft/receipt recovery, workspace/cache/login. The initial run's 5 failures were resolved: 2 outdated styling assertions, dense document-tree row height, and 2 obsolete upstream-context failure cases replaced by one actual IndexedDB failure/retry case. The management rerun also corrected an outdated five-tab expectation to include existing Confluence navigation.
- Browser-plugin skill is absent; the repository's regular Playwright workflow was used. Related test runtime scratch was removed by `TemporaryDirectory` finally cleanup. Cleanup of the first, orphaned launcher scratch (`tmp79ep_1yi`) was separately denied by automatic command review; its process was stopped and the folder remains outside the repository.
- Remaining acceptance work on the follow-on branch: recent-tab behavior, enlarged desktop UI/keyboard flow, refreshed evidence links and full PR CI result. No production deployment is included.
