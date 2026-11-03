# Cache policy verification — 2026-11-03

The policy is configured at runtime through Settings → cache settings. Existing payloads and
source collection times survive a policy change; no full cache purge or warming occurs.

| Data | Fresh | Display retention |
|---|---:|---:|
| General tickets, lists, memberships and aggregates | 10 minutes | 24 hours |
| Done ticket source/body | 24 hours | 24 hours |
| Done ticket comments | 1 hour | 24 hours |
| Epic metadata and profile photos | 24 hours | 30 days |

Drafts retain their separate seven-day policy. Permissions, edit metadata and mutation validation
never use the archived display copy as authoritative input.

## Verified scenarios

| Scenario | Evidence |
|---|---|
| 10-minute, 1-hour, 24-hour and 30-day boundaries | Fake-clock cache tests, including purge |
| Policy precedence, validation, persisted save and failed save | Runtime policy tests and browser settings save/reload/defaults |
| Full/light reuse, search write-through and resolved freshness | Jira/cache regression tests |
| Stale display, shared refresh and complete-success-only storage | Controlled concurrent producers and malformed/error responses |
| Mutation during refresh, late permission denial | A new request uses the new generation; old work cannot store or remove newer data |
| Pagination after the general freshness interval | New search gets a fresh snapshot; an existing cursor retains its original rows for 30 minutes |
| Old Epic name decorating fresh tickets | Source ages remain separate; a valid old name does not make new ticket data immediately dead |
| Active Workload/WBS/VIT display refresh | Current resources update; old module/period responses do not replace the current filter |
| Failed profile images | Actual Vue/mention DOM recovers on auth/online; successful images keep the same node and one request |

Focused policy/JQL/browser-memo verification passed 57 tests. The additional profile-image
regressions passed three tests, including a real Chromium page and a fake clock.
The WBS/VIT/workload frontend contracts passed 14 tests. These are targeted runs with overlapping
coverage, not an additive count of distinct test cases.

The five-feature integration also exercised cache settings alongside draft creation, rich editor
roundtrips and the MCP settings screen. Normal scenarios use zero injected latency. The broader
integration backend run passed 3,120 cases; its sole failure was an outdated launcher expectation
for the newly required `proxy_headers=False`, handled in the MCP PR.

## Operational limits

- Production Jira availability and permissions can still prevent refresh. Previously valid display
  data remains available only within its configured retention period.
- A definitive resource deletion or permission denial removes that resource's display copy.
  A list endpoint denial does not by itself prove that every member ticket became inaccessible.
- Browser display values use server deadlines. Policy changes revalidate affected open resources
  without manufacturing a new source timestamp or extending a mixed response's lifetime.
