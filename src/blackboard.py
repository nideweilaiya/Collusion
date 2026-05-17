"""Collusion 黑板+顾问模式 — 核心编排引擎

黑板模式架构:
  主 Agent (Reasonix/Claude Code)
    → 写入 task.json (上下文摘要 + 环节清单)
    → 启动 3 个子 Agent 进程（静默后台）
    → 主 Agent 继续服务用户
    → 子 Agent 遇疑问 → 写入 queries.json → 主 Agent 通知用户
    → 全部子 Agent 完成 → 合并器运行 → 输出最终方案

文件结构:
  ~/.collusion/blackboard/{task_id}/
  ├── task.json          # 任务摘要
  ├── agents/
  │   ├── security/      # 安全专家
  │   │   ├── proposal.md
  │   │   ├── queries.json
  │   │   └── status.json  # {phase, progress, heartbeat}
  │   ├── architecture/  # 架构师
  │   └── ux/            # UX专家
  └── merged/
      └── final_report.md

Windows 适配: 子进程使用 CREATE_NO_WINDOW 隐藏窗口，文件锁用原子 rename
"""
import json
import os
import sys
import time
import uuid
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional


BLACKBOARD_ROOT = Path.home() / ".collusion" / "blackboard"


class BlackboardOrchestrator:
    """黑板模式编排器 — 管理子 Agent 生命周期和黑板状态"""

    AGENT_ROLES = {
        "ux": {
            "name": "UX/产品专家",
            "object": "业务价值对象",
            "focus": "用户能否用起来？操作是否流畅？部署门槛低不低？关键场景是否遗漏？",
            "model": "flash",  # 低复杂度，Flash 足够
        },
        "architecture": {
            "name": "性能架构师",
            "object": "技术架构对象",
            "focus": "技术选型是否合理？扩展性够不够？性能瓶颈在哪？数据流是否清晰？",
            "model": "strong",  # 需要深度推理，用 R1
        },
        "security": {
            "name": "安全专家",
            "object": "安全与合规对象",
            "focus": "数据安全：加密/脱敏/备份。认证授权。威胁建模。合规要求。",
            "model": "flash",  # 安全模式较固定，Flash 足够
        },
    }

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._active_tasks: Dict[str, dict] = {}
        self._lock = threading.Lock()

    # ==================== 黑板管理 ====================

    def create_task(self, task_description: str, step_list: list = None) -> str:
        """创建新任务，返回 task_id，写入黑板"""
        task_id = f"bb_{uuid.uuid4().hex[:10]}"
        task_dir = BLACKBOARD_ROOT / task_id

        # 创建目录结构
        for role in self.AGENT_ROLES:
            (task_dir / "agents" / role).mkdir(parents=True, exist_ok=True)
        (task_dir / "merged").mkdir(parents=True, exist_ok=True)

        # 写入任务摘要
        task_data = {
            "task_id": task_id,
            "description": task_description,
            "steps": step_list or [],
            "created_at": datetime.now().isoformat(),
            "status": "initialized",
            "agent_status": {role: "pending" for role in self.AGENT_ROLES},
        }
        self._atomic_write(task_dir / "task.json", task_data)

        self._active_tasks[task_id] = task_data
        return task_id

    def read_task(self, task_id: str) -> dict:
        """读取任务摘要"""
        path = BLACKBOARD_ROOT / task_id / "task.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def update_task_status(self, task_id: str, status: str):
        """更新任务状态"""
        data = self.read_task(task_id)
        data["status"] = status
        data["updated_at"] = datetime.now().isoformat()
        self._atomic_write(BLACKBOARD_ROOT / task_id / "task.json", data)

    # ==================== Agent 进程管理 ====================

    def launch_agents(self, task_id: str) -> dict:
        """启动 3 个子 Agent 进程（Windows 隐藏窗口）"""
        task_dir = BLACKBOARD_ROOT / task_id
        task_data = self.read_task(task_id)
        if not task_data:
            return {"error": f"任务不存在: {task_id}"}

        task_data["status"] = "running"
        self._atomic_write(task_dir / "task.json", task_data)

        processes = {}
        for role, info in self.AGENT_ROLES.items():
            try:
                proc = self._spawn_agent(task_id, role, info, task_data)
                processes[role] = {
                    "pid": proc.pid,
                    "role": info["name"],
                    "model": info["model"],
                }
            except Exception as e:
                processes[role] = {"error": str(e)}

        task_data["agent_processes"] = processes
        task_data["launched_at"] = datetime.now().isoformat()
        self._atomic_write(task_dir / "task.json", task_data)

        return {
            "task_id": task_id,
            "agents": len(processes),
            "processes": processes,
            "blackboard_path": str(task_dir),
        }

    def _spawn_agent(self, task_id: str, role: str, info: dict, task_data: dict):
        """启动单个子 Agent 进程"""
        agent_script = Path(__file__).parent / "child_agent.py"
        cmd = [
            sys.executable, str(agent_script),
            "--task-id", task_id,
            "--role", role,
            "--name", info["name"],
            "--object", info["object"],
            "--focus", info["focus"],
            "--model", info.get("model", "flash"),
            "--blackboard", str(BLACKBOARD_ROOT),
        ]

        # Windows: 隐藏窗口
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return proc

    # ==================== 状态查询 ====================

    def get_status(self, task_id: str) -> dict:
        """查询黑板任务进度"""
        task_dir = BLACKBOARD_ROOT / task_id
        task_path = task_dir / "task.json"
        if not task_path.exists():
            return {"error": f"任务不存在: {task_id}"}

        task_data = json.loads(task_path.read_text(encoding="utf-8"))

        # 检查每个 Agent 的状态
        agent_details = {}
        for role in self.AGENT_ROLES:
            status_path = task_dir / "agents" / role / "status.json"
            if status_path.exists():
                agent_details[role] = json.loads(status_path.read_text(encoding="utf-8"))
            else:
                agent_details[role] = {"phase": "unknown"}

        # 检查询问队列
        pending_queries = []
        for role in self.AGENT_ROLES:
            query_path = task_dir / "agents" / role / "queries.json"
            if query_path.exists():
                queries = json.loads(query_path.read_text(encoding="utf-8"))
                unanswered = [q for q in queries if not q.get("answered")]
                if unanswered:
                    pending_queries.extend(unanswered)

        # 聚合进度
        phases = [d.get("phase", "unknown") for d in agent_details.values()]
        done_count = sum(1 for p in phases if p == "done")
        error_count = sum(1 for p in phases if p == "error")

        return {
            "task_id": task_id,
            "status": task_data.get("status", "unknown"),
            "description": task_data.get("description", "")[:100],
            "agents": agent_details,
            "progress": f"{done_count}/{len(self.AGENT_ROLES)} 完成",
            "pending_queries": pending_queries,
            "done": done_count + error_count >= len(self.AGENT_ROLES),
            "blackboard_path": str(task_dir),
        }

    def answer_query(self, task_id: str, role: str, query_index: int, answer: str) -> dict:
        """回答子 Agent 的询问"""
        task_dir = BLACKBOARD_ROOT / task_id
        query_path = task_dir / "agents" / role / "queries.json"
        if not query_path.exists():
            return {"error": "无询问队列"}

        queries = json.loads(query_path.read_text(encoding="utf-8"))
        if query_index >= len(queries):
            return {"error": f"询问索引无效: {query_index}"}

        queries[query_index]["answered"] = True
        queries[query_index]["answer"] = answer
        queries[query_index]["answered_at"] = datetime.now().isoformat()
        self._atomic_write(query_path, queries)

        return {"status": "answered", "role": role, "query_index": query_index}

    # ==================== 合并 ====================

    def merge_proposals(self, task_id: str) -> dict:
        """合并 3 个子 Agent 的方案"""
        task_dir = BLACKBOARD_ROOT / task_id
        proposals = {}
        for role in self.AGENT_ROLES:
            prop_path = task_dir / "agents" / role / "proposal.md"
            if prop_path.exists():
                proposals[role] = prop_path.read_text(encoding="utf-8")

        if not proposals:
            return {"error": "没有子 Agent 完成方案"}

        # 简单合并：拼接所有方案
        merged_parts = [f"# 最终方案 — {task_id}\n"]
        merged_parts.append(f"> 生成时间: {datetime.now().isoformat()}\n")
        merged_parts.append(f"> 参与 Agent: {', '.join(proposals.keys())}\n\n")

        for role, content in proposals.items():
            info = self.AGENT_ROLES.get(role, {})
            merged_parts.append(f"## {info.get('name', role)}视角\n\n")
            merged_parts.append(content[:3000])
            merged_parts.append("\n\n---\n\n")

        merged_content = "".join(merged_parts)
        merged_path = task_dir / "merged" / "final_report.md"
        merged_path.write_text(merged_content, encoding="utf-8")

        # 更新任务状态
        self.update_task_status(task_id, "completed")

        return {
            "task_id": task_id,
            "agents_contributed": len(proposals),
            "merged_path": str(merged_path),
            "content_preview": merged_content[:500],
        }

    # ==================== 工具方法 ====================

    @staticmethod
    def _atomic_write(path: Path, data: dict):
        """原子写入：先写临时文件，再 rename"""
        tmp = path.with_suffix(path.suffix + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


# 全局单例
_orchestrator = BlackboardOrchestrator()
