@echo off
setlocal
set "UV_CACHE_DIR=%~dp0.uv-cache"
set "UV_PYTHON_INSTALL_DIR=%~dp0.tools\python"
if exist "%~dp0.tools\uv\uv.exe" (
  "%~dp0.tools\uv\uv.exe" %*
) else (
  echo Local uv is unavailable. Install uv and use the uv command instead.
  exit /b 1
)
