"""Task/SubTask changes through Jira's native wizard, with shared mutation receipts."""

import threading
import hashlib
import json
from contextlib import contextmanager

from app.auth.base import MutationOutcomeUnknown, MutationPreflightBusy, UpstreamUnavailable, write_upstream
from app.domain.ticket_actions import is_done, issue_tier
from app.jira.cache_policy import MutationEvent
from app.jira.hierarchy_forms import HierarchyFormError, HierarchyForms
from app.jira.mutation_recovery import FOUND, UNKNOWN, Reconciliation
from app.services.ticket_commands import CommandError, _preflight_read


_LOCK_INIT = threading.Lock()


@contextmanager
def _wizard_session(client):
    # Jira's MoveIssueBean belongs to the session, not to an issue or MCP request.
    with _LOCK_INIT:
        if not hasattr(client, "_hierarchy_wizard_lock"):
            client._hierarchy_wizard_lock = threading.Lock()
        lock = client._hierarchy_wizard_lock
    if not lock.acquire(blocking=False):
        raise MutationPreflightBusy("다른 Jira 계층 변경이 진행 중입니다. 같은 요청으로 다시 시도하세요.")
    try:
        yield
    finally:
        lock.release()


class HierarchyCommands:
    def __init__(self, commands):
        self.commands = commands

    @property
    def client(self):
        return self.commands.client

    def _read(self, client, key):
        # The wizard acts on current Jira state. A 24-hour Done display cache cannot authorize it.
        raw = client.provider.get_json(f"/rest/api/2/issue/{key}", params={
            "fields": "issuetype,status,parent,subtasks,project,updated,security," + client.s.epic_link_field_id})
        fields = (raw or {}).get("fields") or {}
        if str((raw or {}).get("key") or "").upper() != key.upper():
            raise CommandError("티켓의 현재 키가 요청한 키와 다릅니다. 이동된 티켓의 현재 키와 조회 범위를 다시 확인하세요.", 409)
        if not str((raw or {}).get("id") or "").isdigit() or not fields.get("status") or not fields.get("issuetype"):
            raise UpstreamUnavailable("Jira 계층 변경에 필요한 티켓 정보가 불완전합니다.")
        return raw

    @staticmethod
    def _tier(raw):
        kind = raw["fields"]["issuetype"]
        return issue_tier(kind.get("name"), kind.get("subtask"))

    def _validate(self, client, key, action, parent_key):
        raw = self._read(client, key)
        if is_done(raw):
            raise CommandError("완료 티켓은 먼저 사용자가 요청한 재열기 전이를 실행한 뒤 계층을 변경하세요.", 409)
        tier = self._tier(raw)
        expected = "task" if action == "convert_to_subtask" else "subtask"
        if tier != expected:
            raise CommandError(f"{action}은 {expected} 티켓에만 사용할 수 있습니다.")
        if action == "convert_to_subtask" and raw["fields"].get("subtasks"):
            raise CommandError("하위 SubTask가 있는 Task는 SubTask로 변환할 수 없습니다.", 409)
        if action in ("convert_to_subtask", "change_parent"):
            if not parent_key or parent_key.upper() == key.upper():
                raise CommandError("자기 자신이 아닌 새 Task 부모 키가 필요합니다.")
            parent = self._read(client, parent_key)
            if self._tier(parent) != "task":
                raise CommandError("새 부모는 Epic/SubTask가 아닌 Task-tier여야 합니다.")
            source_project = raw["fields"].get("project", {}).get("id")
            target_project = parent["fields"].get("project", {}).get("id")
            if not source_project or not target_project or source_project != target_project:
                raise CommandError("같은 Jira 프로젝트의 부모만 선택할 수 있습니다.")
            if (raw["fields"].get("parent") or {}).get("key", "").upper() == parent_key.upper():
                raise CommandError("이미 해당 Task의 SubTask입니다.")
            raw = {**raw, "_hierarchyTarget": parent}
        elif parent_key:
            raise CommandError("Task로 변환할 때 parent_key는 지정하지 않습니다.")
        return raw

    def options(self, key, change):
        client = self.client
        with _wizard_session(client), write_upstream():
            raw = self._validate(client, key, change["action"], change.get("parent_key", ""))
            self._validate_type(client, change)
            prepared = HierarchyForms(client.provider, client.s.jira_base).prepare(
                change["action"], str(raw["id"]), **{k: v for k, v in change.items() if k != "action"})
            return {"key": key, "action": change["action"], **prepared.describe(),
                    "securityLevel": raw["fields"].get("security"),
                    "parentSecurityLevel": (raw.get("_hierarchyTarget") or {}).get("fields", {}).get("security"),
                    "previewHash": self._preview_hash(raw, change, prepared)}

    @staticmethod
    def _preview_hash(raw, change, prepared):
        data = {"source": raw, "change": change, "preview": prepared.describe()}
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _validate_type(client, change):
        identifier = change.get("issue_type_id")
        if not identifier:
            return  # Discovery returns available types without choosing one.
        kind = client.provider.get_json(f"/rest/api/2/issuetype/{identifier}")
        if not isinstance(kind, dict) or str(kind.get("id")) != identifier or not isinstance(kind.get("subtask"), bool):
            raise UpstreamUnavailable("대상 Jira 이슈 타입을 확인하지 못했습니다.")
        tier = issue_tier(kind.get("name"), kind["subtask"])
        expected = "subtask" if change["action"] == "convert_to_subtask" else "task"
        if tier != expected:
            raise CommandError(f"이 작업의 대상은 {expected} 타입이어야 합니다. Epic 변환은 지원하지 않습니다.")

    def _epic(self, client, raw):
        fields = raw["fields"]
        epic = fields.get(client.s.epic_link_field_id)
        parent = (fields.get("parent") or {}).get("key")
        if not epic and parent:
            epic = self._read(client, parent)["fields"].get(client.s.epic_link_field_id)
        return epic

    def execute(self, key, change, mutation_id, preview_hash, acknowledged_removed_fields=()):
        client = self.client
        kind = "hierarchy-change"
        actor, receipt = client._mutation_actor_for(kind, mutation_id)
        context = (receipt or {}).get("context")
        if not context:
            raw = _preflight_read(lambda: self._validate(
                client, key, change["action"], change.get("parent_key", "")))
            fields = raw["fields"]
            context = {"actor": actor, "oldParent": (fields.get("parent") or {}).get("key"),
                       "oldEpic": _preflight_read(lambda: self._epic(client, raw)),
                       "oldType": str(fields["issuetype"].get("id") or "")}

        def result_for(raw):
            fields = raw["fields"]
            parent = (fields.get("parent") or {}).get("key")
            return {"ok": True, "key": key, "action": change["action"],
                    "issueTypeId": str(fields["issuetype"]["id"]), "parentKey": parent,
                    "previousParentKey": context.get("oldParent"),
                    "epicKey": self._epic(client, raw),
                    "previousEpicKey": context.get("oldEpic")}

        def verify():
            raw = self._read(client, key)
            actual_parent = (raw["fields"].get("parent") or {}).get("key", "").upper()
            expected_parent = change.get("parent_key", "").upper()
            expected_type = change.get("issue_type_id", "") or context.get("oldType", "")
            type_matches = not expected_type or str(raw["fields"]["issuetype"].get("id")) == expected_type
            status_matches = (not change.get("target_status_id") or
                              str(raw["fields"]["status"].get("id")) == change["target_status_id"])
            expected_tier = "task" if change["action"] == "convert_to_task" else "subtask"
            if actual_parent == expected_parent and type_matches and status_matches and self._tier(raw) == expected_tier:
                return result_for(raw)
            return None

        def write():
            with _wizard_session(client), write_upstream():
                _preflight_read(lambda: client.assert_mutation_actor_identity(actor))
                raw = _preflight_read(lambda: self._validate(
                    client, key, change["action"], change.get("parent_key", "")))
                _preflight_read(lambda: self._validate_type(client, change))
                provider = client.provider
                engine = HierarchyForms(provider, client.s.jira_base)
                prepared = _preflight_read(lambda: engine.prepare(change["action"], str(raw["id"]),
                    **{k: v for k, v in change.items() if k != "action"}))
                if not prepared.ready:
                    raise CommandError("추가 입력이 필요합니다. get_hierarchy_options로 현재 필수 항목을 확인하세요.")
                if self._preview_hash(raw, change, prepared) != preview_hash:
                    raise CommandError("티켓 또는 변환 영향이 이전 미리보기와 다릅니다. get_hierarchy_options로 다시 확인하세요.", 409)
                if set(prepared.removed_fields) != set(acknowledged_removed_fields):
                    raise CommandError("변환 시 제거되는 필드가 있습니다. get_hierarchy_options의 removed_fields를 "
                                       "사용자가 확인한 뒤 정확히 acknowledged_removed_fields로 전달하세요.", 409)
                _preflight_read(lambda: client.assert_mutation_actor_identity(actor))
                if client.provider is not provider:
                    raise CommandError("Jira 인증 연결이 바뀌었습니다. 같은 요청으로 다시 준비하세요.", 409)
                # From here onward any transport/parsing failure may follow a committed mutation.
                try:
                    engine.commit(prepared, allow_removed_fields=acknowledged_removed_fields)
                except UpstreamUnavailable:
                    raise  # Preserve a pending owner-thread operation for receipt recovery.
                except HierarchyFormError as exc:
                    if exc.code == "jira_rejected":
                        raise  # Explicit Jira validation rejection, not an unknown HTML layout.
                    raise UpstreamUnavailable("Jira 최종 변환 응답을 확인할 수 없습니다.") from exc
                except Exception as exc:
                    # A final login page or unexpected response is not proof of a rejected write.
                    raise UpstreamUnavailable("Jira 최종 제출 결과를 확인할 수 없습니다.") from exc
                try:
                    result = verify()
                except Exception as exc:
                    # Even 401/invalid JSON here happened AFTER the final POST. Keep its receipt.
                    raise UpstreamUnavailable("Jira 제출 후 최종 상태 재조회에 실패했습니다.") from exc
                if result is None:
                    raise MutationOutcomeUnknown(mutation_id, "Jira 계층 변경의 최종 상태를 확인하지 못했습니다.")
                return result

        def reconcile(_receipt):
            # Never replay the final wizard submission after a response loss. Prove the target relation.
            client.assert_mutation_actor_identity(actor)
            if change.get("field_values") or acknowledged_removed_fields:
                # Relation equality alone cannot prove custom-field rewrites/removals after a lost reply.
                return Reconciliation(UNKNOWN)
            result = verify()
            return Reconciliation(FOUND, result) if result else Reconciliation(UNKNOWN)

        def invalidate(result):
            client._invalidate_ticket(key)
            for parent in {context.get("oldParent"), result.get("parentKey")} - {None, ""}:
                client._invalidate_direct_child_views(parent, membership=True)
                client._invalidate_lineage(parent, None)
            for epic in {context.get("oldEpic"), result.get("epicKey")} - {None, ""}:
                client._invalidate_direct_child_views(epic, membership=True)
                client._invalidate_lineage(None, epic)
            client._record_mutation(MutationEvent("fields", key,
                changed_fields=("parent", "issuetype", "status", client.s.epic_link_field_id,
                                *change.get("field_values", {}).keys()),
                parent_key=result.get("parentKey"), epic_key=result.get("epicKey")))

        def recover_inflight(_response):
            if change.get("field_values") or acknowledged_removed_fields:
                raise UpstreamUnavailable("추가 필드를 포함한 변환의 반영 여부를 별도로 확인해야 합니다.")
            result = verify()
            if result is None:
                raise UpstreamUnavailable("Jira 계층 변경 결과를 확인할 수 없습니다.")
            return result

        return client._mutation_receipts.execute(kind=kind, mutation_id=mutation_id,
            payload={"key": key, "change": change, "previewHash": preview_hash,
                     "removedFields": list(acknowledged_removed_fields), "actor": actor},
            write=write, reconcile=reconcile, after_success=invalidate, context=context,
            before_retry=lambda: client.assert_mutation_actor_identity(actor),
            recover_inflight=recover_inflight)
