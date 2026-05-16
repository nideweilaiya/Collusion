"""Brainstorm Orchestrator v3.1 — MCP Server (双传输 + 异步编排)

支持两种启动方式：
  --stdio : 标准输入输出 (Claude Code 集成)
  --sse   : HTTP SSE 传输 (Trae Solo / Reasonix / 任意MCP客户端)
  --port  : SSE模式端口 (默认 8020)

v3.1.1: brainstorm_orchestrate 改为异步模式，立即返回 task_id，
避免 MCP 客户端（如 Reasonix）60s 超时。用户通过 brainstorm_status
和 brainstorm_result 轮询进度和结果。
"""
import json
import sys
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent

from src.orchestrator import BrainstormOrchestrator
from src.models import OrchestratorState

_orchestrator = BrainstormOrchestrator()
_executor = ThreadPoolExecutor(max_workers=3)

server = Server("brainstorm-orchestrator-v3.1")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="brainstorm_orchestrate",
            description="启动多对象协作技术方案编排。输入技术任务，多个对象代言Agent并行生成方案、交叉审查、可行性收束、Owner整合、投票评分。返回Top3方案。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "技术任务描述。例如'设计一个高并发短链接服务'",
                    },
                    "agents": {
                        "type": "integer",
                        "description": "Agent数量(1-3)。1=快速模式，3=完整多对象协作",
                        "default": 3,
                    },
                },
                "required": ["task"],
            },
        ),
        Tool(
            name="brainstorm_status",
            description="查询编排任务进度",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "brainstorm_orchestrate返回的任务ID",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="brainstorm_result",
            description="获取已完成编排任务的Top3方案及完整评分",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "任务ID",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="collusion_refine",
            description="用户提交修改建议后，各Agent独立审查并给出反馈（认可/有隐患/高创新性）。全票通过的修改自动合并，有分歧的告知原因。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "已完成的编排任务ID",
                    },
                    "modifications": {
                        "type": "array",
                        "description": "修改建议列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step_name": {"type": "string", "description": "要修改的环节名称"},
                                "suggestion": {"type": "string", "description": "修改建议内容"},
                            },
                        },
                    },
                },
                "required": ["task_id", "modifications"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "collusion_refine":
        task_id = arguments["task_id"]
        modifications = arguments.get("modifications", [])
        result = _orchestrator.refine(task_id, modifications)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    elif name == "brainstorm_orchestrate":
        task = arguments["task"]
        agents = arguments.get("agents", 3)
        _orchestrator.num_agents = agents

        # 预生成 task_id，立即注册状态（避免轮询时找不到）
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        pre_state = OrchestratorState(
            task_id=task_id,
            original_task=task,
            phase="queued",
        )
        _orchestrator._states[task_id] = pre_state

        # 后台异步执行编排（2-4分钟），立即返回 task_id
        def _run():
            _orchestrator.orchestrate(task=task, task_id=task_id)

        _executor.submit(_run)

        return [TextContent(
            type="text",
            text=json.dumps({
                "task_id": task_id,
                "status": "queued",
                "agents": agents,
                "message": (
                    f"编排已异步启动({agents}个Agent)，预计2-4分钟。\n"
                    f"使用 brainstorm_status(task_id=\"{task_id}\") 查询进度。\n"
                    f"完成后使用 brainstorm_result(task_id=\"{task_id}\") 获取Top3方案。"
                ),
            }, ensure_ascii=False, indent=2),
        )]

    elif name == "brainstorm_status":
        task_id = arguments["task_id"]
        state = _orchestrator.get_state(task_id)
        if state is None:
            return [TextContent(type="text", text=json.dumps(
                {"error": f"任务不存在: {task_id}"}, ensure_ascii=False,
            ))]
        return [TextContent(type="text", text=json.dumps({
            "task_id": state["task_id"],
            "task": state["original_task"],
            "phase": state["phase"],
            "round": f"{state.get('current_round', 0)}/{state.get('max_rounds', 0)}",
            "schemes": len(state.get("schemes", {})),
            "steps": len(state.get("step_list", [])),
            "cost": state.get("total_cost_rmb", 0),
            "tokens": state.get("total_tokens", 0),
            "complexity": state.get("scheme_complexity", {}),
            "coverage": state.get("object_coverage", {}),
        }, ensure_ascii=False, indent=2))]

    elif name == "brainstorm_result":
        task_id = arguments["task_id"]
        result = _orchestrator.get_result(task_id)
        if result is None:
            return [TextContent(type="text", text=json.dumps(
                {"error": f"任务不存在: {task_id}"}, ensure_ascii=False,
            ))]

        # 提取 Top1 方案的完整正文（最顶层，AI 优先看到）
        top_scheme_content = ""
        scheme_details = {}
        for sid, scheme in result.get("schemes", {}).items():
            integrated = scheme.get("integrated_content", "")
            step_designs = scheme.get("steps", {})
            scheme_details[sid] = {
                "agent_role": scheme.get("agent_role", ""),
                "object_name": scheme.get("object_name", ""),
                "integrated_content": integrated,
                "step_designs": step_designs,
                "complexity_score": scheme.get("complexity_score", 0),
            }

        # 找到 Top1 方案 ID 并提取其完整内容
        top3 = result.get("top3", [])
        if top3 and top3[0].get("plan_id"):
            top1_id = top3[0]["plan_id"]
            import re as _re
            m = _re.search(r'[A-C]', top1_id)
            if m:
                top1_id = m.group(0)
            if top1_id in scheme_details:
                top_scheme_content = scheme_details[top1_id].get("integrated_content", "")

        # 合并步骤定义和方案设计内容
        steps_with_designs = []
        for step in result.get("step_list", []):
            sd = dict(step)
            # 从各方案中提取该步骤的设计内容
            sd["designs"] = {}
            for sid, scheme in scheme_details.items():
                design = scheme.get("step_designs", {}).get(step.get("id", ""), "")
                if design:
                    sd["designs"][sid] = design[:300]  # 每方案每步骤最多300字
            steps_with_designs.append(sd)

        output = {
            "task_id": result["task_id"],
            "task": result["original_task"],
            "phase": result["phase"],
            "status": "completed" if result["phase"] == "done" else "running",
            "plan_summary": top_scheme_content,
            "top3": top3,
            "vote_results": result["vote_results"],
            "steps": steps_with_designs,
            "schemes": scheme_details,
            "output_files": result.get("output_files", {}),
            "cost": result["total_cost_rmb"],
            "tokens": result["total_tokens"],
            "error": result.get("error"),
        }
        return [TextContent(type="text", text=json.dumps(
            output, ensure_ascii=False, indent=2,
        ))]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ============================================================
# 启动入口
# ============================================================
async def run_stdio():
    """标准输入输出模式 (Claude Code)"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


async def run_sse(port: int = 8020):
    """HTTP SSE 模式 (Trae Solo / Reasonix / 通用MCP客户端)"""
    import uvicorn

    from mcp.server.transport_security import TransportSecuritySettings

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    sse = SseServerTransport("/messages/", security_settings=security)

    # 纯 ASGI 应用：手动路由到 SSE transport
    async def app(scope, receive, send):
        if scope["type"] != "http":
            return

        path = scope["path"]
        if path == "/sse" and scope["method"] == "GET":
            async with sse.connect_sse(scope, receive, send) as streams:
                await server.run(streams[0], streams[1],
                                 server.create_initialization_options())
        elif path == "/messages/" and scope["method"] == "POST":
            await sse.handle_post_message(scope, receive, send)
        else:
            await send({
                "type": "http.response.start",
                "status": 404,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({
                "type": "http.response.body",
                "body": b"Not Found",
            })

    print(f"MCP Server (SSE) 启动: http://localhost:{port}/sse")
    print(f"消息端点: http://localhost:{port}/messages/")
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server_uvicorn = uvicorn.Server(config)
    await server_uvicorn.serve()


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Brainstorm Orchestrator v3.1 MCP Server")
    parser.add_argument("--stdio", action="store_true",
                        help="标准输入输出模式 (Claude Code)")
    parser.add_argument("--sse", action="store_true",
                        help="HTTP SSE模式 (Trae Solo/Reasonix)")
    parser.add_argument("--port", type=int, default=8020,
                        help="SSE模式端口 (默认8020)")
    args = parser.parse_args()

    if args.sse:
        asyncio.run(run_sse(args.port))
    else:
        # 默认 stdio
        asyncio.run(run_stdio())
