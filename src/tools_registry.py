"""v0.6 结构化工具分发器

替代 mcp_server.py 中展平的 if/elif 链。每个工具处理函数签名统一:
  async def handler(arguments, orchestrator, blackboard, executor) -> list[TextContent]

分组定义:
  probe:    只读发现 (零/极少 LLM 调用)
  check:    检查点评估 (3-8 次 LLM 调用)
  plan:     深度审查/方案 (8-25 次 LLM 调用, 按需)
  workspace: 状态管理 + 渲染
  blackboard: 旧黑板模式 (向后兼容)
  automation: GoalRunner 集成
"""

import json
import uuid
from typing import Dict, List, Callable

from mcp.types import TextContent

# ============================================================
# 分组定义
# ============================================================

TOOL_GROUPS = {
    "probe": [
        "collusion_search_assets",
        "collusion_elicit",
        "collusion_scout",
        "collusion_status",
    ],
    "check": [
        "collusion_assess",
        "collusion_check",
    ],
    "plan": [
        "collusion_orchestrate",
        "collusion_enhance",
        "collusion_review",
        "collusion_plan",
        "collusion_choose",
        "collusion_refine",
        "collusion_diagnose",
        "collusion_route",
    ],
    "workspace": [
        "collusion_result",
        "collusion_render",
        "collusion_branch",
        "collusion_merge",
        "collusion_adopt",
    ],
    "blackboard": [
        "collusion_blackboard_start",
        "collusion_blackboard_status",
        "collusion_blackboard_answer",
        "collusion_blackboard_merge",
    ],
    "automation": [
        "collusion_mod_goal",
        "collusion_goal_start",
        "collusion_goal_status",
    ],
}

# 旧名 → 新名映射
LEGACY_NAME_MAP = {
    "brainstorm_orchestrate": "collusion_orchestrate",
    "brainstorm_search_assets": "collusion_search_assets",
    "brainstorm_elicit": "collusion_elicit",
    "brainstorm_status": "collusion_status",
    "brainstorm_result": "collusion_result",
}


# ============================================================
# v0.6 新增工具处理函数
# ============================================================

async def _handle_assess(arguments, orchestrator, blackboard, executor):
    """collusion_assess — 轻量决策评估（主入口）"""
    task = arguments["task"]
    deep = arguments.get("deep", "auto")
    strict_mode = arguments.get("strict_mode", False)

    if deep not in ("auto", "force", "never"):
        deep = "auto"

    result = orchestrator.assess(task=task, deep=deep, strict_mode=strict_mode)

    if result.get("error"):
        return [TextContent(type="text", text=json.dumps({
            "error": result["error"],
            "task_id": result.get("task_id", ""),
        }, ensure_ascii=False, indent=2))]

    card = result["decision_card"]
    return [TextContent(type="text", text=json.dumps({
        "task_id": result["task_id"],
        "mode": result["mode"],
        "decision_card": card,
        "llm_calls": result["llm_calls"],
        "tokens_used": result["tokens_used"],
        "deep_review_recommended": result["deep_review_recommended"],
        "deep_review_reason": result.get("deep_review_reason", ""),
        "tip": (
            "使用 collusion_render 渲染为 Markdown/HTML 报告。"
            "若 deep_review_recommended=true，使用 collusion_orchestrate 深度审查。"
        ),
    }, ensure_ascii=False, indent=2))]


