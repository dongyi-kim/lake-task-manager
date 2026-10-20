"""Attest the progress producer's actual bounded reads, not semantic PMO state."""
from copy import deepcopy

from app.agent.maxis.backend import attest_query_result


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
    jira["complete"] = complete(("target", "changes", "comments", "children", "links"))
    # The source is an explicit bounded excerpt, not a claim that all history was read.
    jira["projection"] = "progress-report: latest 8 changes, requested comments, first 8 links"

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
