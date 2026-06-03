@echo off
chcp 65001 >nul
title Build Manager - Docker 构建管理工具

echo.
echo ====================================================
echo   Build Manager - Docker 构建管理工具
echo ====================================================
echo.

:: ── 检查 Python ──
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请安装 Python 3.9+
    pause
    exit /b 1
)

:: ── 安装依赖 ──
echo [1/3] 安装依赖...
pip install -r "%~dp0requirements.txt" -q
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败，请手动执行: pip install -r "%~dp0requirements.txt"
    pause
    exit /b 1
)
echo [OK] 依赖就绪

:: ── 安全配置 ──
set FLASK_DEBUG=0
if not defined FLASK_SECRET_KEY (
    if not defined SECRET_KEY (
        echo [安全] 正在生成随机 FLASK_SECRET_KEY ...
        python -c "import secrets; print(secrets.token_hex(32))" > "%TEMP%\build-manager-secret.tmp"
        set /p FLASK_SECRET_KEY=<"%TEMP%\build-manager-secret.tmp"
        del "%TEMP%\build-manager-secret.tmp" 2>nul
        echo [安全] 密钥已生成（本次会话有效）
    )
)

:: ── 启动服务 ──
echo [2/3] 启动服务...
echo.
echo   >>> 浏览器打开: http://127.0.0.1:5000
echo   >>> 按 Ctrl+C 停止
echo.
echo [3/3] 服务已启动，正在打开浏览器...
timeout /t 2 /nobreak >nul
start http://127.0.0.1:5000

python "%~dp0app.py"

pause