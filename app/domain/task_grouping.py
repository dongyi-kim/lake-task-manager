"""Local WBS membership projection for Task snapshots; never queries Jira."""
import hashlib
import json
from yaml import YAMLError

from app.infra.settings import load_plan


def task_grouping_meta(groups, plan=None):
    if plan is None:
        try:
            plan = load_plan()
        except (OSError, ValueError, KeyError, TypeError, AttributeError, YAMLError):
            # A broken optional WBS configuration must not hide otherwise accessible Tasks.
            return {"revision": None, "wbs": [], "unavailable": True}
    revision = hashlib.sha256(json.dumps(
        plan, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    relevant = {group.get("epic") for group in groups if group.get("epic")}
    rows = []
    for wbs in plan.get("wbs") or ():
        epics = list(dict.fromkeys(epic["key"] for epic in wbs.get("epics") or ()
                                   if epic.get("key") in relevant))
        if epics:
            rows.append({"id": wbs["id"], "module": wbs["module"],
                         "name": wbs["name"], "epics": epics})
    # Configuration order is intentional: the first membership represents a shared Epic.
    return {"revision": revision, "wbs": rows}
