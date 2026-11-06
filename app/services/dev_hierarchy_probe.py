"""User-started native hierarchy smoke test through the real local MCP SDK.

The private journal is independent of display caches. Persisted tool arguments and
mutation receipts allow a lost response to be reconciled without creating new tickets.
"""

import hashlib
import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import anyio
from mcp import Client

from app.local_mcp.authentication import AuthenticationUnavailable
from app.local_mcp.runtime import write_context_guard
from app.services.ticket_commands import CommandError


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_STEPS = ("create_parent_a", "create_parent_b", "create_subject",
          "convert_to_subtask", "change_parent", "convert_to_task")
_MESSAGES = {
    "authentication_required": "Jira 로그인 후 같은 실행 ID로 다시 시도하세요.",
    "authentication_pending": "인증 확인 중입니다. 같은 실행 ID로 다시 확인하세요.",
    "permission_denied": "현재 Jira 계정에 이 단계의 권한이 없습니다.",
    "actor_changed": "최초 실행 계정과 다릅니다. 원래 Jira 계정으로 돌아오세요.",
    "scope_changed": "최초 실행의 Jira 서버·환경·프로젝트와 다릅니다.",
    "required_input": "추가 필수 입력이 필요하여 자동 테스트를 중단했습니다.",
    "unsafe_preview": "필드 제거 또는 보안 수준 변경이 있어 자동 테스트를 중단했습니다.",
    "outcome_unknown": "반영 여부가 불명확합니다. 같은 실행 ID로 결과를 확인하세요.",
    "conflict": "상태 또는 미리보기가 달라졌습니다. Jira에서 생성된 테스트 티켓을 확인하세요.",
    "unsupported_hierarchy_form": "이 Jira 폼은 자동 처리할 수 없습니다. Jira 화면에서 확인하세요.",
    "jira_rejected": "Jira가 이 단계의 입력·권한·상태를 거절했습니다.",
    "upstream_unavailable": "Jira 연결을 확인하지 못했습니다. 같은 실행 ID로 다시 시도하세요.",
    "rate_limited": "다른 요청이 처리 중입니다. 잠시 후 같은 실행 ID로 다시 시도하세요.",
    "invalid_request": "선택한 타입 또는 요청이 이 작업에 맞지 않습니다.",
    "journal_unavailable": "실행 기록을 저장하지 못했습니다. 새 실행을 만들지 말고 기존 결과를 확인하세요.",
    "response_too_large": "진단 응답이 너무 큽니다. Jira 화면에서 선택 항목을 확인하세요.",
    "internal_error": "테스트를 완료하지 못했습니다. 생성된 티켓과 실행 ID를 보존하세요.",
}


class ProbeError(CommandError):
    def __init__(self, code, status=400, **details):
        self.code = code if code in _MESSAGES else "internal_error"
        self.status, self.details = status, details
        super().__init__(_MESSAGES[self.code], status)

    def public(self):
        return {"code": self.code, "message": str(self),
                "outcomeUnknown": self.code in {"outcome_unknown", "journal_unavailable"},
                "retryable": self.code in {"outcome_unknown", "upstream_unavailable", "rate_limited", "authentication_pending"},
                **({"formDiagnostic": self.details["formDiagnostic"]} if self.details.get("formDiagnostic") else {})}


def _form_diagnostic(error):
    """Keep only bounded structural evidence, including when resuming SDK errors."""
    value = error.get("formDiagnostic")
    if not isinstance(value, dict) or value.get("reason") not in {
        "http_response", "form_not_identified", "transport_timeout", "transport_failure"
    }:
        return {}
    result = {"reason": value["reason"]}
    for name, lower, upper in (("httpStatus", 100, 599), ("formCount", 0, 20000),
                                ("supportedFormCount", 0, 20000)):
        number = value.get(name)
        if type(number) is int and lower <= number <= upper:
            result[name] = number
    return {"formDiagnostic": result}


