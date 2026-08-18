from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import oss_vendored_asset_audit as audit


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(path: Path, *, packages: list[dict], assets: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {"schemaVersion": 1, "packages": packages, "assets": assets},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _accept_test_spdx(monkeypatch: pytest.MonkeyPatch) -> None:
    categories = {
        "MIT": "permissive",
        "MPL-2.0": "file_copyleft_review",
        "GPL-3.0-only": "project_license_decision",
    }
    monkeypatch.setattr(
        audit,
        "validate_spdx_license",
        lambda value: (value in categories, categories.get(value, "manual_or_block")),
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (
            "/* esm.sh - @tiptap/pm@2.27.2/history */\n",
            ("@tiptap/pm", "2.27.2", "history"),
        ),
        (
            "/* esm.sh - highlight.js@11.11.1/lib/core */\n",
            ("highlight.js", "11.11.1", "lib/core"),
        ),
        ("const local = true;\n", None),
    ],
)
def test_parse_esm_header(
    header: str,
    expected: tuple[str, str, str | None] | None,
) -> None:
    parsed = audit.parse_esm_header(header)
    if expected is None:
        assert parsed is None
    else:
        assert parsed == audit.EmbeddedPackage(*expected)


def test_inventory_uses_header_and_exact_asset_provenance(tmp_path: Path) -> None:
    vendor_root = tmp_path / "vendor"
    esm = vendor_root / "esm"
    esm.mkdir(parents=True)
    module = esm / "module.mjs"
    module.write_text(
        "/* esm.sh - @scope/pkg@1.2.3/subpath */\nexport const ok = true;\n",
        encoding="utf-8",
    )
    theme = vendor_root / "theme.css"
    theme.write_text(".ok { color: green; }\n", encoding="utf-8")
    manifest = vendor_root / "THIRD_PARTY_ASSETS.json"
    _write_manifest(
        manifest,
        packages=[
            {
                "name": "@scope/pkg",
                "version": "1.2.3",
                "license": "MPL-2.0",
                "source": "https://registry.example.test/scope-pkg-1.2.3.tgz",
            }
        ],
        assets=[
            {
                "path": "theme.css",
                "package": "theme-package",
                "version": "4.5.6",
                "license": "MIT",
                "source": "https://registry.example.test/theme-package-4.5.6.tgz",
                "sha256": _sha256(theme),
            }
        ],
    )

    report = audit.build_inventory(
        audit.VendorAuditConfig(vendor_root, manifest, tmp_path / "out")
    )

    assert report["networkEnabled"] is False
    assert report["releaseBlocked"] is True
    assert report["summary"] == {
        "assets": 2,
        "complete": 1,
        "blocked": 1,
        "packages": 2,
    }
    by_path = {item["path"]: item for item in report["assets"]}
    assert by_path["esm/module.mjs"]["package"] == "@scope/pkg"
    assert by_path["esm/module.mjs"]["version"] == "1.2.3"
    assert by_path["esm/module.mjs"]["license"] == "MPL-2.0"
    assert by_path["esm/module.mjs"]["reviewCategory"] == "file_copyleft_review"
    assert "license obligations require release review: file_copyleft_review" in by_path[
        "esm/module.mjs"
    ]["blockers"]
    assert by_path["theme.css"]["sha256"] == _sha256(theme)
    assert by_path["theme.css"]["status"] == "complete"


