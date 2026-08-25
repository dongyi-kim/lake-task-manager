# OSS dependency audit

The dependency audit is development-only and isolated from `requirements.txt`. Its default run is
offline: it reads metadata from a selected Python environment, writes a CycloneDX SBOM plus a
license/NOTICE inventory, and checks copied front-end assets under `app/static/vendor/`. Artifacts
are written under `.cache/oss-audit/`.

## Setup and offline run

Use an approved package index or wheelhouse when installing the tools. Installation can itself use
the configured package index; that is separate from running the audit.

```powershell
python -m venv .cache\oss-audit-venv
.cache\oss-audit-venv\Scripts\python.exe -m pip install -r requirements-oss-audit.txt
.\tools\Invoke-OssDependencyAudit.ps1
```

The four direct tools are exactly pinned: `cyclonedx-bom==7.3.1` (Apache-2.0),
`license-expression==30.4.4` (Apache-2.0), `pip-audit==2.10.1` (Apache-2.0), and
`pip-licenses==5.5.5` (MIT). They are not imported by the app. SPDX expressions are parsed as a
whole; a restrictive or unknown compound term cannot be hidden by an adjacent permissive term.
`cyclonedx-bom` is intentionally retained: `pip-audit --dry-run --format cyclonedx-json` collects
dependencies but emits an empty component list, so it cannot serve as the offline SBOM generator.

The default artifacts are:

- `sbom.cdx.json`: reproducible CycloneDX 1.6 JSON for the target environment.
- `licenses.raw.json`: package metadata, full discovered license texts, URLs, and NOTICE texts.
- `licenses.json` and `licenses.md`: the same inventory with conservative review categories.
- `manifest.json`: tool versions and a machine-readable `networkEnabled: false` declaration.
- `vendored-assets.json` and `vendored-assets.md`: per-file package, version, SPDX license, source,
  SHA-256, provenance evidence, and blockers for copied JS/CSS/module assets.

Pass `-TargetPython` to inspect another environment. Pass `-AuditPython` only when the isolated tool
environment lives elsewhere. Outputs should remain in an ignored or access-controlled directory,
because package names and versions can reveal internal components.

## Vendored static asset release gate

The Python SBOM does not include files copied into `app/static/vendor/`. The offline vendored-asset
check recursively inventories every file (not only entry points), computes its SHA-256, and accepts
metadata only from either an embedded `esm.sh` header or
`app/static/vendor/THIRD_PARTY_ASSETS.json`. It never queries npm, esm.sh, a source repository, or a
license service. Missing, conflicting, invalid, or stale provenance makes the launcher exit 1 after
writing the complete partial inventory.

The editor vendor layout was compacted after the 2026-08-18 baseline:

- The recursive esm.sh mirror was replaced by `vendor/tiptap.bundle.mjs`; the browser now requests
  one editor module instead of hundreds of hashed modules.
- `tools/tiptap-bundle/package.json`, `package-lock.json`, and `Build-TiptapBundle.ps1` pin the npm
  inputs and reproduce the esbuild output while keeping install/build artifacts under `.cache/`.
- The public-release gate still requires authoritative package license/source records and an exact
  hash record for the generated bundle. A generated bundle is not treated as its own dependency.

Do not copy license values from memory or infer them from package popularity. Populate
`THIRD_PARTY_ASSETS.json` from the exact source archives/repositories and archive the applicable
license/NOTICE texts before publication. Package-level records cover files whose embedded header
already identifies the exact package and version; exact-asset records cover files without such a
header and bind the claim to the current content hash. If both record levels apply, package-level
license/source metadata is authoritative and any duplicated exact-asset values must match it;
an exact file record cannot weaken or replace package terms:

```json
{
  "schemaVersion": 1,
  "packages": [
    {
      "name": "example-package",
      "version": "1.2.3",
      "license": "MIT",
      "source": "https://packages.example.test/example-package-1.2.3.tgz"
    }
  ],
  "assets": [
    {
      "path": "example.min.js",
      "package": "example-package",
      "version": "1.2.3",
      "license": "MIT",
      "source": "https://packages.example.test/example-package-1.2.3.tgz",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```

Licenses must be complete recognized SPDX expressions and sources must be absolute HTTPS URLs.
`license-expression` validates the expression. Only a permissive category can complete this
automated release gate. MPL/LGPL are not rejected, but remain blocked pending documented
file/library-copyleft obligation review; GPL/AGPL remain blocked pending the project-license
decision. Manual categories likewise remain blocked. Current internal/non-commercial use does not
waive any of those duties. A complete provenance row is evidence, not legal approval.

`app/static/fonts.css` is outside the vendored directory and currently declares a runtime jsDelivr
fetch for Pretendard. Review that external request, its OFL attribution, and offline behavior as a
separate deployment/publication item; it is not silently treated as a vendored file.

## Explicit online vulnerability audit

No vulnerability service is contacted unless `-VulnerabilityAudit` is present:

```powershell
.\tools\Invoke-OssDependencyAudit.ps1 -VulnerabilityAudit -VulnerabilityService osv
```

This switch sends every discovered package name and exact version to the selected service and caches
responses below the output directory:

- `osv`: POST to `https://api.osv.dev/v1/query` with PyPI package name and version.
- `pypi`: GET `https://pypi.org/pypi/<package>/<version>/json`; name and version are in the URL.

The launcher prints this disclosure before enabling egress. It never passes `--fix`; findings cannot
modify the target environment. `pip-audit` exit code 1 means vulnerabilities were found and is stored
in the manifest rather than treated as a launcher failure.

## License review policy

The generated categories route review; they are not legal advice and do not make an automatic final
license decision.

| Category | Treatment |
|---|---|
| `permissive` | MIT, BSD, Apache, ISC, and Boost Software License 1.0 families; retain copyright/license and applicable NOTICE text on distribution. |
| `file_copyleft_review` | MPL; review file-level source and notice obligations. |
| `library_copyleft_review` | LGPL; review library/linking, relinking, source, and notice obligations. |
| `project_license_decision` | GPL/AGPL; decide compatibility with the project's eventual license and distribution model. |
| `manual_or_block` | SSPL, Business Source/BSL 1.1, Elastic, non-commercial terms, or missing/unknown metadata; do not adopt or distribute until reviewed. |
| `manual_review` | Other identifiers (for example PSF) that the narrow automatic map does not decide. |

Current internal and non-commercial use does not erase license obligations; revenue is not the
classification criterion. The project is expected to become open source, so review distribution,
modification, source-offer, attribution, and NOTICE duties before publishing artifacts.

The exact reviewed audit-tool environment included MPL packages (`certifi`, `fqdn`) and LGPL
`chardet` transitively. These are review items, not automatic failures. It also contained NOTICE text
for `cyclonedx-bom`, `cyclonedx-python-lib`, `license-expression`, and `requests`. If the audit tool
environment itself is redistributed, archive its generated license/NOTICE inventory and satisfy the
applicable obligations; merely keeping the dev environment local does not add these packages to the
LTM runtime distribution.
