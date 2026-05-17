@echo off
cd /d D:\BrainstormOrchestrator
echo ========================================
echo   Collusion MCP Server v3.2
echo   多视角协作技术方案编排引擎
echo ========================================
echo.
echo SSE 端点: http://localhost:8020/sse
echo 消息端点: http://localhost:8020/messages/
echo 反馈 API: http://localhost:8020/api/refine
echo.
echo 保持此窗口打开，然后在 MCP 客户端中配置连接
echo.
collusion-mcp --sse --port 8020
pause
