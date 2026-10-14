# One-time setup for Windows PowerShell. Run from the repo folder:  .\setup.ps1
# If PowerShell refuses to run it:  Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
#
# What it does:
#   1. Finds a Python 3.10 to 3.13 whose ssl module works (pip cannot reach PyPI without it).
#   2. Builds .venv from that Python. Rebuilds .venv if the existing one has no working ssl.
#   3. Installs the packages, creates .env, and runs the tests.

$ErrorActionPreference = "Continue"     # native tools write to stderr; exit codes are checked by hand

function Test-PythonSsl {
    param([string]$Exe, [string[]]$Prefix = @())
    try {
        & $Exe @Prefix -c "import ssl, sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)" 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Stop-Setup {
    param([string]$Message)
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    exit 1
}

# ---- 1. find a usable base Python ---------------------------------------------
$baseExe = $null
$basePrefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.12", "3.13", "3.11", "3.10")) {
        if (Test-PythonSsl -Exe "py" -Prefix @("-$v")) {
            $baseExe = "py"
            $basePrefix = @("-$v")
            break
        }
    }
}
if (-not $baseExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Test-PythonSsl -Exe "python") { $baseExe = "python" }
}
if (-not $baseExe) {
    Stop-Setup @"
No Python 3.10 to 3.13 with a working ssl module was found.
This usually means 'python' is a conda Python outside an activated conda environment.

Fix: install Python from python.org, then open a NEW PowerShell window and rerun this script:
    winget install Python.Python.3.12

To see what you have:
    py -0p
    python -c "import sys; print(sys.base_prefix)"
"@
}
$where = (& $baseExe @basePrefix -c "import sys; print(sys.executable)")
Write-Host "Using Python: $where"

# ---- 2. build or repair the virtual environment ---------------------------------
$venvPython = ".\.venv\Scripts\python.exe"
if ((Test-Path ".venv") -and -not (Test-PythonSsl -Exe $venvPython)) {
    Write-Host "The existing .venv has no working ssl module. Rebuilding it..."
    Remove-Item -Recurse -Force .venv
}
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    & $baseExe @basePrefix -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) { Stop-Setup "Could not create .venv." }
}

# ---- 3. install, configure, test -------------------------------------------------
Write-Host "Installing packages (a few minutes on the first run)..."
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Stop-Setup "pip install failed. Read the error above. Behind a corporate proxy, set HTTPS_PROXY first."
}

if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "Created .env. Open it and add your ANTHROPIC_API_KEY before any live run."
}

Write-Host "Running tests (no API calls)..."
& $venvPython -m pytest -q
if ($LASTEXITCODE -ne 0) { Stop-Setup "Some tests failed. See the output above." }

Write-Host ""
Write-Host "Ready. Activate the environment in each PowerShell window you use:" -ForegroundColor Green
Write-Host "    .\.venv\Scripts\Activate.ps1"
Write-Host "Then try:"
Write-Host "    python studio.py new --offline      (the CubeSat example, replayed from saved answers)"
Write-Host "    python studio.py ui                 (then open http://localhost:8770)"
