"""Observed fault boundaries and saved-source provenance for cold-session recovery."""
from __future__ import annotations

from hashlib import sha256
import json
from threading import Event

RECOVERY_SCENARIOS = ("between-pages", "after-response", "record-chunk", "corrupt-progress",
                      "expired-snapshot", "source-changed")


def complete_search_membership(calls, canonical_jql):
    """Prove the final scoped query from actual fresh, coherent search responses.

    A known-key batch is body evidence, not query membership. Search page size is
    unrestricted; source snapshot, row coverage and terminal metadata prove completion.
    """
    groups, latest = {}, None
    for call in calls:
        result = call.get("result") or {}
        data = result.get("data") or {}
        if (call.get("name") != "search_issues" or call.get("isError") or result.get("ok") is not True
                or data.get("canonicalJql") != canonical_jql):
            continue
        snapshot, start, total = data.get("snapshotId"), data.get("startAt"), data.get("total")
        if (not isinstance(snapshot, str) or not snapshot or type(start) is not int or start < 0
                or type(total) is not int or total < 0):
            latest = None
            continue
        latest = snapshot
        if start == 0 and snapshot not in groups:
            groups[snapshot] = {"rows": {}, "total": total, "valid": True, "ended": False}
        group = groups.setdefault(snapshot, {"rows": {}, "total": total, "valid": False, "ended": False})
        rows, cache = data.get("issues"), result.get("cache") or {}
        valid = (isinstance(rows, list) and result.get("partial") is False and result.get("missing") == 0
                 and cache.get("state") == "fresh" and not cache.get("invalidated")
                 and group["total"] == total)
        if not valid:
            group["valid"] = False
            continue
        end = start + len(rows)
        group["valid"] &= (end <= total and data.get("returned") == len(rows)
            and type(data.get("hasMore")) is bool
            and ((data["hasMore"] and bool(rows) and data.get("nextStartAt") == end and end < total)
                 or (data["hasMore"] is False and data.get("nextStartAt") is None and end == total)))
        for index, row in enumerate(rows, start):
            key = row.get("key") if isinstance(row, dict) else None
            if not isinstance(key, str) or not key or index in group["rows"] and group["rows"][index] != key:
                group["valid"] = False
            group["rows"][index] = key
        group["ended"] |= data.get("hasMore") is False
    group = groups.get(latest)
    if (not group or not group["valid"] or not group["ended"]
            or set(group["rows"]) != set(range(group["total"]))):
        return None
    keys = set(group["rows"].values())
    return keys if len(keys) == group["total"] else None


def _nodes(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nodes(item)


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class RecoveryController:
    def __init__(self, scenario):
        if scenario not in RECOVERY_SCENARIOS:
            raise ValueError("Invalid recovery scenario")
        self.scenario, self.stop, self.trigger = scenario, Event(), None
        self.files, self.progress_written = None, False
        self.observed = []

    def _trigger(self, reason, path=None):
        if self.trigger is None:
            self.trigger = {"scenario": self.scenario, "reason": reason, "path": path}
            self.stop.set()

    def after_write(self, path, content):
        try:
            data = json.loads(content)
        except ValueError:
            return
        is_progress = path.rsplit("/", 1)[-1].startswith("progress")
        if not self.files or not (path.startswith("evidence/") or is_progress):
            return
        saved = persisted_calls(self.files, self.observed)
        if self.scenario == "record-chunk" and path.startswith("evidence/") and any(
                item.get("recordJson") and item.get("nextRecordOffset") is not None
                for call in saved for item in call["result"]["data"].get("items", [])):
            self._trigger("observed record fragment saved", path)
        if not is_progress or not saved:
            return
        nodes = [node for node in _nodes(data) if isinstance(node, dict)]
        page_checkpoint, snapshot_checkpoint, completed_checkpoint = False, False, False
        for call in saved:
            page = call["result"]["data"]
            for node in nodes:
                # Clients may save nextCall.arguments, nextArguments, or a direct
                # argument object. Walked children retain the actual source cursor.
                arguments = node.get("arguments") if isinstance(node.get("arguments"), dict) else node
                if call["name"] == "search_issues" and arguments.get("jql") != call["arguments"].get("jql"):
                    continue
                key = node.get("key", arguments.get("key"))
                kind = node.get("kind", arguments.get("kind"))
                if call["name"] == "get_evidence_page" and (
                        not key or not kind or key != page.get("key") or kind != page.get("kind")):
                    continue
                if call["name"] == "search_issues" and (key is not None or kind is not None):
                    continue
                snapshot = node.get("snapshotId", arguments.get("snapshot_id"))
                if call["name"] == "search_issues" and page.get("snapshotId") and snapshot != page["snapshotId"]:
                    continue
                if snapshot is not None and snapshot != page.get("snapshotId"):
                    continue
                continuation = node.get("nextStartAt", arguments.get("start_at"))
                has_fragment = any(item.get("nextRecordOffset") is not None for item in page.get("items", []))
                page_checkpoint |= (type(continuation) is int and continuation == page.get("nextStartAt")
                                    and page.get("hasMore") is True and not has_fragment)
                snapshot_checkpoint |= bool(snapshot and snapshot == page.get("snapshotId"))
                completed_checkpoint |= (node.get("status") == "complete" and key == page.get("key")
                                         and page.get("hasMore") is False)
        self.progress_written |= page_checkpoint or snapshot_checkpoint or completed_checkpoint
        if self.scenario == "between-pages" and page_checkpoint:
            self._trigger("page checkpoint saved", path)
        elif self.scenario == "expired-snapshot" and snapshot_checkpoint:
            self._trigger("snapshot checkpoint saved", path)
        elif self.scenario in {"corrupt-progress", "source-changed"} and self.progress_written:
            self._trigger("checkpoint saved before fault", path)

    def observe_ltm(self, name, arguments, result):
        if name not in {"search_issues", "get_evidence_page"} or result.get("ok") is not True:
            return
        self.observed.append({"name": name, "arguments": arguments, "result": result})
        data = result.get("data") or {}
        if (self.scenario == "after-response" and self.progress_written and name == "get_evidence_page"
                and arguments.get("start_at", 0) > 0 and data.get("startAt") == arguments.get("start_at")
                and data.get("key") == arguments.get("key")):
            self._trigger("upstream read completed before next checkpoint")


def persisted_calls(files, calls):
    """Only genuine successful responses actually saved before interruption may be reused."""
    hashes = set()
    for item in files.list_files()["files"]:
        if not item["path"].startswith("evidence/"):
            continue
        target = files._path(item["path"])
        text = target.read_text(encoding="utf-8")
        parts = text.splitlines() if target.suffix == ".jsonl" else [text]
        for part in parts:
            try:
                data = json.loads(part)
            except ValueError:
                continue
            hashes.update(_digest(node) for node in _nodes(data) if isinstance(node, (dict, list)))
    return [row for row in calls if not row.get("isError") and (row.get("result") or {}).get("ok") is True
            and any(_digest(value) in hashes for value in (row["result"], row["result"].get("data")))]
