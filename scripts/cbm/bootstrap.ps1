#Requires -Version 5.1
<#
.SYNOPSIS
  Bootstrap the codebase-memory-mcp code index for this repository (Windows).

.DESCRIPTION
  Makes the code knowledge graph available on any machine, for any agent.

  What it does, in order:
    1. Ensures the codebase-memory-mcp binary exists (downloads + verifies SHA256 if missing).
    2. Writes the environment overrides CBM_RUNTIME_DIR / CBM_CACHE_DIR.
       These are required, not cosmetic: CBM refuses to start when any parent
       directory of its coordination directory grants write access to another
       identity (a common leftover from sandbox tooling or profile migration).
       Pointing both at a clean per-user directory avoids that class of failure.
    3. Registers the MCP server in each agent's PROJECT-level config file.
    4. Indexes this repository into the knowledge graph.

  Idempotent: safe to run repeatedly. Skips work that is already done.

.PARAMETER Version
  Release tag to install. Defaults to the pinned version below.

.PARAMETER Force
  Re-download the binary even if it already exists.

.PARAMETER SkipIndex
  Register configs but do not run the indexer.

.PARAMETER Reindex
  Run a full re-index even if an index for this project already exists.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\cbm\bootstrap.ps1
#>
[CmdletBinding()]
param(
    [string] $Version = 'v0.12.0',
    [switch] $Force,
    [switch] $SkipIndex,
    [switch] $Reindex
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Invoke-WebRequest renders a per-chunk progress bar for large downloads, which
# floods the console with tens of thousands of lines and slows the transfer.
$ProgressPreference = 'SilentlyContinue'

# ---------------------------------------------------------------- constants

$Repo      = 'DeusData/codebase-memory-mcp'
$InstallTo = Join-Path $env:USERPROFILE '.codebase-memory-mcp'
$BinDir    = Join-Path $InstallTo 'bin'
$ExePath   = Join-Path $BinDir 'codebase-memory-mcp.exe'
$DataDir   = Join-Path $InstallTo 'data'
$RuntimeDir= Join-Path $InstallTo 'runtime'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

function Write-Step  { param([string]$m) Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok    { param([string]$m) Write-Host "    $m" -ForegroundColor Green }
function Write-Info  { param([string]$m) Write-Host "    $m" -ForegroundColor DarkGray }
function Write-Warn2 { param([string]$m) Write-Host "    $m" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 1. binary

Write-Step "Checking binary at $ExePath"

if ((Test-Path $ExePath) -and -not $Force) {
    Write-Ok "already present, skipping download"
} else {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $asset = "codebase-memory-mcp-windows-$arch.zip"
    $base  = "https://github.com/$Repo/releases/download/$Version"

    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $tmp = Join-Path $env:TEMP ("cbm-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null

    try {
        Write-Host "    downloading $asset ($Version)..."
        $zip = Join-Path $tmp $asset
        Invoke-WebRequest -Uri "$base/$asset" -OutFile $zip -UseBasicParsing
        Invoke-WebRequest -Uri "$base/checksums.txt" -OutFile (Join-Path $tmp 'checksums.txt') -UseBasicParsing

        # integrity check: never execute an unverified third-party binary
        $sumLine = Select-String -Path (Join-Path $tmp 'checksums.txt') -SimpleMatch $asset |
                   Select-Object -First 1
        if (-not $sumLine) { throw "no checksum published for $asset" }
        $expected = ($sumLine.Line -split '\s+')[0]
        $actual   = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $expected.ToLower()) {
            throw "SHA256 mismatch for ${asset}: expected $expected, got $actual"
        }
        Write-Ok "SHA256 verified: $actual"

        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $extracted = Join-Path $tmp 'codebase-memory-mcp.exe'
        if (-not (Test-Path $extracted)) { throw "archive did not contain codebase-memory-mcp.exe" }
        Copy-Item -Path $extracted -Destination $ExePath -Force
        Write-Ok "installed to $ExePath"
    }
    finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

New-Item -ItemType Directory -Force -Path $DataDir, $RuntimeDir | Out-Null

# ------------------------------------------------- 2. environment overrides

$env:CBM_RUNTIME_DIR = $RuntimeDir
$env:CBM_CACHE_DIR   = $DataDir

# ------------------------------------------------- 3. project MCP configs
# Absolute paths are required by every MCP client, so these files are
# machine-specific and are gitignored. The committed AGENTS.md is what
# makes a fresh clone work before this script has ever run.

Write-Step 'Registering project-level MCP configuration'

$serverBlock = [ordered]@{
    command = $ExePath
    args    = @()
    env     = [ordered]@{
        CBM_RUNTIME_DIR = $RuntimeDir
        CBM_CACHE_DIR   = $DataDir
    }
}

function Write-JsonConfig {
    param([string]$Path, [hashtable]$Root)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    ($Root | ConvertTo-Json -Depth 8) | Set-Content -Path $Path -Encoding UTF8
}

# PowerShell 5.1's -Encoding UTF8 writes a BOM, which JSON parsers (and some
# MCP clients) reject. Always write config files BOM-less through .NET.
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    try {
        $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return ($text | ConvertFrom-Json)
    } catch {
        Copy-Item $Path "$Path.bak" -Force
        Write-Warn2 "existing $(Split-Path -Leaf $Path) is not valid JSON; backed up to .bak and rebuilt"
        return $null
    }
}

# Merges our server into mcpServers, preserving any servers already defined
# there, and writes BOM-less UTF-8.
function Set-McpJson {
    param([string]$Path, [string]$Label)
    $servers  = [ordered]@{}
    $existing = Read-JsonFile -Path $Path
    if ($existing -and ($existing.PSObject.Properties.Name -contains 'mcpServers')) {
        $existing.mcpServers.PSObject.Properties | ForEach-Object { $servers[$_.Name] = $_.Value }
    }
    $had = $servers.Contains('codebase-memory-mcp')
    $servers['codebase-memory-mcp'] = $serverBlock

    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($Path, (@{ mcpServers = $servers } | ConvertTo-Json -Depth 10), $Utf8NoBom)

    if ($had) { Write-Ok "$Label (updated)" } else { Write-Ok "$Label" }
}

# Claude Code, and the many agents that adopted the same .mcp.json convention
Set-McpJson -Path (Join-Path $ProjectRoot '.mcp.json') -Label '.mcp.json (Claude Code and compatible agents)'
# Cursor
Set-McpJson -Path (Join-Path $ProjectRoot '.cursor\mcp.json') -Label '.cursor\mcp.json (Cursor)'

# Codex CLI - project-scoped TOML; only honoured for trusted projects.
# Appending a marked block keeps any existing user content intact.
$codexToml = Join-Path $ProjectRoot '.codex\config.toml'
$marker    = '# --- codebase-memory-mcp (managed by scripts/cbm/bootstrap.ps1) ---'
$codexBody = @"

$marker
[mcp_servers.codebase-memory-mcp]
command = "$($ExePath -replace '\\','\\')"
args = []
env = { CBM_RUNTIME_DIR = "$($RuntimeDir -replace '\\','\\')", CBM_CACHE_DIR = "$($DataDir -replace '\\','\\')" }
# --- end codebase-memory-mcp ---
"@

$codexDir = Split-Path -Parent $codexToml
if (-not (Test-Path $codexDir)) { New-Item -ItemType Directory -Force -Path $codexDir | Out-Null }
if (-not (Test-Path $codexToml)) {
    [System.IO.File]::WriteAllText($codexToml, "# Codex CLI project-scoped configuration.`r`n", $Utf8NoBom)
}
$codexContent = [System.IO.File]::ReadAllText($codexToml, [System.Text.Encoding]::UTF8)
if ($codexContent -notmatch [regex]::Escape($marker)) {
    [System.IO.File]::AppendAllText($codexToml, $codexBody, $Utf8NoBom)
    Write-Ok '.codex\config.toml (Codex CLI, block appended)'
} else {
    Write-Info '.codex\config.toml already configured'
}

# ---------------------------------------------------------------- 4. index

if ($SkipIndex) {
    Write-Step 'Skipping index (-SkipIndex)'
} else {
    Write-Step 'Indexing repository (this is the step that builds the graph)'
    Push-Location $ProjectRoot
    # CBM writes its warn-level diagnostics to stderr. With
    # $ErrorActionPreference='Stop', PowerShell turns a native command's first
    # stderr line into a terminating error, so relax it for this call and
    # judge success by the exit code instead.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $cbmArgs = @('cli', 'index_repository', '--repo-path', $ProjectRoot, '--persistence', 'true')
        if ($Reindex) { $cbmArgs += '--force' }
        & $ExePath @cbmArgs 2>&1 |
            ForEach-Object { $_.ToString() } |
            Where-Object { $_ -notmatch '^level=warn' -and $_ -notmatch '^\s*$' } |
            ForEach-Object { Write-Host "    $_" }
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
        Pop-Location
    }
    if ($code -ne 0) { throw "index_repository exited with code $code" }
}

# ---------------------------------------------------------------- summary

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Host ''
Write-Host '  Binary   : ' -NoNewline; Write-Host $ExePath
Write-Host '  Data     : ' -NoNewline; Write-Host $DataDir
Write-Host ''
Write-Host '  Next: restart your coding agent so it picks up the MCP server.'
Write-Host '  CLI  : scripts\cbm\cbm.cmd cli list_projects'
Write-Host ''
