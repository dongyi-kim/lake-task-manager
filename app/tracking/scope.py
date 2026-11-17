"""Conservative current-module membership from lightweight hierarchy metadata."""
from __future__ import annotations


def module_candidates(rows, modules, epic_keys, epic_field, parents=None, *, module_people=None):
    by_key = {**(parents or {}), **{row["key"]: row for row in rows}}
    modules, epic_keys = set(modules), set(epic_keys)
    selected_members = {str(uid).strip().casefold() for module in modules
                        for uid in (module_people or {}).get(module, []) if str(uid).strip()}
    missing, uncertain = set(), set()

    def person_matches(fields, role):
        if role not in fields:
            return None
        user = fields[role]
        if user is None:
            return False
        if not isinstance(user, dict):
            return None
        identities = {str(user.get(field) or '').strip().casefold() for field in ('name', 'key', 'accountId')
                      if user.get(field)}
        if identities.intersection(selected_members):
            return True
        # A key-only projection cannot disprove username membership. Display names
        # are not identities; retain incomplete evidence for the rich source.
        return False if str(user.get('name') or '').strip() else None

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
        # Same union as the Task window: roster assignee OR reporter OR module
        # component. VoC labels and other module/Epic mappings never veto a match.
        membership = [person_matches(f, role) for role in ('assignee', 'reporter')] if selected_members else []
        if True in membership:
            return True
        parent = (f.get("parent") or {}).get("key")
        if parent:
            parent_match = classify(parent, seen | {key})
            if parent_match is not False:
                return parent_match
        elif (f.get("issuetype") or {}).get("subtask"):
            return None
        if None in membership or not isinstance(components, list):
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
