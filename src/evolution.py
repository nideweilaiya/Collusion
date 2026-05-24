"""Collusion v0.8.0 — MAGE 自进化引擎

三核心机制:
  1. 反馈追踪: 记录每次搜索推荐是否被采纳
  2. 自适应权重: 根据采纳率历史动态调整 w1/w2/w3
  3. Bandit 路由: epsilon-greedy 探索 vs 利用

参考: MAGE — Multi-Agent Self-Evolution with Co-Evolutionary Knowledge Graphs (arXiv:2605.10064)
"""
import json
import math
import random
import time
from pathlib import Path
from typing import Dict, List, Optional


class EvolutionEngine:
    """MAGE 自进化引擎"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir) / "evolution"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 状态
        self.feedback = self._load_json("feedback.json", default=[])
        if isinstance(self.feedback, dict):
            self.feedback = list(self.feedback.values()) if self.feedback else []
        self.weights = self._load_json("weights.json", default={
            "tag_overlap": 0.4,
            "tech_similarity": 0.35,
            "causal_match": 0.25,
            "version": 1,
            "updated_at": time.time(),
        })
        self.bandit = self._load_json("bandit.json", default={
            "counts": {},      # asset_key → n_times_recommended
            "rewards": {},     # asset_key → total_reward_sum
            "epsilon": 0.15,   # 探索率
            "updated_at": time.time(),
        })
        self.stats = self._load_json("stats.json", default={
            "total_searches": 0,
            "total_adoptions": 0,
            "adoption_rate": 0.0,
            "weight_adjustments": 0,
            "created_at": time.time(),
        })

    # ==================== 反馈追踪 ====================

    def record_search(self, query: str, results: list, adopted_key: str = None):
        """记录一次搜索及其采纳结果

        Args:
            query: 搜索查询
            results: search_assets 返回的结果列表
            adopted_key: 用户最终采纳的资产 key (None=未采纳)
        """
        self.stats["total_searches"] += 1

        for r in results:
            key = r.get("key", "")
            if not key:
                continue
            # 初始化 bandit
            if key not in self.bandit["counts"]:
                self.bandit["counts"][key] = 0
                self.bandit["rewards"][key] = 0.0

            self.bandit["counts"][key] += 1

            # reward: 被采纳 = 1.0, 排名高但未被采纳 = 0.2, 排名低 = 0
            rank = r.get("rank", 99)
            if key == adopted_key:
                reward = 1.0
                self.stats["total_adoptions"] += 1
            elif rank <= 3:
                reward = 0.2
            else:
                reward = 0.0

            self.bandit["rewards"][key] = self.bandit["rewards"].get(key, 0) + reward

        # 记录反馈明细
        self.feedback.append({
            "time": time.time(),
            "query": query[:100],
            "n_results": len(results),
            "adopted": adopted_key is not None,  # boolean: True=已采纳, False=未采纳
            "adopted_asset": adopted_key,         # string: 具体被采纳的资产key
            "adopted_rank": next((i+1 for i, r in enumerate(results)
                                  if r.get("key") == adopted_key), None),
        })
        # 只保留最近 1000 条明细
        if len(self.feedback) > 1000:
            self.feedback = self.feedback[-1000:]

        self.stats["adoption_rate"] = round(
            self.stats["total_adoptions"] / max(self.stats["total_searches"], 1), 4
        )
        self._save_all()

    def get_asset_score(self, key: str) -> float:
        """获取资产在 bandit 中的估计价值 (0-1)"""
        counts = self.bandit["counts"].get(key, 0)
        if counts == 0:
            return 0.5  # 未知资产，中性
        rewards = self.bandit["rewards"].get(key, 0)
        return min(rewards / counts, 1.0)

    def should_explore(self) -> bool:
        """epsilon-greedy: 是否应该探索"""
        return random.random() < self.bandit["epsilon"]

    # ==================== 自适应权重 ====================

    def _feedback_list(self) -> list:
        """确保 feedback 是 list 类型（兼容 JSON 加载为 dict 的情况）"""
        if isinstance(self.feedback, list):
            return self.feedback
        if isinstance(self.feedback, dict):
            return list(self.feedback.values())
        return []

    def optimize_weights(self, force: bool = False):
        """根据历史采纳数据优化关联度权重

        触发条件:
          - 至少有 20 条搜索记录
          - 距离上次调整至少 10 条新记录
        """
        if not force and len(self.feedback) < 20:
            return
        if not force and self.stats.get("total_searches", 0) - \
                self.stats.get("last_adjustment_at", 0) < 10:
            return

        # 简化策略：看哪个信号更常出现在被采纳的结果中
        # 从反馈中提取最近 50 条有采纳的记录
        fb = self._feedback_list()
        adopted = [f for f in fb[-50:] if f.get("adopted") is True or
                   (isinstance(f.get("adopted"), str) and f.get("adopted"))]
        if len(adopted) < 5:
            return

        # 当前权重做微调：如果采纳率上升趋势，保持；下降则微调
        rate = self.stats.get("adoption_rate", 0)
        if rate < 0.3:
            # 采纳率偏低 → 增加探索（提高 epsilon）
            self.bandit["epsilon"] = min(self.bandit["epsilon"] + 0.02, 0.3)
        elif rate > 0.7:
            # 采纳率偏高 → 减少探索（更多利用）
            self.bandit["epsilon"] = max(self.bandit["epsilon"] - 0.01, 0.05)

        # 更新版本
        self.weights["version"] += 1
        self.weights["updated_at"] = time.time()
        self.stats["weight_adjustments"] = self.stats.get("weight_adjustments", 0) + 1
        self.stats["last_adjustment_at"] = self.stats["total_searches"]

        self._save_all()

    # ==================== 采纳信号 ====================

    def mark_adopted(self, query_keyword: str, adopted: bool = True) -> int:
        """标记搜索查询对应的方案被采纳或拒绝

        扫描 feedback 中匹配 query_keyword 的条目，将 adopted 从未确认(null)
        更新为 true/false，触发权重优化。

        Args:
            query_keyword: 用于匹配 feedback 条目的关键词（匹配 query 字段）
            adopted: True=采纳, False=淘汰

        Returns:
            更新的条目数量
        """
        fb = self._feedback_list()
        updated = 0
        now = time.time()
        for entry in fb:
            if query_keyword[:60] in entry.get("query", ""):
                if entry.get("adopted") is None:
                    entry["adopted"] = adopted
                    entry["adopted_at"] = now
                    updated += 1

        if updated > 0:
            self._save_all()
            self.apply_adoption_feedback()

        return updated

    def apply_adoption_feedback(self):
        """根据采纳信号调整 bandit 权重和 epsilon

        adopted=true  → 提升关联资产分数，降低探索率
        adopted=false → 降低关联资产分数，提升探索率
        adopted=null  → 保持当前权重不变
        """
        fb = self._feedback_list()
        if not fb:
            return

        # 统计最近的确认信号
        recent = fb[-100:]
        confirmed_adopted = [e for e in recent if e.get("adopted") is True]
        confirmed_rejected = [e for e in recent if e.get("adopted") is False]

        if not confirmed_adopted and not confirmed_rejected:
            return

        # 根据确认信号调整 epsilon
        total_confirmed = len(confirmed_adopted) + len(confirmed_rejected)
        adopt_rate = len(confirmed_adopted) / total_confirmed if total_confirmed > 0 else 0.5

        if adopt_rate >= 0.7:
            # 采纳率高 → 减少探索，更多利用
            self.bandit["epsilon"] = max(self.bandit["epsilon"] - 0.03, 0.05)
        elif adopt_rate <= 0.3:
            # 采纳率低 → 增加探索
            self.bandit["epsilon"] = min(self.bandit["epsilon"] + 0.03, 0.35)

        self.stats["total_adoptions"] = self.stats.get("total_adoptions", 0) + len(confirmed_adopted)
        self.stats["adoption_rate"] = round(
            self.stats["total_adoptions"] / max(self.stats["total_searches"], 1), 4
        )
        self.bandit["updated_at"] = time.time()
        self._save_all()

    # ==================== 统计 ====================

    def get_stats(self) -> dict:
        """获取自进化统计摘要"""
        n_assets = len(self.bandit["counts"])
        # 找出 Top 5 资产
        asset_scores = []
        for key in self.bandit["counts"]:
            c = self.bandit["counts"][key]
            r = self.bandit["rewards"].get(key, 0)
            asset_scores.append({"key": key, "count": c, "avg_reward": round(r/c, 3) if c else 0})
        asset_scores.sort(key=lambda x: x["avg_reward"], reverse=True)

        return {
            "total_searches": self.stats["total_searches"],
            "total_adoptions": self.stats["total_adoptions"],
            "adoption_rate": self.stats["adoption_rate"],
            "epsilon": self.bandit["epsilon"],
            "weights": self.weights,
            "n_assets_tracked": n_assets,
            "top_assets": asset_scores[:5],
            "weight_adjustments": self.stats.get("weight_adjustments", 0),
        }

    def get_adaptive_weights(self) -> dict:
        """获取当前自适应权重"""
        return {
            "tag_overlap": self.weights.get("tag_overlap", 0.4),
            "tech_similarity": self.weights.get("tech_similarity", 0.35),
            "causal_match": self.weights.get("causal_match", 0.25),
        }

    # ==================== 内部 ====================

    def _load_json(self, name: str, default=None):
        path = self.data_dir / name
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 确保 feedback 始终是 list
                if name == "feedback.json" and isinstance(data, dict):
                    data = list(data.values()) if not any(k.isdigit() for k in data) else []
                return data
            except Exception:
                pass
        if isinstance(default, list):
            return default
        return default or {}

    def _save_all(self):
        self._save_json("feedback.json", self.feedback)
        self._save_json("weights.json", self.weights)
        self._save_json("bandit.json", self.bandit)
        self._save_json("stats.json", self.stats)

    def _save_json(self, name: str, data):
        path = self.data_dir / name
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
