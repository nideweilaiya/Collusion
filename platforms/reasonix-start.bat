@echo off
REM Collusion MCP Server 自启动脚本 (Reasonix)
REM 在 Reasonix 打开项目前运行此脚本，或加入 Windows 启动项

cd /d D:\BrainstormOrchestrator

echo ========================================
echo   Collusion MCP Server v0.4.0
echo   正在启动 SSE 服务...
echo ========================================

REM 检查是否已经在运行
curl -s http://localhost:8020/sse >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] MCP Server 已在运行 (port 8020)
    goto :done
)

REM 启动 MCP Server（后台运行）
start "Collusion-MCP" /MIN collusion-mcp --sse --port 8020

REM 等待服务就绪
echo 等待服务就绪...
:wait
timeout /t 1 /nobreak >nul
curl -s http://localhost:8020/sse >nul 2>&1
if %errorlevel% neq 0 goto :wait

echo [OK] MCP Server 启动成功
echo SSE 端点: http://localhost:8020/sse

:done
echo.
echo 现在可以打开 Reasonix 使用 /collusion 了
echo.
