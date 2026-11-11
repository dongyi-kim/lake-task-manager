"""Six observable interruption cuts; no manufactured model or transport output."""
from copy import deepcopy
from threading import Event

from tools.codex_mcp_eval.work_evidence import evidence_collection
from tools.codex_mcp_eval.work_recovery import _digest, complete_search_membership, persisted_calls

RECOVERY_SCENARIOS = ("search-pagination", "comments-pagination", "history-pagination",
                      "group-boundary", "client-restart", "backend-restart")


def source_group(call):
    """Identity from a genuine returned source, never checkpoint vocabulary."""
    result, arguments = call.get("result") or {}, call.get("arguments") or {}
    data = result.get("data") or {}
    if result.get("ok") is not True or call.get("isError") or not isinstance(data, dict):
        return None
    if call.get("name") == "search_issues":
        canonical = data.get("canonicalJql")
        return ("search", canonical) if isinstance(canonical, str) and canonical else None
    key, kind = data.get("key"), data.get("kind")
    requested = arguments.get("key")
    if (call.get("name") == "get_evidence_page" and isinstance(key, str) and key
            and isinstance(requested, str) and requested.strip().upper() == key
            and kind in {"comments", "history"} and arguments.get("kind", "comments") == kind):
        return kind, key
    return None


def eligible_source(call):
    """Observed validity only; this does not promise perpetual freshness/access."""
    result = call.get("result") or {}
    cache = result.get("cache") or {}
    group = source_group(call)
    data = result.get("data") or {}
    # Omitted mode uses the resource's default freshness policy; its observed
    # cache has compliant=None. Do not invent an explicit max-age requirement.
    compliant = (cache.get("compliant") is True or (cache.get("compliant") is None
                 and (call.get("arguments") or {}).get("cache_mode") is None))
    return (group is not None and (group[0] == "search" or data.get("sourceComplete") is True)
            and result.get("partial") is False
            and result.get("missing") == 0 and cache.get("available") is True
            and not cache.get("invalidated") and cache.get("state") == "fresh"
            and compliant)


def complete_saved_group(files, calls):
    """Prove the latest traversal with genuine saved bytes and existing coverage rules."""
    if not calls or (group := source_group(calls[-1])) is None:
        return False
    if any(source_group(call) != group for call in calls):
        return False
    # A newer first page supersedes old coverage. An explicit old search cursor
    # is not a new traversal and cannot reactivate a superseded population.
    first = 0
    for index, call in enumerate(calls):
        page, arguments = call["result"]["data"], call["arguments"]
        items = page.get("items") or []
        offset = items[0].get("recordOffset", 0) if items else 0
        if page.get("startAt") == 0 and offset == 0 and (
                group[0] != "search" or arguments.get("snapshot_id") is None):
            first = index
    current = calls[first:]
    if len(persisted_calls(files, current)) != len(current):
        return False
    if group[0] == "search":
        # This also binds frozen live continuations to their validated anchor.
        return (all((call["result"].get("cache") or {}).get("available") is True and
                    ((call["result"].get("cache") or {}).get("state") != "fresh" or eligible_source(call))
                    for call in current)
                and complete_search_membership(current, group[1]) is not None)
    return (all(eligible_source(call) for call in current)
            and evidence_collection(current, group[1], group[0])["complete"])


SOURCE_GROUP_DEPENDENCIES = (source_group, eligible_source, complete_saved_group)


class RecoveryController:
    def __init__(self, scenario):
        if scenario not in RECOVERY_SCENARIOS:
            raise ValueError("Invalid ETL recovery scenario")
        self.scenario, self.stop, self.trigger = scenario, Event(), None
        self.files, self.observed, self.cut_call = None, [], None
        self.completed_calls = []
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
        if name not in {"search_issues", "get_evidence_page"}:
            return
        call = {"name": name, "arguments": deepcopy(arguments), "result": deepcopy(result)}
        group = source_group(call)
        if (self.scenario in {"group-boundary", "client-restart", "backend-restart"}
                and self.trigger is None and self.files and group and group[0] != "search"
                and eligible_source(call) and self.observed):
            previous = source_group(self.observed[-1])
            if previous and previous != group:
                prior = []
                for row in reversed(self.observed):
                    if source_group(row) != previous:
                        break  # Failure/identity change cannot donate older coverage.
                    prior.append(row)
                prior.reverse()
                if complete_saved_group(self.files, prior):
                    self.cut_call = call
                    self.completed_calls = prior
                    completed = ({"name": "search_issues", "canonicalJql": previous[1]}
                                 if previous[0] == "search" else
                                 {"name": "get_evidence_page", "key": previous[1], "kind": previous[0]})
                    self._trigger("next group response after complete saved source", name=name,
                        key=group[1], kind=group[0], failedGroup={"key": group[1], "kind": group[0]},
                        completedSourceGroup=completed, incomingResponseDigest=_digest(result),
                        requiresBackendRestart=self.contract["backendRestart"])
        # Failed responses are barriers, not silently discarded older proof.
        self.observed.append(call)
        if result.get("ok") is not True:
            return
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

    def settle_cut(self, *, settled):
        """Classify actual cancellation tail; never promise instantaneous before-save."""
        if self.cut_call is None or not self.files:
            return
        if settled is not True:
            self.trigger["cutPosition"] = {"position": "observer_settlement_unproved", "resumeCall": None,
                "targetResponsePersisted": None, "qualifiesFailedGroupWindow": False}
            return
        group = source_group(self.cut_call)
        start = next(index for index, call in enumerate(self.observed) if call is self.cut_call)
        target = [call for call in self.observed[start:] if source_group(call) == group]
        saved = persisted_calls(self.files, target)
        position, resume = "target_position_unproved", None
        if not complete_saved_group(self.files, self.completed_calls):
            position = "completed_source_no_longer_saved"
        elif complete_saved_group(self.files, target):
            position = "target_group_already_complete"
        elif not any(call is self.cut_call for call in saved):
            position = "target_response_unpersisted"
            resume = {"name": self.cut_call["name"], "arguments": deepcopy(self.cut_call["arguments"])}
        else:
            missing = next((call for call in target if not any(call is row for row in saved)), None)
            resume = ({"name": missing["name"], "arguments": deepcopy(missing["arguments"])} if missing else
                      deepcopy(saved[-1]["result"]["data"].get("nextCall")))
            if resume:
                position = "target_prefix_saved"
        self.trigger["cutPosition"] = {"position": position, "resumeCall": resume,
            "targetResponsePersisted": any(call is self.cut_call for call in saved),
            "qualifiesFailedGroupWindow": position in {"target_response_unpersisted", "target_prefix_saved"}}


def recovery_cases():
    from .cases import CASES
    base = next(row for row in CASES if row["id"] == "ETL-F08-boundary")
    return [{**deepcopy(base), "id": "ETL-RECOVERY-" + scenario, "scenario": scenario,
             "sourceCaseId": base["id"], "prompts": [base["prompts"][0] +
                 " 조사 파일의 manifest·그룹별 progress·불변 evidence chunk·cursor와 source age를 보존하고 중단 후 재개해줘."]}
            for scenario in RECOVERY_SCENARIOS]
