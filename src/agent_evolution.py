"""Collusion v1.5-v2.0 — Agent 能力自评与自组织进化引擎

三阶段演进路径:
  v1.5: 能力自评 + 动态路由（经验标签积累，Coordinator智能派活）
  v1.7: 角色提议 + 任务谈判（Agent主动提议承担不同角色）
  v2.0: 自发分工 + 动态团队组建（无预设团队，Agent自组织）

参考:
  Drop the Hierarchy and Roles (Mar 2026, 25K任务)
  Self-Organizing MAS for Continuous Software Dev (Mar 2026)
"""
import json
import time
import math
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict


class AgentCapabilityProfile:
    """单个 Agent 的能力画像 — 积累经验标签、成功率、专业领域"""

    def __init__(self, agent_id: str, role: str = ""):
        self.agent_id = agent_id
        self.role = role
        self.total_tasks = 0
        self.successes = 0
        self.experience_tags: Dict[str, int] = defaultdict(int)  # tag → 执行次数
        self.task_scores: Dict[str, List[float]] = defaultdict(list)  # task_type → [scores]
        self.peer_ratings: Dict[str, float] = {}  # 其他Agent给的评分
        self.role_proposals: List[dict] = []  # 角色提议历史
        self.created_at = time.time()

    def record_task(self, task_type: str, success: bool, score: float,
                    tags: List[str] = None, peer_feedback: dict = None):
        """记录一次任务执行"""
        self.total_tasks += 1
        if success:
            self.successes += 1

        for tag in (tags or []):
            self.experience_tags[tag] = self.experience_tags.get(tag, 0) + 1

        self.task_scores.setdefault(task_type, []).append(score)

        if peer_feedback:
            for peer_role, rating in peer_feedback.items():
                current = self.peer_ratings.get(peer_role, 0)
                n = self.task_scores[task_type].count(score)  # rough count
                self.peer_ratings[peer_role] = (current * (n-1) + rating) / max(n, 1)

    def get_expertise(self, tags: List[str]) -> float:
        """计算对新任务的适配度 (基于经验标签和历史成功率)"""
        if self.total_tasks == 0:
            return 0.3  # 新手，给低基础分

        # 标签匹配度 (Sanity.io)
        shared = sum(1 for t in tags if t in self.experience_tags)
        if shared == 0:
            return 0.2  # 无匹配经验，但至少是已知Agent

        tag_score = (shared * 2) / max(len(tags) + len(self.experience_tags), 1)

        # 历史成功率
        success_rate = self.successes / max(self.total_tasks, 1)

        # 专业深度 — 相关任务做越多分越高
        relevant_tasks = sum(
            count for tag, count in self.experience_tags.items()
            if tag in tags
        )
        depth_score = min(relevant_tasks / 10, 1.0)

        return round(tag_score * 0.4 + success_rate * 0.35 + depth_score * 0.25, 3)

    def propose_role_change(self, task_tags: List[str]) -> Optional[dict]:
        """v1.7: Agent 主动提议角色变更"""
        expertise = self.get_expertise(task_tags)

        if expertise > 0.6 and self.successes / max(self.total_tasks, 1) > 0.7:
            return {
                "agent_id": self.agent_id,
                "current_role": self.role,
                "proposed_role": f"{self.role}_lead",
                "confidence": round(expertise, 2),
                "reason": f"高经验匹配 (expertise={expertise:.2f}) + 高成功率",
            }
        elif expertise < 0.2 and self.total_tasks > 5:
            # 能力不匹配，提议缩减范围
            return {
                "agent_id": self.agent_id,
                "current_role": self.role,
                "proposed_scope": "reduced",
                "confidence": round(expertise, 2),
                "reason": "经验不匹配当前任务标签",
            }
        return None

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "total_tasks": self.total_tasks,
            "success_rate": round(self.successes / max(self.total_tasks, 1), 3),
            "top_tags": sorted(self.experience_tags.items(),
                               key=lambda x: x[1], reverse=True)[:10],
            "expertise_areas": list(self.task_scores.keys()),
            "peer_ratings": self.peer_ratings,
        }


