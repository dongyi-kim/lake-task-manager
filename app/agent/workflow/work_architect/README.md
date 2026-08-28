# Work Architect modules

`workflow.agents.work_architect` remains the compatibility facade. New policy code belongs
in the narrowest module here instead of expanding that facade again.

- `contracts.py`: structured-output schemas
- `context.py`: current-request and continuation authority
- `due_dates.py`: deterministic deadline parsing
- `body_text.py`: HTML-safe text and reference handling
- `change_plan.py`: existing-ticket mutation planning
- `apply_pipeline.py`: creation/update projection and draft post-processing
- `finalize.py`: final payload sealing, questions, and response assembly

The facade passes its live policy bindings into the two pipelines on every invocation. This
keeps existing private imports and monkeypatch-based tests compatible without sharing mutable
pipeline globals between concurrent requests.

Run the focused regression folder while developing these modules:

```powershell
python -B -m pytest tests/agent/core/work_architect -q
```
