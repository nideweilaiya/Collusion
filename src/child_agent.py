#!/usr/bin/env python3
"""Collusion 子 Agent 进程 — 黑板模式后台工作者

由 BlackboardOrchestrator 启动，每个 Agent 独立进程：
  1. 读取 task.json 获取任务摘要
  2. 读取 config.json 获取 API 配置
  3. 用自己的视角生成完整方案
  4. 写入 proposal.md
  5. 过程中遇到不确定的信息 → 写入 queries.json
  6. 等待主 Agent 回答 → 继续
  7. 完成后写入 status.json (phase=done)

用法:
  python child_agent.py --task-id bb_xxx --role ux --name "UX/产品专家" --focus "..."

模型选择:
  --model flash  → DeepSeek Flash (快速, ¥0.001/1k input)
  --model strong → DeepSeek Chat/R1 (深度推理, ¥0.004/1k output)
"""
import argparse
import json
import os
import sys
import time
import threading
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.deepseek import DeepSeekAdapter


def update_status(agent_dir: Path, **kwargs):
    """写入 Agent 状态文件"""
    status_path = agent_dir / "status.json"
    current = {}
    if status_path.exists():
        try:
            current = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    current.update(kwargs)
    current["updated_at"] = datetime.now().isoformat()
    tmp = status_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(status_path)


def check_queries(agent_dir: Path) -> list:
    """检查是否有已回答的询问"""
    query_path = agent_dir / "queries.json"
    if not query_path.exists():
        return []
    queries = json.loads(query_path.read_text(encoding="utf-8"))
    return [q for q in queries if q.get("answered") and not q.get("consumed")]


def mark_queries_consumed(agent_dir: Path):
    """标记所有已回答的询问为已消费"""
    query_path = agent_dir / "queries.json"
    if not query_path.exists():
        return
    queries = json.loads(query_path.read_text(encoding="utf-8"))
    for q in queries:
        if q.get("answered"):
            q["consumed"] = True
    tmp = query_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(query_path)


def ask_query(agent_dir: Path, question: str, context: str = ""):
    """向主 Agent 发出询问"""
    query_path = agent_dir / "queries.json"
    queries = []
    if query_path.exists():
        queries = json.loads(query_path.read_text(encoding="utf-8"))

    queries.append({
        "question": question,
        "context": context,
        "asked_at": datetime.now().isoformat(),
        "answered": False,
        "answer": "",
    })

    tmp = query_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(query_path)


def wait_for_answers(agent_dir: Path, timeout: int = 120) -> list:
    """等待主 Agent 回答询问，最多等 timeout 秒"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        answered = check_queries(agent_dir)
        if answered:
            return answered
        time.sleep(3)
    return []  # 超时，继续执行


def run_agent(task_id: str, role: str, name: str, object_name: str,
               focus: str, model_type: str, blackboard_root: str):
    """子 Agent 主循环"""
    agent_dir = Path(blackboard_root) / task_id / "agents" / role
    task_path = Path(blackboard_root) / task_id / "task.json"

    # ---- Phase 1: 启动 ----
    update_status(agent_dir, phase="starting", role=role, name=name)

    if not task_path.exists():
        update_status(agent_dir, phase="error", error="task.json not found")
        return

    task_data = json.loads(task_path.read_text(encoding="utf-8"))
    description = task_data.get("description", "")
    steps = task_data.get("steps", [])

    # ---- Phase 2: 初始化 LLM ----
    update_status(agent_dir, phase="connecting", progress=0.05)

    try:
        adapter = DeepSeekAdapter(model="deepseek-chat")
    except ValueError as e:
        update_status(agent_dir, phase="error", error=str(e))
        return

    # ---- Phase 3: 方案调研 ----
    update_status(agent_dir, phase="researching", progress=0.1)

    # 向主 Agent 询问不清楚的地方（如果需要）
    if len(description) < 20:
        ask_query(agent_dir, "任务描述过于简短，能否补充具体需求和约束？", description)
        update_status(agent_dir, phase="waiting", progress=0.15)
        answers = wait_for_answers(agent_dir, timeout=120)
        if answers:
            mark_queries_consumed(agent_dir)
            for a in answers:
                description += f"\n[补充]: {a['answer']}"

    # ---- Phase 4: 生成方案 ----
    update_status(agent_dir, phase="generating", progress=0.3)

    steps_text = "\n".join(
        f"环节{i+1}: {s.get('name', '')} — {s.get('description', '')}"
        for i, s in enumerate(steps)
    ) if steps else "（由 Agent 自行判断关键环节）"

    prompt = (
        f"你是{name}，代言{object_name}。你的核心关注点：{focus}\n\n"
        f"任务: {description}\n\n"
        f"环节清单:\n{steps_text}\n\n"
        f"请输出一份完整技术方案。每个环节都要有具体设计（技术选型、关键决策、代码示例）。"
        f"不要泛泛而谈，给出可落地的细节。用 Markdown 格式输出。"
    )

    try:
        proposal = adapter.cached_call(prompt, temperature=0.1, max_tokens=4096)

        # 检查是否需要补充信息
        if "TODO" in proposal or "待确认" in proposal:
            ask_query(agent_dir, "方案中存在不确定的部分（TODO/待确认），请确认或补充信息。",
                      proposal[:500])

        # ---- Phase 5: 写入方案 ----
        update_status(agent_dir, phase="writing", progress=0.8)

        proposal_path = agent_dir / "proposal.md"
        proposal_path.write_text(proposal, encoding="utf-8")

        # ---- Phase 6: 完成 ----
        update_status(
            agent_dir,
            phase="done",
            progress=1.0,
            proposal_length=len(proposal),
            tokens_used=adapter.total_input_tokens + adapter.total_output_tokens,
        )

    except Exception as e:
        update_status(agent_dir, phase="error", error=str(e))


# ==================== CLI 入口 ====================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collusion 子 Agent 进程")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--object", required=True)
    parser.add_argument("--focus", required=True)
    parser.add_argument("--model", default="flash")
    parser.add_argument("--blackboard", required=True)

    args = parser.parse_args()
    run_agent(
        task_id=args.task_id,
        role=args.role,
        name=args.name,
        object_name=args.object,
        focus=args.focus,
        model_type=args.model,
        blackboard_root=args.blackboard,
    )
