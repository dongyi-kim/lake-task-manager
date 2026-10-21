"""Attest the progress producer's actual bounded reads, not semantic PMO state."""
from copy import deepcopy

from app.agent.maxis.backend import attest_query_result


def _child_counts(children, branch):
    """Count only the returned, identity/status-checked rows, never SP progress."""
    reads = [read for read in branch.reads if read.operation == "ticket_children"]
    if len(reads) != 1 or not isinstance(children, list):
        return None
    read = reads[0]
    if (not isinstance(read.metadata, list) or len(children) != len(read.metadata)
            or read.returned_count != len(children)):
        return None
    seen, done = set(), 0
    for child, original in zip(children, read.metadata):
        if not isinstance(child, dict) or not isinstance(original, dict):
            return None
        key = original.get("key")
        category = original.get("statusCat") or original.get("statusCategory")
        if (not isinstance(key, str) or not key or key in seen or child.get("key") != key
                or category not in {"new", "indeterminate", "todo", "inprogress", "done"}
                or type(child.get("done")) is not bool
                or child["done"] != (category == "done")):
            return None
        seen.add(key)
        done += int(child["done"])
    def count(value):
        # Both Korean counters describe the same observed issue count; the
        # response guard still rejects unprovided numbers or measurement units.
        return f"{value}건 (티켓 {value}개)"
    return {"returned": count(len(children)), "done": count(done),
            "not_done": count(len(children) - done),
            "basis": "조회 반환 하위 티켓 개수; SP 기반 진척률이나 전체 프로젝트 규모가 아님"}


def progress_evidence(key, report, acquisition, provenance):
    """Keep Jira and Confluence authority separate; missing proof stays partial."""
    branches = acquisition.branches
    target = branches["target"]
    target_valid = (
        acquisition.finished and acquisition.requested_key == key
        and report.get("key") == key and target.state == "returned"
        and len(target.reads) == 1 and target.reads[0].state == "returned"
        and not target.error_type and not target.reads[0].error_type
        and isinstance(target.reads[0].metadata, dict)
        and str(target.reads[0].metadata.get("key", "")).upper() == key
    )

    def complete(names):
        return target_valid and all(
            branches[name].state == "returned"
            and not branches[name].error_type
            and bool(branches[name].reads)
            and all(read.state == "returned" and not read.error_type
                    and (read.operation not in {"get_issue", "ticket_badge"} or (
                        isinstance(read.metadata, dict) and read.arguments
                        and str(read.metadata.get("key", "")).upper()
                        == str(read.arguments[0]).upper()))
                    for read in branches[name].reads)
            for name in names)

    rows, proofs = [], {}

    def append(source, suffix, result, partial=False):
        query_id = "progress-" + key + "-" + suffix
        proof = attest_query_result(query_id, source, result,
            retrieved_at=provenance.get("retrieved_at"), provenance=provenance,
            partial=partial or provenance.get("partial", True))
        rows.append({"id": query_id, "source": source, "result": result})
        proofs[source + ":" + query_id] = proof

    jira_fields = ("key", "title", "status", "done", "assigneeId", "due", "updated",
                   "changes", "comments", "children", "children_done", "links")
    jira = {field: deepcopy(report[field]) for field in jira_fields if field in report}
    if not target_valid:
        jira = {"key": key, "error": "progress target identity was not verified"}
    else:
        # Partial is a coverage warning, not an authority boundary. Never retain
        # content whose read failed or whose identity disagrees with its request.
        for name in ("changes", "comments", "children", "links"):
            if not complete((name,)):
                jira.pop(name, None)
                if name == "children":
                    jira.pop("children_done", None)
        if (complete(("children",)) and provenance.get("tracked") is True
                and not provenance.get("partial", True)
                and not provenance.get("served_stale", True)
                and isinstance(provenance.get("read_count"), int)
                and provenance["read_count"] > 0):
            counts = _child_counts(jira.get("children", []), branches["children"])
            if counts is not None:
                jira["children_counts"] = counts
    jira["complete"] = complete(("target", "changes", "comments", "children", "links"))
    # Projection limits live in acquisition metadata, never in business evidence:
    # a writer must not turn "first 8 links" into a ticket's requirement or DoD.

    from app.agent.retrieval.harvest import _conf_id
    page_reads = {}
    for read in branches["documents"].reads:
        if read.operation == "confluence_page" and read.arguments:
            page_reads[str(read.arguments[0])] = read
    for index, document in enumerate(report.get("documents") or []):
        page_id = str(_conf_id(document.get("url") or "") or "")
        read = page_reads.get(page_id)
        metadata = read.metadata if read and isinstance(read.metadata, dict) else {}
        space = metadata.get("space")
        space = space.get("key") if isinstance(space, dict) else space
        valid = (complete(("documents",)) and read is not None and read.state == "returned"
                 and not read.error_type and str(metadata.get("id") or "") == page_id
                 and isinstance(space, str) and bool(space.strip()))
        if not valid:
            # No source body may cross with an invented space or borrowed Jira scope.
            jira["complete"] = False
            continue
        result = {"documents": [{**deepcopy(document), "id": page_id, "space": space}],
                  "complete": complete(("documents",))}
        append("confluence", "document-" + str(index), result, not result["complete"])
    if not complete(("documents",)):
        jira["complete"] = False
    append("jira", "ticket", jira, not jira["complete"])
    return {"query_results": rows, "query_evidence_attestations": proofs}
