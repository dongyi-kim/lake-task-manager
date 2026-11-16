# Tracking

Date-only activity collection for configured people and modules. Uses the same Jira provider in mock, local, and production environments.

| File | Responsibility |
|---|---|
| `manager.py` | Authorized asynchronous runs, scope orchestration, checkpoint/resume and projections |
| `source.py` | Batched REST collection, strict completeness, revision cache and shared admission |
| `analysis.py` | Pure event-time responsibility, event classification and hierarchy aggregation |
| `scope.py` | Conservative module membership, including parent inheritance |
| `storage.py` | Durable run checkpoints and separately expiring raw sources in SQLite |
| `summary.py` | Optional synthesis from collected evidence only, citation checks and usage |

Routes: `app/routes/tracking.py`. UI: `tracking-view.js`, `trackingModel.js`, `tracking.css`.
Design and measurement: `docs/design/tracking/plan.md`, `performance.md`.

Do not turn incomplete evidence into zero activity or skip failed query groups. Do not infer historical people from current assignees, or completion from current status alone. Additional field changes are observations from already-required histories, not another collection pass. Keep raw cache independent of report period and tree decomposition. Never trust stale display identity for authorization.
