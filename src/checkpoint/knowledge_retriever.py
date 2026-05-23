"""KnowledgeRetriever — 从资产库/废案库检索原始上下文

与压缩器正交分离：
  - KnowledgeRetriever: 负责 IO + 检索 → 输出 RetrievedContext
  - SituationCompressor: 纯函数，压缩 RetrievedContext → CompressedSnapshot
"""

import time
from pathlib import Path
from typing import Dict, List, Optional

from src.models import RetrievedContext


class KnowledgeRetriever:
    """检索器 — 封装 search_assets() + check_discarded_warnings()

    数据流:
      task → search_assets() + check_discarded_warnings()
           → query_causal_memory()
           → RetrievedContext (结构化原始数据)
    """

    def __init__(self, orchestrator=None):
        self._orch = orchestrator

    def retrieve(self, task: str, task_id: str = "",
                 top_k: int = 5, max_age_months: int = 6) -> RetrievedContext:
        """从知识库检索与任务相关的全部原始上下文。

        Args:
            task: 任务描述
            task_id: 任务ID（可选）
            top_k: 资产检索数量
            max_age_months: 废案最大年龄（月），超过此时间的废案过滤

        Returns:
            RetrievedContext — 未压缩的原始检索结果
        """
        ctx = RetrievedContext(task_id=task_id or f"ret_{int(time.time())}")

        if self._orch is None:
            return ctx

        # 1. 资产搜索
        try:
            precheck = self._orch.pre_check_knowledge(task)
            if precheck:
                ctx.relevant_assets = self._filter_recent(
                    precheck.get("relevant_assets", []),
                    max_age_months,
                )
                ctx.discard_warnings = self._filter_recent(
                    precheck.get("discarded_warnings", []),
                    max_age_months,
                )
        except Exception:
            pass

        # 2. 因果记忆查询
        try:
            if hasattr(self._orch, 'query_causal_memory'):
                ctx.causal_memories = self._orch.query_causal_memory(task, top_k=3)
        except Exception:
            pass

        # 2b. 因果风险预警 (失败的因果路径)
        try:
            if hasattr(self._orch, 'causal_risk_warning'):
                risks = self._orch.causal_risk_warning(task)
                if risks:
                    ctx.causal_memories.extend(risks)
        except Exception:
            pass

        # 3. Agent Graph 统计
        try:
            if (self._orch._enable_agent_graph and
                    self._orch.agent_graph is not None):
                ctx.agent_graph_stats = {
                    "total_tasks": len(
                        self._orch.agent_graph.graph.get("task_records", [])),
                }
        except Exception:
            pass

        return ctx

    @staticmethod
    def _filter_recent(entries: List[Dict], max_age_months: int) -> List[Dict]:
        """过滤超过 max_age_months 的条目"""
        if max_age_months <= 0:
            return entries
        import datetime
        cutoff = datetime.datetime.now() - datetime.timedelta(
            days=max_age_months * 30)
        filtered = []
        for e in entries:
            created = e.get("created_at", "")
            if created:
                try:
                    dt = datetime.datetime.fromisoformat(created)
                    if dt < cutoff:
                        reason = e.get("discard_reasons", [])
                        reason.append(f"[时效过期: 创建于{created}，超过{max_age_months}个月]")
                        e["discard_reasons"] = reason
                        continue
                except (ValueError, TypeError):
                    pass
            filtered.append(e)
        return filtered
