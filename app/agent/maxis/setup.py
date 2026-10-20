"""Repository-only MAXIS preparation; no portal login, API calls, or runtime keys.

Generated release assets stay on this PC. Public status exposes source identities and
steps, while the release marker itself is available only in the manager's download.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import threading
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import yaml

from app.agent.maxis.public_metadata import require_safe_public_text

WORKFLOWS = ("agent_core", "editor_author", "pmo_brief")
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_LOCK = threading.RLock()


def _root() -> Path:
    from app.infra.settings import SRC_DIR
    return Path(SRC_DIR).resolve()


def profile_id(profile_name: str = "prod") -> str:
    """Validate an opaque storage identifier, never a display name or path."""
    if not isinstance(profile_name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", profile_name):
        raise ValueError("MAXIS 프로필 식별자를 확인하세요.")
    return profile_name


def _directory(profile_name: str = "prod") -> Path:
    from app.infra.settings import CACHE_DIR
    name = profile_id(profile_name)
    root = Path(CACHE_DIR) / "maxis" / "setup"
    return root if name == "prod" else root / "profiles" / name


def _stage(profile_name: str) -> str:
    from app.agent.maxis import profiles
    return profiles.get(profile_id(profile_name))["stage"]


def _download_url(run_id: str, kind: str, profile_name: str) -> str:
    path = f"/api/agent/maxis/setup/download/{run_id}/{kind}"
    return path if profile_name == "prod" else path + f"?profile={quote(profile_name)}"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict:
    try:
        if path.stat().st_size > 262144:
            raise ValueError("oversize")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("shape")
        return value
    except (OSError, ValueError, UnicodeError):
        raise ValueError("MAXIS 준비 기록을 읽을 수 없습니다. 준비 파일을 다시 생성하세요.") from None


def _candidate_base(value: str) -> tuple[str, str]:
    from app.agent.maxis.destination_policy import _ascii_fqdn
    raw = str(value or "").strip()
    try:
        require_safe_public_text(raw, "MAXIS Service API")
        parsed = urlsplit(raw)
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.port not in (None, 443)
                or "\\" in raw or any(ord(char) <= 32 for char in raw)):
            raise ValueError("invalid")
        host = _ascii_fqdn(parsed.hostname or "")
        path = parsed.path.rstrip("/") or "/v1"
        if path != "/v1" and (not path.endswith("/v1") or ".." in path or "%" in path):
            raise ValueError("path")
        return urlunsplit(("https", host, path, "", "")), host
    except (ValueError, TypeError):
        raise ValueError("MAXIS Service API 주소는 인증정보 없는 HTTPS:443 FQDN과 /v1 경로여야 합니다.") from None


def registered_prod_hosts() -> frozenset[str]:
    return registered_profile_hosts("prod")


def registered_profile_hosts(stage: str) -> frozenset[str]:
    """Deployment policy hook: only exact, explicitly registered local hosts."""
    try:
        from app.agent.maxis.destination_policy import _ascii_fqdn
        paths = list((_directory() / "profiles").glob("*/destinations.json"))
        if stage == "prod":
            paths.append(_directory() / "destinations.json")
        result = set()
        for path in paths:
            if not path.is_file():
                continue
            data = _read_json(path)
            if data.get("stage", "prod") != stage:
                continue
            hosts = data.get("hosts")
            if not isinstance(hosts, list) or len(hosts) > 8:
                raise ValueError("invalid hosts")
            result.update(_ascii_fqdn(host) for host in hosts)
        return frozenset(result)
    except ValueError:
        return frozenset()


def register_prod_host(base_url: str, *, confirmed: bool, profile_name: str = "prod") -> dict:
    """Manager route supplies fresh identity; this step never transmits a key."""
    if confirmed is not True:
        raise ValueError("주소를 확인하고 이 MAXIS 호스트 등록에 동의하세요.")
    base, host = _candidate_base(base_url)
    from app.agent.maxis.destination_policy import allowed_hosts, ALLOWED_HOSTS_ENV_NAMES
    stage = _stage(profile_name)
    if str(os.getenv(ALLOWED_HOSTS_ENV_NAMES[stage]) or "").strip():
        if host not in allowed_hosts(stage):
            raise ValueError("배포 환경변수가 MAXIS 호스트를 고정했습니다. 배포 관리자 설정을 확인하세요.")
    with _LOCK:
        _write_json(_directory(profile_name) / "destinations.json",
                    {"contract": "ltm-maxis-destinations.v1", "stage": stage, "hosts": [host]})
    return {"ok": True, "baseUrl": base, "host": host}


def source_inventory() -> dict:
    """One canonical public corpus; never discover ignored/private source copies."""
    root = _root()
    try:
        source_bytes = (root / "config/maxis-knowledge.yml").read_bytes()
        source = yaml.safe_load(source_bytes)
        documents = source["documents"]
        if source.get("contract") != "ltm-knowledge-source.v2" or not documents:
            raise ValueError("source")
        versions = {row["reviewed_for_version"] for row in documents}
        if len(versions) != 1:
            raise ValueError("version")
        from app.agent.maxis.knowledge_manifest import build_manifest
        build_manifest(knowledge_version=next(iter(versions)), dataset_id="11111111-1111-4111-8111-111111111111",
                       config_path=root / "config/maxis-knowledge.yml", source_root=root)
        digest = hashlib.sha256(source_bytes)
        rows = []
        for row in documents:
            relative = Path(row["path"])
            target = (root / relative).resolve()
            if (relative.is_absolute() or ".." in relative.parts
                    or not target.is_relative_to(root / "knowledge") or target.suffix != ".md"):
                raise ValueError("path")
            raw = target.read_bytes()
            file_hash = hashlib.sha256(raw).hexdigest()
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(raw)
            rows.append({"id": row["id"], "sourceName": target.name,
                         "remoteName": target.with_suffix(".txt").name,
                         "description": row["description"],
                         "contentSha256": file_hash, "allowedRoles": list(row["allowed_roles"])})
        for name in ("config/maxis-workflows.yml", "app/agent/maxis/setup.py",
                     "app/agent/maxis/knowledge_manifest.py"):
            digest.update((root / name).read_bytes())
        # Bind generated imports to the actual current deployment source as well.
        for directory in (root / "deploy/maxis-dify", root / "app/agent/prompts"):
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix in {".py", ".md", ".yml", ".json"}:
                    digest.update(path.relative_to(root).as_posix().encode("utf-8"))
                    digest.update(path.read_bytes())
        return {"contract": "ltm-maxis-setup-source.v1", "knowledgeVersion": next(iter(versions)),
                "sourceDigest": digest.hexdigest(), "documents": rows,
                "workflowBundleVersion": "ltm-agent-" + digest.hexdigest()[:12]}
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise ValueError("저장소의 정본 Knowledge 또는 배포 소스를 확인할 수 없습니다.") from None


def _run(run_id: str, *, current: bool = True, profile_name: str = "prod") -> tuple[Path, dict]:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("MAXIS 준비 식별자가 올바르지 않습니다.")
    directory = _directory(profile_name) / run_id
    metadata = _read_json(directory / "metadata.json")
    if metadata.get("runId") != run_id:
        raise ValueError("MAXIS 준비 기록의 식별자가 다릅니다.")
    if metadata.get("profile", "prod") != profile_name:
        raise ValueError("준비 파일의 MAXIS 프로필이 다릅니다.")
    if current and metadata.get("sourceDigest") != source_inventory()["sourceDigest"]:
        raise ValueError("저장소 소스가 변경되었습니다. 새 준비 파일을 생성하고 MAXIS에서 다시 게시하세요.")
    return directory, metadata


def _public_run(metadata: dict) -> dict:
    keys = ("runId", "profile", "sourceDigest", "knowledgeVersion", "workflowBundleVersion", "documents",
            "knowledgeSentinelSha256", "baseUrl", "bundleReady", "candidateSaved", "assetInputs")
    return {key: metadata[key] for key in keys if key in metadata}


def status(*, profile_name: str = "prod") -> dict:
    inventory = source_inventory()
    current_run = None
    current_path = _directory(profile_name) / "current.json"
    if current_path.exists():
        try:
            _, metadata = _run(_read_json(current_path).get("runId"), current=False, profile_name=profile_name)
            current_run = {**_public_run(metadata), "sourceCurrent": metadata.get("sourceDigest") == inventory["sourceDigest"]}
        except ValueError:
            pass
    hosts_path = _directory(profile_name) / "destinations.json"
    hosts = _read_json(hosts_path).get("hosts", []) if hosts_path.exists() else []
    return {"profile": profile_name, "source": inventory, "currentRun": current_run, "registeredHosts": hosts,
            "runtimeAuthentication": "dify_app_bearer", "portalLoginStored": False}


def _zip(path: Path, entries: dict[str, bytes]) -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)
    temporary = path.with_suffix(".zip.tmp")
    temporary.write_bytes(stream.getvalue())
    os.replace(temporary, path)


def prepare_knowledge(*, profile_name: str = "prod") -> dict:
    with _LOCK:
        inventory = source_inventory()
        previous = {}
        current_path = _directory(profile_name) / "current.json"
        if current_path.exists():
            try:
                old_directory, previous = _run(_read_json(current_path).get("runId"), current=False, profile_name=profile_name)
                if (previous.get("sourceDigest") == inventory["sourceDigest"]
                        and (old_directory / "knowledge.zip").is_file()):
                    return {"ok": True, **_public_run(previous),
                            "downloadUrl": _download_url(previous["runId"], "knowledge", profile_name)}
            except ValueError:
                previous = {}
        run_id = uuid.uuid4().hex
        directory = _directory(profile_name) / run_id
        directory.mkdir(parents=True)
        marker = "LTM_RELEASE_SENTINEL_V1=" + secrets.token_hex(24)
        entries = {}
        csv_stream = io.StringIO(newline="")
        writer = csv.writer(csv_stream)
        writer.writerow(("document", "description", "content_sha256", "ltm_allowed_roles_v1"))
        for row in inventory["documents"]:
            raw = (_root() / "knowledge" / row["sourceName"]).read_bytes()
            entries["knowledge/" + row["remoteName"]] = raw
            writer.writerow((row["remoteName"], row["description"], row["contentSha256"],
                             "|" + "|".join(row["allowedRoles"]) + "|"))
        entries["sentinel/ltm-release-sentinel-prod.txt"] = (marker + "\n").encode("utf-8")
        entries["document-metadata.csv"] = csv_stream.getvalue().encode("utf-8-sig")
        metadata = {**inventory, "runId": run_id, "profile": profile_name, "environment": "prod",
                    "knowledgeSentinelSha256": hashlib.sha256(marker.encode("utf-8")).hexdigest(),
                    "bundleReady": False, "candidateSaved": False}
        # Source changes require new local output and a new sentinel, not a duplicate
        # server dataset. Keep known PROD IDs/model as editable update suggestions.
        if previous.get("assetInputs"):
            retained = dict(previous["assetInputs"])
            retained["documentIds"] = {
                row["id"]: retained.get("documentIds", {}).get(row["id"], "")
                for row in inventory["documents"]}
            metadata["assetInputs"] = retained
            metadata["baseUrl"] = previous.get("baseUrl", "")
        entries["deployment-source.json"] = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
        entries["README.txt"] = (
            "처음에는 MAXIS Knowledge와 별도 sentinel dataset을 만들고, 갱신 시에는 기존 자산을 사용하세요.\n"
            "knowledge/*.txt를 Knowledge에, sentinel/*.txt를 별도 sentinel dataset에 업로드하세요.\n"
            "document-metadata.csv는 업로드 문서별 ltm_allowed_roles_v1 메타데이터의 대조표입니다.\n"
            "포털에서 실제 문서마다 해당 문자열을 설정하고 메타데이터 필드 UUID를 기록하세요.\n"
            "생성된 marker·원문을 Git/메신저에 게시하지 마세요. Runtime API token은 이 ZIP에 포함되지 않습니다.\n"
            "LTM 설정으로 돌아와 실제 자산 UUID와 승인된 모델 정보를 입력하세요.\n"
        ).encode("utf-8")
        _zip(directory / "knowledge.zip", entries)
        _write_json(directory / "metadata.json", metadata)
        _write_json(_directory(profile_name) / "current.json", {"runId": run_id})
        return {"ok": True, **_public_run(metadata), "downloadUrl": _download_url(run_id, "knowledge", profile_name)}


def _uuid(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value or ""))
        if parsed.version not in (1, 2, 3, 4, 5) or str(parsed) != str(value).lower():
            raise ValueError("uuid")
        return str(parsed)
    except (ValueError, AttributeError):
        raise ValueError(f"{label}: 실제 MAXIS 자산의 UUID를 입력하세요.") from None


def _text(value: str, label: str) -> str:
    clean = require_safe_public_text(value, label)
    if not clean or len(clean) > 256 or any(ord(char) < 32 for char in clean):
        raise ValueError(f"{label} 값을 확인하세요.")
    return clean


def _materializer():
    path = _root() / "deploy/maxis-dify/materialize_environment.py"
    spec = importlib.util.spec_from_file_location("ltm_maxis_setup_materializer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_from_export(yaml_text: str) -> dict:
    """Extract approved model identifiers and public options from a portal export.

    Input is never persisted, executed, or included in errors. Unrelated exported
    environment variables, names, prompts, credentials and graphs are discarded.
    """
    try:
        if not isinstance(yaml_text, str) or len(yaml_text.encode("utf-8")) > 2097152:
            raise ValueError("size")
        exported = yaml.safe_load(yaml_text)
        nodes = exported["workflow"]["graph"]["nodes"]
        models = {(node["data"]["model"]["provider"], node["data"]["model"]["name"])
                  for node in nodes if node.get("data", {}).get("type") == "llm"}
        if len(models) != 1:
            raise ValueError("models")
        provider, name = next(iter(models))
        validate_params = _materializer().validate_completion_params
        parameters = [validate_params(node["data"]["model"].get("completion_params", {}))
                      for node in nodes if node.get("data", {}).get("type") == "llm"]
        if any(item != parameters[0] for item in parameters[1:]):
            raise ValueError("mixed_parameters")
        prefix = provider.rsplit("/", 1)[0] + ":"
        packages = {row["value"]["plugin_unique_identifier"] for row in exported["dependencies"]
                    if str(row.get("value", {}).get("plugin_unique_identifier", "")).startswith(prefix)}
        if len(packages) != 1:
            raise ValueError("dependency")
        model = {"provider": _text(provider, "모델 provider"), "name": _text(name, "모델 이름"),
                 "plugin_unique_identifier": _text(next(iter(packages)), "모델 plugin"),
                 "completion_params": parameters[0]}
        return {"ok": True, "model": model}
    except (ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        raise ValueError("동일한 모델·파라미터 설정 하나를 사용하는 MAXIS Chatflow를 내보내세요. 지원되지 않는 옵션은 제거하고 다시 확인하세요.") from None


def prepare_bundle(body: dict, *, profile_name: str = "prod") -> dict:
    from app.agent.maxis.destination_policy import canonical_maxis_api_base
    with _LOCK:
        directory, metadata = _run(body["runId"], profile_name=profile_name)
        stage = _stage(profile_name)
        if stage not in {"dev", "prod"}:
            raise ValueError("이 연결 정책에서는 저장소 배포 파일 생성을 지원하지 않습니다.")
        base = canonical_maxis_api_base(body["baseUrl"], stage=stage, require_https=True)
        documents = body["documentIds"]
        if set(documents) != {row["id"] for row in metadata["documents"]}:
            raise ValueError("모든 정본 문서의 실제 MAXIS UUID를 입력하세요.")
        environment = {
            "contract": "ltm-maxis-dify-environment.v1", "environment": stage, "status": "ready",
            "maxis_project_name": "Lake Task Manager", "service_base_url": base,
            "knowledge": {"source_manifest": "config/maxis-knowledge.yml",
                "version": metadata["knowledgeVersion"], "name": "LTM Governed Knowledge",
                "dataset_id": _uuid(body["datasetId"], "Knowledge dataset"),
                "sentinel_dataset_id": _uuid(body["sentinelDatasetId"], "Sentinel dataset"),
                "role_metadata_field_id": _uuid(body["roleMetadataFieldId"], "Role metadata field"),
                "sentinel_file": {"source_name": "ltm-release-sentinel-prod.md", "remote_name": "ltm-release-sentinel-prod.txt",
                                  "document_id": _uuid(body["sentinelDocumentId"], "Sentinel document")},
                "documents": {row["id"]: {"source_name": row["sourceName"], "remote_name": row["remoteName"],
                                          "document_id": _uuid(documents[row["id"]], "Knowledge document")}
                              for row in metadata["documents"]}},
            "model": {key: _text(body["model"][key], "승인된 모델") for key in ("plugin_unique_identifier", "provider", "name")},
            "workflows": {key: {"app_name": "LTM " + key.replace("_", " ").title(),
                                "source_dsl": "deploy/maxis-dify/dsl/ltm-" + key.replace("_", "-") + ".yml"}
                          for key in WORKFLOWS},
        }
        # The reviewed core source uses its stable historical filename.
        environment["workflows"]["agent_core"]["source_dsl"] = "deploy/maxis-dify/dsl/ltm-agent-core.yml"
        materializer = _materializer()
        if "completion_params" in body["model"]:
            environment["model"]["completion_params"] = materializer.validate_completion_params(
                body["model"]["completion_params"])
        generated = materializer.materialize(environment, directory / "workflows")
        environment_text = yaml.safe_dump(environment, allow_unicode=True, sort_keys=False)
        (directory / "environment.yml").write_text(environment_text, encoding="utf-8")
        entries = {"workflows/" + path.name: path.read_bytes() for path in generated}
        entries["environment.yml"] = environment_text.encode("utf-8")
        entries["README.txt"] = (
            "선택한 MAXIS의 Dify 포털에서 workflows/*.yml을 각각 Chatflow로 import하세요.\n"
            "승인된 모델 연결과 Knowledge 조회를 확인하고 세 앱을 Publish하세요.\n"
            "실제 게시 Workflow UUID와 각 앱의 Service API token을 LTM에 입력하세요.\n"
            "포털 ID/PW를 LTM API token 입력란에 넣지 마세요. 아직 운영 활성화된 상태가 아닙니다.\n"
        ).encode("utf-8")
        _zip(directory / "workflows.zip", entries)
        metadata.update(baseUrl=base, bundleReady=True, candidateSaved=False,
                        assetInputs={**body, "baseUrl": base})
        _write_json(directory / "metadata.json", metadata)
        return {"ok": True, **_public_run(metadata), "downloadUrl": _download_url(metadata["runId"], "workflows", profile_name)}


def save_candidate(body: dict, *, profile_name: str = "prod") -> dict:
    from app.agent.maxis import profiles
    with _LOCK:
        directory, metadata = _run(body["runId"], profile_name=profile_name)
        if not metadata.get("bundleReady"):
            raise ValueError("실제 MAXIS 식별자로 Workflow 준비 파일을 먼저 생성하세요.")
        versions = body["workflowVersions"]
        if not {"agent_core", "editor_author"}.issubset(versions) or set(versions) - set(WORKFLOWS):
            raise ValueError("Agent Core와 Editor Author의 실제 게시 Workflow UUID를 입력하세요. PMO는 선택 사항입니다.")
        relative = directory.relative_to(_directory()).as_posix()
        environment_pointer = "cache:maxis/setup/" + relative + "/environment.yml"
        environment = yaml.safe_load((directory / "environment.yml").read_text(encoding="utf-8"))
        profiles.save(profile_name, {"base_url": metadata["baseUrl"], "data_policy": "internal_read_only",
            "workflow_bundle_version": metadata["workflowBundleVersion"],
            "knowledge_version": metadata["knowledgeVersion"], "knowledge_dataset_id": environment["knowledge"]["dataset_id"],
            "knowledge_source_manifest": "config/maxis-knowledge.yml", "knowledge_environment_binding": environment_pointer,
            "knowledge_sentinel_sha256": metadata["knowledgeSentinelSha256"],
            "remote_log_policy_id": _text(body["remoteLogPolicyId"], "로그 승인 정책 ID"),
            "remote_log_mode": body["remoteLogMode"], "remote_log_retention_days": body["remoteLogRetentionDays"],
            "workflows": {key: {"binding": key, "version": _uuid(versions[key], "게시 Workflow") if key in versions else ""} for key in WORKFLOWS}})
        metadata["candidateSaved"] = True
        _write_json(directory / "metadata.json", metadata)
        return {"ok": True, **_public_run(metadata)}


def download(run_id: str, kind: str, *, profile_name: str = "prod") -> Path:
    if kind not in {"knowledge", "workflows"}:
        raise ValueError("알 수 없는 MAXIS 준비 파일입니다.")
    directory, _ = _run(run_id, current=False, profile_name=profile_name)
    target = directory / ("knowledge.zip" if kind == "knowledge" else "workflows.zip")
    if not target.is_file():
        raise ValueError("MAXIS 준비 파일을 먼저 생성하세요.")
    return target
