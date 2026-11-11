"""Finite local checkpoint bindings; saved source bytes remain the authority."""
from tools.codex_mcp_eval.work_evidence import evidence_collection
from tools.codex_mcp_eval.work_recovery import (
    _digest, complete_search_membership, is_evidence_path, saved_response_hashes,
)


def checkpoint_nodes(value, group=None):
    """Traverse wrappers; each local binding is one indivisible candidate."""
    if isinstance(value, dict):
        yield value, group
        # Arguments, references and arbitrary descendants belong to this
        # binding. Reconsidering them alone would discard its rejected identity.
        if group is not None or any(field in value for field in (
                "arguments", "lastSavedArguments", "lastSaved", "lastChunk")):
            return
        for name, child in value.items():
            if name == "groups" and isinstance(child, dict):
                for key, node in child.items():
                    constraint = {"name": "search_issues"} if key == "search" else None
                    for separator in ("/", ":"):
                        kind, found, issue = key.partition(separator)
                        if found and kind in {"comments", "history"} and issue and not any(
                                part in issue for part in ("/", ":")):
                            constraint = {"name": "get_evidence_page", "key": issue, "kind": kind}
                    if isinstance(node, dict):
                        yield node, constraint
            # An overall run state may contain independent explicit groups,
            # but its other descendants are not new checkpoint candidates.
            elif name == "groups" or not any(field in value for field in ("status", "state")):
                yield from checkpoint_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from checkpoint_nodes(child)


def checkpoint_state(node):
    states = [node[field] for field in ("status", "state") if field in node]
    return states[0] if states and isinstance(states[0], str) and all(state == states[0] for state in states) else None


def local_identities(node):
    """Only these explicitly local argument containers participate in binding."""
    identities = [node]
    for field in ("arguments", "lastSavedArguments"):
        if field in node:
            if not isinstance(node[field], dict):
                return None
            identities.append(node[field])
    return identities


def pending_group(node, constraint):
    if checkpoint_state(node) not in {"pending", "incomplete", "collecting", "failed"}:
        return None
    identities = local_identities(node)
    if identities is None:
        return None
    fields = {field: [row[field] for row in [*identities, constraint or {}] if field in row]
              for field in ("key", "kind")}
    if (not all(fields.values()) or any(any(value != values[0] for value in values)
                                       for values in fields.values())):
        return None
    key, kind = fields["key"][0], fields["kind"][0]
    return ({"key": key, "kind": kind} if isinstance(key, str) and key and isinstance(kind, str)
            and kind in {"comments", "history"} else None)


def checkpoint_references(files, node):
    references = []
    for field in ("lastSaved", "lastChunk"):
        if field not in node:
            continue
        path = node[field]
        if not is_evidence_path(path):
            return None
        # Validate even absent paths through the capability, not pathlib joins.
        files._path(path)
        references.append(saved_response_hashes(files, {path}))
    return references


def binds_saved_call(node, constraint, call, references):
    identities = local_identities(node)
    if identities is None or references is None:
        return False
    page, arguments = call["result"]["data"], call["arguments"]
    search = call["name"] == "search_issues"
    allowed = {"jql": (arguments.get("jql"), page.get("canonicalJql")) if search else (),
               "canonicalJql": (arguments.get("jql"), page.get("canonicalJql")) if search else (),
               "snapshotId": (page.get("snapshotId"),), "snapshot_id": (page.get("snapshotId"),),
               "key": () if search else (page.get("key"),),
               "kind": () if search else (page.get("kind"),)}
    for identity in identities:
        for field, valid in allowed.items():
            if field in identity:
                value = identity[field]
                # A null initial request snapshot requests a new snapshot.
                if field == "snapshot_id" and value is None:
                    continue
                # Live evidence pages have no snapshot identity to bind.
                if field == "snapshotId" and value is None and not search and page.get("snapshotId") is None:
                    continue
                if not isinstance(value, str) or not value or value not in valid:
                    return False
    if constraint and any(value != (call["name"] if field == "name" else page.get(field))
                          for field, value in constraint.items()):
        return False
    digests = {_digest(call["result"]), _digest(page)}
    if any(not digests.intersection(hashes) for hashes in references):
        return False
    explicit = {field for identity in identities for field in allowed if identity.get(field) is not None}
    if references:
        return True
    if search:
        return bool(explicit & {"jql", "canonicalJql"}) and bool(explicit & {"snapshotId", "snapshot_id"})
    return {"key", "kind"} <= explicit


def completed_saved_call(files, node, constraint, saved):
    if checkpoint_state(node) != "complete":
        return None
    references = checkpoint_references(files, node)
    for call in saved:
        page = call["result"].get("data") or {}
        if (call["name"] not in {"search_issues", "get_evidence_page"} or page.get("hasMore") is not False
                or not binds_saved_call(node, constraint, call, references)):
            continue
        if call["name"] == "search_issues":
            population = [row for row in saved if (row["result"].get("data") or {}).get("snapshotId")
                          == page.get("snapshotId")]
            complete = complete_search_membership(population, page.get("canonicalJql")) is not None
        else:
            records = [row for row in saved if (row["result"].get("data") or {}).get("snapshotId")
                       == page.get("snapshotId")]
            complete = evidence_collection(records, page.get("key"), page.get("kind"))["complete"]
        if complete:
            return call
    return None


CHECKPOINT_DEPENDENCIES = (checkpoint_nodes, checkpoint_state, local_identities, pending_group,
                           checkpoint_references, binds_saved_call, completed_saved_call)
