@echo off
chcp 65001 >nul
title Build Manager - Stop
echo.
echo ====================================================
echo   Build Manager - 停止服务
echo ====================================================
echo.

echo [1/3] 正在停止服务（端口 5000）...

:: 查找端口 5000 的进程 PID 并终止
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /c:":5000"') do (
    if not "%%a"=="0" (
        taskkill /f /pid %%a >nul 2>&1
    )
)

:: 删除临时标记文件
del "%TEMP%\build-manager-env.tmp" 2>nul

:: 验证
timeout /t 1 /nobreak >nul

netstat -ano | findstr /c:":5000" >nul 2>&1
if %errorlevel% equ 0 (
    echo [错误] 端口 5000 仍被占用，尝试二次终止 ...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr /c:":5000"') do (
        if not "%%a"=="0" (
            taskkill /f /pid %%a >nul 2>&1
        )
    )
    timeout /t 1 /nobreak >nul
    netstat -ano | findstr /c:":5000" >nul 2>&1
    if %errorlevel% equ 0 (
        echo [错误] 无法释放端口 5000，请手动检查占用进程。
    ) else (
        echo [OK] 服务已停止
    )
) else (
    echo [OK] 服务已停止
)

echo.
pause