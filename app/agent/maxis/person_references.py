"""Local-only resolution of pseudonymous people in guarded, read-only replies."""

import json
import re

_MARKER = re.compile(r"\[\[person:[0-9a-f]{12}\]\]")
_ID_FIELDS = {"assigneeId", "authorId", "reporterId", "userId", "user_id",
              "user_identity", "username", "userName", "accountId", "account_id"}
_NAME_FIELDS = {"assignee", "assigneeName", "author", "display", "displayName",
                "owner", "reporter", "who"}
_MAX_IDENTITY_DEPTH = 32
_MAX_IDENTITY_VALUES = 50000


def identity_types(state, cfg):
    """Keep stronger typed IDs distinct from the broad one-way DLP inventory."""
    from app.infra.settings import load_people

    kinds, visited = {}, 0
    def add(value, kind):
        if not isinstance(value, str) or not value.strip():
            return
        key = value.strip().casefold()
        kinds.setdefault(key, set()).add(kind)

    add(getattr(cfg, "jira_user", ""), "id")
    for uid in getattr(cfg, "managers", None) or ():
        add(uid, "id")
    for people in (load_people() or {}).values():
        for uid in people or ():
            add(uid, "id")

    def collect(value, depth=0):
        nonlocal visited
        if depth > _MAX_IDENTITY_DEPTH or visited >= _MAX_IDENTITY_VALUES:
            raise ValueError("person_identity_inventory_incomplete")
        visited += 1
        if isinstance(value, dict):
            for key, item in value.items():
                if key in _ID_FIELDS or (key == "name" and "displayName" in value):
                    add(item, "id")
                elif key == "emailAddress":
                    add(item, "email")
                elif key in _NAME_FIELDS:
                    add(item, "display")
                collect(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item, depth + 1)
        elif isinstance(value, str):
            for uid in re.findall(r"\[~([^\]\s]{1,128})\]", value):
                add(uid, "id")

    collect(state)
    return kinds


def result_person_references(value, evidence, identities, pseudonymize, kinds):
    """Resolve only returned markers that occurred in this call's guarded input.

    DLP identifiers are not necessarily usernames: progress history also carries
    display names. Never turn those strings directly into Jira mentions. Exact
    directory matches must be unique and pass the canonical reference resolver.
    Neither this mapping nor its lookup results cross the MAXIS boundary.
    """
    from app.agent.references import resolve_references
    from app.agent.tools._ctx import client
    from app.domain.names import real_name

    wanted = set(_MARKER.findall(json.dumps(value, ensure_ascii=False)))
    if not wanted:
        return {}
    available = set(_MARKER.findall(json.dumps(evidence, ensure_ascii=False)))
    if not wanted <= available:
        raise ValueError("person_reference_not_in_input")
    originals = {}
    for identity in identities:
        marker = pseudonymize(identity)
        if marker in wanted:
            originals.setdefault(marker, {}).setdefault(identity.casefold(), identity)
    if set(originals) != wanted or any(len(rows) != 1 for rows in originals.values()):
        raise ValueError("person_reference_unknown_or_ambiguous")

    resolved = {}
    for marker, originals_for_marker in originals.items():
        original = next(iter(originals_for_marker.values()))
        observed_kinds = kinds.get(original.casefold()) or set()
        if not observed_kinds or not observed_kinds <= {"id", "email", "display"}:
            raise ValueError("person_identity_type_unknown")
        # A directory lookup may match a username or display name. Require a
        # complete bounded response and exact equality, never a fuzzy/first hit.
        users = client().provider.get_json(
            "/rest/api/2/user/search",
            params={"username": original, "maxResults": 100},
        )
        if not isinstance(users, list) or len(users) >= 100:
            raise ValueError("person_directory_incomplete")
        def matches(row, kind):
            # A missing username must never resolve through another user's
            # identical display name. The same rule applies to email addresses.
            fields = {"id": ("name", "key"), "email": ("emailAddress",),
                      "display": ("displayName",)}[kind]
            aliases = {str(row.get(field) or "").strip().casefold() for field in fields}
            if kind == "display":
                aliases.add(real_name(str(row.get("displayName") or "").strip()).casefold())
                # Legacy producer `who` can hold an ID; only its exact canonical
                # match is accepted, never a prefix/fuzzy username.
                aliases.update(str(row.get(field) or "").strip().casefold()
                               for field in ("name", "key"))
            return original.casefold() in aliases

        channels = [{
            str(row.get("name") or row.get("key") or "").strip()
            for row in users if isinstance(row, dict) and matches(row, kind)
        } - {""} for kind in observed_kinds]
        ids = set().union(*channels)
        if any(not channel for channel in channels) or len(ids) != 1:
            raise ValueError("person_directory_unknown_or_ambiguous")
        uid = next(iter(ids))
        result = resolve_references([{"id": marker, "kind": "person", "user_id": uid}])
        rows = result.get("references") or []
        if (not result.get("ok") or len(rows) != 1
                or str(rows[0].get("userId") or "").casefold() != uid.casefold()
                or rows[0].get("mention") != f"[~{rows[0]['userId']}]"):
            raise ValueError("person_canonical_identity_mismatch")
        resolved[marker] = rows[0]["mention"]
    return resolved
