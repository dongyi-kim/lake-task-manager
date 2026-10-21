"""Local pointers to governed private Knowledge configuration, never remote input."""
import hashlib
from pathlib import Path, PurePosixPath

import yaml


def clean_path(value: object) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("Invalid local Knowledge configuration path.")
    if value.startswith("cache:"):
        suffix = value[len("cache:"):]
        path = PurePosixPath(suffix)
        if (suffix != path.as_posix() or "\\" in suffix or ":" in suffix
                or path.is_absolute() or ".." in path.parts
                or not suffix.startswith("maxis/") or path.suffix not in {".yml", ".yaml"}):
            raise ValueError("Invalid local Knowledge configuration path.")
        return value
    path = PurePosixPath(value)
    if (value != path.as_posix() or "\\" in value or ":" in value
            or path.is_absolute() or ".." in path.parts
            or path.suffix not in {".yml", ".yaml"}
            or not (value.startswith("config/") or value.startswith(".cache/maxis/"))):
        raise ValueError("Invalid local Knowledge configuration path.")
    return value


def resolve_path(value: object, *, root: Path | None = None) -> Path | None:
    relative = clean_path(value)
    if not relative:
        return None
    if relative.startswith("cache:"):
        from app.infra.settings import CACHE_DIR
        cache = Path(CACHE_DIR).resolve()
        boundary = (cache / "maxis").resolve()
        target = (cache / relative[len("cache:"):]).resolve()
        if (not boundary.is_relative_to(cache) or not target.is_relative_to(boundary)
                or not target.is_file()):
            raise ValueError("Local Knowledge configuration is unavailable.")
        return target
    if root is None:
        from app.infra.settings import SRC_DIR
        root = Path(SRC_DIR)
    root = root.resolve()
    boundary = (root / ("config" if relative.startswith("config/") else ".cache/maxis")).resolve()
    target = (root / relative).resolve()
    if (not boundary.is_relative_to(root) or not target.is_relative_to(boundary)
            or not target.is_file()):
        raise ValueError("Local Knowledge configuration is unavailable.")
    return target


def profile_binding_sha256(profile: dict) -> str:
    """Bind private runtime configuration to the same release as materialization."""
    source = resolve_path(profile.get("knowledge_source_manifest"))
    environment = resolve_path(profile.get("knowledge_environment_binding"))
    if source is None and environment is None:
        return ""
    if source is None or environment is None:
        raise ValueError("Both local Knowledge configuration paths are required.")
    try:
        raw = environment.read_bytes()
        if len(raw) > 262144:
            raise ValueError("Local Knowledge environment is too large.")
        binding = yaml.safe_load(raw)
        knowledge = binding["knowledge"]
        selected = resolve_path(knowledge.get("source_manifest"))
        stage = "prod" if profile.get("stage") in {"prod", "internal-pilot"} else "dev"
        if (binding.get("contract") != "ltm-maxis-dify-environment.v1"
                or binding.get("status") != "ready"
                or binding.get("environment") != stage
                or selected != source
                or knowledge.get("version") != profile.get("knowledge_version")
                or knowledge.get("dataset_id") != profile.get("knowledge_dataset_id")):
            raise ValueError("Local Knowledge environment does not match the profile.")
    except (OSError, UnicodeError, yaml.YAMLError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Local Knowledge environment is unavailable or invalid.") from exc
    return hashlib.sha256(raw).hexdigest()
