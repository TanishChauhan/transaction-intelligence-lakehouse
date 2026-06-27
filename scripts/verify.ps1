# Agent Tester — dbt project verification harness
# Runs structural checks + offline dbt validation (no warehouse required for parse/compile)

param(
    [switch]$Strict,
    [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$VenvDbt = Join-Path $ProjectRoot ".venv\Scripts\dbt.exe"
$Failures = @()
$Warnings = @()

function Fail($msg) { $script:Failures += $msg; Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Warn($msg) { $script:Warnings += $msg; Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Pass($msg) { Write-Host "[PASS] $msg" -ForegroundColor Green }

Write-Host "`n=== Agent Tester: dbt Project Verification ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot`n"

# --- Structural checks (Supervisor acceptance criteria) ---
$RequiredFiles = @(
    "dbt_project.yml",
    "packages.yml",
    ".gitignore"
)

$ExpectedDirs = @(
    "models/staging",
    "models/intermediate",
    "models/marts",
    "seeds",
    "macros",
    "tests"
)

$ExpectedSeeds = @(
    "raw_customers.csv",
    "raw_products.csv",
    "raw_orders.csv",
    "raw_payments.csv"
)

foreach ($file in $RequiredFiles) {
    $path = Join-Path $ProjectRoot $file
    if (Test-Path $path) { Pass "Required file exists: $file" }
    else { Fail "Missing required file: $file" }
}

foreach ($dir in $ExpectedDirs) {
    $path = Join-Path $ProjectRoot $dir
    if (Test-Path $path) { Pass "Directory exists: $dir" }
    else { if ($Strict) { Fail "Missing directory: $dir" } else { Warn "Missing directory: $dir" } }
}

foreach ($seed in $ExpectedSeeds) {
    $path = Join-Path $ProjectRoot "seeds\$seed"
    if (Test-Path $path) { Pass "Seed exists: $seed" }
    else { if ($Strict) { Fail "Missing seed: $seed" } else { Warn "Missing seed: $seed" } }
}

# Staging / intermediate / marts SQL models
$ModelDirs = @("models/staging", "models/intermediate", "models/marts")
foreach ($dir in $ModelDirs) {
    $path = Join-Path $ProjectRoot $dir
    if (Test-Path $path) {
        $sqlCount = (Get-ChildItem -Path $path -Filter "*.sql" -Recurse -ErrorAction SilentlyContinue).Count
        if ($sqlCount -gt 0) { Pass "$dir has $sqlCount SQL model(s)" }
        else { Warn "$dir has no SQL models yet" }
    }
}

# Schema YAML with tests
$schemaYmls = Get-ChildItem -Path (Join-Path $ProjectRoot "models") -Filter "*.yml" -Recurse -ErrorAction SilentlyContinue
if ($schemaYmls.Count -gt 0) { Pass "Found $($schemaYmls.Count) schema YAML file(s)" }
else { Warn "No schema YAML files found (tests/docs may be missing)" }

# --- Toolchain checks ---
if (-not (Test-Path $VenvDbt)) {
    Fail "dbt not found in .venv — run: python -m venv .venv && .venv\Scripts\pip install dbt-databricks"
} else {
  Pass "dbt CLI available in .venv"
  $dbtVersion = & $VenvDbt --version 2>&1 | Out-String
  Write-Host "       $($dbtVersion.Trim())" -ForegroundColor DarkGray
}

# --- Offline dbt validation ---
if ((Test-Path (Join-Path $ProjectRoot "dbt_project.yml")) -and (Test-Path $VenvDbt)) {
    Push-Location $ProjectRoot
    try {
        if (-not $SkipDeps) {
            Write-Host "`n--- dbt deps ---" -ForegroundColor Cyan
            & $VenvDbt deps 2>&1 | ForEach-Object { Write-Host $_ }
            if ($LASTEXITCODE -ne 0) { Fail "dbt deps failed (exit $LASTEXITCODE)" }
            else { Pass "dbt deps succeeded" }
        }

        Write-Host "`n--- dbt parse ---" -ForegroundColor Cyan
        & $VenvDbt parse 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { Fail "dbt parse failed (exit $LASTEXITCODE)" }
        else { Pass "dbt parse succeeded" }

        Write-Host "`n--- dbt compile ---" -ForegroundColor Cyan
        & $VenvDbt compile 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { Fail "dbt compile failed (exit $LASTEXITCODE)" }
        else { Pass "dbt compile succeeded" }
    }
    finally {
        Pop-Location
    }
} else {
    Warn "Skipping dbt deps/parse/compile (project or toolchain not ready)"
}

# --- Summary ---
Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Failures: $($Failures.Count)  |  Warnings: $($Warnings.Count)"

if ($Failures.Count -gt 0) {
    Write-Host "`nFailures:" -ForegroundColor Red
    $Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

if ($Warnings.Count -gt 0) {
    Write-Host "`nWarnings:" -ForegroundColor Yellow
    $Warnings | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
}

Write-Host "`nVerification complete." -ForegroundColor Green
exit 0
