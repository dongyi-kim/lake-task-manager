"""Named LLM connection commands shared by the settings UI and local MCP."""

from functools import wraps

from app.agent import config, profiles, secrets


def _local_change(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with config.CONNECTION_LOCK:
            return fn(*args, **kwargs)
    return run


def get_config(config_id: str) -> dict:
    row = next((row for row in profiles.list_all() if row["id"] == config_id), None)
    if not row:
        raise KeyError("설정을 찾을 수 없습니다.")
    return {**row, "authOk": config.auth_ok(config_id), "modelsOk": config.models_ok(config_id),
            "verified": bool(row.get("active") and config.verified())}


def read_settings() -> dict:
    active = profiles.active()
    return {"configs": [get_config(row["id"]) for row in profiles.list_all()],
            "activeConfigId": str((active or {}).get("id") or ""),
            "runtimeConfigSource": config._runtime_config_source(),
            "envSupplied": config._env_supplied(), "envOverrides": secrets.env_overrides()}


@_local_change
def create_config(name: str, provider: str) -> dict:
    from app.infra.settings import get_settings
    if provider.strip().lower() == "fake" and (get_settings().jira_env or "").lower() == "prod":
        raise ValueError("운영 환경에서는 테스트(가짜) 연결을 추가할 수 없습니다.")
    return get_config(profiles.create(name, provider)["id"])


@_local_change
def update_config(config_id: str, changes: dict) -> dict:
    profiles.update(config_id, {key: value for key, value in changes.items()
                               if key in profiles.FIELDS and value is not None})
    if changes.get("secrets"):
        secrets.save_for(config_id, changes["secrets"])
    return get_config(config_id)


@_local_change
def delete_config(config_id: str) -> dict:
    profiles.remove(config_id)  # Includes the UI's active-profile deletion guard.
    return {"id": config_id, "deleted": True}


def list_models(config_id: str) -> dict:
    get_config(config_id)
    return config.list_models(config_id=config_id)


def check_connection(config_id: str, stage: str) -> dict:
    get_config(config_id)
    if stage == "auth":
        return config.probe_auth(config_id=config_id)
    if stage == "models":
        return config.probe(config_id=config_id)
    raise ValueError("연결 확인 단계는 auth 또는 models여야 합니다.")


def activate_config(config_id: str) -> dict:
    with config.CONNECTION_LOCK:
        result = config.activate(config_id)
    if result.get("ok"):
        reset_runtime()
    return result


def reset_runtime():
    """Release model clients and indexes only when the user applies a connection."""
    try:
        from app.agent.workflow import graph
        graph.reset()
    except Exception:
        pass
    try:
        from app.agent import capabilities
        capabilities.reset()
    except Exception:
        pass
    try:
        from app.agent.retrieval import dynamic_index, static_index
        static_index._cached.update(sig=None, store=None)
        dynamic_index._cached.update(sig=None, store=None)
    except Exception:
        pass
