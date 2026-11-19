@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_ALWAYS_INSTALL="
if not defined LOCALAPPDATA if defined USERPROFILE set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
pushd "%~dp0" || exit /b 1
if /i "%~1"=="--prepare" goto system_python
if not defined LOCALAPPDATA goto system_python
set "local_python=%LOCALAPPDATA%\QuizLens\runtime\windows-venv\Scripts\python.exe"
if not exist "%local_python%" goto system_python
"%local_python%" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if errorlevel 1 goto system_python
"%local_python%" -u "app\packaging\windows_start.py" %*
goto finish

:system_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if errorlevel 1 goto try_python
py -3 -u "app\packaging\windows_start.py" %*
goto finish

:try_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if errorlevel 1 goto need_python
python -u "app\packaging\windows_start.py" %*
goto finish

:need_python
echo Для QuizLens нужен установленный 64-разрядный Python версии 3.9 или новее.
echo Открой https://www.python.org/downloads/windows/ и установи Python x64.
echo Проверь в PowerShell командой py -3 --version и снова открой Start.cmd.
echo Инструкция находится рядом в START-HERE.txt.
pause
popd
exit /b 1

:finish
set "quiz_exit=%errorlevel%"
if not "%quiz_exit%"=="0" pause
if "%quiz_exit%"=="0" if /i "%~1"=="--prepare" pause
popd
exit /b %quiz_exit%
