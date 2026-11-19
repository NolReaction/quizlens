@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
call Start.cmd --prepare
set "quiz_exit=%errorlevel%"
popd
exit /b %quiz_exit%
