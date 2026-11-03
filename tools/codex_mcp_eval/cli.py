"""Codex subprocess boundary. No credential files are read or copied."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

TOKEN_ENV = "LTM_EVAL_MCP_BEARER"
MODEL = "gpt-6-astra"
EFFORT = "ultra"
HARDENING = {
    "project_doc_max_bytes": 0,
    "features.shell_tool": False,
    "features.shell_snapshot": False,
    "features.apps": False,
    "features.plugins": False,
    "features.multi_agent": False,
    "features.browser_use": False,
    "features.browser_use_external": False,
    "features.browser_use_full_cdp_access": False,
    "features.computer_use": False,
    "features.image_generation": False,
    "features.in_app_browser": False,
    "features.view_image": False,
    "features.skip_host_skill_discovery": True,
    "suppress_unstable_features_warning": True,
    "web_search": "disabled",
}


def executable() -> str:
    result = os.environ.get("LTM_CODEX_EXECUTABLE") or shutil.which("codex.exe") or shutil.which("codex")
    if not result:
        raise RuntimeError("codex CLI is not installed; no evaluation started")
    return result


def command(binary: str, workspace: Path, *, model=MODEL, effort=EFFORT, mcp_url=None, schema_path=None) -> list[str]:
    args = [binary, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
            "--sandbox", "read-only", "--json", "--color", "never", "-C", str(workspace), "-m", model]
    config = {**HARDENING, "model_reasoning_effort": effort}
    if mcp_url:
        config.update({"mcp_servers.ltm.url": mcp_url,
                       "mcp_servers.ltm.bearer_token_env_var": TOKEN_ENV,
                       "mcp_servers.ltm.required": True})
    for key, value in config.items():
        args.extend(["-c", key + "=" + json.dumps(value, ensure_ascii=False)])
    if schema_path:
        args.extend(["--output-schema", str(schema_path)])
    return args + ["-"]


def safe_event(value, secret=""):
    """Exclude private reasoning and transport credentials, retaining actual task evidence."""
    if isinstance(value, dict):
        if value.get("type") in {"reasoning", "analysis"}:
            return None
        result = {}
        for key, item in value.items():
            if key.lower() in {"authorization", "api_key", "apikey", "access_token", "bearer_token"}:
                result[key] = "[REDACTED]"
            elif key.lower() not in {"reasoning", "analysis"}:
                cleaned = safe_event(item, secret)
                if cleaned is not None:
                    result[key] = cleaned
        return result
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := safe_event(item, secret)) is not None]
    return value.replace(secret, "[REDACTED]") if isinstance(value, str) and secret else value


def parse_events(stdout: str, *, secret="") -> dict:
    events, replies, errors, forbidden = [], [], [], []
    usage, completed = None, False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors.append("invalid_json_event")
            continue
        if not isinstance(event, dict):
            errors.append("invalid_event_shape")
            continue
        item = event.get("item") or {}
        if item.get("type") in {"reasoning", "analysis"}:
            continue
        cleaned = safe_event(event, secret)
        events.append(cleaned)
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            replies.append(str(item.get("text") or ""))
        if item.get("type") in {"command_execution", "file_change", "web_search"}:
            forbidden.append(item.get("type"))
        elif item.get("type") and item["type"] not in {"agent_message", "mcp_tool_call", "todo_list", "error"}:
            forbidden.append("unexpected_item:" + item["type"])
        if item.get("type") == "mcp_tool_call" and item.get("server") != "ltm":
            forbidden.append("unapproved_mcp_server")
        if event.get("type") == "turn.completed":
            completed, usage = True, event.get("usage") or {}
        if event.get("type") in {"error", "turn.failed"}:
            errors.append(cleaned)
    usage = usage or {}
    def number(key):
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    prompt, completion = number("input_tokens"), number("output_tokens")
    return {"reply": replies[-1] if replies else "", "events": events, "completed": completed,
            "errors": errors, "forbiddenToolEvents": forbidden,
            "usage": {"calls": None, "promptTokens": prompt, "completionTokens": completion,
                      "totalTokens": prompt + completion if prompt is not None and completion is not None else None,
                      "cachedTokens": number("cached_input_tokens"), "costUsd": None},
            "measurementAvailability": {"modelCalls": False, "costUsd": False, "timeToFirstToken": False,
                                        "tokens": prompt is not None and completion is not None}}


def run(binary: str, prompt: str, workspace: Path, *, model=MODEL, effort=EFFORT,
        mcp_url=None, token="", timeout=600, schema=None, process_run=subprocess.run) -> dict:
    workspace.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    # Inherit the user's existing Codex login, never copy/read its credential files.
    env.pop(TOKEN_ENV, None)
    if token:
        env[TOKEN_ENV] = token
    schema_path = None
    if schema is not None:
        schema_path = workspace.parent / (workspace.name + "-output-schema.json")
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    args = command(binary, workspace, model=model, effort=effort, mcp_url=mcp_url, schema_path=schema_path)
    started = time.perf_counter()
    try:
        result = process_run(args, input=prompt, encoding="utf-8", errors="replace",
                             capture_output=True, timeout=timeout, env=env, shell=False)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        parsed = parse_events(partial, secret=token)
        parsed.update(ok=False, error="codex_timeout", durationSeconds=time.perf_counter() - started,
                      stderrPresent=bool(exc.stderr), incomplete=True)
        return parsed
    parsed = parse_events(result.stdout, secret=token)
    parsed.update(durationSeconds=time.perf_counter() - started, exitCode=result.returncode)
    parsed["ok"] = bool(result.returncode == 0 and parsed["completed"] and parsed["reply"]
                        and not parsed["errors"] and not parsed["forbiddenToolEvents"])
    if schema is not None:
        from jsonschema import ValidationError, validate
        try:
            answer = json.loads(parsed["reply"])
            validate(answer, schema)
            parsed["structuredAnswer"], parsed["reply"] = answer, answer["reply"]
        except (ValueError, TypeError, KeyError, ValidationError):
            parsed["ok"] = False
            parsed["errors"].append("invalid_final_output_schema")
    if not parsed["ok"]:
        parsed["error"] = "codex_execution_failed"
    # stderr may include host/profile paths or credentials. Keep only its presence.
    parsed["stderrPresent"] = bool(result.stderr.strip())
    return parsed


def identity(binary: str, *, model=MODEL, effort=EFFORT, process_run=subprocess.run) -> dict:
    version = process_run([binary, "--version"], capture_output=True, encoding="utf-8", timeout=15, shell=False)
    login = process_run([binary, "login", "status"], capture_output=True, encoding="utf-8", timeout=15, shell=False)
    message = (login.stdout + login.stderr).lower()
    if version.returncode or login.returncode or "logged in" not in message or "chatgpt" not in message:
        raise RuntimeError("Codex existing ChatGPT login is unavailable; no evaluation started")
    return {"runtime": "codex-cli", "version": version.stdout.strip(), "model": model,
            "reasoningEffort": effort, "authKind": "existing-chatgpt-login", "hardening": HARDENING}
