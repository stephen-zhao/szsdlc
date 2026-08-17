@echo off
setlocal EnableDelayedExpansion
rem szsdlc launcher for cmd.exe and PowerShell on Windows.
rem
rem Same resolution order as bin/szsdlc: an installed console script, then a
rem venv this launcher made under the plugin data dir, then create it once.
rem
rem Note `py -3` before `python`: on Windows `python` is frequently a Store
rem stub that opens the Microsoft Store instead of running anything, which is
rem the single most common way a hook silently does nothing here.

if "%SZSDLC_LAUNCHER%"=="1" (
    echo szsdlc: launcher recursion; is bin\ on PATH? 1>&2
    exit /b 5
)
set "SZSDLC_LAUNCHER=1"

where szsdlc.exe >nul 2>&1
if %ERRORLEVEL%==0 (
    szsdlc.exe %*
    exit /b %ERRORLEVEL%
)

set "PLUGIN_ROOT=%CLAUDE_PLUGIN_ROOT%"
if "%PLUGIN_ROOT%"=="" set "PLUGIN_ROOT=%~dp0.."

set "DATA_DIR=%CLAUDE_PLUGIN_DATA%"
if "%DATA_DIR%"=="" set "DATA_DIR=%LOCALAPPDATA%\szsdlc"
set "VENV=%DATA_DIR%\venv"

if exist "%VENV%\Scripts\python.exe" (
    "%VENV%\Scripts\python.exe" -m szsdlc.cli %*
    exit /b %ERRORLEVEL%
)

rem Candidates are probed by running them, not by asking whether they exist:
rem `python` here is frequently the Microsoft Store stub, which is on PATH and
rem cannot run anything. The probe enforces the 3.12 floor at the same time.
set "PROBE=import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
set "PY="
py -3 -c "%PROBE%" >nul 2>&1 && set "PY=py -3"
if "%PY%"=="" (
    python -c "%PROBE%" >nul 2>&1 && set "PY=python"
)
if "%PY%"=="" (
    echo szsdlc: no working Python 3.12+ found ^(tried py -3, python^). 1>&2
    echo Fix: install Python 3.12 or later and rerun 1>&2
    exit /b 5
)

if not exist "%DATA_DIR%" mkdir "%DATA_DIR%" >nul 2>&1
%PY% -m venv "%VENV%" >nul 2>&1
if not exist "%VENV%\Scripts\python.exe" (
    echo szsdlc: could not create a virtualenv at %VENV%. 1>&2
    echo Fix: pip install "%PLUGIN_ROOT%" 1>&2
    exit /b 5
)

"%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check "%PLUGIN_ROOT%" >nul 2>&1
if not %ERRORLEVEL%==0 (
    echo szsdlc: could not install szsdlc into %VENV%. 1>&2
    echo Fix: "%VENV%\Scripts\python.exe" -m pip install "%PLUGIN_ROOT%" 1>&2
    exit /b 5
)

"%VENV%\Scripts\python.exe" -m szsdlc.cli %*
exit /b %ERRORLEVEL%
