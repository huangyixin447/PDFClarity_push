@echo off
set "workdir=%~dp0work"
if exist "%workdir%" (
    pushd "%workdir%" 2>nul || exit /b
    del /s /q *.* >nul 2>&1
    for /d %%i in (*) do rd /s /q "%%i" 2>nul
    popd
)