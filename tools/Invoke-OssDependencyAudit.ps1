[CmdletBinding()]
param(
    [string]$TargetPython,
    [string]$AuditPython,
    [string]$OutputDirectory,
    [string]$VendorRoot,
    [string]$VendorProvenanceManifest,
    [switch]$VulnerabilityAudit,
    [ValidateSet("osv", "pypi")]
    [string]$VulnerabilityService = "osv"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $TargetPython) {
    $TargetPython = Join-Path $repoRoot "..\.venv\Scripts\python.exe"
}
if (-not $AuditPython) {
    $AuditPython = Join-Path $repoRoot ".cache\oss-audit-venv\Scripts\python.exe"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot ".cache\oss-audit"
}
if (-not $VendorRoot) {
    $VendorRoot = Join-Path $repoRoot "app\static\vendor"
}

if (-not (Test-Path -LiteralPath $AuditPython -PathType Leaf)) {
    throw "Audit Python not found at '$AuditPython'. Create the isolated environment and install requirements-oss-audit.txt first."
}
if (-not (Test-Path -LiteralPath $TargetPython -PathType Leaf)) {
    throw "Target Python not found at '$TargetPython'."
}

$auditScript = Join-Path $PSScriptRoot "oss_dependency_audit.py"
$auditArgs = @(
    $auditScript,
    "--target-python", $TargetPython,
    "--output-dir", $OutputDirectory,
    "--vulnerability-service", $VulnerabilityService
)
if ($VulnerabilityAudit) {
    $endpoint = if ($VulnerabilityService -eq "osv") {
        "https://api.osv.dev/v1/query"
    } else {
        "https://pypi.org/pypi/<package>/<version>/json"
    }
    Write-Warning "NETWORK EGRESS ENABLED: package names and exact versions from the target environment will be sent to $endpoint."
    $auditArgs += "--vulnerability-audit"
} else {
    Write-Host "Offline mode: generating SBOM and license/NOTICE inventory only."
}

& $AuditPython @auditArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$vendoredAuditScript = Join-Path $PSScriptRoot "oss_vendored_asset_audit.py"
$vendoredAuditArgs = @(
    $vendoredAuditScript,
    "--vendor-root", $VendorRoot,
    "--output-dir", $OutputDirectory
)
if ($VendorProvenanceManifest) {
    $vendoredAuditArgs += @("--provenance-manifest", $VendorProvenanceManifest)
}

Write-Host "Offline mode: inventorying vendored static assets without network access."
& $AuditPython @vendoredAuditArgs
$vendoredAuditExitCode = $LASTEXITCODE
Write-Host "OSS audit artifacts: $OutputDirectory"
if ($vendoredAuditExitCode -ne 0) {
    exit $vendoredAuditExitCode
}