def test_missing_manifest_still_emits_partial_inventory_and_blocks(tmp_path: Path) -> None:
    vendor_root = tmp_path / "vendor"
    vendor_root.mkdir()
    module = vendor_root / "module.mjs"
    module.write_text(
        "/* esm.sh - known-package@7.8.9 */\nexport default 1;\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"
    config = audit.VendorAuditConfig(
        vendor_root,
        vendor_root / "THIRD_PARTY_ASSETS.json",
        output_dir,
    )

    assert audit.run_audit(config) is True

    report = json.loads((output_dir / "vendored-assets.json").read_text(encoding="utf-8"))
    assert report["releaseBlocked"] is True
    assert report["assets"][0]["package"] == "known-package"
    assert report["assets"][0]["version"] == "7.8.9"
    assert report["assets"][0]["license"] is None
    assert report["assets"][0]["source"] is None
    assert "provenance manifest is missing" in report["manifestBlockers"]
    assert (output_dir / "vendored-assets.md").is_file()


def test_permissive_complete_inventory_passes(tmp_path: Path) -> None:
    vendor_root = tmp_path / "vendor"
    vendor_root.mkdir()
    module = vendor_root / "module.mjs"
    module.write_text("/* esm.sh - pkg@1.0.0 */\nexport {};\n", encoding="utf-8")
    manifest = vendor_root / "THIRD_PARTY_ASSETS.json"
    _write_manifest(
        manifest,
        packages=[
            {
                "name": "pkg",
                "version": "1.0.0",
                "license": "MIT",
                "source": "https://registry.example.test/pkg-1.0.0.tgz",
            }
        ],
        assets=[],
    )

    report = audit.build_inventory(
        audit.VendorAuditConfig(vendor_root, manifest, tmp_path / "out")
    )

    assert report["releaseBlocked"] is False
    assert report["assets"][0]["status"] == "complete"


def test_project_license_decision_blocks_release(tmp_path: Path) -> None:
    vendor_root = tmp_path / "vendor"
    vendor_root.mkdir()
    module = vendor_root / "module.mjs"
    module.write_text("/* esm.sh - pkg@1.0.0 */\nexport {};\n", encoding="utf-8")
    manifest = vendor_root / "THIRD_PARTY_ASSETS.json"
    _write_manifest(
        manifest,
        packages=[
            {
                "name": "pkg",
                "version": "1.0.0",
                "license": "GPL-3.0-only",
                "source": "https://registry.example.test/pkg-1.0.0.tgz",
            }
        ],
        assets=[],
    )

    report = audit.build_inventory(
        audit.VendorAuditConfig(vendor_root, manifest, tmp_path / "out")
    )

    assert report["releaseBlocked"] is True
    assert "project license decision is unresolved: project_license_decision" in report[
        "assets"
    ][0]["blockers"]


def test_exact_asset_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    vendor_root = tmp_path / "vendor"
    vendor_root.mkdir()
    asset = vendor_root / "bundle.js"
    asset.write_text("changed();\n", encoding="utf-8")
    manifest = vendor_root / "THIRD_PARTY_ASSETS.json"
    _write_manifest(
        manifest,
        packages=[],
        assets=[
            {
                "path": "bundle.js",
                "package": "bundle",
                "version": "1.0.0",
                "license": "MIT",
                "source": "https://registry.example.test/bundle-1.0.0.tgz",
                "sha256": "0" * 64,
            }
        ],
    )

    report = audit.build_inventory(
        audit.VendorAuditConfig(vendor_root, manifest, tmp_path / "out")
    )

    assert report["releaseBlocked"] is True
    assert "sha256 does not match provenance manifest" in report["assets"][0]["blockers"]


@pytest.mark.parametrize(
    ("license_name", "source", "blocker"),
    [
        (
            "UNKNOWN",
            "https://registry.example.test/pkg.tgz",
            "license is missing or is not a recognized SPDX expression",
        ),
        ("MIT", "http://registry.example.test/pkg.tgz", "source must be an absolute HTTPS URL"),
    ],
)
def test_unresolved_package_metadata_blocks_release(
    tmp_path: Path,
    license_name: str,
    source: str,
    blocker: str,
) -> None:
    vendor_root = tmp_path / "vendor"
    vendor_root.mkdir()
    module = vendor_root / "module.mjs"
    module.write_text("/* esm.sh - pkg@1.0.0 */\nexport {};\n", encoding="utf-8")
    manifest = vendor_root / "THIRD_PARTY_ASSETS.json"
    _write_manifest(
        manifest,
        packages=[
            {
                "name": "pkg",
                "version": "1.0.0",
                "license": license_name,
                "source": source,
            }
        ],
        assets=[],
    )

    report = audit.build_inventory(
        audit.VendorAuditConfig(vendor_root, manifest, tmp_path / "out")
    )

    assert report["releaseBlocked"] is True
    assert blocker in report["assets"][0]["blockers"]


def test_manifest_cannot_relabel_embedded_package(tmp_path: Path) -> None:
    vendor_root = tmp_path / "vendor"
    vendor_root.mkdir()
    module = vendor_root / "module.mjs"
    module.write_text("/* esm.sh - real-pkg@1.0.0 */\nexport {};\n", encoding="utf-8")
    manifest = vendor_root / "THIRD_PARTY_ASSETS.json"
    _write_manifest(
        manifest,
        packages=[],
        assets=[
            {
                "path": "module.mjs",
                "package": "other-pkg",
                "version": "1.0.0",
                "license": "MIT",
                "source": "https://registry.example.test/other-pkg-1.0.0.tgz",
                "sha256": _sha256(module),
            }
        ],
    )

    report = audit.build_inventory(
        audit.VendorAuditConfig(vendor_root, manifest, tmp_path / "out")
    )

    assert report["releaseBlocked"] is True
    assert report["assets"][0]["embeddedPackage"] == "real-pkg"
    assert report["assets"][0]["embeddedVersion"] == "1.0.0"
    assert "manifest package/version conflicts with embedded esm.sh header" in report[
        "assets"
    ][0]["blockers"]


def test_exact_asset_cannot_override_restrictive_package_provenance(tmp_path: Path) -> None:
    vendor_root = tmp_path / "vendor"
    vendor_root.mkdir()
    module = vendor_root / "module.mjs"
    module.write_text("/* esm.sh - pkg@1.0.0 */\nexport {};\n", encoding="utf-8")
    manifest = vendor_root / "THIRD_PARTY_ASSETS.json"
    _write_manifest(
        manifest,
        packages=[
            {
                "name": "pkg",
                "version": "1.0.0",
                "license": "GPL-3.0-only",
                "source": "https://registry.example.test/pkg-1.0.0-gpl.tgz",
            }
        ],
        assets=[
            {
                "path": "module.mjs",
                "package": "pkg",
                "version": "1.0.0",
                "license": "MIT",
                "source": "https://registry.example.test/pkg-1.0.0-mit.tgz",
                "sha256": _sha256(module),
            }
        ],
    )

    report = audit.build_inventory(
        audit.VendorAuditConfig(vendor_root, manifest, tmp_path / "out")
    )

    item = report["assets"][0]
    assert report["releaseBlocked"] is True
    assert item["license"] == "GPL-3.0-only"
    assert item["reviewCategory"] == "project_license_decision"
    assert "exact asset license conflicts with package provenance" in item["blockers"]
    assert "exact asset source conflicts with package provenance" in item["blockers"]


def test_exact_asset_matching_package_provenance_stays_complete(tmp_path: Path) -> None:
    vendor_root = tmp_path / "vendor"
    vendor_root.mkdir()
    module = vendor_root / "module.mjs"
    module.write_text("/* esm.sh - pkg@1.0.0 */\nexport {};\n", encoding="utf-8")
    manifest = vendor_root / "THIRD_PARTY_ASSETS.json"
    source = "https://registry.example.test/pkg-1.0.0.tgz"
    _write_manifest(
        manifest,
        packages=[
            {
                "name": "pkg",
                "version": "1.0.0",
                "license": "MIT",
                "source": source,
            }
        ],
        assets=[
            {
                "path": "module.mjs",
                "package": "pkg",
                "version": "1.0.0",
                "license": "MIT",
                "source": source,
                "sha256": _sha256(module),
            }
        ],
    )

    report = audit.build_inventory(
        audit.VendorAuditConfig(vendor_root, manifest, tmp_path / "out")
    )

    assert report["releaseBlocked"] is False
    assert report["assets"][0]["status"] == "complete"


def test_default_launcher_runs_vendored_audit_without_a_skip_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    launcher = (repo_root / "tools" / "Invoke-OssDependencyAudit.ps1").read_text(
        encoding="utf-8"
    )

    assert '"oss_vendored_asset_audit.py"' in launcher
    assert '"--vendor-root", $VendorRoot' in launcher
    assert '"--output-dir", $OutputDirectory' in launcher
    assert "SkipVendored" not in launcher
