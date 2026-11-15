@echo off
setlocal EnableDelayedExpansion
rem szsdlc launcher for cmd.exe and PowerShell on Windows.
rem
rem Resolution order, and why it is here rather than in the package:
rem docs/reference.md, "Resolving an interpreter". Same order as bin/szsdlc.

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

set "PLUGIN_ROOT_PATH=%CLAUDE_PLUGIN_ROOT%"
if "%PLUGIN_ROOT_PATH%"=="" set "PLUGIN_ROOT_PATH=%~dp0.."

set "DATA_DIR_PATH=%CLAUDE_PLUGIN_DATA%"
if "%DATA_DIR_PATH%"=="" set "DATA_DIR_PATH=%LOCALAPPDATA%\szsdlc"
set "VENV_PATH=%DATA_DIR_PATH%\venv"
set "STAMP_FILE_PATH=%DATA_DIR_PATH%\installed-from"

set "RELEASE_VERSION="
for /f "tokens=2 delims=:," %%v in ('findstr /c:"\"version\"" "%PLUGIN_ROOT_PATH%\.claude-plugin\plugin.json" 2^>nul') do (
    if not defined RELEASE_VERSION set "RELEASE_VERSION=%%~v"
)
set "RELEASE_VERSION=!RELEASE_VERSION: =!"
set RELEASE_VERSION=!RELEASE_VERSION:"=!
set "EXPECTED_STAMP=%PLUGIN_ROOT_PATH%@!RELEASE_VERSION!"

rem A venv built earlier, reinstalled over when it holds an older release:
rem Claude Code installs each release beside the last, so without the stamp
rem `/plugin update` writes code that nothing ever runs.
if exist "%VENV_PATH%\Scripts\python.exe" (
    set "CURRENT_STAMP="
    if exist "%STAMP_FILE_PATH%" set /p CURRENT_STAMP=<"%STAMP_FILE_PATH%"
    if not "!CURRENT_STAMP!"=="!EXPECTED_STAMP!" (
        "%VENV_PATH%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade "%PLUGIN_ROOT_PATH%" >nul 2>&1
        if !ERRORLEVEL!==0 (
            >"%STAMP_FILE_PATH%" echo !EXPECTED_STAMP!
        ) else (
            echo szsdlc: could not refresh %VENV_PATH% to !RELEASE_VERSION!; running what is there. 1>&2
            echo Fix: "%VENV_PATH%\Scripts\python.exe" -m pip install --upgrade "%PLUGIN_ROOT_PATH%" 1>&2
        )
    )
    "%VENV_PATH%\Scripts\python.exe" -m szsdlc.cli %*
    exit /b %ERRORLEVEL%
)

rem First run: build that venv, with the first interpreter that can.
rem
rem Must agree with `requires-python` in pyproject.toml; tests/test_hooks.py
rem fails if they drift apart.
set "MINIMUM_PYTHON_VERSION=3.12"
set "VERSION_PROBE=import sys; sys.exit(0 if sys.version_info >= tuple(map(int, '%MINIMUM_PYTHON_VERSION%'.split('.'))) else 1)"

set "CANDIDATE_LIST_FILE_PATH=%DATA_DIR_PATH%\pythons.txt"
set "USABLE_LIST_FILE_PATH=%DATA_DIR_PATH%\usable.txt"
if not exist "%DATA_DIR_PATH%" mkdir "%DATA_DIR_PATH%" >nul 2>&1
break>"%CANDIDATE_LIST_FILE_PATH%"
break>"%USABLE_LIST_FILE_PATH%"

if defined VIRTUAL_ENV echo "%VIRTUAL_ENV%\Scripts\python.exe">>"%CANDIDATE_LIST_FILE_PATH%"

set "SCAN_DIR_PATH=%CLAUDE_PROJECT_DIR%"
if not defined SCAN_DIR_PATH set "SCAN_DIR_PATH=%CD%"
:walk
echo "%SCAN_DIR_PATH%\.venv\Scripts\python.exe">>"%CANDIDATE_LIST_FILE_PATH%"
for %%p in ("%SCAN_DIR_PATH%\..") do set "PARENT_DIR_PATH=%%~fp"
if /i "%PARENT_DIR_PATH%"=="%SCAN_DIR_PATH%" goto :walked
set "SCAN_DIR_PATH=%PARENT_DIR_PATH%"
goto :walk
:walked

