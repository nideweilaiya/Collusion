@echo off
setlocal enabledelayedexpansion
title Collusion (共谋) 一键安装 v0.4.0

echo ========================================
echo   Collusion (共谋) 一键安装 v0.4.0
echo   多视角协作技术方案编排引擎
echo ========================================
echo.

REM ---- 1. 检查 Python ----
echo [1/5] 检查 Python 环境...
set PYTHON=
for %%c in (python python3) do (
    where %%c >nul 2>nul
    if !errorlevel! equ 0 (
        for /f "tokens=2 delims= " %%v in ('%%c --version 2^>^&1') do (
            echo   ✓ %%c %%v
            set PYTHON=%%c
        )
    )
)
if "%PYTHON%"=="" (
    echo   错误: 需要 Python 3.10+，请先安装
    pause
    exit /b 1
)

REM ---- 2. 安装 Collusion ----
echo [2/5] 安装 Collusion MCP 包...
if exist "pyproject.toml" (
    echo   检测到源码目录，使用 pip install -e .
    %PYTHON% -m pip install -e . --quiet
) else (
    echo   从 PyPI 安装 collusion-mcp
    %PYTHON% -m pip install collusion-mcp --quiet
)
echo   ✓ collusion-mcp 已安装

REM ---- 3. 检测 API Key ----
echo [3/5] 检测 API Key...
set KEY_OK=0
if not "%DEEPSEEK_API_KEY%"=="" (
    echo   ✓ 检测到 DEEPSEEK_API_KEY 环境变量
    set KEY_OK=1
) else if exist "%USERPROFILE%\.reasonix\config.json" (
    echo   ✓ 检测到 Reasonix 配置（零配置模式）
    set KEY_OK=1
) else if exist "config.json" (
    echo   ✓ 检测到 config.json
    set KEY_OK=1
)
if !KEY_OK! equ 0 (
    echo   ⚠ 未检测到 API Key
    echo   请设置环境变量: set DEEPSEEK_API_KEY=sk-...
    echo   或注册免费 Key: https://platform.deepseek.com
)

REM ---- 4. 检测宿主平台 ----
echo [4/5] 检测宿主平台...
set HOST=unknown
set CONFIG_FILE=

REM 检测 Claude Code
if exist "%USERPROFILE%\.claude\settings.json" (
    set HOST=claude-code
    echo   ✓ 检测到 Claude Code
    echo { > .mcp.json
    echo   "mcpServers": { >> .mcp.json
    echo     "brainstorm": { >> .mcp.json
    echo       "command": "collusion-mcp", >> .mcp.json
    echo       "args": ["--stdio"] >> .mcp.json
    echo     } >> .mcp.json
    echo   } >> .mcp.json
    echo } >> .mcp.json
)

REM 检测 Reasonix
if exist "%USERPROFILE%\.reasonix\config.json" (
    set HOST=reasonix
    echo   ✓ 检测到 Reasonix（零配置模式）
)

REM 检测 Cursor
if exist "%APPDATA%\Cursor" (
    set HOST=cursor
    echo   ✓ 检测到 Cursor
)

REM 检测 Trae Solo
if exist ".trae\mcp.json" (
    set HOST=trae-solo
    echo   ✓ 检测到 Trae Solo
)

if "!HOST!"=="unknown" (
    echo   ⚠ 未检测到已知宿主
    echo   支持: Claude Code / Cursor / Reasonix / Trae Solo
)

REM ---- 5. 完成 ----
echo [5/5] 安装完成
echo.
echo ========================================
echo   Collusion 安装完成！
echo ========================================
echo.
echo 启动 SSE 服务器: collusion-mcp --sse --port 8020
echo 或通过 MCP 客户端直接使用 stdio 模式
echo.
echo 使用方法（在 AI 编码助手中）：
echo   /collusion scheme ^<任务^>     — 方案设计
echo   /collusion review ^<文件^>     — 代码审查
echo   /collusion plan ^<任务^>       — 任务拆解
echo   /collusion diagnose ^<问题^>   — 问题诊断
echo   /collusion choose ^<选型^>     — 技术选型
echo.
pause
