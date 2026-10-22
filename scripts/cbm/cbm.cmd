@echo off
rem ---------------------------------------------------------------------------
rem codebase-memory-mcp CLI wrapper for this repository (Windows).
rem
rem Lets ANY agent query the code knowledge graph from the command line,
rem even when MCP is not available in that agent.
rem
rem Usage:
rem   scripts\cbm\cbm.cmd cli list_projects
rem   scripts\cbm\cbm.cmd cli search_graph --project <name> --query loginUser
rem   scripts\cbm\cbm.cmd cli get_architecture --project <name> --aspects overview
rem
rem Binary resolution order:
rem   1. %CBM_BIN%                     (explicit override)
rem   2. %USERPROFILE%\.codebase-memory-mcp\bin\
rem   3. codebase-memory-mcp on PATH
rem ---------------------------------------------------------------------------
setlocal EnableDelayedExpansion

set "EXE="

if defined CBM_BIN if exist "%CBM_BIN%" set "EXE=%CBM_BIN%"

if not defined EXE if exist "%USERPROFILE%\.codebase-memory-mcp\bin\codebase-memory-mcp.exe" (
  set "EXE=%USERPROFILE%\.codebase-memory-mcp\bin\codebase-memory-mcp.exe"
  set "CBM_ROOT=%USERPROFILE%\.codebase-memory-mcp"
)

if not defined EXE (
  for /f "delims=" %%i in ('where codebase-memory-mcp 2^>nul') do (
    if not defined EXE set "EXE=%%i"
  )
)

if not defined EXE (
  echo [cbm] codebase-memory-mcp not found.
  echo [cbm] Run the bootstrap first:
  echo [cbm]   powershell -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
  exit /b 1
)

rem Only set the overrides when we resolved the canonical location, so an
rem existing CBM installation keeps its own cache root (two different roots
rem cannot be active at the same time).
if defined CBM_ROOT (
  set "CBM_RUNTIME_DIR=%CBM_ROOT%\runtime"
  set "CBM_CACHE_DIR=%CBM_ROOT%\data"
)

"%EXE%" %*
exit /b %ERRORLEVEL%
