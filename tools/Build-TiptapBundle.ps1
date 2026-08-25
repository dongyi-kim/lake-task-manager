param(
  [string]$NpmCommand = "npm"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SpecDir = Join-Path $PSScriptRoot "tiptap-bundle"
$BuildDir = Join-Path $RepoRoot ".cache\tiptap-bundle-build"
$NpmCache = Join-Path $RepoRoot ".cache\npm"
$Output = Join-Path $RepoRoot "app\static\vendor\tiptap.bundle.mjs"

if (Test-Path -LiteralPath $BuildDir) {
  Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildDir | Out-Null
try {
  Copy-Item -LiteralPath (Join-Path $SpecDir "package.json") -Destination $BuildDir
  Copy-Item -LiteralPath (Join-Path $SpecDir "package-lock.json") -Destination $BuildDir
  Copy-Item -LiteralPath (Join-Path $SpecDir "entry.mjs") -Destination $BuildDir
  & $NpmCommand ci --prefix $BuildDir --cache $NpmCache --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed ($LASTEXITCODE)" }
  $Esbuild = Join-Path $BuildDir "node_modules\.bin\esbuild.cmd"
  & $Esbuild (Join-Path $BuildDir "entry.mjs") --bundle --format=esm --platform=browser `
    --target=es2020 --minify --legal-comments=inline --outfile=$Output
  if ($LASTEXITCODE -ne 0) { throw "esbuild failed ($LASTEXITCODE)" }
} finally {
  if (Test-Path -LiteralPath $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
  }
  if (Test-Path -LiteralPath $NpmCache) {
    Remove-Item -LiteralPath $NpmCache -Recurse -Force
  }
}
