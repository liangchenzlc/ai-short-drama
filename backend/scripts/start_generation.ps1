param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('api', 'scheduler', 'text', 'image', 'video', 'audio', 'render', 'agent')]
    [string]$Role,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
# Windows environment blocks are case-insensitive but may still hold keys that differ
# only in case (Path/PATH, http_proxy/HTTP_PROXY). PowerShell 5.1 Start-Process rejects
# such duplicates while building the child environment, so collapse them first.
$processVariables = [System.Environment]::GetEnvironmentVariables('Process')
$collapsedVariables = @{}
foreach ($variableName in @($processVariables.Keys)) {
    $collapsedVariables[$variableName.ToUpperInvariant()] = [string]$processVariables[$variableName]
}
if ($collapsedVariables.Count -ne $processVariables.Count) {
    foreach ($variableName in @($processVariables.Keys)) {
        [System.Environment]::SetEnvironmentVariable($variableName, $null, 'Process')
    }
    foreach ($variableName in @($collapsedVariables.Keys)) {
        [System.Environment]::SetEnvironmentVariable($variableName, $collapsedVariables[$variableName], 'Process')
    }
}
$backendDirectory = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExecutable = Join-Path $backendDirectory '.venv/Scripts/python.exe'
$runtimeDirectory = Join-Path $backendDirectory '.runtime'
$pidFile = Join-Path $runtimeDirectory "$Role.pid"
if (-not $DryRun -and (Test-Path -LiteralPath $pidFile)) {
    $recordedProcessId = 0
    $pidRecorded = [int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$recordedProcessId)
    if ($pidRecorded -and (Get-Process -Id $recordedProcessId -ErrorAction SilentlyContinue)) {
        throw "$Role already has a recorded running process: $recordedProcessId"
    }
    # Stale or unreadable record must not block the next start.
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
$roleNodes = @{ api = '1'; scheduler = '10'; text = '2'; image = '3'; video = '4'; render = '5'; audio = '6'; agent = '7' }
$originalNode = $env:SNOWFLAKE_WORKER_ID
try {
    $env:SNOWFLAKE_WORKER_ID = $roleNodes[$Role]
    if ($Role -eq 'api') {
        $processArguments = @('-m', 'uvicorn', 'short_drama.main:app', '--host', '127.0.0.1', '--port', '8000')
    } elseif ($Role -eq 'scheduler') {
        $processArguments = @('-m', 'short_drama.tasks.runtime')
    } else {
        Push-Location $backendDirectory
        try {
            $queueName = & $pythonExecutable -c "import sys; from short_drama.core.config import Settings; from short_drama.tasks.celery_app import topology, render_queue, agent_queue; s=Settings(); role=sys.argv[1]; print(agent_queue(s).name if role == 'agent' else render_queue(s).name if role == 'render' else topology(s)[2][role].name)" $Role
            if ($LASTEXITCODE -ne 0 -or $queueName -notmatch '^[a-zA-Z0-9_.]+$') {
                throw 'Unable to resolve the configured generation queue'
            }
        } finally {
            Pop-Location
        }
        $workerConcurrency = if ($Role -eq 'text') { '4' } elseif ($Role -eq 'render') { '1' } else { '2' }
        $processArguments = @('-m', 'celery', '-A', 'short_drama.tasks.celery_app:app', 'worker',
            '--pool=threads', "--concurrency=$workerConcurrency", '-Q', $queueName,
            "--hostname=$Role@%h", '--loglevel=WARNING')
    }
    if ($DryRun) {
        Write-Output "$Role command (SNOWFLAKE_WORKER_ID=$($roleNodes[$Role])): python $($processArguments -join ' ')"
        return
    }
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
    $process = Start-Process -FilePath $pythonExecutable -ArgumentList $processArguments `
        -WorkingDirectory $backendDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDirectory "$Role.out.log") `
        -RedirectStandardError (Join-Path $runtimeDirectory "$Role.err.log")
    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii
    Write-Output "$Role started: PID $($process.Id)"
} finally {
    if ($null -eq $originalNode) { Remove-Item Env:SNOWFLAKE_WORKER_ID -ErrorAction SilentlyContinue }
    else { $env:SNOWFLAKE_WORKER_ID = $originalNode }
}
