# Agent Tester - BUILD_SPEC.md verification harness
# Runs phase-aware structural checks + offline dbt/pytest validation

param(
    [int]$Phase = 0,
    [switch]$SkipDeps,
    [switch]$SkipPytest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvDbt = Join-Path $ProjectRoot ".venv\Scripts\dbt.exe"
$DbtProjectDir = Join-Path $ProjectRoot "dbt"
$Failures = @()
$Warnings = @()

function Fail($msg) { $script:Failures += $msg; Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Warn($msg) { $script:Warnings += $msg; Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Pass($msg) { Write-Host "[PASS] $msg" -ForegroundColor Green }

function Require-Path($relPath, [switch]$IsDir) {
    $path = Join-Path $ProjectRoot $relPath
    if ($IsDir) {
        if (Test-Path $path) { Pass "Directory: $relPath" } else { Fail "Missing directory: $relPath" }
    } else {
        if (Test-Path $path) { Pass "File: $relPath" } else { Fail "Missing file: $relPath" }
    }
}

Write-Host "`n=== Agent Tester: BUILD_SPEC Verification (Phase <= $Phase) ===" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot`n"

# --- Phase 0: Scaffold ---
Write-Host "--- Phase 0: Scaffold ---" -ForegroundColor Cyan
Require-Path "BUILD_SPEC.md"
Require-Path "README.md"
Require-Path "pyproject.toml"
Require-Path "docs/architecture.md"
Require-Path ".gitignore"
Require-Path "generator" -IsDir
Require-Path "ingestion" -IsDir
Require-Path "dbt" -IsDir
Require-Path "infra/terraform" -IsDir
Require-Path "tests" -IsDir
Require-Path ".github/workflows" -IsDir

if (Test-Path (Join-Path $ProjectRoot "README.md")) {
    $readme = Get-Content (Join-Path $ProjectRoot "README.md") -Raw
    if ($readme -match "Databricks Free Edition") { Pass "README mentions Databricks Free Edition" }
    else { Warn "README missing Free Edition setup notes" }
}

# --- Phase 1: Synthetic source ---
if ($Phase -ge 1) {
    Write-Host "`n--- Phase 1: Synthetic source ---" -ForegroundColor Cyan
    Require-Path "generator/config.py"
    Require-Path "generator/reference_data.py"
    Require-Path "generator/generate_transactions.py"
    Require-Path "tests/test_generator.py"
}

# --- Phase 2: Terraform ---
if ($Phase -ge 2) {
    Write-Host "`n--- Phase 2: Terraform ---" -ForegroundColor Cyan
    $tfFiles = Get-ChildItem (Join-Path $ProjectRoot "infra/terraform") -Filter "*.tf" -ErrorAction SilentlyContinue
    if ($tfFiles.Count -gt 0) { Pass "Terraform files present ($($tfFiles.Count))" }
    else { Fail "No .tf files in infra/terraform" }
}

# --- Phase 3: Bronze ingestion ---
if ($Phase -ge 3) {
    Write-Host "`n--- Phase 3: Bronze ingestion ---" -ForegroundColor Cyan
    Require-Path "ingestion/bronze_autoloader.py"
}

# --- Phase 4: dbt silver ---
if ($Phase -ge 4) {
    Write-Host "`n--- Phase 4: dbt silver ---" -ForegroundColor Cyan
    Require-Path "dbt/dbt_project.yml"
    Require-Path "dbt/profiles.yml.example"
    Require-Path "dbt/models/silver" -IsDir
    $silverSql = Get-ChildItem (Join-Path $ProjectRoot "dbt/models/silver") -Filter "*.sql" -Recurse -ErrorAction SilentlyContinue
    if ($silverSql.Count -ge 2) { Pass "Silver models: $($silverSql.Count) SQL file(s)" }
    else { Fail "Expected at least 2 silver SQL models (stg_transactions, silver_transactions)" }
    $silverYml = Get-ChildItem (Join-Path $ProjectRoot "dbt/models/silver") -Filter "*_silver.yml" -ErrorAction SilentlyContinue
    if ($silverYml) { Pass "Silver schema YAML present" } else { Fail "Missing _silver.yml with tests" }
}

# --- Phase 5: dbt gold ---
if ($Phase -ge 5) {
    Write-Host "`n--- Phase 5: dbt gold ---" -ForegroundColor Cyan
    Require-Path "dbt/models/gold" -IsDir
    $goldModels = @("dim_customer", "dim_merchant", "fct_transaction", "fraud_signals", "customer_spend_daily", "merchant_risk_daily")
    foreach ($m in $goldModels) {
        $found = Get-ChildItem (Join-Path $ProjectRoot "dbt/models/gold") -Filter "$m.sql" -Recurse -ErrorAction SilentlyContinue
        if ($found) { Pass "Gold model: $m.sql" } else { Fail "Missing gold model: $m.sql" }
    }
    $goldYml = Get-ChildItem (Join-Path $ProjectRoot "dbt/models/gold") -Filter "*_gold.yml" -ErrorAction SilentlyContinue
    if ($goldYml) { Pass "Gold schema YAML with exposures present" } else { Fail "Missing _gold.yml" }
}

# --- Phase 6: DAB orchestration ---
if ($Phase -ge 6) {
    Write-Host "`n--- Phase 6: DAB orchestration ---" -ForegroundColor Cyan
    Require-Path "databricks.yml"
    Require-Path "resources" -IsDir
}

# --- Phase 7: CI/CD ---
if ($Phase -ge 7) {
    Write-Host "`n--- Phase 7: CI/CD ---" -ForegroundColor Cyan
    $workflows = Get-ChildItem (Join-Path $ProjectRoot ".github/workflows") -Filter "*.yml" -ErrorAction SilentlyContinue
    if ($workflows.Count -gt 0) { Pass "GitHub Actions workflow(s): $($workflows.Count)" }
    else { Fail "No GitHub Actions workflows" }
}

# --- Toolchain ---
Write-Host "`n--- Toolchain ---" -ForegroundColor Cyan
if (Test-Path $VenvPython) { Pass "Python venv present" } else { Warn "No .venv - create with: python -m venv .venv" }
if (Test-Path $VenvDbt) {
    Pass "dbt CLI in venv"
    $dbtVer = & $VenvDbt --version 2>&1 | Out-String
    Write-Host "       $($dbtVer.Trim())" -ForegroundColor DarkGray
} else {
    Warn "dbt not installed in venv"
}

# --- pytest (Phase 1+) ---
if (-not $SkipPytest -and $Phase -ge 1 -and (Test-Path $VenvPython)) {
    Write-Host "`n--- pytest ---" -ForegroundColor Cyan
    Push-Location $ProjectRoot
    try {
        & $VenvPython -m pytest 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { Fail "pytest failed (exit $LASTEXITCODE)" }
        else { Pass "pytest passed" }
    } finally { Pop-Location }
}

# --- dbt offline validation (Phase 4+) ---
$dbtProjectYml = Join-Path $DbtProjectDir "dbt_project.yml"
if ($Phase -ge 4 -and (Test-Path $dbtProjectYml) -and (Test-Path $VenvDbt)) {
    Push-Location $DbtProjectDir
    try {
        if (-not $SkipDeps) {
            Write-Host "`n--- dbt deps ---" -ForegroundColor Cyan
            & $VenvDbt deps --profiles-dir "." 2>&1 | ForEach-Object { Write-Host $_ }
            if ($LASTEXITCODE -ne 0) { Fail "dbt deps failed" } else { Pass "dbt deps OK" }
        }
        Write-Host "`n--- dbt parse ---" -ForegroundColor Cyan
        & $VenvDbt parse --profiles-dir "." 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { Fail "dbt parse failed" } else { Pass "dbt parse OK" }
        Warn "dbt compile/build skipped offline (require a live SQL warehouse connection)"
    } finally { Pop-Location }
}

# --- Security: no secrets git-TRACKED (gitignored local files are fine) ---
Write-Host "`n--- Security scan ---" -ForegroundColor Cyan
$tracked = & git -C $ProjectRoot ls-files 2>$null
$sensitive = $tracked | Where-Object {
    $b = ($_ -split '/')[-1]
    ($b -eq 'profiles.yml') -or ($b -eq '.env') -or ($b -like '*.tfstate') -or ($b -like '*.tfstate.*')
}
if ($sensitive) {
    foreach ($s in $sensitive) { Fail "Sensitive file is git-tracked: $s" }
} else {
    Pass "No sensitive files git-tracked (profiles.yml / .env / *.tfstate)"
}

# --- Summary ---
Write-Host "`n=== Summary (Phase $Phase) ===" -ForegroundColor Cyan
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
