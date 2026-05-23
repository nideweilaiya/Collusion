"""Collusion MCP Server v0.4.0 — MCP Server (双传输 + 异步编排 + Sampling 委托)

支持两种启动方式：
  --stdio : 标准输入输出 (Claude Code / Cursor 集成)
  --sse   : HTTP SSE 传输 (Reasonix / Trae Solo / 任意MCP客户端)
  --port  : SSE模式端口 (默认 8020)

v0.4.0 新增:
  - Mermaid 架构分层图（HTML 报告）
  - 代码入口锚点 + MVP 自动检测
  - Elicitation 引导交互（缺失信息补全）
  - 废案资产库与语义检索（分支方案复用）
  - 会话分支与合并 (collusion_branch / collusion_merge)
  - MCP Sampling 委托调用（保留宿主缓存）
"""
import json
import os
import sys
import uuid
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent

from src.orchestrator import BrainstormOrchestrator
from src.models import OrchestratorState
from src.blackboard import BlackboardOrchestrator
from src.tools_registry import TOOL_REGISTRY, TOOL_GROUPS, LEGACY_NAME_MAP, resolve_handler

_blackboard = BlackboardOrchestrator()

_orchestrator = BrainstormOrchestrator()
_executor = ThreadPoolExecutor(max_workers=3)

# MCP Sampling 委托模式: 轮询队列中的 LLM 请求，通过宿主代为调用
_sampling_enabled = (
    os.environ.get("COLLUSION_SAMPLING_MODE") == "1"
    or _orchestrator.config.get("sampling", {}).get("enabled", False)
)

