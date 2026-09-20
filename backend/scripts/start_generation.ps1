param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('api', 'scheduler', 'text', 'image', 'video')]
    [string]$Role
)

$ErrorActionPreference = 'Stop'
$backendDirectory = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExecutable = Join-Path $backendDirectory '.venv/Scripts/python.exe'
$runtimeDirectory = Join-Path $backendDirectory '.runtime'
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$pidFile = Join-Path $runtimeDirectory "$Role.pid"
if (Test-Path -LiteralPath $pidFile) {
    $existingProcessId = [int](Get-Content -LiteralPath $pidFile)
    if (Get-Process -Id $existingProcessId -ErrorAction SilentlyContinue) {
        throw "$Role already has a recorded running process: $existingProcessId"
    }
}
$roleNodes = @{ api = '1'; scheduler = '10'; text = '2'; image = '3'; video = '4' }
$originalNode = $env:SNOWFLAKE_WORKER_ID
try {
    $env:SNOWFLAKE_WORKER_ID = $roleNodes[$Role]
    if ($Role -eq 'api') {
        $processArguments = @('-m', 'uvicorn', 'short_drama.main:app', '--host', '127.0.0.1', '--port', '8000')
    } elseif ($Role -eq 'scheduler') {
        $processArguments = @('-m', 'short_drama.tasks.runtime')
    } else {
        $workerConcurrency = if ($Role -eq 'text') { '4' } else { '2' }
        $processArguments = @('-m', 'celery', '-A', 'short_drama.tasks.celery_app:app', 'worker',
            '--pool=threads', "--concurrency=$workerConcurrency", '-Q', "tasks.ai.$Role",
            "--hostname=$Role@%h", '--loglevel=WARNING')
    }
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