class AgentEvolutionEngine:
    """Agent 自组织进化引擎 — 管理所有Agent的能力画像和数据积累

    数据驱动三阶段:
      - 现在: 收集执行日志(evolution.py) + Agent能力画像(本模块)
      - v1.5: 基于能力自评的动态路由
      - v1.7: Agent角色提议
      - v2.0: 自发分工 + 动态团队
    """

    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "agent_evolution"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.profiles: Dict[str, AgentCapabilityProfile] = {}
        self.team_history: List[dict] = []  # 团队组建历史
        self._load()

    def get_or_create(self, agent_id: str, role: str = "") -> AgentCapabilityProfile:
        if agent_id not in self.profiles:
            self.profiles[agent_id] = AgentCapabilityProfile(agent_id, role)
        return self.profiles[agent_id]

    def record_execution(self, agent_id: str, role: str, task_desc: str,
                         success: bool, score: float, tags: List[str] = None):
        """Agent执行一次任务后记录"""
        profile = self.get_or_create(agent_id, role)

        # 从任务描述提取任务类型
        task_type = self._classify_task(task_desc)

        profile.record_task(task_type, success, score, tags)
        self._save()
        return profile.to_dict()

    def select_agents(self, task_tags: List[str], n_agents: int = 3) -> List[Tuple[str, float]]:
        """v1.5: 基于能力自评的动态路由 — 选择最适合的Agent组合"""
        if not self.profiles:
            return []

        scored = []
        for agent_id, profile in self.profiles.items():
            expertise = profile.get_expertise(task_tags)
            scored.append((agent_id, expertise))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n_agents]

    def get_role_proposals(self, task_tags: List[str]) -> List[dict]:
        """v1.7: 收集所有Agent的角色提议"""
        proposals = []
        for agent_id, profile in self.profiles.items():
            proposal = profile.propose_role_change(task_tags)
            if proposal:
                proposals.append(proposal)
        return proposals

    def record_team(self, task_desc: str, agents: List[str],
                    success: bool, task_tags: List[str] = None):
        """记录团队组建结果 — v2.0数据基础"""
        self.team_history.append({
            "time": time.time(),
            "task": task_desc[:100],
            "agents": agents,
            "tags": task_tags or [],
            "success": success,
        })
        if len(self.team_history) > 500:
            self.team_history = self.team_history[-500:]
        self._save()

    def find_best_team(self, task_tags: List[str], n_agents: int = 3) -> List[str]:
        """v2.0: 从历史中找到类似任务的最佳团队组合"""
        if not self.team_history or not task_tags:
            return []

        tag_set = set(task_tags)
        scored = []
        for record in self.team_history:
            record_tags = set(record.get("tags", []))
            overlap = len(tag_set & record_tags)
            if overlap > 0:
                success_bonus = 2.0 if record["success"] else 0.5
                scored.append((record["agents"], overlap + success_bonus))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0][:n_agents] if scored else []

    def get_stats(self) -> dict:
        profiles_data = {aid: p.to_dict() for aid, p in self.profiles.items()}
        return {
            "n_agents": len(self.profiles),
            "total_executions": sum(p.total_tasks for p in self.profiles.values()),
            "profiles": profiles_data,
            "n_team_records": len(self.team_history),
        }

    def _classify_task(self, task_desc: str) -> str:
        """从描述文本分类任务类型"""
        task_lower = task_desc.lower()
        patterns = {
            "api设计": ["api", "rest", "接口", "crud"],
            "数据库": ["数据库", "存储", "sql", "nosql", "postgres"],
            "安全": ["安全", "认证", "授权", "oauth", "jwt", "加密"],
            "部署": ["部署", "docker", "k8s", "ci", "ci/cd"],
            "性能": ["性能", "高并发", "缓存", "优化", "扩展"],
            "前端": ["前端", "ui", "ux", "页面", "界面"],
            "架构": ["架构", "设计", "方案", "选型"],
        }
        for task_type, keywords in patterns.items():
            if any(kw in task_lower for kw in keywords):
                return task_type
        return "通用"

    def _load(self):
        path = self.dir / "profiles.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for aid, pdata in data.get("profiles", {}).items():
                    profile = AgentCapabilityProfile(aid, pdata.get("role", ""))
                    profile.total_tasks = pdata.get("total_tasks", 0)
                    profile.successes = pdata.get("successes", 0)
                    profile.experience_tags = defaultdict(int, pdata.get("experience_tags", {}))
                    profile.task_scores = defaultdict(list, pdata.get("task_scores", {}))
                    self.profiles[aid] = profile
            self.team_history = data.get("team_history", [])

    def _save(self):
        data = {
            "profiles": {aid: p.to_dict() for aid, p in self.profiles.items()},
            "team_history": self.team_history[-200:],  # 只保留最近200条
        }
        # 重建完整数据以便下次加载
        full_data = {
            "profiles": {
                aid: {
                    "agent_id": p.agent_id,
                    "role": p.role,
                    "total_tasks": p.total_tasks,
                    "successes": p.successes,
                    "experience_tags": dict(p.experience_tags),
                    "task_scores": {k: list(v) for k, v in p.task_scores.items()},
                    "peer_ratings": p.peer_ratings,
                }
                for aid, p in self.profiles.items()
            },
            "team_history": self.team_history[-200:],
        }
        with open(self.dir / "profiles.json", "w", encoding="utf-8") as f:
            json.dump(full_data, f, ensure_ascii=False, indent=2)