class HierarchyProbe:
    MAX_RUNS = 100

    def __init__(self, runtime, state_dir):
        self.runtime, self.state_dir = runtime, Path(state_dir)
        self._lock, self._active, self._live = threading.RLock(), None, {}
        self._task, self._closing = None, False

    def _path(self, run_id):
        if not _ID.fullmatch(run_id):
            raise ProbeError("invalid_request")
        return self.state_dir / (hashlib.sha256(run_id.encode()).hexdigest() + ".json")

    def _scope(self, actor):
        client = self.runtime.client
        url = urlsplit(client.s.jira_base)
        server = urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path.rstrip("/"), "", ""))
        if len(server.encode()) > 1024:
            raise ProbeError("response_too_large", 413)
        context = {"environment": client.s.jira_env, "cacheEnvironment": client.env,
                   "projectKey": client.s.project_key, "server": server, "actor": actor}
        # Cached Jira aliases are optional reconciliation evidence, not local workspace identity.
        # Warming /myself between options/start or during a run must not change its server scope.
        scope_context = context
        if isinstance(actor, dict) and actor.get("localScope"):
            scope_context = {**context, "actor": {"id": actor.get("id"), "localScope": actor["localScope"]}}
        return hashlib.sha256(json.dumps(scope_context, sort_keys=True).encode()).hexdigest(), context

    def _identity(self):
        state = self.runtime.authentication.check("jira")
        if not state["authenticated"]:
            raise AuthenticationUnavailable(state)
        return self.runtime.client.mutation_actor_identity()

    def _guard_actor(self, run):
        # Receipts expire after eight days. Never let an older journal repeat a write
        # merely because its receipt has expired; an interrupted run has seven days.
        age = time.time() - run.get("_createdAt", 0)
        if age < 0 or age > 7 * 24 * 3600:
            raise ProbeError("outcome_unknown", 409)
        if self._scope(run["_actor"])[0] != run["scope"]:
            raise ProbeError("scope_changed", 409)
        try:
            self.runtime.client.assert_mutation_actor_identity(run["_actor"])
        except ValueError as exc:
            raise ProbeError("actor_changed", 409) from exc

    def error(self, exc):
        while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]
        if isinstance(exc, ProbeError):
            return exc
        classified = self.runtime._error(exc)
        return ProbeError(classified.get("code", "internal_error"), classified.get("status", 503),
                          **_form_diagnostic(classified))

    def _save(self, run):
        path = self._path(run["runId"])
        temp = path.with_suffix(".tmp")
        try:
            with self._lock:
                self.state_dir.mkdir(parents=True, exist_ok=True)
                raw = json.dumps(run, ensure_ascii=False).encode("utf-8")
                if len(raw) > 64 * 1024:
                    raise ValueError("journal limit")
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temp, path)
                self._live[run["runId"]] = run
        except Exception as exc:
            raise ProbeError("journal_unavailable", 503) from exc
        finally:
            temp.unlink(missing_ok=True)

    def _load(self, run_id):
        path = self._path(run_id)
        if run_id in self._live:
            return self._live[run_id]
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 64 * 1024:
                raise ValueError("journal limit")
            run = json.loads(path.read_text(encoding="utf-8"))
            if run.get("runId") != run_id or len(run["steps"]) != len(_STEPS):
                raise ValueError("journal shape")
            return run
        except Exception as exc:
            raise ProbeError("journal_unavailable", 503) from exc

    @staticmethod
    def _public(run):
        result = json.loads(json.dumps({key: run[key] for key in
            ("runId", "scope", "status", "environment", "projectKey", "steps", "createdTickets", "error") if key in run}))
        if len(json.dumps(result, ensure_ascii=False).encode()) > 8192:
            for step in result["steps"]:
                step.pop("inputs", None)
                step.pop("changes", None)
            result.update(detailsOmitted=True, detailMessage="상세 입력 목록이 응답 한도를 넘어 생략됐습니다. Jira 화면에서 확인하세요.")
        if len(json.dumps(result, ensure_ascii=False).encode()) > 8192:
            raise ProbeError("response_too_large", 413)
        return result

    async def get(self, run_id):
        actor = await anyio.to_thread.run_sync(self.runtime.client.mutation_actor_identity)
        with self._lock:
            run = self._load(run_id)
            if run is None:
                raise ProbeError("invalid_request", 404)
            if self._scope(actor)[0] != run["scope"]:
                raise ProbeError("scope_changed", 409)
            result = json.loads(json.dumps(self._public(run)))
            if result["status"] == "running" and self._active != run_id:
                result.update(status="blocked", error={"code": "resume_required", "message": "중단된 실행입니다. 같은 실행 ID로 다시 시작하세요."})
            return result

    async def _call(self, mcp, name, arguments):
        result = await mcp.call_tool(name, arguments)
        payload = result.structured_content or {}
        if result.is_error or not payload.get("ok"):
            error = payload.get("error") or {}
            if error.get("code") == "output_schema_mismatch" or (error.get("repeatWrite") is False and "operationResult" in error):
                raise ProbeError("outcome_unknown")
            raise ProbeError(error.get("code", "internal_error"), error.get("status", 400),
                             **_form_diagnostic(error))
        return payload["data"]

    async def _project_types(self):
        client = self.runtime.client
        raw = await anyio.to_thread.run_sync(lambda: client.provider.get_json("/rest/api/2/issue/createmeta",
            params={"projectKeys": client.s.project_key, "expand": "projects.issuetypes"}))
        project = next((row for row in (raw or {}).get("projects", []) if row.get("key") == client.s.project_key), {})
        types = project.get("issuetypes") or []
        if not types or any(not str(row.get("id", "")).isdigit() or not row.get("name") for row in types):
            raise ProbeError("upstream_unavailable", 503)
        return types

    async def options(self):
        actor = await anyio.to_thread.run_sync(self._identity)
        async with Client(self.runtime.server) as mcp:
            tasks = (await self._call(mcp, "list_options", {"kind": "task_types"}))["options"]
        types = await self._project_types()
        subs = [row["name"] for row in types if row.get("subtask") and row.get("name")]
        scope, context = self._scope(actor)
        def default(values, preferred):
            return next((v for v in values if v.casefold() == preferred.casefold()), values[0] if len(values) == 1 else "")
        result = {"scope": scope, "environment": context["environment"], "projectKey": context["projectKey"],
                "taskTypes": tasks, "subtaskTypes": subs,
                "taskType": default(tasks, "Task"), "subtaskType": default(subs, "Sub-task")}
        if len(json.dumps(result, ensure_ascii=False).encode()) > 8192:
            raise ProbeError("response_too_large", 413)
        return result

    async def start(self, run_id, task_type, subtask_type, expected_scope):
        self._path(run_id)
        actor = await anyio.to_thread.run_sync(self._identity)
        scope, context = self._scope(actor)
        if scope != expected_scope:
            raise ProbeError("scope_changed", 409)
        with self._lock:
            if self._closing:
                raise ProbeError("rate_limited", 503)
            run = self._load(run_id)
            if run and (run["scope"] != scope or run["_types"] != [task_type, subtask_type]):
                raise ProbeError("scope_changed", 409)
            if self._active:
                if self._active != run_id:
                    raise ProbeError("rate_limited", 409)
                return self._public(run)
            if run and run["status"] == "passed":
                return self._public(run)
            if not run:
                if len(list(self.state_dir.glob("*.json"))) >= self.MAX_RUNS:
                    raise ProbeError("rate_limited", 429)
                run = {"runId": run_id, "scope": scope, "environment": context["environment"],
                       "projectKey": context["projectKey"], "_actor": actor, "_createdAt": time.time(), "_types": [task_type, subtask_type],
                       "_calls": {}, "steps": [{"name": name, "status": "pending"} for name in _STEPS], "createdTickets": []}
            run.update(status="running")
            run.pop("error", None)
            self._save(run)
            self._active = run_id
            self._task = asyncio.create_task(self._run(run), name="ltm-hierarchy-probe")
            return self._public(run)

    async def close(self):
        self._closing = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _requirements(self, mcp, run):
        tasks = (await self._call(mcp, "list_options", {"kind": "task_types"}))["options"]
        if run["_types"][0] not in tasks:
            raise ProbeError("invalid_request")
        types = await self._project_types()
        task = [row for row in types if row["name"] == run["_types"][0] and not row.get("subtask")]
        subtask = [row for row in types if row["name"] == run["_types"][1] and row.get("subtask")]
        if len(task) != 1 or len(subtask) != 1:
            raise ProbeError("invalid_request")
        run["_typeIds"] = [str(task[0]["id"]), str(subtask[0]["id"])]
        run["_createFields"] = []
        start = 0
        while True:
            data = await self._call(mcp, "list_options", {"kind": "create_fields", "issue_type": run["_types"][0], "start_at": start})
            run["_createFields"].extend(data["options"])
            missing = [{"id": key, "label": row["name"], "required": True} for key, row in data["options"].items()
                       if row["required"] and not row["managed"] and not row["hasDefaultValue"] and key not in {"summary", "description", "labels"}]
            if missing:
                raise ProbeError("required_input", inputs=missing)
            if not data["hasMore"]:
                return
            start = data["nextStartAt"]

    async def _plan(self, mcp, run, step, index):
        digest = hashlib.sha256(run["runId"].encode()).hexdigest()[:24]
        mutation_id = f"dev-hierarchy:{digest}:{index}"
        if index < 3:
            body = {"type": run["_types"][0], "summary": f"[LTM hierarchy test {digest[:12]}] {step['name']}",
                    "clientMutationId": mutation_id}
            if "description" in run["_createFields"]:
                body["descriptionHtml"] = "<p>LTM DevTool hierarchy test. Retained for diagnosis.</p>"
            if "labels" in run["_createFields"]:
                body["labels"] = ["ltm-hierarchy-test"]
            return "create_task", {"body": body}
        key = run["steps"][2]["key"]
        body = {"action": step["name"]}
        if index < 5:
            body["parent_key"] = run["steps"][index - 3]["key"]
        preview = await self._call(mcp, "get_hierarchy_options", {"key": key, "body": body})
        if index != 4:
            wanted = run["_typeIds"][1 if index == 3 else 0]
            type_input = next((field for field in preview["inputs"] if field["id"] == "issuetype"), {})
            matches = [option["id"] for option in type_input.get("options", []) if option.get("id") == wanted]
            if len(matches) != 1:
                raise ProbeError("required_input", inputs=preview["inputs"])
            body["issue_type_id"] = matches[0]
            preview = await self._call(mcp, "get_hierarchy_options", {"key": key, "body": body})
        if not preview["ready"]:
            raise ProbeError("required_input", inputs=preview["inputs"], changes=preview["changes"])
        if preview["removedFields"] or (index < 5 and preview["securityLevel"] != preview["parentSecurityLevel"]):
            raise ProbeError("unsafe_preview", changes=preview["changes"])
        return "change_ticket_hierarchy", {"key": key, "body": body, "preview_hash": preview["previewHash"], "client_mutation_id": mutation_id}

    async def _run(self, run):
        step = None
        token = write_context_guard.set(lambda: self._guard_actor(run))
        try:
            async with Client(self.runtime.server) as mcp:
                await anyio.to_thread.run_sync(self._guard_actor, run)
                if not run.get("_typeIds") or "_createFields" not in run:
                    await self._requirements(mcp, run)
                for index, step in enumerate(run["steps"]):
                    if step["status"] == "passed":
                        continue
                    await anyio.to_thread.run_sync(self._guard_actor, run)
                    step.update(status="running")
                    for key in ("error", "inputs", "changes"):
                        step.pop(key, None)
                    self._save(run)
                    if step["name"] not in run["_calls"]:
                        run["_calls"][step["name"]] = await self._plan(mcp, run, step, index)
                        self._save(run)  # Exact mutation payload is durable BEFORE SDK dispatch.
                    name, arguments = run["_calls"][step["name"]]
                    await anyio.to_thread.run_sync(self._guard_actor, run)
                    try:
                        data = await self._call(mcp, name, arguments)
                    except ProbeError:
                        raise
                    except Exception as exc:
                        # SDK transport loss is not evidence that its submitted write failed.
                        raise ProbeError("outcome_unknown") from exc
                    step.update(status="passed", key=data["key"])
                    if index < 3:
                        previous = next((row for row in run["createdTickets"] if row["role"] == step["name"]), None)
                        if previous and previous["key"] != data["key"]:
                            raise ProbeError("outcome_unknown")
                        if previous is None:
                            run["createdTickets"].append({"key": data["key"], "role": step["name"],
                                "url": self._scope(run["_actor"])[1]["server"] + "/browse/" + quote(data["key"], safe="")})
                    else:
                        step.update(issueTypeId=data["issueTypeId"], parentKey=data["parentKey"])
                    self._save(run)
                run["status"] = "passed"
        except Exception as exc:
            problem = self.error(exc)
            run.update(status="blocked" if problem.code in {"required_input", "unsafe_preview", "actor_changed", "scope_changed", "outcome_unknown"} else "failed", error=problem.public())
            if step:
                step.update(status=run["status"], error=problem.public(), **problem.details)
            elif problem.details:
                run["steps"][0].update(status=run["status"], error=problem.public(), **problem.details)
        finally:
            write_context_guard.reset(token)
            if run["status"] == "running":
                run.update(status="blocked", error=ProbeError("outcome_unknown").public())
            try:
                self._save(run)
            except ProbeError:
                run.update(status="blocked", error=ProbeError("journal_unavailable").public())
                self._live[run["runId"]] = run
            with self._lock:
                self._active = None