server = Server("collusion-v0.4.0")


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
                    "format": {
                        "type": "string",
                        "description": "输出格式: md(默认，仅Markdown，~800 token增量) / html(可视化报告+Markdown，~2500 token增量) / both(同html)",
                        "default": "md",
                    },
                    "preset": {
                        "type": "string",
                        "description": "Agent配置: auto(自动检测)/quick(1Agent)/standard(3Agent)/full(5Agent)",
                        "default": "auto",
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
            name="collusion_branch",
            description="从已有任务分叉出新分支，探索替代方案。可选择指定探索方向，或自动从废案中选取最优备选方案。",
            inputSchema={
                "type": "object",
                "properties": {
                    "parent_task_id": {
                        "type": "string",
                        "description": "父任务ID（已完成的任务）",
                    },
                    "branch_point": {
                        "type": "string",
                        "description": "分叉点：步骤名称、'top1'（从Top1方案分叉）、'alternative'（从废案中选最优）",
                    },
                    "direction": {
                        "type": "string",
                        "description": "替代方向描述。留空则自动从废案中选最佳备选思路。例如'用Go重写后端'或'增加微服务拆分'",
                    },
                },
                "required": ["parent_task_id", "branch_point"],
            },
        ),
        Tool(
            name="collusion_merge",
            description="合并多个分支的方案，每个环节提取最高分设计，生成综合方案。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_ids": {
                        "type": "array",
                        "description": "要合并的任务ID列表",
                        "items": {"type": "string"},
                    },
                    "strategy": {
                        "type": "string",
                        "description": "合并策略: best_per_step(默认)/vote/combine",
                        "default": "best_per_step",
                    },
                },
                "required": ["task_ids"],
            },
        ),
        Tool(
            name="brainstorm_search_assets",
            description="搜索历史方案资产库（含废案）。找到相似任务的历史方案以供参考复用。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或任务描述，例如'高并发API设计'或'博客平台'",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认5",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="brainstorm_elicit",
            description="回答编排引擎的引导问题。当 brainstorm_status 显示 pending_questions 时，用此工具回答缺失信息，帮助引擎产出更精准的方案。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "任务ID",
                    },
                    "answers": {
                        "type": "object",
                        "description": "回答映射，key为问题ID，value为答案。例如: {\"elicit_0\": \"需要支持10万并发用户\"}",
                    },
                },
                "required": ["task_id", "answers"],
            },
        ),
        # v0.6: 检查点引擎工具
        Tool(
            name="collusion_assess",
            description="轻量决策评估 — v0.6 主入口。检索→压缩→核心检查点链→决策卡片。4-5次LLM调用，≤15k token。默认不跑深度检查点。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "技术任务描述",
                    },
                    "deep": {
                        "type": "string",
                        "description": "深度模式: auto(默认,按风险自动)/force(强制深度)/never(仅核心)",
                        "default": "auto",
                    },
                    "strict_mode": {
                        "type": "boolean",
                        "description": "True=warning升级为blocking",
                        "default": False,
                    },
                },
                "required": ["task"],
            },
        ),
        Tool(
            name="collusion_check",
            description="运行单个检查点。可独立调用任一核心/深度检查点。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "任务描述"},
                    "task_id": {"type": "string", "description": "已有任务ID（复用快照）"},
                    "checkpoint": {
                        "type": "string",
                        "description": "检查点ID: semantic_consistency/interface_conflict/pattern_match",
                    },
                    "strict_mode": {"type": "boolean", "default": False},
                    "artifacts": {"type": "object", "description": "设计草案(接口定义/Schema等)"},
                },
                "required": ["checkpoint"],
            },
        ),
        Tool(
            name="collusion_render",
            description="渲染决策卡片或方案为 Markdown/HTML 报告。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务ID"},
                    "format": {
                        "type": "string",
                        "description": "输出格式: md/html/both",
                        "default": "md",
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
        Tool(
            name="collusion_enhance",
            description="多视角增强已有方案。传入一份半成品方案，3个Agent从业务/技术/安全视角审查并输出优化后的完整方案。",
            inputSchema={
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": "已有的方案文本（Markdown或纯文本），需要多视角审查增强",
                    },
                    "focus": {
                        "type": "string",
                        "description": "可选：重点关注维度（business/architecture/security），不填则全视角",
                        "default": "",
                    },
                },
                "required": ["plan"],
            },
        ),
        Tool(
            name="collusion_review",
            description="多视角代码审查。3个Agent从安全/性能/可维护性视角并行审查代码。",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要审查的代码内容",
                    },
                    "language": {
                        "type": "string",
                        "description": "编程语言（python/javascript/go/rust/java等）",
                        "default": "python",
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="collusion_plan",
            description="多视角任务拆解。架构师+产品经理+工程专家协作将大型任务拆解为可执行清单。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "要拆解的大型任务描述",
                    },
                },
                "required": ["task"],
            },
        ),
        Tool(
            name="collusion_diagnose",
            description="多视角问题诊断。3个Agent独立构建故障树，交叉验证后输出综合诊断报告和排查路径。",
            inputSchema={
                "type": "object",
                "properties": {
                    "problem": {
                        "type": "string",
                        "description": "异常现象描述（错误信息、症状、复现步骤等）",
                    },
                },
                "required": ["problem"],
            },
        ),
        Tool(
            name="collusion_choose",
            description="多维度技术选型评估。成本/性能/安全/维护四维加权打分，输出推荐排名。",
            inputSchema={
                "type": "object",
                "properties": {
                    "options": {
                        "type": "array",
                        "description": "候选技术方案列表",
                        "items": {"type": "string"},
                    },
                    "context": {
                        "type": "string",
                        "description": "选型背景（项目类型、规模、约束等）",
                        "default": "",
                    },
                },
                "required": ["options"],
            },
        ),
        Tool(
            name="collusion_blackboard_start",
            description="启动黑板+顾问模式。3个子Agent在后台静默运行，独立设计方案，遇疑问询问主Agent。完成后自动合并输出最终方案。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "任务描述",
                    },
                    "steps": {
                        "type": "array",
                        "description": "环节清单（可选，不填则Agent自行判断）",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "model": {
                        "type": "string",
                        "description": "模型策略: hybrid(默认,架构R1+其余Flash) / full_flash / full_strong",
                        "default": "hybrid",
                    },
                },
                "required": ["task"],
            },
        ),
        Tool(
            name="collusion_blackboard_status",
            description="查询黑板模式任务进度。返回各子Agent状态、待回答的询问、整体进度。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "黑板任务ID (bb_开头)",
                    },
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="collusion_blackboard_answer",
            description="回答子Agent的询问。答案写回黑板后子Agent继续执行。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "role": {"type": "string", "description": "security/architecture/ux"},
                    "query_index": {"type": "integer", "description": "询问索引（从0开始）"},
                    "answer": {"type": "string", "description": "回答内容"},
                },
                "required": ["task_id", "role", "query_index", "answer"],
            },
        ),
        Tool(
            name="collusion_blackboard_merge",
            description="强制合并子Agent的方案（即使未全部完成）。输出最终方案。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="collusion_scout",
            description="多视角项目侦察。3个Agent从业务/架构/安全视角并行审查项目代码，输出侦察报告。",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "项目根目录路径",
                    },
                    "files": {
                        "type": "array",
                        "description": "要审查的文件列表（相对路径），不填则自动发现",
                        "items": {"type": "string"},
                        "default": [],
                    },
                },
                "required": ["project_path"],
            },
        ),
        Tool(
            name="collusion_mod_goal",
            description="【MC Mod】为 AICompanion Mod 生成 Goal 模板。输入自然语言任务描述，自动匹配模板并生成可执行的 GoalRunner 配置。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "开发任务描述, 如「添加一个新技能 AutoFish」"},
                    "params": {
                        "type": "object",
                        "description": "模板参数 (选填, 如 name/description 等)",
                        "default": {},
                    },
                },
                "required": ["task"],
            },
        ),
        Tool(
            name="collusion_goal_start",
            description="【GoalRunner】启动自动化执行闭环。输入Goal配置，自动执行：Coder改代码→Evaluator验证→Reviewer审查→蓝图归档。生成与评估分离架构。",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal_config": {
                        "type": "object",
                        "description": "Goal配置JSON",
                        "properties": {
                            "goal_id": {"type": "string", "description": "Goal标识"},
                            "description": {"type": "string", "description": "任务描述"},
                            "verification": {"type": "object", "description": "验证命令配置"},
                            "review": {"type": "object", "description": "审查配置"},
                            "constraints": {"type": "object", "description": "文件约束"},
                        },
                        "required": ["goal_id", "description", "verification"],
                    },
                },
                "required": ["goal_config"],
            },
        ),
        Tool(
            name="collusion_goal_status",
            description="查询 GoalRunner 执行进度。返回当前迭代次数、错误信息、状态。",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string", "description": "goal_start 返回的 goal_id"},
                },
                "required": ["goal_id"],
            },
        ),
        Tool(
            name="collusion_route",
            description="【v1.2】双关联路由。给定起点文件和终点需求，通过结构图谱计算需要阅读的最小文件集合。5层fallback确保永不无路可走。",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_file": {"type": "string", "description": "起点文件路径 (相对于项目根目录)"},
                    "goal": {"type": "string", "description": "终点需求描述"},
                    "project_root": {"type": "string", "description": "项目根目录（Windows绝对路径）"},
                },
                "required": ["start_file", "goal", "project_root"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    # v0.6: 结构化分发 → 新注册表优先
    handler = resolve_handler(name)
    if handler is not None:
        return await handler(arguments, _orchestrator, _blackboard, _executor)

    # 旧分发链 (逐步迁移)
    if name == "collusion_branch":
        parent_id = arguments["parent_task_id"]
        branch_point = arguments.get("branch_point", "top1")
        direction = arguments.get("direction", "")
        branch_id = _orchestrator.branch(parent_id, branch_point, direction)
        if not branch_id:
            return [TextContent(type="text", text=json.dumps(
                {"error": f"父任务不存在: {parent_id}"}, ensure_ascii=False,
            ))]
        return [TextContent(type="text", text=json.dumps({
            "branch_task_id": branch_id,
            "parent_task_id": parent_id,
            "branch_point": branch_point,
            "action": (
                f"新分支已创建。使用 brainstorm_orchestrate("
                f"task=\"新方向描述\""
                f") 在新分支上启动编排。"
                if not direction else
                f"新分支已创建，方向: {direction}。"
                f"使用 brainstorm_orchestrate 在新分支上启动编排。"
            ),
        }, ensure_ascii=False, indent=2))]

    if name == "collusion_merge":
        task_ids = arguments["task_ids"]
        strategy = arguments.get("strategy", "best_per_step")
        result = _orchestrator.merge_branches(task_ids, strategy)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "brainstorm_search_assets":
        query = arguments["query"]
        top_k = arguments.get("top_k", 5)
        results = _orchestrator.search_assets(query, top_k)
        return [TextContent(type="text", text=json.dumps({
            "query": query,
            "count": len(results),
            "results": results,
            "tip": "可参考历史方案的思路和架构，避免从零设计。使用 task_id 获取完整方案内容。",
        }, ensure_ascii=False, indent=2))]

    if name == "brainstorm_elicit":
        task_id = arguments["task_id"]
        answers = arguments.get("answers", {})
        state = _orchestrator.get_state(task_id)
        if state is None:
            return [TextContent(type="text", text=json.dumps(
                {"error": f"任务不存在: {task_id}"}, ensure_ascii=False,
            ))]
        state_obj = _orchestrator._load_state(task_id)
        if state_obj is None and task_id in _orchestrator._states:
            state_obj = _orchestrator._states[task_id]
        if state_obj is None:
            return [TextContent(type="text", text=json.dumps(
                {"error": f"任务状态无法加载: {task_id}"}, ensure_ascii=False,
            ))]
        state_obj = _orchestrator.apply_elicitation_answers(state_obj, answers)
        _orchestrator._save_state(state_obj)
        answered = sum(1 for q in state_obj.elicitation_questions if q.get("answered"))
        total = len(state_obj.elicitation_questions)
        return [TextContent(type="text", text=json.dumps({
            "task_id": task_id,
            "answered": f"{answered}/{total}",
            "all_answered": state_obj.elicitation_answered,
            "message": (f"已保存 {answered}/{total} 个回答。"
                        f"方案将在下次编排时整合这些信息。"
                        if not state_obj.elicitation_answered else
                        "所有引导问题已回答，编排将继续进行。"),
        }, ensure_ascii=False, indent=2))]

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
        fmt = arguments.get("format", "md")
        preset = arguments.get("preset", "auto")

        # 动态检测 Agent 配置
        if preset == "auto":
            role_ids, detected_count, _ = _orchestrator.detect_agents_for_task(task)
            if agents == 3:  # 用户未显式指定时使用检测结果
                agents = detected_count

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
            _orchestrator.orchestrate(task=task, task_id=task_id, output_format=fmt)

        _executor.submit(_run)

        return [TextContent(
            type="text",
            text=json.dumps({
                "task_id": task_id,
                "status": "queued",
                "agents": agents,
                "format": fmt,
                "format_note": "md=仅Markdown(~800 token增量) / html=可视化报告+MD(~2500 token增量)",
                "message": (
                    f"编排已异步启动({agents}个Agent, 输出格式={fmt})，预计2-4分钟。\n"
                    f"Token 预估: md≈800增量, html≈2500增量。\n"
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
        # Elicitation 引导问题
        pending_questions = state.get("elicitation_questions", [])
        unanswered = [q for q in pending_questions if not q.get("answered", False)]

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
            "pending_questions": unanswered,
            "elicitation_note": (
                f"有 {len(unanswered)} 个引导问题待回答，使用 brainstorm_elicit 回复"
                if unanswered else None
            ),
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

    if name == "collusion_enhance":
        plan = arguments["plan"]
        focus = arguments.get("focus", "")
        result = _orchestrator.enhance(plan, focus)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "collusion_review":
        code = arguments["code"]
        language = arguments.get("language", "python")
        result = _orchestrator.review_code(code, language)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "collusion_plan":
        task = arguments["task"]
        result = _orchestrator.decompose_task(task)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "collusion_diagnose":
        problem = arguments["problem"]
        result = _orchestrator.diagnose(problem)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "collusion_choose":
        options = arguments["options"]
        context = arguments.get("context", "")
        result = _orchestrator.evaluate_options(options, context)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "collusion_blackboard_start":
        task = arguments["task"]
        steps_raw = arguments.get("steps", [])
        model_strategy = arguments.get("model", "hybrid")
        steps = [{"index": i+1, "name": s, "description": s}
                 for i, s in enumerate(steps_raw)] if steps_raw else []
        task_id = _blackboard.create_task(task, steps)

        # 后台执行完整 7 阶段编排
        def _run_full():
            _blackboard.orchestrate_full(task_id)

        _executor.submit(_run_full)

        return [TextContent(type="text", text=json.dumps({
            "task_id": task_id,
            "model_strategy": model_strategy,
            "phases": ["proposal", "review", "brake", "integrate", "vote", "merge"],
            "note": (
                "完整 7 阶段编排已在后台启动。\n"
                f"使用 collusion_blackboard_status(task_id=\"{task_id}\") 查询进度。\n"
                "无需手动 collusion_blackboard_merge，编排完成后自动合并。"
            ),
        }, ensure_ascii=False, indent=2))]

    if name == "collusion_blackboard_status":
        task_id = arguments["task_id"]
        result = _blackboard.get_status(task_id)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "collusion_blackboard_answer":
        task_id = arguments["task_id"]
        role = arguments["role"]
        idx = arguments["query_index"]
        answer = arguments["answer"]
        result = _blackboard.answer_query(task_id, role, idx, answer)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "collusion_blackboard_merge":
        task_id = arguments["task_id"]
        result = _blackboard.merge_proposals(task_id)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "collusion_scout":
        project_path = arguments["project_path"]
        files = arguments.get("files", [])
        result = _orchestrator.scout(project_path, files)
        return [TextContent(type="text", text=json.dumps(
            result, ensure_ascii=False, indent=2,
        ))]

    if name == "collusion_mod_goal":
        from src.mod_goals import ModAnalyzer
        analyzer = ModAnalyzer("D:/AI_Workbench/integrations/minecraft/forge-mod")
        suggestion = analyzer.suggest_goal(arguments["task"])
        params = arguments.get("params", {})
        template = suggestion.get("goal_config")
        if template and params:
            template["goal_id"] = template["goal_id"].format(**params)
            template["description"] = template["description"].format(**params)
        return [TextContent(type="text", text=json.dumps({
            "task": arguments["task"],
            "template": suggestion["template"],
            "goal_config": template,
            "suggestion": suggestion["suggestion"],
            "how_to_use": "将此 goal_config 传给 collusion_goal_start 即可自动化执行",
        }, ensure_ascii=False, indent=2))]

    if name == "collusion_goal_start":
        from src.goal_runner import GoalConfig
        cfg = GoalConfig.from_dict(arguments["goal_config"])
        goal_id = _orchestrator.goal_runner.start_goal(cfg)
        return [TextContent(type="text", text=json.dumps({
            "goal_id": goal_id,
            "status": "started",
            "message": "GoalRunner ready. Use collusion_goal_status to poll.",
        }, ensure_ascii=False, indent=2))]

    if name == "collusion_goal_status":
        result = _orchestrator.goal_runner.get_status(arguments["goal_id"])
        if result:
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        return [TextContent(type="text", text=json.dumps({"error": "goal_id not found"}))]

    if name == "collusion_route":
        from src.structure_graph import route
        result = route(
            start_file=arguments["start_file"],
            goal_description=arguments["goal"],
            project_root=arguments["project_root"],
            orchestrator=_orchestrator,
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ============================================================
# 启动入口
# ============================================================
async def _run_sampling_bridge():
    """MCP Sampling 桥接：从队列取出 LLM 请求，委托宿主执行"""
    from src.llm.mcp_sampling import MCPSamplingAdapter
    while True:
        try:
            # 非阻塞检查队列
            try:
                request = MCPSamplingAdapter._request_queue.get(timeout=1)
            except Exception:
                await asyncio.sleep(0.5)
                continue

            req_id = request["id"]
            messages = request["messages"]
            max_tokens = request["max_tokens"]

            try:
                result = await server.create_message(
                    messages=messages,
                    max_tokens=max_tokens,
                )
                # 提取响应
                content = ""
                if hasattr(result, 'content'):
                    if isinstance(result.content, list):
                        for block in result.content:
                            if hasattr(block, 'text'):
                                content += block.text
                    elif hasattr(result.content, 'text'):
                        content = result.content.text

                response = {
                    "text": content,
                    "input_tokens": getattr(result, 'usage', None).input_tokens if hasattr(result, 'usage') and result.usage else 0,
                    "output_tokens": getattr(result, 'usage', None).output_tokens if hasattr(result, 'usage') and result.usage else 0,
                }
            except Exception as e:
                response = RuntimeError(f"Sampling 委托失败: {e}")

            # 放回响应队列
            resp_queue = MCPSamplingAdapter._response_queues.get(req_id)
            if resp_queue:
                resp_queue.put(response)

        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.5)


async def run_stdio():
    """标准输入输出模式 (Claude Code)"""
    async with stdio_server() as (read_stream, write_stream):
        # 启动 Sampling 桥接（如果启用）
        bridge_task = None
        if _sampling_enabled:
            from src.llm.mcp_sampling import MCPSamplingAdapter
            bridge_task = asyncio.create_task(_run_sampling_bridge())
            print("[Sampling] MCP Sampling 委托模式已启用 — LLM 调用委托宿主执行")

        try:
            await server.run(read_stream, write_stream,
                             server.create_initialization_options())
        finally:
            if bridge_task:
                bridge_task.cancel()


async def run_sse(port: int = 8020):
    """HTTP SSE 模式 (Trae Solo / Reasonix / 通用MCP客户端)"""
    import uvicorn

    from mcp.server.transport_security import TransportSecuritySettings

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    sse = SseServerTransport("/messages/", security_settings=security)

    # ====== REST 工具函数 ======
    async def _http_response(send, status: int, body: bytes, content_type: str = "application/json"):
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type.encode()),
                (b"access-control-allow-origin", b"*"),
                (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                (b"access-control-allow-headers", b"Content-Type"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def _read_body(receive) -> bytes:
        body = b""
        more_body = True
        while more_body:
            msg = await receive()
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                more_body = msg.get("more_body", False)
        return body

    # 纯 ASGI 应用：路由分发
    async def app(scope, receive, send):
        if scope["type"] != "http":
            return

        path = scope["path"]
        method = scope["method"]

        # CORS preflight
        if method == "OPTIONS":
            await _http_response(send, 204, b"")
            return

        # === MCP 端点 ===
        if path == "/sse" and method == "GET":
            async with sse.connect_sse(scope, receive, send) as streams:
                await server.run(streams[0], streams[1],
                                 server.create_initialization_options())
        elif path == "/messages/" and method == "POST":
            await sse.handle_post_message(scope, receive, send)

        # === 静态文件：生成的报告 ===
        elif path.startswith("/outputs/") and method == "GET":
            import os as _os
            rel = path[len("/outputs/"):]
            file_path = _os.path.join("data/outputs", rel.replace("\\", "/"))
            if _os.path.isfile(file_path):
                ct = "text/html" if file_path.endswith(".html") else "text/markdown"
                with open(file_path, "rb") as f:
                    await _http_response(send, 200, f.read(), ct)
            else:
                await _http_response(send, 404, b'{"error":"file not found"}')

        # === 反馈回路 REST API ===
        elif path == "/api/refine" and method == "POST":
            body = await _read_body(receive)
            try:
                data = json.loads(body)
                result = _orchestrator.refine(
                    data.get("task_id", ""),
                    data.get("modifications", []),
                )
                await _http_response(send, 200,
                    json.dumps(result, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                await _http_response(send, 400,
                    json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))

        # === 应用修改并重新生成 ===
        elif path.startswith("/api/apply/") and method == "POST":
            task_id = path[len("/api/apply/"):]
            body = await _read_body(receive)
            try:
                data = json.loads(body)
                # 重新渲染输出文件（已应用修改的新方案）
                state = _orchestrator._load_state(task_id)
                if state is None and task_id in _orchestrator._states:
                    state = _orchestrator._states[task_id]
                if state is None:
                    await _http_response(send, 404, b'{"error":"task not found"}')
                else:
                    # 将修改合并到 scheme 中
                    applied = data.get("applied", [])
                    schemes = state.schemes
                    for mod in applied:
                        step_name = mod.get("step_name", "")
                        suggestion = mod.get("suggestion", "")
                        for sid, scheme in schemes.items():
                            for step_id, content in scheme.get("steps", {}).items():
                                for s in state.step_list:
                                    if s.get("name") == step_name and s.get("id") == step_id:
                                        scheme["steps"][step_id] = content + f"\n\n[用户修改（已通过Agent审查）]: {suggestion}"
                                        break
                    # 重新渲染
                    fmt = state.output_paths.get("format", "md")
                    _orchestrator._states[task_id] = state
                    paths = _orchestrator._render_outputs(state)
                    state.output_paths = paths
                    _orchestrator._save_state(state)
                    await _http_response(send, 200,
                        json.dumps({"output_files": paths}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                await _http_response(send, 400,
                    json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))

        else:
            await _http_response(send, 404, b'{"error":"not found"}')

    print(f"MCP Server (SSE) 启动: http://localhost:{port}/sse")
    print(f"消息端点:     http://localhost:{port}/messages/")
    print(f"反馈 API:    http://localhost:{port}/api/refine")
    print(f"报告文件:    http://localhost:{port}/outputs/{{task_id}}/report.html")

    # 启动 Sampling 桥接（如果启用）
    bridge_task = None
    if _sampling_enabled:
        from src.llm.mcp_sampling import MCPSamplingAdapter
        bridge_task = asyncio.create_task(_run_sampling_bridge())
        print("[Sampling] MCP Sampling 委托模式已启用")

    try:
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        server_uvicorn = uvicorn.Server(config)
        await server_uvicorn.serve()
    finally:
        if bridge_task:
            bridge_task.cancel()


def main():
    """命令行入口 — collusion-mcp 命令"""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Collusion MCP Server v3.2 — 多视角协作技术方案编排引擎")
    parser.add_argument("--stdio", action="store_true",
                        help="标准输入输出模式 (Claude Code / Cursor)")
    parser.add_argument("--sse", action="store_true",
                        help="HTTP SSE模式 (Reasonix / Trae Solo)")
    parser.add_argument("--port", type=int, default=8020,
                        help="SSE模式端口 (默认8020)")
    args = parser.parse_args()

    if args.sse:
        asyncio.run(run_sse(args.port))
    else:
        # 默认 stdio
        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
