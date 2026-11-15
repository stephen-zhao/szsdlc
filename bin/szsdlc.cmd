@echo off
setlocal EnableDelayedExpansion
rem szsdlc launcher for cmd.exe and PowerShell on Windows.
rem
rem Same resolution order as bin/szsdlc: an installed console script, then a
rem venv this launcher made under the plugin data dir, then build that venv
rem trying every compatible interpreter, then run from the checkout.
rem
rem Note that candidates are probed by *running* them rather than by asking
rem whether they exist: on Windows `python` is frequently a Store stub, which
rem is on PATH and cannot run anything, and that is the single most common way
rem a hook here silently does nothing. Running the candidate enforces the
rem 3.12 floor at the same time.

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
set "STAMP=%DATA_DIR%\installed-from"

rem What the venv is supposed to contain. Claude Code installs each release
rem into its own cache directory and leaves the old ones in place, so the path
rem alone catches an update; the version also catches an in-place checkout.
set "RELEASE="
for /f "tokens=2 delims=:," %%v in ('findstr /c:"\"version\"" "%PLUGIN_ROOT%\.claude-plugin\plugin.json" 2^>nul') do (
    if not defined RELEASE set "RELEASE=%%~v"
)
set "RELEASE=!RELEASE: =!"
set RELEASE=!RELEASE:"=!
set "WANT=%PLUGIN_ROOT%@!RELEASE!"

rem A venv this launcher built earlier -- but only if it holds *this* release.
rem Without the check the launcher runs whichever venv it built first, forever:
rem `/plugin update` writes new code to disk that nothing ever runs.
if exist "%VENV%\Scripts\python.exe" (
    set "HAVE="
    if exist "%STAMP%" set /p HAVE=<"%STAMP%"
    if not "!HAVE!"=="!WANT!" (
        rem Install over the venv rather than rebuilding it, and stamp only on
        rem success -- a broken pip keeps retrying and keeps saying so rather
        rem than silently pinning the caller to old code.
        "%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade "%PLUGIN_ROOT%" >nul 2>&1
        if !ERRORLEVEL!==0 (
            >"%STAMP%" echo !WANT!
        ) else (
            echo szsdlc: could not refresh %VENV% to !RELEASE!; running what is there. 1>&2
            echo Fix: "%VENV%\Scripts\python.exe" -m pip install --upgrade "%PLUGIN_ROOT%" 1>&2
        )
    )
    "%VENV%\Scripts\python.exe" -m szsdlc.cli %*
    exit /b %ERRORLEVEL%
)

rem -------------------------------------------------------------------------
rem First run: find an interpreter and build the venv.
rem -------------------------------------------------------------------------

rem The floor is stated twice in this file, here and in the message at the
rem bottom, and it must agree with `requires-python` in pyproject.toml.
rem tests/test_hooks.py fails if any of the three drift apart.
set "PROBE=import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"

set "LIST=%DATA_DIR%\pythons.txt"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%" >nul 2>&1
break>"%LIST%"

rem An interpreter somebody already chose for this shell or this tree. Both uv
rem and the `py` launcher consult these ahead of PATH, for the same reason: if
rem a developer picked a Python here, that is the one whose version and
rem packages they actually maintain.
if defined VIRTUAL_ENV echo "%VIRTUAL_ENV%\Scripts\python.exe">>"%LIST%"

set "SCAN=%CLAUDE_PROJECT_DIR%"
if not defined SCAN set "SCAN=%CD%"
:walk
echo "%SCAN%\.venv\Scripts\python.exe">>"%LIST%"
for %%p in ("%SCAN%\..") do set "PARENT=%%~fp"
if /i "%PARENT%"=="%SCAN%" goto :walked
set "SCAN=%PARENT%"
goto :walk
:walked

