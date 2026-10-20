"""Local-only resolution of pseudonymous people in guarded, read-only replies."""

import json
import re

_MARKER = re.compile(r"\[\[person:[0-9a-f]{12}\]\]")


def result_person_references(value, evidence, identities, pseudonymize):
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
        # A directory lookup may match a username or display name. Require a
        # complete bounded response and exact equality, never a fuzzy/first hit.
        users = client().provider.get_json(
            "/rest/api/2/user/search",
            params={"username": original, "maxResults": 100},
        )
        if not isinstance(users, list) or len(users) >= 100:
            raise ValueError("person_directory_incomplete")
        ids = {
            str(row.get("name") or row.get("key") or "").strip()
            for row in users if isinstance(row, dict) and original.casefold() in {
                str(row.get(field) or "").strip().casefold()
                for field in ("name", "key", "displayName", "emailAddress")
            } | {real_name(row.get("displayName") or "").casefold()}
        } - {""}
        if len(ids) != 1:
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
