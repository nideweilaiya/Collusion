@echo off
cd /d D:\BrainstormOrchestrator
echo ========================================
echo Brainstorm Orchestrator v3.1 MCP Server
echo ========================================
echo.
echo SSE 端点: http://localhost:8020/sse
echo 消息端点: http://localhost:8020/messages/
echo.
echo 保持此窗口打开，然后在Trae Solo中配置MCP连接
echo.
python -m src.mcp_server --sse --port 8020
pause