rem Every interpreter the `py` launcher knows about, which it already reports
rem newest first. This closes the gap that actually bit on the POSIX side too:
rem asking a single bare name for a single answer means a box with a usable
rem Python alongside an old default is reported as having none.
for /f "tokens=2 delims=:" %%a in ('py --list 2^>nul') do (
    for /f "tokens=1" %%b in ("%%a") do echo py -%%b>>"%LIST%"
)

rem The ambiguous names last: whatever they point at, a versioned name above
rem said it better.
echo py -3>>"%LIST%"
echo python>>"%LIST%"

rem An interpreter uv manages. Somebody who installs Python only through uv has
rem nothing that matches any of the above. `uv python find` is a lookup and
rem never downloads, so this stays a search rather than a hook that quietly
rem reaches for the network.
where uv >nul 2>&1
if %ERRORLEVEL%==0 (
    for /f "usebackq delims=" %%u in (`uv python find ">=3.12" 2^>nul`) do echo "%%u">>"%LIST%"
)

set "USABLES=%DATA_DIR%\usable.txt"
break>"%USABLES%"
set "PICK="

for /f "usebackq delims=" %%c in ("%LIST%") do (
    set "CAND=%%c"
    call :attempt
    if defined PICK goto :run
)

rem No venv could be built anywhere. If one of the interpreters that passed the
rem probe already imports the three dependencies, run straight from the
rem checkout: slower to start and not hermetic, but a hook that works beats a
rem correct explanation of why it does not. PYTHONPATH is set once here rather
rem than inside the probe, which would append src\ again for every candidate.
set "PYTHONPATH=%PLUGIN_ROOT%\src;%PYTHONPATH%"
for /f "usebackq delims=" %%c in ("%USABLES%") do (
    set "CAND=%%c"
    call :hasdeps
    if defined PICK goto :runsource
)

rem Both messages name the thing to run next, and which of the two failures
rem this was: nothing compatible exists, or something does and would not install.
for %%s in ("%USABLES%") do set "ANY=%%~zs"
if "%ANY%"=="0" (
    echo szsdlc: no working Python 3.12+ found. 1>&2
    echo Tried %%VIRTUAL_ENV%%, .venv up the tree, every version py knows, python, uv. 1>&2
    echo Fix: install Python 3.12 or later, then rerun 1>&2
) else (
    set "FIRST="
    for /f "usebackq delims=" %%c in ("%USABLES%") do if not defined FIRST set "FIRST=%%c"
    echo szsdlc: found Python 3.12+ but could not build a virtualenv at %VENV%. 1>&2
    echo Fix: !FIRST! -m pip install "%PLUGIN_ROOT%" 1>&2
)
exit /b 5

:run
%PICK% -m szsdlc.cli %*
exit /b %ERRORLEVEL%

:runsource
%PICK% -m szsdlc.cli %*
exit /b %ERRORLEVEL%

rem Probe one candidate, and if it is compatible try to build the venv with it.
rem A candidate can pass the version probe and still fail to make a virtualenv,
rem so this is a loop over every compatible interpreter rather than a single
rem choice: the next one along is usually fine, and giving up on the whole box
rem because the first answer was incomplete is the behaviour being fixed.
:attempt
%CAND% -c "%PROBE%" >nul 2>&1
if not %ERRORLEVEL%==0 goto :eof
echo %CAND%>>"%USABLES%"
rd /s /q "%VENV%" >nul 2>&1
%CAND% -m venv "%VENV%" >nul 2>&1
if not exist "%VENV%\Scripts\python.exe" goto :eof
"%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check "%PLUGIN_ROOT%" >nul 2>&1
if not %ERRORLEVEL%==0 goto :eof
>"%STAMP%" echo %WANT%
set PICK="%VENV%\Scripts\python.exe"
goto :eof

:hasdeps
%CAND% -c "import yaml, jinja2, jsonschema" >nul 2>&1
if not %ERRORLEVEL%==0 goto :eof
set "PICK=%CAND%"
goto :eof