rem Every interpreter py knows about, which it already reports newest first.
for /f "tokens=2 delims=:" %%a in ('py --list 2^>nul') do (
    for /f "tokens=1" %%b in ("%%a") do echo py -%%b>>"%CANDIDATE_LIST_FILE_PATH%"
)

echo py -3>>"%CANDIDATE_LIST_FILE_PATH%"
echo python>>"%CANDIDATE_LIST_FILE_PATH%"

rem `uv python find` is a lookup and never downloads.
where uv >nul 2>&1
if %ERRORLEVEL%==0 (
    for /f "usebackq delims=" %%u in (`uv python find ">=%MINIMUM_PYTHON_VERSION%" 2^>nul`) do echo "%%u">>"%CANDIDATE_LIST_FILE_PATH%"
)

set "CHOSEN_PYTHON="

for /f "usebackq delims=" %%c in ("%CANDIDATE_LIST_FILE_PATH%") do (
    set "CANDIDATE_PYTHON=%%c"
    call :attempt
    if defined CHOSEN_PYTHON goto :run
)

rem No venv anywhere. Run from the checkout if the dependencies are already
rem importable: not hermetic, but a working hook beats an explanation.
set "PYTHONPATH=%PLUGIN_ROOT_PATH%\src;%PYTHONPATH%"
for /f "usebackq delims=" %%c in ("%USABLE_LIST_FILE_PATH%") do (
    set "CANDIDATE_PYTHON=%%c"
    call :check_dependencies
    if defined CHOSEN_PYTHON goto :run
)

for %%s in ("%USABLE_LIST_FILE_PATH%") do set "USABLE_FILE_SIZE=%%~zs"
if "%USABLE_FILE_SIZE%"=="0" (
    echo szsdlc: no working Python %MINIMUM_PYTHON_VERSION%+ found. 1>&2
    echo Tried %%VIRTUAL_ENV%%, .venv up the tree, every version py knows, python, uv. 1>&2
    echo Fix: install Python %MINIMUM_PYTHON_VERSION% or later, then rerun 1>&2
) else (
    set "FIRST_USABLE_PYTHON="
    for /f "usebackq delims=" %%c in ("%USABLE_LIST_FILE_PATH%") do if not defined FIRST_USABLE_PYTHON set "FIRST_USABLE_PYTHON=%%c"
    echo szsdlc: found Python %MINIMUM_PYTHON_VERSION%+ but could not build a virtualenv at %VENV_PATH%. 1>&2
    echo Fix: !FIRST_USABLE_PYTHON! -m pip install "%PLUGIN_ROOT_PATH%" 1>&2
)
exit /b 5

:run
%CHOSEN_PYTHON% -m szsdlc.cli %*
exit /b %ERRORLEVEL%

rem Candidates are probed by running them: on Windows `python` is frequently
rem the Microsoft Store stub, which is on PATH and cannot run anything.
rem Compatible is not sufficient either, so every one of them gets a try at
rem building the venv rather than only the first.
:attempt
%CANDIDATE_PYTHON% -c "%VERSION_PROBE%" >nul 2>&1
if not %ERRORLEVEL%==0 goto :eof
echo %CANDIDATE_PYTHON%>>"%USABLE_LIST_FILE_PATH%"
rd /s /q "%VENV_PATH%" >nul 2>&1
%CANDIDATE_PYTHON% -m venv "%VENV_PATH%" >nul 2>&1
if not exist "%VENV_PATH%\Scripts\python.exe" goto :eof
"%VENV_PATH%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check "%PLUGIN_ROOT_PATH%" >nul 2>&1
if not %ERRORLEVEL%==0 goto :eof
>"%STAMP_FILE_PATH%" echo %EXPECTED_STAMP%
set CHOSEN_PYTHON="%VENV_PATH%\Scripts\python.exe"
goto :eof

:check_dependencies
%CANDIDATE_PYTHON% -c "import yaml, jinja2, jsonschema" >nul 2>&1
if not %ERRORLEVEL%==0 goto :eof
set "CHOSEN_PYTHON=%CANDIDATE_PYTHON%"
goto :eof
