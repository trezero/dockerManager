param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = 'Stop'

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $ScriptRoot ".venv"
$pythonExe = Join-Path $venv "Scripts\python.exe"

function Initialize-Venv {
  if (-not (Test-Path $pythonExe)) {
    Write-Host "[+] Creating virtual environment..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) {
      & py -3 -m venv $venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
      & python -m venv $venv
    } else {
      throw "Python launcher not found. Install Python 3 and ensure 'py' or 'python' is in PATH."
    }
  }
}

function Install-Requirements {
  Write-Host "[+] Installing dependencies..." -ForegroundColor Cyan
  & $pythonExe -m pip install --upgrade pip setuptools wheel | Out-Host
  & $pythonExe -m pip install -r (Join-Path $ScriptRoot 'requirements.txt') | Out-Host
}

Initialize-Venv
Install-Requirements

Write-Host "[+] Starting docker_manager..." -ForegroundColor Green
& $pythonExe -m docker_manager @Args
