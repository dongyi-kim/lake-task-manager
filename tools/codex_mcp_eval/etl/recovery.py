"""Six observable interruption cuts; no manufactured model or transport output."""
from copy import deepcopy
from threading import Event

from tools.codex_mcp_eval.work_recovery import persisted_calls

RECOVERY_SCENARIOS = ("search-pagination", "comments-pagination", "history-pagination",
                      "group-boundary", "client-restart", "backend-restart")


class RecoveryController:
    def __init__(self, scenario):
        if scenario not in RECOVERY_SCENARIOS:
            raise ValueError("Invalid ETL recovery scenario")
        self.scenario, self.stop, self.trigger = scenario, Event(), None
        self.files, self.observed = None, []
        self.contract = {"originalDialogueAvailable": False, "requiresSavedFilesRead": True,
                         "backendRestart": scenario == "backend-restart",
                         "clientRestart": scenario in {"client-restart", "backend-restart"},
                         "resumeFailedGroupFirst": True, "incompleteTotalsStayNull": True,
                         "revalidateInvalidatedSnapshots": True}

    def _trigger(self, reason, **details):
        if self.trigger is None:
            self.trigger = {"scenario": self.scenario, "reason": reason, **details}
            self.stop.set()

    def observe_ltm(self, name, arguments, result):
        if name not in {"search_issues", "get_evidence_page"} or result.get("ok") is not True:
            return
        self.observed.append({"name": name, "arguments": deepcopy(arguments), "result": deepcopy(result)})
        data = result.get("data") or {}
        target = {"search-pagination": ("search_issues", None),
                  "comments-pagination": ("get_evidence_page", "comments"),
                  "history-pagination": ("get_evidence_page", "history")}.get(self.scenario)
        if (target and name == target[0] and (target[1] is None or data.get("kind") == target[1])
                and data.get("hasMore") is True and type(data.get("nextStartAt")) is int
                and data["nextStartAt"] > data.get("startAt", -1)):
            self._trigger("real response before remaining pages", name=name,
                          key=data.get("key"), kind=data.get("kind"), snapshotId=data.get("snapshotId"),
                          nextStartAt=data["nextStartAt"], canonicalJql=data.get("canonicalJql"))

    def after_write(self, path, content):
        # Restarts/group cuts are eligible only after genuine returned evidence
        # has been durably written. persisted_calls checks exact JSON digests.
        if self.scenario not in {"group-boundary", "client-restart", "backend-restart"} or not self.files:
            return
        saved = persisted_calls(self.files, self.observed)
        if not saved or not path.rsplit("/", 1)[-1].startswith("progress"):
            return
        import json
        try:
            checkpoint = json.loads(content)
        except ValueError:
            return
        def nodes(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from nodes(child)
            elif isinstance(value, list):
                for child in value:
                    yield from nodes(child)
        complete = [call for call in saved if (call["result"].get("data") or {}).get("hasMore") is False]
        checkpoint_nodes = list(nodes(checkpoint))
        pending = next(({"key": node["key"], "kind": node["kind"]} for node in checkpoint_nodes
                        if node.get("key") and node.get("kind") in {"comments", "history"}
                        and node.get("status") in {"pending", "incomplete", "collecting", "failed"}), None)
        for node in checkpoint_nodes:
            if node.get("status") != "complete":
                continue
            for call in complete:
                data = call["result"]["data"]
                evidence_match = (call["name"] == "get_evidence_page" and bool(node.get("key"))
                    and node.get("key") == data.get("key") and node.get("kind") == data.get("kind"))
                search_match = (call["name"] == "search_issues" and bool(data.get("snapshotId"))
                    and node.get("snapshotId", node.get("snapshot_id")) == data["snapshotId"]
                    and node.get("jql", node.get("canonicalJql")) in {
                        call["arguments"].get("jql"), data.get("canonicalJql")})
                if evidence_match or search_match:
                    self._trigger("completed group checkpoint before interruption", path=path,
                                  key=data.get("key"), kind=data.get("kind"), failedGroup=pending,
                                  requiresBackendRestart=self.contract["backendRestart"])


def recovery_cases():
    from .cases import CASES
    base = next(row for row in CASES if row["id"] == "ETL-F08-boundary")
    return [{**deepcopy(base), "id": "ETL-RECOVERY-" + scenario, "scenario": scenario,
             "sourceCaseId": base["id"], "prompts": [base["prompts"][0] +
                 " 조사 파일의 manifest·그룹별 progress·불변 evidence chunk·cursor와 source age를 보존하고 중단 후 재개해줘."]}
            for scenario in RECOVERY_SCENARIOS]
