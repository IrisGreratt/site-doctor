@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Site Doctor - 网站健康诊断工具
echo ========================================
echo.

python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo [!] 缺少依赖，正在安装...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [X] 依赖安装失败，请检查 Python 和网络环境。
        pause
        exit /b 1
    )
)

echo [OK] 正在启动服务，稍后浏览器打开：http://127.0.0.1:8000
echo [提示] 按 Ctrl+C 停止服务
echo.

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo 服务已停止。
pause