async def _handle_check(arguments, orchestrator, blackboard, executor):
    """collusion_check — 运行单个检查点"""
    from src.checkpoint.engine import create_engine
    from src.checkpoint.knowledge_retriever import KnowledgeRetriever
    from src.checkpoint.situation_compressor import SituationCompressor
    from src.models import CompressedSnapshot

    task = arguments.get("task", "")
    task_id = arguments.get("task_id", "")
    checkpoint_id = arguments.get("checkpoint", "")
    strict_mode = arguments.get("strict_mode", False)

    if not checkpoint_id:
        return [TextContent(type="text", text=json.dumps({
            "error": "checkpoint 参数必填。可选: semantic_consistency, interface_conflict, pattern_match"
        }, ensure_ascii=False))]

    if not task_id and not task:
        return [TextContent(type="text", text=json.dumps({
            "error": "task 或 task_id 必填"
        }, ensure_ascii=False))]

    # 检索 + 压缩
    task_text = task or ""
    if not task_id:
        task_id = f"check_{uuid.uuid4().hex[:8]}"

    retriever = KnowledgeRetriever(orchestrator=orchestrator)
    retrieved = retriever.retrieve(task=task_text, task_id=task_id)
    compressor = SituationCompressor(fast_llm=orchestrator.fast_llm)
    snapshot = compressor.compress(task_text, retrieved)

    # 单检查点执行
    engine = create_engine(orchestrator=orchestrator)
    cp_cls = engine.registry.get(checkpoint_id)
    if cp_cls is None:
        return [TextContent(type="text", text=json.dumps({
            "error": f"未知检查点: {checkpoint_id}",
            "available": list(engine.registry._checkpoints.keys()),
        }, ensure_ascii=False))]

    cp = cp_cls(fast_llm=orchestrator.fast_llm, strict_mode=strict_mode)
    result = cp.run(snapshot, arguments.get("artifacts", {}))

    return [TextContent(type="text", text=json.dumps({
        "task_id": task_id,
        "checkpoint_id": checkpoint_id,
        "result": result.to_dict(),
    }, ensure_ascii=False, indent=2))]


async def _handle_render(arguments, orchestrator, blackboard, executor):
    """collusion_render — 渲染 DecisionCard 或方案为 MD/HTML"""
    from src.render import render_decision_card
    from pathlib import Path

    task_id = arguments.get("task_id", "")
    fmt = arguments.get("format", "md")

    if not task_id:
        return [TextContent(type="text", text=json.dumps({
            "error": "task_id 必填"
        }, ensure_ascii=False))]

    # 先尝试从状态中获取 DecisionCard
    state = orchestrator.get_state(task_id)
    output_paths = {}

    if state:
        # 检查是否有旧编排的输出路径
        output_paths = state.get("output_paths", {})

    # 尝试渲染 DecisionCard (从 assess 结果)
    card_dict = None
    if state and state.get("decision_card"):
        card_dict = state["decision_card"]

    if card_dict:
        paths = render_decision_card(
            card_dict, fmt=fmt,
            data_dir=orchestrator.data_dir,
            task_id=task_id,
        )
        return [TextContent(type="text", text=json.dumps({
            "task_id": task_id,
            "format": fmt,
            "output_files": {k: str(v) for k, v in paths.items()},
        }, ensure_ascii=False, indent=2))]

    # 回退: 检查是否有旧编排的结果
    if output_paths:
        return [TextContent(type="text", text=json.dumps({
            "task_id": task_id,
            "format": fmt,
            "output_files": output_paths,
            "note": "已存在旧编排输出文件",
        }, ensure_ascii=False, indent=2))]

    return [TextContent(type="text", text=json.dumps({
        "error": f"任务 {task_id} 无可用内容渲染。请先用 collusion_assess 或 collusion_orchestrate 生成。"
    }, ensure_ascii=False))]


# ============================================================
# 旧工具处理函数 (包装为统一签名)
# ============================================================

async def _handle_search_assets(arguments, orchestrator, blackboard, executor):
    query = arguments["query"]
    top_k = arguments.get("top_k", 5)
    results = orchestrator.search_assets(query, top_k)
    return [TextContent(type="text", text=json.dumps({
        "query": query, "count": len(results), "results": results,
        "tip": "可参考历史方案的思路和架构，避免从零设计。",
    }, ensure_ascii=False, indent=2))]


