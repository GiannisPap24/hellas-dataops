# Ordinary host PowerShell only. No policy changes or automatic Docker cleanup.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Project environment missing' }
    & $python -I -B (Join-Path $PSScriptRoot 'demo.py')
    if ($LASTEXITCODE -ne 0) { throw 'Demonstration failed; inspect its sanitized report' }
}
finally { Pop-Location }
