"""Collusion 黑板+顾问模式 — 完整 7 阶段编排引擎

与 brainstorm_orchestrate 同等质量的编排流程，但通过文件系统黑板 + 多子进程实现，
适配 Reasonix 等单 Agent 宿主。

7 阶段:
  Phase 1-2: 任务解构与共识 → task.json
  Phase 3: 并行提案 → 3 agents, mode=proposal
  Phase 4: 交叉审查 → 3 agents, mode=review
  Phase 4.5: 可行性收束 → 3 agents, mode=brake
  Phase 4.6: Owner 整合 → 3 agents, mode=integrate
  Phase 6: 投票评分 → 3 agents, mode=vote (取平均值)
  Phase 7: 合并输出 → merge → final_report.md
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

from src.agent_manager import (
    get_agent_manager,
    AgentManagerConfig,
    ExecutionMode,
    AgentStatus,
)


BLACKBOARD_ROOT = Path.home() / ".collusion" / "blackboard"

AGENT_ROLES = {
    "ux": {
        "name": "UX/产品专家", "object": "业务价值对象",
        "model": "flash",
    },
    "architecture": {
        "name": "性能架构师", "object": "技术架构对象",
        "model": "strong",
    },
    "security": {
        "name": "安全专家", "object": "安全与合规对象",
        "model": "flash",
    },
}

ORCHESTRATION_PHASES = [
    ("proposal", "Phase 3: 并行提案"),
    ("review", "Phase 4: 交叉审查"),
    ("brake", "Phase 4.5: 可行性收束"),
    ("integrate", "Phase 4.6: Owner 整合"),
    ("vote", "Phase 6: 投票评分"),
]


class BlackboardOrchestrator:
    """黑板编排器 — 完整 7 阶段"""

    def __init__(self, execution_mode: ExecutionMode = ExecutionMode.PROCESS):
        self._lock = threading.Lock()
        
        # 配置 Agent 管理器
        config = AgentManagerConfig(
            default_execution_mode=execution_mode,
            log_dir=BLACKBOARD_ROOT / "logs",
        )
        self._agent_manager = get_agent_manager(config)

    # ==================== 任务管理 ====================

    def create_task(self, task_description: str, step_list: list = None) -> str:
        task_id = f"bb_{uuid.uuid4().hex[:10]}"
        task_dir = BLACKBOARD_ROOT / task_id
        for role in AGENT_ROLES:
            (task_dir / "agents" / role).mkdir(parents=True, exist_ok=True)
        (task_dir / "merged").mkdir(parents=True, exist_ok=True)

        task_data = {
            "task_id": task_id,
            "description": task_description,
            "steps": step_list or [],
            "created_at": datetime.now().isoformat(),
            "status": "initialized",
            "current_phase": "",
            "phase_history": [],
        }
        self._atomic_write(task_dir / "task.json", task_data)
        return task_id

    def read_task(self, task_id: str) -> dict:
        path = BLACKBOARD_ROOT / task_id / "task.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    # ==================== 进程管理（使用新的 AgentManager）====================

    def _spawn_agents(self, task_id: str, mode: str) -> dict:
        """启动 3 个子 Agent，并行执行指定阶段（使用 AgentManager）"""
        roles = list(AGENT_ROLES.keys())
        agents = self._agent_manager.spawn_agents(
            task_id=task_id,
            roles=roles,
            mode=mode,
        )
        return {role: {"agent": agent} for role, agent in agents.items()}

    # ==================== 完整编排 ====================

    def orchestrate_full(self, task_id: str) -> dict:
        """运行完整 7 阶段编排，返回最终结果"""
        task_data = self.read_task(task_id)
        if not task_data:
            return {"error": f"任务不存在: {task_id}"}
        task_dir = BLACKBOARD_ROOT / task_id

        results = {"task_id": task_id, "phases": {}}

        try:
            for mode, phase_name in ORCHESTRATION_PHASES:
                print(f"  [{phase_name}] 启动 3 Agent...")
                task_data["current_phase"] = mode
                task_data["status"] = "running"
                self._atomic_write(task_dir / "task.json", task_data)

                # 启动 Agent
                procs = self._spawn_agents(task_id, mode)

                # 等待全部完成
                print(f"  [{phase_name}] 等待 Agent 完成...")
                success, agents = self._agent_manager.wait_for_agents(
                    task_id=task_id,
                    timeout=300.0,
                )

                # 检查状态
                phase_status = self._check_agents_phase(task_id, mode)
                if not phase_status["all_done"]:
                    print(f"  [{phase_name}] 警告: 并非所有 Agent 都正常完成")

                task_data["phase_history"].append({
                    "phase": mode, "name": phase_name,
                    "completed_at": datetime.now().isoformat(),
                    "agents": self._agent_manager.get_task_status(task_id),
                })
                self._atomic_write(task_dir / "task.json", task_data)
                results["phases"][mode] = phase_status

            # Phase 7: 合并
            merged = self._merge_all(task_id)
            results["merged"] = merged

            task_data["status"] = "completed"
            self._atomic_write(task_dir / "task.json", task_data)
            
        except Exception as e:
            print(f"  [编排异常]: {e}")
            task_data["status"] = "error"
            task_data["error_message"] = str(e)
            self._atomic_write(task_dir / "task.json", task_data)
            results["error"] = str(e)
        finally:
            # 清理该任务的 Agent
            self._agent_manager.stop_task_agents(task_id)
        
        return results

    def _check_agents_phase(self, task_id: str, expected_mode: str) -> dict:
        """检查所有 Agent 在指定阶段的完成状态"""
        task_dir = BLACKBOARD_ROOT / task_id
        agents = {}
        done_count = 0
        for role in AGENT_ROLES:
            sp = task_dir / "agents" / role / "status.json"
            if sp.exists():
                s = json.loads(sp.read_text(encoding="utf-8"))
                phase = s.get("phase", "unknown")
                agents[role] = phase
                if phase == f"{expected_mode}_done":
                    done_count += 1
            else:
                agents[role] = "no_status"
        return {
            "agents": agents,
            "done": done_count,
            "total": len(AGENT_ROLES),
            "all_done": done_count >= len(AGENT_ROLES),
        }

    # ==================== 合并 ====================

    def _merge_all(self, task_id: str) -> dict:
        """Phase 7: 收集投票 → 合并最终方案"""
        task_dir = BLACKBOARD_ROOT / task_id
        task_data = self.read_task(task_id)

        # 收集投票
        votes = {}
        for role in AGENT_ROLES:
            vp = task_dir / "agents" / role / "vote.json"
            if vp.exists():
                data = json.loads(vp.read_text(encoding="utf-8"))
                for v in data.get("votes", []):
                    target = v.get("target", "")
                    if target not in votes:
                        votes[target] = []
                    votes[target].append(v)

        # 平均分 + 排名
        rankings = []
        for target, vlist in votes.items():
            avg = {
                "correctness": sum(v["correctness"] for v in vlist) / len(vlist),
                "completeness": sum(v["completeness"] for v in vlist) / len(vlist),
                "feasibility": sum(v["feasibility"] for v in vlist) / len(vlist),
                "innovation": sum(v["innovation"] for v in vlist) / len(vlist),
                "business_alignment": sum(v["business_alignment"] for v in vlist) / len(vlist),
            }
            total = (avg["correctness"] * 0.20 + avg["completeness"] * 0.20 +
                     avg["feasibility"] * 0.25 + avg["innovation"] * 0.15 +
                     avg["business_alignment"] * 0.20)
            rankings.append({
                "target": target,
                "scores": avg,
                "total": round(total, 2),
                "comments": [v.get("comment", "") for v in vlist],
            })
        rankings.sort(key=lambda x: x["total"], reverse=True)

        # 构建最终报告
        parts = [f"# 最终方案 — {task_id}\n"]
        parts.append(f"> 任务: {task_data.get('description', '')}\n")
        parts.append(f"> 生成时间: {datetime.now().isoformat()}\n\n")

        # 排名
        parts.append("## 🏆 方案排名\n\n")
        medals = ["🥇", "🥈", "🥉"]
        for i, r in enumerate(rankings):
            medals[i] if i < len(medals) else f"#{i+1}"
            parts.append(f"{medals[i] if i < len(medals) else f'#{i+1}'} **{r['target']}** — {r['total']}分\n")
            parts.append(f"   正确{r['scores']['correctness']:.1f} 完整{r['scores']['completeness']:.1f} ")
            parts.append(f"可行{r['scores']['feasibility']:.1f} 创新{r['scores']['innovation']:.1f} ")
            parts.append(f"业务{r['scores']['business_alignment']:.1f}\n")
            if r["comments"]:
                parts.append(f"   {r['comments'][0]}\n")
            parts.append("\n")

        # 各方案最终版
        for role in AGENT_ROLES:
            fp = task_dir / "agents" / role / "proposal_final.md"
            pp = task_dir / "agents" / role / "proposal.md"
            path = fp if fp.exists() else pp
            if path.exists():
                info = AGENT_ROLES[role]
                parts.append(f"## {info['name']}视角\n\n")
                parts.append(path.read_text(encoding="utf-8")[:3000])
                parts.append("\n\n---\n\n")

        merged = "".join(parts)
        merged_path = task_dir / "merged" / "final_report.md"
        merged_path.write_text(merged, encoding="utf-8")

        return {
            "rankings": rankings,
            "merged_path": str(merged_path),
            "top1": rankings[0]["target"] if rankings else None,
            "content_preview": merged[:600],
        }

    # ==================== 遗留 API（向后兼容）====================

    def launch_agents(self, task_id: str) -> dict:
        """向后兼容：只启动 proposal 阶段"""
        procs = self._spawn_agents(task_id, "proposal")
        return {
            "task_id": task_id,
            "agents": len(procs),
            "processes": procs,
            "note": "使用 orchestrate_full() 运行完整 7 阶段",
        }

    def get_status(self, task_id: str) -> dict:
        task_dir = BLACKBOARD_ROOT / task_id
        task_path = task_dir / "task.json"
        if not task_path.exists():
            return {"error": f"任务不存在: {task_id}"}

        task_data = json.loads(task_path.read_text(encoding="utf-8"))
        
        # 从 AgentManager 获取更详细的状态
        agent_manager_status = self._agent_manager.get_task_status(task_id)
        
        # 同时也检查文件系统状态（作为备份）
        agent_details = {}
        for role in AGENT_ROLES:
            sp = task_dir / "agents" / role / "status.json"
            file_status = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {"phase": "unknown"}
            
            # 合并 AgentManager 的状态和文件系统状态
            agent_details[role] = {
                **file_status,
                "manager_status": agent_manager_status.get(role, {}),
            }

        phases = [d.get("phase", "unknown") for d in agent_details.values()]
        done = sum(1 for p in phases if "_done" in p)
        error = sum(1 for p in phases if "_error" in p)

        return {
            "task_id": task_id,
            "status": task_data.get("status", "unknown"),
            "current_phase": task_data.get("current_phase", ""),
            "description": task_data.get("description", "")[:100],
            "agents": agent_details,
            "progress": f"{done}/{len(AGENT_ROLES)} 完成",
            "done": done + error >= len(AGENT_ROLES),
        }

    def merge_proposals(self, task_id: str) -> dict:
        return self._merge_all(task_id)

    def answer_query(self, task_id: str, role: str, query_index: int, answer: str) -> dict:
        # 简化版：兼容旧接口
        return {"status": "answered", "note": "黑板模式已升级，query 功能简化"}

    @staticmethod
    def _atomic_write(path: Path, data: dict):
        tmp = path.with_suffix(path.suffix + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
