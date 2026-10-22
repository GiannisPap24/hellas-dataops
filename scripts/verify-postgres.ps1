# Run in an ordinary host PowerShell session. Never changes execution policy.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python environment missing' }
    & $python -I -B (Join-Path $PSScriptRoot 'verify_postgres.py')
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL verification failed; see the sanitized report' }
}
finally { Pop-Location }
