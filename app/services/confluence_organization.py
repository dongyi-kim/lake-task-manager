"""Same-space structure commands; no copy, cross-space move or permanent purge."""

import hashlib
import json
from copy import deepcopy

from app.auth.base import MutationOutcomeUnknown, UpstreamUnavailable
from app.jira.mutation_recovery import FOUND, UNKNOWN, Reconciliation
from app.services.ticket_commands import CommandError


def reject(message, status=400):
    error = CommandError(message, status)
    error.service = "confluence"
    raise error


def _parent(page):
    ancestors = page.get("ancestors") or []
    return str(ancestors[-1]["id"]) if ancestors else ""


class ConfluenceOrganization:
    def __init__(self, commands):
        self.commands = commands

    def _target_path(self, parent, projected, moving_id):
        parents, names = {}, {}

        def learn(page):
            previous = ""
            for row in [*(page.get("ancestors") or []), page]:
                key = str(row["id"])
                parents.setdefault(key, previous)
                names[key] = row.get("title") or key
                previous = key

        learn(parent)
        parents.update({str(key): _parent(value) for key, value in projected.items()})
        cursor, seen, path = str(parent["id"]), set(), []
        while cursor:
            if cursor == str(moving_id) or cursor in seen:
                reject("자기 자신이나 하위 문서로 이동할 수 없습니다.")
            seen.add(cursor)
            if cursor not in parents:
                learn(self.commands.content(cursor))
            path.append({"id": cursor, "title": names.get(cursor, cursor)})
            cursor = parents[cursor]
        return list(reversed(path))

    def _move_state(self, page_id, parent_id, expected_version, position, projected=None):
        if position is not None and (isinstance(position, bool) or position < -1):
            reject("position은 0 이상의 순서 또는 -1(명시 순서 해제)이어야 합니다.")
        projected = projected or {}
        current = deepcopy(projected[str(page_id)]) if str(page_id) in projected else self.commands.content(page_id, expected_version)
        if current.get("status") != "current" or current["version"]["number"] != expected_version:
            reject("문서 상태 또는 예상 버전이 현재 작업 순서와 일치하지 않습니다.", 409)
        if current.get("type") != "page":
            reject("문서 페이지만 이동할 수 있습니다.")
        if parent_id:
            parent = deepcopy(projected[str(parent_id)]) if str(parent_id) in projected else self.commands.content(parent_id)
            if parent.get("status") != "current":
                reject("휴지통의 문서를 상위로 지정할 수 없습니다.", 409)
            if parent.get("type") != "page":
                reject("상위 문서는 페이지여야 합니다.")
            if (parent.get("space") or {}).get("key") != (current.get("space") or {}).get("key"):
                reject("Space 간 이동은 지원하지 않습니다.")
            current["_targetAncestors"] = self._target_path(parent, projected, page_id)
        else:
            current["_targetAncestors"] = []
        return current

    def move(self, page_id, parent_id, expected_version, mutation_id, position=None):
        payload = dict(page_id=str(page_id), parent_id=str(parent_id),
                       expected_version=expected_version, position=position)

        def completed(raw):
            if (not isinstance(raw, dict) or str(raw.get("id")) != str(page_id)
                    or (raw.get("version") or {}).get("number") != expected_version + 1):
                raise UpstreamUnavailable("이동 응답의 문서·버전을 확인하지 못했습니다.")
            return {"id": str(page_id), "version": expected_version + 1, "parentId": str(parent_id),
                    "position": position, "draftState": "unknown", "verified": False}

        def write():
            self.commands.require_compatible("move_page")
            current = self._move_state(page_id, parent_id, expected_version, position)
            body = {"type": "page", "title": current["title"], "status": "current",
                    "version": {"number": expected_version + 1, "message": "LTM request " + mutation_id},
                    "ancestors": [{"id": str(parent_id)}] if parent_id else []}
            if position is not None:
                body["position"] = position
            result = self.commands.request("PUT", f"/rest/api/content/{page_id}", body=body,
                                           params={"conflictPolicy": "abort", "asyncReconciliation": "false"})
            response = {**completed(result), "previousParentId": _parent(current),
                        "restrictionInheritanceMayChange": _parent(current) != str(parent_id)}
            return response

        def reconcile(receipt):
            try:
                actual = self.commands.request("GET", f"/rest/api/content/{page_id}",
                    params={"version": expected_version + 1, "expand": "version,space"})
                if (actual.get("version") or {}).get("message") == "LTM request " + mutation_id:
                    return Reconciliation(FOUND, completed(actual))
            except Exception:
                pass
            return Reconciliation(UNKNOWN)

        result = dict(self.commands.execute(kind="move_page", mutation_id=mutation_id, payload=payload,
                          write=write, reconcile=reconcile, recover_inflight=completed,
                          affected_ids=(str(page_id), str(parent_id)), invalidate_permissions=True))
        def verify():
            actual = self.commands.content(page_id)
            actual_position = (actual.get("extensions") or {}).get("position", actual.get("position"))
            if not (_parent(actual) == str(parent_id) and (position is None
                    or actual_position == position or position == -1 and actual_position is None)):
                reject("이동 후 문서 위치를 확인하지 못했습니다. 같은 요청으로 다시 확인하세요.", 409)
            return {"verified": True, "verificationPending": False,
                    "actualParentId": _parent(actual), "actualPosition": actual_position}

        # Persist successful observation separately from the committed PUT. Later batch
        # steps may intentionally move this page again; replay must retain that earlier
        # success. Failed verification never becomes a success receipt and is retried.
        try:
            result.update(self.commands.execute(kind="move_page_verification", mutation_id=mutation_id,
                payload=payload, write=verify, reconcile=lambda receipt: Reconciliation(FOUND, verify()),
                invalidate_result=False))
        except Exception:
            result["verificationPending"] = True
            result["verified"] = False
        return result

    def _check_leaf(self, page_id, projected=None):
        rows, seen, start, total = [], set(), 0, None
        while True:
            children = self.commands.request("GET", f"/rest/api/content/{page_id}/child/page",
                params={"start": start, "limit": 100 if projected else 1})
            if (not isinstance(children, dict) or not isinstance(children.get("results"), list)
                    or children.get("partial") or children.get("complete") is False
                    or children.get("error") or children.get("errors")):
                reject("하위 문서 목록을 완전히 확인하지 못했습니다. 삭제하지 않았습니다.", 503)
            page = children["results"]
            count = children.get("totalSize")
            if (any(not isinstance(row, dict) or not str(row.get("id", "")).isdigit() for row in page)
                    or children.get("start", start) != start or children.get("size", len(page)) != len(page)
                    or count is not None and (type(count) is not int or count < start + len(page)
                                              or total is not None and count != total)):
                reject("하위 문서 목록의 페이지 구간을 확인하지 못했습니다. 삭제하지 않았습니다.", 503)
            ids = [str(row["id"]) for row in page]
            if seen.intersection(ids) or len(ids) != len(set(ids)):
                reject("하위 문서 목록이 반복되거나 변경되었습니다. 다시 조회하세요.", 503)
            seen.update(ids)
            rows.extend(page)
            start += len(page)
            total = count if count is not None else total
            more = bool((children.get("_links") or {}).get("next")) or total is not None and start < total
            if not projected:
                if rows or more:
                    reject("하위 문서가 있습니다. 먼저 이동하거나 하위 문서부터 별도로 휴지통에 넣으세요.", 409)
                return
            if not more:
                break
            if not page:
                reject("하위 문서 목록의 다음 구간을 확인하지 못했습니다. 삭제하지 않았습니다.", 503)
        if projected:
            rows = [projected.get(str(row["id"]), row) for row in rows]
            rows = [row for row in rows if str(row.get("id")) not in projected
                    or (row.get("status") == "current" and _parent(row) == str(page_id))]
            rows += [row for key, row in projected.items() if row.get("status") == "current"
                     and _parent(row) == str(page_id) and not any(str(r.get("id")) == key for r in rows)]
        if rows:
            reject("하위 문서가 있습니다. 먼저 이동하거나 하위 문서부터 별도로 휴지통에 넣으세요.", 409)

    def trash(self, page_id, expected_version, mutation_id):
        def completed(raw):
            if raw is not None and raw != {} and raw not in (200, 202, 204):
                raise UpstreamUnavailable("휴지통 응답을 확인하지 못했습니다.")
            return {"id": str(page_id), "status": "trashed", "version": expected_version, "permanent": False}

        def write():
            current = self.commands.content(page_id, expected_version)
            if current.get("type") != "page":
                reject("페이지 휴지통 기능입니다. 댓글·첨부 삭제 도구를 사용하세요.")
            self._check_leaf(page_id)
            return completed(self.commands.request("DELETE", f"/rest/api/content/{page_id}"))
        return self.commands.execute(kind="trash_page", mutation_id=mutation_id,
                                     payload={"page_id": str(page_id), "expected_version": expected_version},
                                     write=write, recover_inflight=completed, affected_ids=(str(page_id),), invalidate_permissions=True)

    def restore(self, page_id, expected_version, mutation_id):
        def completed(raw):
            if (not isinstance(raw, dict) or str(raw.get("id")) != str(page_id)
                    or (raw.get("version") or {}).get("number") != expected_version + 1):
                raise UpstreamUnavailable("복원 응답의 문서·버전을 확인하지 못했습니다.")
            return {"id": str(page_id), "status": "current", "version": expected_version + 1}

        def write():
            current = self.commands.content(page_id, expected_version, status="trashed")
            if current.get("type") != "page":
                reject("페이지 복원만 지원합니다.")
            result = self.commands.request("PUT", f"/rest/api/content/{page_id}",
                body={"type": "page", "title": current["title"], "status": "current",
                      "version": {"number": expected_version + 1}}, params={"status": "trashed"})
            return completed(result)
        return self.commands.execute(kind="restore_page", mutation_id=mutation_id,
                                     payload={"page_id": str(page_id), "expected_version": expected_version},
                                     write=write, recover_inflight=completed, affected_ids=(str(page_id),), invalidate_permissions=True)

    def restrictions(self, page_id):
        self.commands.content(page_id)
        data = self.commands.request("GET", f"/rest/api/content/{page_id}/restriction/byOperation",
                                     params={"expand": "read.restrictions.user,read.restrictions.group,update.restrictions.user,update.restrictions.group"})
        if not isinstance(data, dict) or not ("results" in data or {"read", "update"} & data.keys()):
            reject("페이지 제한 응답이 불완전합니다.", 503)
        rows = data.get("results") if isinstance(data.get("results"), list) else [
            value for key, value in data.items() if key in {"read", "update"} and isinstance(value, dict)]
        restrictions, complete = [], not bool(data.get("partial") or data.get("complete") is False
                                              or data.get("error") or data.get("errors")
                                              or (data.get("_links") or {}).get("next"))
        if not isinstance(rows, list) or data.get("totalSize", len(rows)) > len(rows):
            complete = False
        for row in rows:
            if not isinstance(row, dict) or row.get("operation") not in {"read", "update"}:
                reject("페이지 제한 응답의 작업 종류가 올바르지 않습니다.", 503)
            subjects = row.get("restrictions") or {}
            if not isinstance(subjects, dict) or not {"user", "group"}.issubset(subjects):
                complete = False
            users, groups = subjects.get("user") or {}, subjects.get("group") or {}
            for collection in (users, groups):
                if isinstance(collection, dict) and ((collection.get("_links") or {}).get("next")
                        or collection.get("totalSize", len(collection.get("results") or [])) > len(collection.get("results") or [])):
                    complete = False
            user_rows = users if isinstance(users, list) else users.get("results") or []
            group_rows = groups if isinstance(groups, list) else groups.get("results") or []
            if any(not isinstance(u, dict) or not u.get("userKey") for u in user_rows) or any(
                    not isinstance(g, dict) or not g.get("name") for g in group_rows):
                complete = False
            restrictions.append({"operation": row.get("operation"),
                "users": sorted(str(u["userKey"]) for u in user_rows if isinstance(u, dict) and u.get("userKey")),
                "groups": sorted(str(g["name"]) for g in group_rows if isinstance(g, dict) and g.get("name"))})
        if len({r["operation"] for r in restrictions}) != len(restrictions):
            complete = False
        restrictions.sort(key=lambda row: row["operation"])
        fingerprint = hashlib.sha256(json.dumps(restrictions, sort_keys=True).encode()).hexdigest()
        return {"id": str(page_id), "restrictions": restrictions, "complete": complete,
                "fingerprint": fingerprint if complete else None, "inheritedRestrictionsIncluded": False}

    def set_restrictions(self, page_id, restrictions, expected_fingerprint, mutation_id):
        operations = [row["operation"] for row in restrictions]
        if not restrictions or len(operations) != len(set(operations)) or set(operations) - {"read", "update"}:
            reject("read/update 제한을 중복 없이 명시하세요.")

        def completed(raw):
            if not isinstance(raw, dict) or raw.get("error") or raw.get("errors"):
                raise UpstreamUnavailable("접근 제한 변경 응답을 확인하지 못했습니다.")
            return {"id": str(page_id), "operations": operations, "inheritedRestrictionsUnchanged": True,
                    "concurrencyProtection": "preflight_only"}

        def write():
            current = self.restrictions(page_id)
            if not current["complete"] or current["fingerprint"] != expected_fingerprint:
                reject("페이지 제한이 변경됐거나 전체 목록을 확인하지 못했습니다. 다시 조회하세요.", 409)
            body = [{"operation": row["operation"], "restrictions": {
                "user": [{"type": "known", "userKey": user} for user in row["users"]],
                "group": [{"type": "group", "name": group} for group in row["groups"]]}}
                for row in restrictions]
            return completed(self.commands.request("PUT", f"/rest/api/content/{page_id}/restriction", body=body))
        return self.commands.execute(kind="set_restrictions", mutation_id=mutation_id,
            payload={"page_id": str(page_id), "restrictions": restrictions, "expected_fingerprint": expected_fingerprint},
            write=write, recover_inflight=completed, affected_ids=(str(page_id),), invalidate_permissions=True)

    def bulk(self, operations, mutation_id, dry_run=True):
        """Ordered independent receipts; unknown/dependent work is never blindly repeated."""
        if not operations or len(operations) > 100:
            reject("1~100개의 문서 작업이 필요합니다.")
        seen = set()
        for operation in operations:
            key = operation["operation_id"]
            if operation["action"] not in {"move", "update", "trash", "restore"}:
                reject("지원하지 않는 문서 정리 작업입니다.")
            if key in seen or not set(operation.get("depends_on") or []).issubset(seen):
                reject("작업 ID는 고유해야 하며 의존 작업은 앞에 위치해야 합니다.")
            seen.add(key)
        if not dry_run:
            # Pin the entire request as well as each item. Removing/changing successful
            # rows under the same batch ID must not silently become a different batch.
            self.commands.execute(kind="organization_manifest", mutation_id=mutation_id,
                payload={"operations": operations}, write=lambda: {"accepted": True}, invalidate_result=False)
        results, successful, failed, pending, projected = [], set(), [], [], {}
        for index, op in enumerate(operations):
            key, action = op["operation_id"], op["action"]
            if set(op.get("depends_on") or []) - successful:
                row = {"operationId": key, "status": "skipped", "reason": "dependency_not_completed"}
                results.append(row)
                pending.append(row)
                continue
            child_id = "confbulk:" + hashlib.sha256(f"{mutation_id}:{key}".encode()).hexdigest()
            try:
                if dry_run:
                    if action == "move":
                        current = self._move_state(op["page_id"], op.get("parent_id", ""), op["expected_version"], op.get("position"), projected)
                        current["ancestors"] = current.pop("_targetAncestors")
                    else:
                        current = deepcopy(projected[op["page_id"]]) if op["page_id"] in projected else self.commands.content(
                            op["page_id"], op["expected_version"], status="trashed" if action == "restore" else "current")
                        if current["version"]["number"] != op["expected_version"] or current.get("status") != ("trashed" if action == "restore" else "current"):
                            reject("예상 문서 상태·버전이 앞선 작업 결과와 일치하지 않습니다.", 409)
                        if current.get("type") != "page":
                            reject("문서 페이지 정리 작업입니다.")
                        if action == "update":
                            from app.services.confluence_commands import storage_value
                            if op.get("title") is not None and not op["title"].strip():
                                reject("문서 제목은 비울 수 없습니다.")
                            if op.get("body") is None and op.get("title") is None:
                                reject("변경할 본문 또는 제목이 필요합니다.")
                            if op.get("body") is not None:
                                storage_value(op["body"], op.get("format", "storage"))
                        if action == "trash":
                            self._check_leaf(op["page_id"], projected)
                    current["version"] = {"number": op["expected_version"] + (0 if action == "trash" else 1)}
                    current["status"] = "trashed" if action == "trash" else "current"
                    projected[op["page_id"]] = current
                    value = {"writeExecuted": False, "permissionChecked": "read_only",
                             "version": op["expected_version"], "draftState": "unknown"}
                elif action == "move":
                    value = self.move(op["page_id"], op.get("parent_id", ""), op["expected_version"], child_id, op.get("position"))
                elif action == "trash":
                    value = self.trash(op["page_id"], op["expected_version"], child_id)
                elif action == "restore":
                    value = self.restore(op["page_id"], op["expected_version"], child_id)
                elif action == "update":
                    value = self.commands.update_page(page_id=op["page_id"], expected_version=op["expected_version"],
                        mutation_id=child_id, title=op.get("title"), body=op.get("body"), format=op.get("format", "storage"))
                else:
                    reject("지원하지 않는 문서 정리 작업입니다.")
                verified = value.get("verified", True)
                row = {"operationId": key, "status": "validated" if dry_run else "completed" if verified else "verification_pending",
                       "result": value}
                if verified:
                    successful.add(key)
                else:
                    pending.append(row)
            except Exception as exc:
                from app.local_mcp.confluence_errors import confluence_error
                error = confluence_error(exc)
                row = {**{k: v for k, v in error.items() if k != "status"}, "operationId": key,
                       "status": "outcome_unknown" if isinstance(exc, MutationOutcomeUnknown) else "failed",
                       "upstreamStatus": error.get("status")}
                failed.append(row)
            results.append(row)
        return {"ok": not failed and not pending, "dryRun": dry_run, "results": results,
                "updated": [row for row in results if row["status"] == "completed"],
                "failed": failed, "pending": pending, "partial": bool(successful) and bool(failed or pending)}
