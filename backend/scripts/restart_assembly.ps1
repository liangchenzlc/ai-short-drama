param()
$ErrorActionPreference = 'Stop'
$backendDirectory = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExecutable = (Join-Path $backendDirectory '.venv/Scripts/python.exe').Replace('/', '\')
Push-Location $backendDirectory
try {
    & $pythonExecutable scripts/check_db_schema.py
    if ($LASTEXITCODE -ne 0) { throw 'Apply database migrations before restarting.' }
    & $pythonExecutable -c "from sqlalchemy import select,func; from short_drama.core.config import Settings; from short_drama.db.session import build_engine; from short_drama.domain import EpisodeRenderJob; e=build_engine(Settings()); c=e.connect(); n=c.scalar(select(func.count()).select_from(EpisodeRenderJob).where(EpisodeRenderJob.status.in_(['queued','running']))); c.close(); e.dispose(); print('Active render jobs:',n); raise SystemExit(1 if n else 0)"
    if ($LASTEXITCODE -ne 0) { throw 'Wait for active renders to finish before restarting.' }
    $ownedProcesses = @()
    foreach ($serviceRole in @('api', 'scheduler', 'render')) {
        $pidPath = Join-Path $backendDirectory ".runtime/$serviceRole.pid"
        if (!(Test-Path -LiteralPath $pidPath)) { continue }
        $recordedId = [int](Get-Content -LiteralPath $pidPath)
        $info = Get-CimInstance Win32_Process -Filter "ProcessId = $recordedId"
        if ($null -eq $info) { continue }
        if ($info.CommandLine -notmatch 'short_drama\.(main|tasks)' -or $info.ExecutablePath -ne $pythonExecutable) {
            throw "Recorded $serviceRole process does not belong to this backend."
        }
        $ownedProcesses += $recordedId
    }
    foreach ($ownedId in $ownedProcesses) {
        Stop-Process -Id $ownedId
        Wait-Process -Id $ownedId -Timeout 15 -ErrorAction SilentlyContinue
    }
    foreach ($serviceRole in @('api', 'scheduler', 'render')) {
        & (Join-Path $PSScriptRoot 'start_generation.ps1') -Role $serviceRole
    }
} finally { Pop-Location }
