"""Collusion v1.0.0 — Agent-as-a-Graph 知识图谱路由

参考: PwC Agent-as-a-Graph (ICAART 2026), LiveMCPBenchmark Recall@5 +14.9%

核心思想:
  将 Agent 角色表示为知识图谱节点，边权重 = 历史协作成功率
  新任务到来时，匹配相似历史任务 → 选择最优 Agent 组合
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional


class AgentGraph:
    """Agent 知识图谱路由引擎"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir) / "agent_graph"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.graph = self._load("graph.json", default={
            "nodes": {},   # agent_role → {name, success_count, total_count, tags}
            "edges": {},   # "role1→role2" → {weight, success_count, total_count}
            "task_records": [],  # [{task_id, roles_used, success, tags}]
        })

    def record_task(self, task_id: str, task_desc: str, roles: List[str],
                    success: bool = True, tags: List[str] = None):
        """记录一次任务编排的 Agent 组合结果"""
        tags = tags or []

        # 更新节点
        for role in roles:
            if role not in self.graph["nodes"]:
                self.graph["nodes"][role] = {
                    "name": role, "success_count": 0, "total_count": 0, "tags": [],
                }
            self.graph["nodes"][role]["total_count"] += 1
            if success:
                self.graph["nodes"][role]["success_count"] += 1
            # 合并标签
            for t in tags:
                if t not in self.graph["nodes"][role]["tags"]:
                    self.graph["nodes"][role]["tags"].append(t)

        # 更新边（Agent 协作对）
        for i in range(len(roles)):
            for j in range(i + 1, len(roles)):
                edge_key = f"{roles[i]}→{roles[j]}"
                if edge_key not in self.graph["edges"]:
                    self.graph["edges"][edge_key] = {
                        "source": roles[i], "target": roles[j],
                        "weight": 0.5, "success_count": 0, "total_count": 0,
                    }
                self.graph["edges"][edge_key]["total_count"] += 1
                if success:
                    self.graph["edges"][edge_key]["success_count"] += 1
                # 更新权重 (成功率)
                e = self.graph["edges"][edge_key]
                e["weight"] = round(
                    e["success_count"] / max(e["total_count"], 1), 3
                )

        # 记录任务
        self.graph["task_records"].append({
            "task_id": task_id,
            "task_desc": task_desc[:100],
            "roles_used": roles,
            "success": success,
            "tags": tags,
            "time": time.time(),
        })
        if len(self.graph["task_records"]) > 500:
            self.graph["task_records"] = self.graph["task_records"][-500:]

        self._save("graph.json", self.graph)

    def select_agents(self, task_tags: List[str],
                      available_roles: List[str],
                      top_k: int = 3) -> List[str]:
        """根据任务标签和 Agent 图选择最优 Agent 组合

        Returns:
            排序后的角色列表 [最优, 次优, ...]
        """
        if not self.graph["nodes"]:
            return available_roles[:top_k]

        # 计算每个 Agent 对当前任务的适用度
        scores = {}
        for role, node in self.graph["nodes"].items():
            if role not in available_roles:
                continue

            # 1. 历史成功率 (0-1)
            success_rate = node["success_count"] / max(node["total_count"], 1)

            # 2. 标签匹配度 (Sanity.io 风格)
            shared = sum(1 for t in task_tags if t in node.get("tags", []))
            total = max(len(task_tags) + len(node.get("tags", [])), 1)
            tag_score = (shared * 2) / total if shared > 0 else 0

            # 3. 经验量 (归一化)
            exp_score = min(node["total_count"] / 10, 1.0)

            # 综合 (经验少时偏标签, 经验多时偏成功率)
            if node["total_count"] < 3:
                score = tag_score * 0.7 + exp_score * 0.3
            else:
                score = success_rate * 0.5 + tag_score * 0.3 + exp_score * 0.2

            scores[role] = round(score, 3)

        # 按分数排序
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [r[0] for r in ranked[:top_k]]

    def get_best_pair(self, role: str) -> Optional[str]:
        """获取与指定 Agent 协作效果最好的搭档"""
        candidates = []
        for edge_key, edge in self.graph["edges"].items():
            if edge["source"] == role:
                candidates.append((edge["target"], edge["weight"]))
            elif edge["target"] == role:
                candidates.append((edge["source"], edge["weight"]))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    # ==================== v0.6: 检查点角色选择 ====================

    # 检查点→推荐角色映射
    CHECKPOINT_ROLE_MAP = {
        "architecture_review": ["技术架构对象", "性能架构师"],
        "security_audit": ["安全与合规对象", "安全专家"],
        "business_alignment": ["业务价值对象", "UX/产品专家"],
        "complexity_brake": ["工程实现对象"],
        "semantic_consistency": ["业务价值对象"],
        "interface_conflict": ["技术架构对象"],
        "pattern_match": ["业务价值对象", "技术架构对象"],
    }

    # 角色→关注标签映射 (用于标签匹配)
    ROLE_TAG_MAP = {
        "技术架构对象": ["架构", "扩展", "性能", "模块", "API", "微服务"],
        "安全与合规对象": ["安全", "认证", "合规", "加密", "PCI", "GDPR"],
        "业务价值对象": ["业务", "用户", "需求", "体验", "MVP"],
        "工程实现对象": ["复杂度", "成本", "部署", "DevOps", "Docker"],
        "性能架构师": ["性能", "高并发", "缓存", "数据库", "优化"],
        "安全专家": ["安全", "渗透", "漏洞", "加密", "审计"],
        "UX/产品专家": ["用户体验", "交互", "界面", "可用性"],
    }

    def select_agents_for_checkpoint(self, checkpoint_id: str,
                                     task_tags: List[str] = None,
                                     top_k: int = 2) -> List[str]:
        """v0.6: 为指定检查点选择最优角色

        先查 CHECKPOINT_ROLE_MAP 获得候选，再用历史成功率+标签匹配排序。
        图中无历史数据时返回默认推荐角色。

        Args:
            checkpoint_id: 检查点ID
            task_tags: 任务标签
            top_k: 返回前k个角色

        Returns:
            排序后的角色名列表
        """
        default_roles = self.CHECKPOINT_ROLE_MAP.get(checkpoint_id, [])

        if not self.graph["nodes"] or not task_tags:
            return default_roles[:top_k]

        # 为每个候选角色计算匹配分数
        scores = {}
        for role in default_roles:
            node = self.graph["nodes"].get(role, {})
            if not node:
                scores[role] = 0.1
                continue

            # 成功率
            total = max(node.get("total_count", 1), 1)
            success_rate = node.get("success_count", 0) / total

            # 角色标签与任务标签的匹配
            role_tags = self.ROLE_TAG_MAP.get(role, [])
            shared = sum(1 for t in (task_tags or []) if t in role_tags)
            tag_score = (shared * 2) / max(len(task_tags or []) + len(role_tags), 1)

            # 经验
            exp_score = min(total / 10, 1.0)

            if total < 3:
                scores[role] = tag_score * 0.7 + exp_score * 0.3
            else:
                scores[role] = success_rate * 0.5 + tag_score * 0.3 + exp_score * 0.2

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        result = [r[0] for r in ranked[:top_k]]
        return result if result else default_roles[:top_k]

    def get_stats(self) -> dict:
        """获取图统计"""
        return {
            "n_agents": len(self.graph["nodes"]),
            "n_edges": len(self.graph["edges"]),
            "n_tasks": len(self.graph["task_records"]),
            "agents": {
                k: {"success_rate": v["success_count"]/max(v["total_count"],1),
                    "total": v["total_count"]}
                for k, v in self.graph["nodes"].items()
            },
        }

    def _load(self, name: str, default=None):
        path = self.data_dir / name
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default or {}

    def _save(self, name: str, data):
        path = self.data_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
