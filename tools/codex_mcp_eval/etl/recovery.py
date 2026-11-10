"""Six observable interruption cuts; no manufactured model or transport output."""
from copy import deepcopy
from threading import Event

from tools.codex_mcp_eval.work_recovery import persisted_calls
from tools.codex_mcp_eval.recovery_checkpoint import checkpoint_nodes, pending_group, completed_saved_call

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
        if (self.scenario not in {"group-boundary", "client-restart", "backend-restart"} or not self.files
                or not path.rsplit("/", 1)[-1].startswith("progress")):
            return
        saved = persisted_calls(self.files, self.observed)
        if not saved:
            return
        import json
        try:
            checkpoint = json.loads(content)
        except ValueError:
            return
        nodes = list(checkpoint_nodes(checkpoint))
        pending = next((group for node, constraint in nodes
                        if (group := pending_group(node, constraint)) is not None), None)
        for node, constraint in nodes:
            call = completed_saved_call(self.files, node, constraint, saved)
            if call:
                data = call["result"]["data"]
                self._trigger("completed group checkpoint before interruption", path=path,
                              key=data.get("key"), kind=data.get("kind"), failedGroup=pending,
                              requiresBackendRestart=self.contract["backendRestart"])
                return


def recovery_cases():
    from .cases import CASES
    base = next(row for row in CASES if row["id"] == "ETL-F08-boundary")
    return [{**deepcopy(base), "id": "ETL-RECOVERY-" + scenario, "scenario": scenario,
             "sourceCaseId": base["id"], "prompts": [base["prompts"][0] +
                 " 조사 파일의 manifest·그룹별 progress·불변 evidence chunk·cursor와 source age를 보존하고 중단 후 재개해줘."]}
            for scenario in RECOVERY_SCENARIOS]
