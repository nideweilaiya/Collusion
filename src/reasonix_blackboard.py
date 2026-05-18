"""
Collusion for Reasonix - 黑板文件管理工具

这个模块提供结构化的文件读写，配合 Reasonix 的文件操作工具使用。
所有数据存储在 ~/.reasonix/collusion/ 下。
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime


COLLUSION_ROOT = Path.home() / ".reasonix" / "collusion"


def ensure_collusion_dirs(task_id: str) -> None:
    """确保任务目录结构存在"""
    task_dir = COLLUSION_ROOT / "tasks" / task_id
    for role in ["ux", "architecture", "security"]:
        (task_dir / "agents" / role).mkdir(parents=True, exist_ok=True)
    (task_dir / "final").mkdir(parents=True, exist_ok=True)


def init_task(task_id: str, description: str, steps: Optional[List[Dict]] = None) -> Dict:
    """初始化新任务"""
    ensure_collusion_dirs(task_id)
    task_data = {
        "task_id": task_id,
        "description": description,
        "steps": steps or [],
        "created_at": datetime.now().isoformat(),
        "status": "initialized",
        "current_phase": "init",
        "phase_history": [],
    }
    task_file = COLLUSION_ROOT / "tasks" / task_id / "task.json"
    task_file.write_text(json.dumps(task_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return task_data


def save_proposal(task_id: str, role: str, content: str) -> None:
    """保存 Agent 提案"""
    proposal_file = COLLUSION_ROOT / "tasks" / task_id / "agents" / role / "proposal.md"
    proposal_file.write_text(content, encoding="utf-8")


def save_reviews(task_id: str, role: str, reviews: List[Dict]) -> None:
    """保存评审结果"""
    reviews_file = COLLUSION_ROOT / "tasks" / task_id / "agents" / role / "reviews.json"
    reviews_file.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")


def save_final_proposal(task_id: str, role: str, content: str) -> None:
    """保存整合后的最终方案"""
    final_file = COLLUSION_ROOT / "tasks" / task_id / "agents" / role / "final.md"
    final_file.write_text(content, encoding="utf-8")


def save_rankings(task_id: str, rankings: List[Dict]) -> None:
    """保存排名结果"""
    rankings_file = COLLUSION_ROOT / "tasks" / task_id / "final" / "rankings.json"
    rankings_file.write_text(json.dumps(rankings, ensure_ascii=False, indent=2), encoding="utf-8")


def save_final_report(task_id: str, content: str) -> None:
    """保存最终报告"""
    report_file = COLLUSION_ROOT / "tasks" / task_id / "final" / "report.md"
    report_file.write_text(content, encoding="utf-8")


def get_task(task_id: str) -> Optional[Dict]:
    """读取任务数据"""
    task_file = COLLUSION_ROOT / "tasks" / task_id / "task.json"
    if not task_file.exists():
        return None
    return json.loads(task_file.read_text(encoding="utf-8"))


def get_proposal(task_id: str, role: str) -> Optional[str]:
    """读取提案"""
    proposal_file = COLLUSION_ROOT / "tasks" / task_id / "agents" / role / "proposal.md"
    if not proposal_file.exists():
        return None
    return proposal_file.read_text(encoding="utf-8")


def get_all_proposals(task_id: str) -> Dict[str, str]:
    """读取所有提案"""
    proposals = {}
    for role in ["ux", "architecture", "security"]:
        proposal = get_proposal(task_id, role)
        if proposal:
            proposals[role] = proposal
    return proposals


def update_task_status(task_id: str, status: str, phase: Optional[str] = None) -> None:
    """更新任务状态"""
    task = get_task(task_id)
    if not task:
        return
    task["status"] = status
    if phase:
        task["current_phase"] = phase
        if "phase_history" not in task:
            task["phase_history"] = []
        task["phase_history"].append({
            "phase": phase,
            "timestamp": datetime.now().isoformat(),
        })
    task_file = COLLUSION_ROOT / "tasks" / task_id / "task.json"
    task_file.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")


def get_collusion_root() -> str:
    """获取黑板根目录"""
    return str(COLLUSION_ROOT)


def list_tasks() -> List[str]:
    """列出所有任务"""
    tasks_dir = COLLUSION_ROOT / "tasks"
    if not tasks_dir.exists():
        return []
    return sorted([d.name for d in tasks_dir.iterdir() if d.is_dir()])


# 快速测试
if __name__ == "__main__":
    print(f"Collusion root: {get_collusion_root()}")
    print(f"Existing tasks: {list_tasks()}")