async def _handle_adopt(arguments, orchestrator, blackboard, executor):
    """collusion_adopt — 用户确认采纳方案, 反馈给进化引擎"""
    plan_id = arguments["plan_id"]
    status = arguments.get("adopted", True)
    updated = 0
    if orchestrator.evolution is not None:
        updated = orchestrator.evolution.mark_adopted(plan_id, status)
    return [TextContent(type="text", text=json.dumps({
        "plan_id": plan_id,
        "adopted": status,
        "updated_entries": updated,
        "hint": (
            f"已标记 {updated} 条反馈记录为 adopted={status}。"
            "进化引擎权重已自动更新。" if updated else
            f"未找到匹配 '{plan_id}' 的未确认反馈记录。"
        ),
    }, ensure_ascii=False, indent=2))]


async def _handle_elicit(arguments, orchestrator, blackboard, executor):
    task_id = arguments["task_id"]
    answers = arguments.get("answers", {})
    state = orchestrator.get_state(task_id)
    if state is None:
        return [TextContent(type="text", text=json.dumps(
            {"error": f"任务不存在: {task_id}"}, ensure_ascii=False))]
    state_obj = orchestrator._load_state(task_id)
    if state_obj is None and task_id in orchestrator._states:
        state_obj = orchestrator._states[task_id]
    if state_obj is None:
        return [TextContent(type="text", text=json.dumps(
            {"error": f"任务状态无法加载: {task_id}"}, ensure_ascii=False))]
    state_obj = orchestrator.apply_elicitation_answers(state_obj, answers)
    orchestrator._save_state(state_obj)
    answered = sum(1 for q in state_obj.elicitation_questions if q.get("answered"))
    total = len(state_obj.elicitation_questions)
    return [TextContent(type="text", text=json.dumps({
        "task_id": task_id,
        "answered": f"{answered}/{total}",
        "all_answered": state_obj.elicitation_answered,
    }, ensure_ascii=False, indent=2))]


async def _handle_status(arguments, orchestrator, blackboard, executor):
    task_id = arguments["task_id"]
    state = orchestrator.get_state(task_id)
    if state is None:
        return [TextContent(type="text", text=json.dumps(
            {"error": f"任务不存在: {task_id}"}, ensure_ascii=False))]
    pending_questions = state.get("elicitation_questions", [])
    unanswered = [q for q in pending_questions if not q.get("answered", False)]
    return [TextContent(type="text", text=json.dumps({
        "task_id": state["task_id"],
        "task": state["original_task"],
        "phase": state["phase"],
        "schemes": len(state.get("schemes", {})),
        "steps": len(state.get("step_list", [])),
        "cost": state.get("total_cost_rmb", 0),
        "tokens": state.get("total_tokens", 0),
        "pending_questions": unanswered,
    }, ensure_ascii=False, indent=2))]


async def _handle_result(arguments, orchestrator, blackboard, executor):
    task_id = arguments["task_id"]
    result = orchestrator.get_result(task_id)
    if result is None:
        return [TextContent(type="text", text=json.dumps(
            {"error": f"任务不存在: {task_id}"}, ensure_ascii=False))]
    return [TextContent(type="text", text=json.dumps(
        result, ensure_ascii=False, indent=2))]


# ============================================================
# 分发器
# ============================================================

# 工具名 → 处理函数
TOOL_REGISTRY: Dict[str, Callable] = {
    # v0.6 新工具
    "collusion_assess": _handle_assess,
    "collusion_check": _handle_check,
    "collusion_render": _handle_render,
    "collusion_adopt": _handle_adopt,
    # 重命名的工具
    "collusion_search_assets": _handle_search_assets,
    "collusion_elicit": _handle_elicit,
    "collusion_status": _handle_status,
    "collusion_result": _handle_result,
}


def resolve_handler(name: str):
    """解析工具名 → 处理函数

    1. 先在 TOOL_REGISTRY 中查找
    2. 查 LEGACY_NAME_MAP 旧名映射
    3. 返回 None 表示需要走旧分发链
    """
    # 直接命中
    if name in TOOL_REGISTRY:
        return TOOL_REGISTRY[name]

    # 旧名映射
    mapped = LEGACY_NAME_MAP.get(name)
    if mapped and mapped in TOOL_REGISTRY:
        return TOOL_REGISTRY[mapped]

    return None
