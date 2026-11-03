"""Validated local WBS/people commands shared by the settings UI and MCP.

Only the two existing config files can be written. Jira data, manager membership,
connection settings and arbitrary paths are deliberately outside this service.
"""
import copy
import hashlib
import os
import re
import tempfile
import threading
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth.base import SessionExpired, UpstreamUnavailable
from app.infra import settings as config
from app.services.ticket_commands import CommandError

_LOCK = threading.RLock()


def _text(value, title, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f"{title}: 비어 있지 않은 {maximum}자 이하 문자열이 필요합니다.")
    return value.strip()


class EpicWeight(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str
    weight: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("key")
    @classmethod
    def ticket_key(cls, value):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+", value):
            raise ValueError("Epic 키 형식이 올바르지 않습니다.")
        return value.upper()


class WbsTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    module: str
    name: str
    start: str
    end: str
    epics: list[EpicWeight] = Field(min_length=1, max_length=200)

    @field_validator("id", "module", "name")
    @classmethod
    def text(cls, value, info):
        return _text(value, info.field_name, 500 if info.field_name == "name" else 200)

    @field_validator("start", "end")
    @classmethod
    def iso_date(cls, value):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("일정은 YYYY-MM-DD 형식이어야 합니다.")
        return date.fromisoformat(value).isoformat()

    @model_validator(mode="after")
    def valid_plan_item(self):
        if self.start >= self.end:
            raise ValueError("시작일은 종료일보다 앞서야 합니다.")
        if len({e.key for e in self.epics}) != len(self.epics):
            raise ValueError("한 WBS Task에 같은 Epic을 중복 등록할 수 없습니다.")
        if sum(e.weight for e in self.epics) <= 0:
            raise ValueError("Epic 상대 가중치 합은 양수여야 합니다.")
        return self


class WbsTaskChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    module: str | None = None
    name: str | None = None
    start: str | None = None
    end: str | None = None
    epics: list[EpicWeight] | None = None


def validate_people(people):
    if not isinstance(people, dict) or len(people) > 500:
        raise ValueError("인력은 모듈별 Jira 사용자 ID 목록이어야 합니다 (모듈 최대 500개).")
    cleaned = {}
    for module, ids in people.items():
        name = _text(module, "모듈")
        if name in cleaned or not isinstance(ids, list) or len(ids) > 2000:
            raise ValueError("모듈명이 중복되거나 사용자 목록이 올바르지 않습니다.")
        users = [_text(uid, "Jira 사용자 ID", 255) for uid in ids]
        if any(re.search(r"\s", uid) for uid in users) or len({uid.casefold() for uid in users}) != len(users):
            raise ValueError("한 모듈 안의 Jira 사용자 ID는 공백 없이 중복되지 않아야 합니다.")
        cleaned[name] = users
    return cleaned


def _atomic_write(path, data, *, staging=None):
    """Temporary files stay under .cache on the config file's filesystem."""
    staging = staging or path.parent / ".cache" / "workspace-config"
    staging.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=staging)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class WorkspaceConfigCommands:
    def __init__(self, get_client, require_manager, config_dir=None):
        self.get_client, self.require_manager = get_client, require_manager
        self.config_dir = Path(config_dir) if config_dir is not None else None

    def _authorize(self):
        client = self.get_client()
        try:
            user = client.current_user_for_authorization() or {}
        except UpstreamUnavailable as exc:
            # An unreachable server is not proof of logout, but stale identity cannot authorize.
            raise CommandError("현재 사용자 권한을 확인할 수 없습니다. 연결 복구 후 다시 시도해 주세요.", 401) from exc
        if not (user.get("id") or user.get("name")):
            raise SessionExpired("설정을 변경하려면 Jira 사용자 인증을 확인해 주세요.")
        self.require_manager()
        return client

    def _paths(self):
        directory = self.config_dir or config.CONFIG_DIR
        return directory / "wbs_config.yaml", directory / "people.yaml"

    def revision(self):
        """Cheap status polling only: no Jira/auth probe and no configuration disclosure."""
        with _LOCK:
            try:
                return hashlib.sha256(b"\0".join(path.read_bytes() for path in self._paths())).hexdigest()
            except OSError:
                return ""

    def _read(self):
        blobs = [path.read_bytes() for path in self._paths()]
        raw, people = [yaml.safe_load(blob) or {} for blob in blobs]
        plan = config._normalize_wbs(raw)
        config.validate_plan(plan)
        state = {"revision": hashlib.sha256(b"\0".join(blobs)).hexdigest(),
                 "projectKey": plan["project_key"], "tasks": [], "people": validate_people(people)}
        for task in plan["wbs"]:
            state["tasks"].append({**task, "start": str(task["start"]), "end": str(task["end"])})
        return state, raw, blobs

    def read(self):
        self._authorize()
        with _LOCK:
            return self._read()[0]

    def _wbs_raw(self, raw, tasks):
        if not isinstance(tasks, list) or len(tasks) > 2000:
            raise ValueError("WBS Task는 최대 2000개까지 등록할 수 있습니다.")
        rows = [WbsTask.model_validate(task).model_dump() for task in tasks]
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError("WBS Task ID가 중복되었습니다.")
        result = copy.deepcopy(raw)
        groups = {group["module"]: copy.deepcopy(group) for group in raw.get("wbs", [])}
        previous = {}
        for group in raw.get("wbs", []):
            for i, task in enumerate(group.get("tasks") or [], 1):
                previous[task.get("id") or f"{group['module']}-{i}"] = task
        for group in groups.values():
            group["tasks"] = []
        for row in rows:
            module = row["module"]
            group = groups.setdefault(module, {"module": module, "tasks": []})
            original = copy.deepcopy(previous.get(row["id"], {}))
            epics = {e["ticket"]: e for e in original.get("epics", [])}
            original.update({k: v for k, v in row.items() if k not in {"module", "epics"}})
            original["epics"] = [{**epics.get(e["key"], {}), "ticket": e["key"], "weight": e["weight"]}
                                 for e in row["epics"]]
            group["tasks"].append(original)
        result["wbs"] = list(groups.values())
        config.validate_plan(config._normalize_wbs(result))
        return result

    def save(self, kind, revision, value):
        client = self._authorize()
        if kind not in {"wbs", "people"}:
            raise ValueError("지원하지 않는 설정 종류입니다.")
        with _LOCK:
            return self._save_locked(client, kind, revision, value)

    def _save_locked(self, client, kind, revision, value):
        state, raw, blobs = self._read()
        desired = self._wbs_raw(raw, value) if kind == "wbs" else validate_people(value)
        existing = raw if kind == "wbs" else state["people"]
        # A lost response can be retried with the old revision without rewriting anything.
        if desired == existing:
            return state
        if revision != state["revision"]:
            raise CommandError("다른 화면에서 설정이 변경되었습니다. 최신 설정을 불러온 후 다시 저장하세요.", 409)
        target = self._paths()[0 if kind == "wbs" else 1]
        data = yaml.safe_dump(desired, allow_unicode=True, sort_keys=False).encode("utf-8")
        try:
            # Keep one previous version, never grow a history or write caller-selected paths.
            backup = target.parent / ".cache" / "workspace-config" / (target.name + ".bak")
            backup.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(backup, blobs[0 if kind == "wbs" else 1], staging=backup.parent)
            _atomic_write(target, data)
        except OSError as exc:
            raise CommandError("설정 파일을 저장하지 못했습니다. 파일 권한과 사용 가능한 공간을 확인해 주세요.", 500) from exc
        config.reload_people()
        kinds = ("wbs_build:", "vit_build:", "vit_list:")
        if kind == "people":
            kinds += ("workload:", "workload_bucket:", "mytasks:", "mt:", "mentionctx:", "mentiondef:")
        for prefix in kinds:
            client.cache.invalidate(prefix)
        return self._read()[0]

    def create_task(self, task, revision):
        client = self._authorize()
        with _LOCK:
            state = self._read()[0]
            rows = state["tasks"]
            existing = next((row for row in rows if row["id"] == task["id"]), None)
            if existing is not None:
                if existing == task:
                    return state
                raise CommandError("이미 사용 중인 WBS Task ID입니다.", 409)
            return self._save_locked(client, "wbs", revision, [*rows, task])

    def update_task(self, task_id, changes, revision):
        client = self._authorize()
        with _LOCK:
            state = self._read()[0]
            if not any(row["id"] == task_id for row in state["tasks"]):
                raise CommandError("WBS Task를 찾을 수 없습니다.", 404)
            rows = [{**row, **changes} if row["id"] == task_id else row for row in state["tasks"]]
            return self._save_locked(client, "wbs", revision, rows)

    def delete_task(self, task_id, revision):
        client = self._authorize()
        with _LOCK:
            state = self._read()[0]
            return self._save_locked(client, "wbs", revision, [row for row in state["tasks"] if row["id"] != task_id])

    def module_people(self, module, users, revision, *, rename=None, remove=False):
        client = self._authorize()
        with _LOCK:
            state = self._read()[0]
            people = dict(state["people"])
            module = _text(module, "모듈")
            if rename is not None:
                rename = _text(rename, "새 모듈명")
                if module not in people or rename in people:
                    raise CommandError("기존 모듈이 없거나 새 모듈명이 이미 사용 중입니다.", 409)
                people = {rename if name == module else name: ids for name, ids in people.items()}
            elif remove:
                people.pop(module, None)
            else:
                people[module] = users
            return self._save_locked(client, "people", revision, people)
