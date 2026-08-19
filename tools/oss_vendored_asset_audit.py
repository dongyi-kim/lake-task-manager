"""Inventory vendored static assets offline and fail closed on missing provenance."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Sequence
from urllib.parse import urlparse


PROVENANCE_FILENAME = "THIRD_PARTY_ASSETS.json"
_ESM_HEADER = re.compile(r"^/\*\s*esm\.sh\s+-\s*(?P<specifier>[^*]+?)\s*\*/")
_BLOCKING_LICENSE_CATEGORIES = {
    "file_copyleft_review",
    "library_copyleft_review",
    "project_license_decision",
    "manual_review",
    "manual_or_block",
}


@dataclass(frozen=True)
class EmbeddedPackage:
    name: str
    version: str
    subpath: str | None = None


@dataclass(frozen=True)
class VendorAuditConfig:
    vendor_root: Path
    provenance_manifest: Path
    output_dir: Path


def parse_esm_header(text: str) -> EmbeddedPackage | None:
    """Parse the package identity asserted by an esm.sh module header."""
    match = _ESM_HEADER.match(text)
    if not match:
        return None
    specifier = match.group("specifier").strip()
    version_separator = specifier.rfind("@")
    if version_separator <= 0:
        return None
    name = specifier[:version_separator]
    version_and_path = specifier[version_separator + 1 :]
    version, separator, subpath = version_and_path.partition("/")
    if not name or not version or any(char.isspace() for char in name + version):
        return None
    return EmbeddedPackage(name, version, subpath if separator else None)


def validate_spdx_license(value: str) -> tuple[bool, str]:
    """Validate the complete SPDX expression using the pinned audit dependency."""
    try:
        from license_expression import get_spdx_licensing
    except ImportError:
        return False, "manual_or_block"
    try:
        validation = get_spdx_licensing().validate(value)
    except Exception:
        return False, "manual_or_block"
    if validation.errors:
        return False, "manual_or_block"
    try:
        from tools.oss_dependency_audit import classify_license
    except ModuleNotFoundError:  # Direct execution sets tools/ as sys.path[0].
        from oss_dependency_audit import classify_license
    return True, classify_license(value)[0]


def _clean_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_https_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme.lower() == "https" and bool(parsed.netloc)


def _canonical_asset_path(value: Any) -> str | None:
    text = _clean_text(value)
    if not text or "\\" in text or ":" in text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != text:
        return None
    return text


def _load_provenance(
    path: Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    if not path.is_file():
        return {}, {}, ["provenance manifest is missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, {}, [f"provenance manifest cannot be read: {exc}"]
    if not isinstance(payload, dict):
        return {}, {}, ["provenance manifest root must be an object"]

    blockers: list[str] = []
    if payload.get("schemaVersion") != 1:
        blockers.append("provenance manifest schemaVersion must be 1")
    raw_packages = payload.get("packages", [])
    raw_assets = payload.get("assets", [])
    if not isinstance(raw_packages, list):
        blockers.append("provenance manifest packages must be an array")
        raw_packages = []
    if not isinstance(raw_assets, list):
        blockers.append("provenance manifest assets must be an array")
        raw_assets = []

    packages: dict[tuple[str, str], dict[str, Any]] = {}
    for index, item in enumerate(raw_packages):
        if not isinstance(item, dict):
            blockers.append(f"packages[{index}] must be an object")
            continue
        name = _clean_text(item.get("name"))
        version = _clean_text(item.get("version"))
        if not name or not version:
            blockers.append(f"packages[{index}] requires name and version")
            continue
        key = (name, version)
        if key in packages:
            blockers.append(f"duplicate package provenance: {name}@{version}")
            continue
        packages[key] = item

    assets: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw_assets):
        if not isinstance(item, dict):
            blockers.append(f"assets[{index}] must be an object")
            continue
        asset_path = _canonical_asset_path(item.get("path"))
        if asset_path is None:
            blockers.append(f"assets[{index}] has a non-canonical relative path")
            continue
        if asset_path in assets:
            blockers.append(f"duplicate asset provenance: {asset_path}")
            continue
        assets[asset_path] = item
    return packages, assets, blockers


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _embedded_package(path: Path) -> EmbeddedPackage | None:
    with path.open("rb") as stream:
        first_line = stream.readline(4096).decode("utf-8", errors="replace")
    return parse_esm_header(first_line)


def _metadata_for_asset(
    relative_path: str,
    embedded: EmbeddedPackage | None,
    actual_sha256: str,
    packages: dict[tuple[str, str], dict[str, Any]],
    exact_assets: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    exact = exact_assets.get(relative_path)
    if exact is not None:
        exact_package = _clean_text(exact.get("package"))
        exact_version = _clean_text(exact.get("version"))
        exact_license = _clean_text(exact.get("license"))
        exact_source = _clean_text(exact.get("source"))
        expected_sha256 = _clean_text(exact.get("sha256"))
        if embedded:
            package, version = embedded.name, embedded.version
        else:
            package, version = exact_package, exact_version
        if embedded and (exact_package or exact_version) and (
            exact_package,
            exact_version,
        ) != (embedded.name, embedded.version):
            blockers.append("manifest package/version conflicts with embedded esm.sh header")
        if not expected_sha256 or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            blockers.append("manifest sha256 is missing or invalid")
        elif expected_sha256.lower() != actual_sha256:
            blockers.append("sha256 does not match provenance manifest")
        package_record = packages.get((package, version)) if package and version else None
        if package_record is not None:
            package_license = _clean_text(package_record.get("license"))
            package_source = _clean_text(package_record.get("source"))
            if exact_license and exact_license != package_license:
                blockers.append("exact asset license conflicts with package provenance")
            if exact_source and exact_source != package_source:
                blockers.append("exact asset source conflicts with package provenance")
            license_name, source = package_license, package_source
            evidence = "package-provenance+exact-asset-hash"
            if embedded:
                evidence = "esm.sh-header+" + evidence
        elif embedded is not None:
            license_name = source = None
            evidence = "esm.sh-header+exact-asset-hash"
            blockers.append("package provenance record is missing")
        else:
            license_name, source = exact_license, exact_source
            evidence = "exact-asset-provenance"
    elif embedded is not None:
        package = embedded.name
        version = embedded.version
        record = packages.get((package, version))
        license_name = _clean_text(record.get("license")) if record else None
        source = _clean_text(record.get("source")) if record else None
        evidence = "esm.sh-header+package-provenance" if record else "esm.sh-header"
        if record is None:
            blockers.append("package provenance record is missing")
    else:
        package = version = license_name = source = None
        evidence = None
        blockers.append("asset provenance record is missing")

    if not package:
        blockers.append("package is missing")
    if not version:
        blockers.append("version is missing")
    valid_license = False
    review_category = None
    if license_name:
        valid_license, review_category = validate_spdx_license(license_name)
    if not valid_license:
        blockers.append("license is missing or is not a recognized SPDX expression")
    elif review_category in _BLOCKING_LICENSE_CATEGORIES:
        if review_category in {"file_copyleft_review", "library_copyleft_review"}:
            blockers.append(f"license obligations require release review: {review_category}")
        elif review_category == "project_license_decision":
            blockers.append(f"project license decision is unresolved: {review_category}")
        else:
            blockers.append(f"license review category is unresolved: {review_category}")
    if not _is_https_url(source):
        blockers.append("source must be an absolute HTTPS URL")
    return (
        {
            "package": package,
            "version": version,
            "license": license_name,
            "source": source,
            "reviewCategory": review_category,
            "provenanceEvidence": evidence,
        },
        blockers,
    )


def build_inventory(config: VendorAuditConfig) -> dict[str, Any]:
    root = config.vendor_root.resolve()
    manifest = config.provenance_manifest.resolve()
    packages, exact_assets, manifest_blockers = _load_provenance(manifest)
    if not root.is_dir():
        manifest_blockers.append("vendor root is missing")
        paths: list[Path] = []
    else:
        paths = sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file() and path.resolve() != manifest
            ),
            key=lambda path: path.relative_to(root).as_posix(),
        )

    assets: list[dict[str, Any]] = []
    scanned_paths: set[str] = set()
    for path in paths:
        relative_path = path.relative_to(root).as_posix()
        scanned_paths.add(relative_path)
        if path.is_symlink():
            actual_sha256 = None
            embedded = None
            blockers = ["symbolic-link assets are not accepted"]
            metadata = {
                "package": None,
                "version": None,
                "license": None,
                "source": None,
                "reviewCategory": None,
                "provenanceEvidence": None,
            }
        else:
            actual_sha256 = _sha256(path)
            embedded = _embedded_package(path)
            metadata, blockers = _metadata_for_asset(
                relative_path,
                embedded,
                actual_sha256,
                packages,
                exact_assets,
            )
        assets.append(
            {
                "path": relative_path,
                "sha256": actual_sha256,
                "sizeBytes": path.stat().st_size,
                **metadata,
                "embeddedPackage": embedded.name if embedded else None,
                "embeddedVersion": embedded.version if embedded else None,
                "embeddedSubpath": embedded.subpath if embedded else None,
                "status": "blocked" if blockers else "complete",
                "blockers": blockers,
            }
        )

    stale_assets = sorted(set(exact_assets) - scanned_paths)
    manifest_blockers.extend(
        f"manifest asset does not exist: {asset_path}" for asset_path in stale_assets
    )
    used_packages = {
        (str(item["package"]), str(item["version"]))
        for item in assets
        if item["package"] and item["version"]
    }
    complete = sum(item["status"] == "complete" for item in assets)
    release_blocked = bool(manifest_blockers or complete != len(assets))
    return {
        "schemaVersion": 1,
        "networkEnabled": False,
        "vendorRoot": str(root),
        "provenanceManifest": str(manifest),
        "releaseBlocked": release_blocked,
        "summary": {
            "assets": len(assets),
            "complete": complete,
            "blocked": len(assets) - complete,
            "packages": len(used_packages),
        },
        "manifestBlockers": manifest_blockers,
        "assets": assets,
    }


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    summary = report["summary"]
    lines = [
        "# Vendored static asset provenance",
        "",
        "> Offline inventory only (`networkEnabled: false`). A complete row is provenance",
        "> evidence, not a final license-compatibility decision.",
        "",
        f"**Release provenance gate:** {'BLOCKED' if report['releaseBlocked'] else 'complete'}",
        "",
        f"Assets: {summary['assets']} total, {summary['complete']} complete, "
        f"{summary['blocked']} blocked; package/version pairs: {summary['packages']}.",
        "",
    ]
    if report["manifestBlockers"]:
        lines.extend(["## Manifest blockers", ""])
        lines.extend(f"- {item}" for item in report["manifestBlockers"])
        lines.append("")
    lines.extend(
        [
            "## Asset inventory",
            "",
            "| Path | Package | Version | License | Review | Source | SHA-256 | "
            "Status / blockers |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in report["assets"]:
        status = item["status"]
        if item["blockers"]:
            status += ": " + "; ".join(item["blockers"])
        cells = [
            item["path"],
            item["package"],
            item["version"],
            item["license"],
            item["reviewCategory"],
            item["source"],
            item["sha256"],
            status,
        ]
        lines.append("| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(config: VendorAuditConfig) -> bool:
    report = build_inventory(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "vendored-assets.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(report, config.output_dir / "vendored-assets.md")
    return bool(report["releaseBlocked"])


def _parse_args(argv: Sequence[str] | None = None) -> VendorAuditConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor-root", type=Path, required=True)
    parser.add_argument("--provenance-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    vendor_root = args.vendor_root.resolve()
    provenance_manifest = (
        args.provenance_manifest.resolve()
        if args.provenance_manifest
        else vendor_root / PROVENANCE_FILENAME
    )
    return VendorAuditConfig(vendor_root, provenance_manifest, args.output_dir.resolve())


def main(argv: Sequence[str] | None = None) -> int:
    try:
        blocked = run_audit(_parse_args(argv))
    except (OSError, ValueError) as exc:
        print(f"Vendored asset audit failed: {exc}", file=sys.stderr)
        return 2
    if blocked:
        print(
            "Vendored asset provenance is incomplete; public release is blocked. "
            "See vendored-assets.json and vendored-assets.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
