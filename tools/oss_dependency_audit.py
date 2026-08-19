"""Generate an offline SBOM/license inventory and an opt-in vulnerability audit."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable, Sequence

try:  # Installed in the isolated audit environment, not in the app runtime.
    from license_expression import get_spdx_licensing
except ImportError:  # Unit tests may import the offline planner without audit extras.
    get_spdx_licensing = None


TOOL_VERSIONS = {
    "cyclonedx-bom": "7.3.1",
    "license-expression": "30.4.4",
    "pip-audit": "2.10.1",
    "pip-licenses": "5.5.5",
}
VULNERABILITY_ENDPOINTS = {
    "osv": "https://api.osv.dev/v1/query",
    "pypi": "https://pypi.org/pypi/<package>/<version>/json",
}


@dataclass(frozen=True)
class AuditConfig:
    target_python: Path
    output_dir: Path
    vulnerability_audit: bool = False
    vulnerability_service: str = "osv"


_CATEGORY_PRIORITY = {
    "permissive": 0,
    "manual_review": 1,
    "file_copyleft_review": 2,
    "library_copyleft_review": 3,
    "project_license_decision": 4,
    "manual_or_block": 5,
}


def _spdx_key_category(key: str) -> str:
    folded = str(key or "").strip().upper()
    if folded == "BSL-1.0":
        return "permissive"
    if (folded.startswith(("MIT", "BSD-", "APACHE-", "ISC"))
            or folded in {"0BSD"}):
        return "permissive"
    if folded.startswith("MPL-"):
        return "file_copyleft_review"
    if folded.startswith("LGPL-"):
        return "library_copyleft_review"
    if folded.startswith(("GPL-", "AGPL-")):
        return "project_license_decision"
    if (folded.startswith(("BUSL-", "SSPL-", "ELASTIC-", "LICENSEREF-"))
            or "-NC-" in folded or "-ND-" in folded):
        return "manual_or_block"
    return "manual_review"


def _classify_valid_spdx(value: str) -> tuple[str, str] | None:
    if get_spdx_licensing is None:
        return None
    licensing = get_spdx_licensing()
    try:
        validation = licensing.validate(value)
        if validation.errors:
            return None
        expression = licensing.parse(value, validate=True)
        categories = [_spdx_key_category(key) for key in licensing.license_keys(expression)]
    except Exception:
        return None
    if not categories:
        return None
    category = max(categories, key=_CATEGORY_PRIORITY.__getitem__)
    return category, "parsed complete SPDX expression; most restrictive term selected"


def classify_license(value: str | None) -> tuple[str, str]:
    """Classify metadata conservatively; this is a review queue, not legal advice."""
    license_name = (value or "").strip()
    folded = license_name.upper()
    if not folded or folded in {"UNKNOWN", "NONE", "UNLICENSED"}:
        return "manual_or_block", "missing or unknown license metadata"
    if folded in {"BSL-1.0", "BOOST SOFTWARE LICENSE", "BOOST SOFTWARE LICENSE 1.0"}:
        return "permissive", "recognized Boost Software License 1.0"
    if folded == "BSL":
        return "manual_or_block", "ambiguous BSL metadata requires exact license identity"
    if re.search(
        r"\bSSPL\b|\bBUSL\b|BUSINESS SOURCE|\bBSL[- ]1\.1\b|"
        r"\bELASTIC(?:\b|[- ])|LICENSEREF|PROPRIETARY|"
        r"\b(?:CC[- ]?)?BY[- ](?:NC|ND)\b|\bNC\b|NON[- ]?COMMERCIAL|"
        r"NO[- ]DERIVATIVES",
        folded,
    ):
        return "manual_or_block", "source-available or non-commercial terms require review"
    spdx = _classify_valid_spdx(license_name)
    if spdx is not None:
        return spdx
    compound_parts = [
        part.strip(" ()")
        for part in re.split(r"\s+(?:AND|OR|WITH)\s+", license_name, flags=re.I)
        if part.strip(" ()")
    ]
    if len(compound_parts) > 1:
        categories = [classify_license(part)[0] for part in compound_parts]
        category = max(categories, key=_CATEGORY_PRIORITY.__getitem__)
        return category, "conservative fallback for an unparsed compound expression"
    if (
        "LGPL" in folded
        or "LESSER GENERAL PUBLIC LICENSE" in folded
        or "LIBRARY GENERAL PUBLIC LICENSE" in folded
    ):
        return "library_copyleft_review", "LGPL library/linking obligations require review"
    if "MPL" in folded or "MOZILLA PUBLIC LICENSE" in folded:
        return "file_copyleft_review", "MPL file-level obligations require review"
    if "AGPL" in folded or "GPL" in folded or "GENERAL PUBLIC LICENSE" in folded:
        return "project_license_decision", "GPL-family compatibility depends on project licensing"
    if re.fullmatch(
        r"(?:MIT(?: LICENSE)?|"
        r"BSD(?:[- ](?:2|3)(?:[- ]CLAUSE)?(?: LICENSE)?| LICENSE)?|"
        r"APACHE(?: SOFTWARE)? LICENSE(?: VERSION)? 2(?:\.0)?|APACHE-2\.0|"
        r"ISC(?: LICENSE)?)",
        folded,
    ):
        return "permissive", "recognized MIT/BSD/Apache/ISC family"
    return "manual_review", "license is not in the automated policy map"


def enrich_license_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        category, rationale = classify_license(str(row.get("License") or ""))
        enriched.append(
            {
                **row,
                "PolicyCategory": category,
                "PolicyRationale": rationale,
            }
        )
    return sorted(enriched, key=lambda row: str(row.get("Name", "")).casefold())


def write_license_outputs(raw_path: Path, json_path: Path, markdown_path: Path) -> None:
    rows = json.loads(raw_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("pip-licenses output must be a JSON array")
    enriched = enrich_license_rows(row for row in rows if isinstance(row, dict))
    json_path.write_text(
        json.dumps(enriched, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for row in enriched:
        category = str(row["PolicyCategory"])
        counts[category] = counts.get(category, 0) + 1
    lines = [
        "# Python dependency license review",
        "",
        "> Automated classification is a review queue, not legal advice.",
        "",
        "## Summary",
        "",
        "| Category | Packages |",
        "|---|---:|",
    ]
    lines.extend(f"| `{category}` | {count} |" for category, count in sorted(counts.items()))
    lines.extend(
        [
            "",
            "## Inventory",
            "",
            "| Package | Version | Declared license | Review category | NOTICE |",
            "|---|---|---|---|---|",
        ]
    )
    for row in enriched:
        notice = row.get("NoticeText")
        has_notice = bool(notice and str(notice).strip().upper() != "UNKNOWN")
        cells = [
            row.get("Name", ""),
            row.get("Version", ""),
            row.get("License", ""),
            row.get("PolicyCategory", ""),
            "yes" if has_notice else "no/unknown",
        ]
        escaped = [str(cell).replace("|", "\\|").replace("\n", " ") for cell in cells]
        lines.append("| " + " | ".join(escaped) + " |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def offline_commands(config: AuditConfig, audit_python: Path) -> list[list[str]]:
    sbom = config.output_dir / "sbom.cdx.json"
    raw_licenses = config.output_dir / "licenses.raw.json"
    return [
        [
            str(audit_python),
            "-m",
            "cyclonedx_py",
            "environment",
            str(config.target_python),
            "--spec-version",
            "1.6",
            "--output-format",
            "JSON",
            "--output-reproducible",
            "--output-file",
            str(sbom),
        ],
        [
            str(audit_python),
            "-m",
            "piplicenses",
            "--python",
            str(config.target_python),
            "--format",
            "json",
            "--with-urls",
            "--with-license-file",
            "--with-notice-file",
            "--no-license-path",
            "--output-file",
            str(raw_licenses),
        ],
    ]


def vulnerability_command(
    config: AuditConfig,
    audit_python: Path,
    site_paths: Sequence[Path],
) -> list[str]:
    if config.vulnerability_service not in VULNERABILITY_ENDPOINTS:
        raise ValueError(f"unsupported vulnerability service: {config.vulnerability_service}")
    command = [str(audit_python), "-m", "pip_audit"]
    for site_path in site_paths:
        command.extend(("--path", str(site_path)))
    command.extend(
        (
            "--vulnerability-service",
            config.vulnerability_service,
            "--format",
            "json",
            "--output",
            str(config.output_dir / "vulnerabilities.json"),
            "--cache-dir",
            str(config.output_dir / "pip-audit-http-cache"),
            "--progress-spinner",
            "off",
        )
    )
    return command


def _run(command: Sequence[str], accepted_codes: tuple[int, ...] = (0,)) -> int:
    env = {**os.environ, "PYTHONUTF8": "1"}
    result = subprocess.run(command, check=False, env=env)  # noqa: S603
    if result.returncode not in accepted_codes:
        rendered = subprocess.list2cmdline(list(command))
        raise RuntimeError(f"command failed with exit {result.returncode}: {rendered}")
    return result.returncode


def _verify_tool_versions() -> None:
    mismatches = []
    for distribution, expected in TOOL_VERSIONS.items():
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            actual = "missing"
        if actual != expected:
            mismatches.append(f"{distribution} expected {expected}, found {actual}")
    if mismatches:
        detail = "; ".join(mismatches)
        raise RuntimeError(
            f"OSS audit tool environment does not match requirements-oss-audit.txt: {detail}"
        )


def _target_site_paths(target_python: Path) -> list[Path]:
    script = (
        "import json,sysconfig;"
        "p=sysconfig.get_paths();"
        "print(json.dumps(sorted(set([p['purelib'],p['platlib']]))))"
    )
    result = subprocess.run(
        [str(target_python), "-c", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"cannot inspect target Python: {result.stderr.strip()}")
    paths = [Path(value).resolve() for value in json.loads(result.stdout)]
    if not paths or any(not path.is_dir() for path in paths):
        raise RuntimeError("target Python returned a missing site-packages path")
    return paths


def _write_manifest(config: AuditConfig, vulnerability_exit_code: int | None) -> None:
    service = config.vulnerability_service if config.vulnerability_audit else None
    manifest = {
        "schemaVersion": 1,
        "targetPython": str(config.target_python),
        "toolVersions": TOOL_VERSIONS,
        "offlineArtifacts": [
            "sbom.cdx.json",
            "licenses.raw.json",
            "licenses.json",
            "licenses.md",
        ],
        "networkEnabled": config.vulnerability_audit,
        "vulnerabilityService": service,
        "vulnerabilityEndpoint": VULNERABILITY_ENDPOINTS.get(service or ""),
        "vulnerabilityExitCode": vulnerability_exit_code,
    }
    (config.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_audit(config: AuditConfig) -> None:
    _verify_tool_versions()
    if not config.target_python.is_file():
        raise FileNotFoundError(f"target Python not found: {config.target_python}")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    audit_python = Path(sys.executable).resolve()
    for command in offline_commands(config, audit_python):
        _run(command)
    write_license_outputs(
        config.output_dir / "licenses.raw.json",
        config.output_dir / "licenses.json",
        config.output_dir / "licenses.md",
    )
    vulnerability_exit_code = None
    if config.vulnerability_audit:
        if config.vulnerability_service not in VULNERABILITY_ENDPOINTS:
            raise ValueError(
                f"unsupported vulnerability service: {config.vulnerability_service}"
            )
        endpoint = VULNERABILITY_ENDPOINTS[config.vulnerability_service]
        print(
            "NETWORK EGRESS ENABLED: package names and exact versions from the target "
            f"environment will be sent to {endpoint}.",
            file=sys.stderr,
        )
        site_paths = _target_site_paths(config.target_python)
        vulnerability_output = config.output_dir / "vulnerabilities.json"
        vulnerability_output.unlink(missing_ok=True)
        vulnerability_exit_code = _run(
            vulnerability_command(config, audit_python, site_paths),
            accepted_codes=(0, 1),
        )
        if not vulnerability_output.is_file():
            raise RuntimeError(
                "pip-audit returned without a vulnerability result; exit 1 can also mean a "
                "service or dependency-source failure"
            )
        try:
            json.loads(vulnerability_output.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("pip-audit wrote invalid vulnerability JSON") from exc
    _write_manifest(config, vulnerability_exit_code)


def _parse_args(argv: Sequence[str] | None = None) -> AuditConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-python", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--vulnerability-audit", action="store_true")
    parser.add_argument(
        "--vulnerability-service",
        choices=sorted(VULNERABILITY_ENDPOINTS),
        default="osv",
    )
    args = parser.parse_args(argv)
    return AuditConfig(
        target_python=args.target_python.resolve(),
        output_dir=args.output_dir.resolve(),
        vulnerability_audit=args.vulnerability_audit,
        vulnerability_service=args.vulnerability_service,
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        run_audit(_parse_args(argv))
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"OSS dependency audit failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
