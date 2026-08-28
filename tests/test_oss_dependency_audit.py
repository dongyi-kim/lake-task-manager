from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.oss_dependency_audit import (
    AuditConfig,
    TOOL_VERSIONS,
    classify_license,
    offline_commands,
    vulnerability_command,
    write_license_outputs,
)


def test_license_policy_is_conservative() -> None:
    cases = [
        ("MIT", "permissive"),
        ("Apache-2.0 OR BSD-2-Clause", "permissive"),
        ("BSL-1.0", "permissive"),
        ("Boost Software License 1.0", "permissive"),
        ("Mozilla Public License 2.0 (MPL 2.0)", "file_copyleft_review"),
        ("GNU Lesser General Public License v2+", "library_copyleft_review"),
        ("GPL-3.0-only", "project_license_decision"),
        ("AGPL-3.0", "project_license_decision"),
        ("SSPL-1.0", "manual_or_block"),
        ("BSL", "manual_or_block"),
        ("Business Source License 1.1", "manual_or_block"),
        ("Elastic-2.0", "manual_or_block"),
        ("CC-BY-NC-4.0", "manual_or_block"),
        ("MIT AND LicenseRef-Proprietary", "manual_or_block"),
        ("Boost Software License 1.0 AND GPL-3.0-only", "project_license_decision"),
        ("Boost Software License 1.0 / Proprietary", "manual_or_block"),
        ("MIT / Unknown-License", "manual_review"),
        ("MIT, UNKNOWN", "manual_review"),
        ("MIT AND CC-BY-ND-4.0", "manual_or_block"),
        ("UNKNOWN", "manual_or_block"),
        ("PSF-2.0", "manual_review"),
    ]
    failures = {}
    for license_name, expected in cases:
        actual = classify_license(license_name)[0]
        if actual != expected:
            failures[license_name] = {"expected": expected, "actual": actual}
    assert not failures, failures


def test_offline_plan_contains_no_vulnerability_client(tmp_path: Path) -> None:
    config = AuditConfig(Path("target-python"), tmp_path)
    commands = offline_commands(config, Path("audit-python"))

    flattened = [token for command in commands for token in command]
    assert "pip_audit" not in flattened
    assert "cyclonedx_py" in flattened
    assert "piplicenses" in flattened
    assert config.vulnerability_audit is False


def test_vulnerability_plan_is_read_only_and_scoped_to_target_paths(tmp_path: Path) -> None:
    config = AuditConfig(
        Path("target-python"),
        tmp_path,
        vulnerability_audit=True,
        vulnerability_service="osv",
    )
    command = vulnerability_command(
        config,
        Path("audit-python"),
        [Path("target-site"), Path("target-plat")],
    )

    assert command.count("--path") == 2
    assert "target-site" in command and "target-plat" in command
    assert command[command.index("--vulnerability-service") + 1] == "osv"
    assert "--fix" not in command


def test_license_outputs_preserve_notices_and_add_review_category(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    json_path = tmp_path / "licenses.json"
    markdown_path = tmp_path / "licenses.md"
    raw_path.write_text(
        json.dumps(
            [
                {
                    "Name": "atlas-lib",
                    "Version": "1.2.3",
                    "License": "MPL-2.0",
                    "LicenseText": "license body",
                    "NoticeText": "copyright Atlas",
                }
            ]
        ),
        encoding="utf-8",
    )

    write_license_outputs(raw_path, json_path, markdown_path)

    row = json.loads(json_path.read_text(encoding="utf-8"))[0]
    assert row["PolicyCategory"] == "file_copyleft_review"
    assert row["LicenseText"] == "license body"
    assert row["NoticeText"] == "copyright Atlas"
    assert "| atlas-lib | 1.2.3 | MPL-2.0 | file_copyleft_review | yes |" in (
        markdown_path.read_text(encoding="utf-8")
    )


def test_direct_tool_pins_match_runtime_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    requirement_lines = {
        line.strip()
        for line in (repo_root / "requirements-oss-audit.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert requirement_lines == {
        f"{distribution}=={version}" for distribution, version in TOOL_VERSIONS.items()
    }


def test_powershell_launcher_requires_explicit_network_switch() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    launcher = (repo_root / "tools" / "Invoke-OssDependencyAudit.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch]$VulnerabilityAudit" in launcher
    assert 'if ($VulnerabilityAudit)' in launcher
    assert 'NETWORK EGRESS ENABLED' in launcher
    assert '$auditArgs += "--vulnerability-audit"' in launcher
