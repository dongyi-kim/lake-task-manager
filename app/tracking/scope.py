"""Conservative current-module membership from lightweight hierarchy metadata."""
from __future__ import annotations


def module_candidates(rows, modules, epic_keys, epic_field, parents=None, *, voc_component=None, module_people=None,
                      configured_epics=None):
    by_key = {**(parents or {}), **{row["key"]: row for row in rows}}
    modules, epic_keys = set(modules), set(epic_keys)
    voc_name = str(voc_component or '').strip().casefold()
    known_epics = set(configured_epics or ()) | epic_keys
    known_modules = set(module_people or {})
    selected_members = {str(uid).strip().casefold() for module in modules
                        for uid in (module_people or {}).get(module, []) if str(uid).strip()}
    missing, uncertain = set(), set()

    def explicitly_mapped(key, seen):
        if key in seen or key not in by_key:
            return False
        f = by_key[key].get('fields') or {}
        components = f.get('components')
        if key in known_epics or f.get(epic_field) in known_epics or (isinstance(components, list) and
                any(isinstance(component, dict) and component.get('name') in known_modules for component in components)):
            return True
        parent = (f.get('parent') or {}).get('key')
        return bool(parent and explicitly_mapped(parent, seen | {key}))

    def classify(key, seen):
        if key in seen:
            return None
        raw = by_key.get(key)
        if raw is None:
            missing.add(key)
            return None
        f = raw.get("fields") or {}
        components = f.get("components")
        if key in epic_keys or f.get(epic_field) in epic_keys or (isinstance(components, list) and
                modules.intersection(c.get("name") for c in components if isinstance(c, dict))):
            return True
        is_voc = bool(voc_name) and isinstance(components, list) and any(
            isinstance(component, dict) and str(component.get('name') or '').strip().casefold() == voc_name
            for component in components)
        parent = (f.get("parent") or {}).get("key")
        if parent:
            parent_match = classify(parent, seen | {key})
            if parent_match is not False:
                return parent_match
        elif (f.get("issuetype") or {}).get("subtask"):
            return None
        # Assignee is a narrow supplement for otherwise unmapped VoC. A real module
        # component or configured WBS Epic (including a parent's mapping) keeps the
        # established component/Epic/parent rules authoritative. An unconfigured Epic
        # alone does not establish module membership and does not block the supplement.
        if is_voc and not explicitly_mapped(key, set()):
            assignee = f.get('assignee')
            identities = {str(assignee.get(field) or '').strip().casefold() for field in ('name', 'key', 'accountId')
                          if isinstance(assignee, dict) and assignee.get(field)}
            if identities.intersection(selected_members):
                return True
            # The roster uses Jira usernames. A key/accountId can confirm a direct
            # match, but a mismatch without a username does not establish that the
            # assignee is outside the module. Retain it for richer-source resolution;
            # displayName is never identity evidence. Unassigned also remains unknown.
            if not isinstance(assignee, dict) or not str(assignee.get('name') or '').strip():
                return None
        if not isinstance(components, list):
            return None
        # A requested custom field absent from a Task projection is not proof that
        # it is unlinked. Preserve it until a richer source can establish scope.
        if epic_field not in f and str((f.get("issuetype") or {}).get("name", "")).casefold() != "epic":
            return None
        return False

    kept = []
    for row in rows:
        match = classify(row["key"], set())
        if match is not False:
            kept.append(row)
        if match is None:
            uncertain.add(row["key"])
    return kept, missing, uncertain
