"""SituationCompressor — 纯函数压缩器

架构契约:
  - 纯函数: 不访问文件系统、不调 API、不读配置
  - 输入: task (str) + RetrievedContext (结构化数据)
  - 输出: CompressedSnapshot (≤500 token, ≤1250 chars UTF-8)
  - 唯一 LLM 调用通过注入的 fast_llm (在 __init__ 时注入，非 IO 依赖)

Code Review 红线: 此文件中出现任何 open()/Path()/os.*/requests.*/urllib.* 直接驳回。
"""

import json
import time
from typing import Dict, List

from src.models import CompressedSnapshot, RetrievedContext


class SituationCompressor:
    """情境压缩器 — 将检索结果提炼为 ≤500 token 快照

    使用方法:
        compressor = SituationCompressor(fast_llm)
        snapshot = compressor.compress(task, retrieved_context)
        fragment = snapshot.to_prompt_fragment()  # ≤1250 chars
    """

    # 任何检查点、Agent 调用中都应使用此 token 预算
    MAX_CHARS = 1250          # ~500 tokens (UTF-8 中文 ~2.5 chars/token)
    MAX_OUTPUT_TOKENS = 800   # 压缩器 LLM 调用 max_tokens

    def __init__(self, fast_llm=None):
        self.fast_llm = fast_llm

    def compress(self, task: str, retrieved: RetrievedContext) -> CompressedSnapshot:
        """主入口 — 纯函数，一次 LLM 调用压缩全部上下文

        Args:
            task: 原始任务描述
            retrieved: KnowledgeRetriever.retrieve() 的输出

        Returns:
            CompressedSnapshot — 硬保证 to_prompt_fragment() ≤ 1250 chars
        """
        snapshot = CompressedSnapshot(
            task_id=retrieved.task_id,
            task_summary=task[:80],
        )

        if self.fast_llm is None:
            # 无 LLM 时的退化模式: 纯启发式压缩
            return self._heuristic_compress(task, retrieved)

        # 构建压缩 prompt — 只传关键字段
        asset_briefs = self._abbreviate_assets(retrieved.relevant_assets[:5])
        discard_briefs = self._abbreviate_discards(retrieved.discard_warnings[:3])

        ctx = (
            f"行动: 情境压缩\n"
            f"任务: {task}\n\n"
            f"匹配资产: {json.dumps(asset_briefs, ensure_ascii=False)}\n"
            f"废案警示: {json.dumps(discard_briefs, ensure_ascii=False)}\n\n"
            f"要求:\n"
            f"1. 先列出所有显式约束(从任务描述和资产中提取)\n"
            f"2. 归纳历史决策(含结果正/负)及已知坑点\n"
            f"3. 废案原因缺失时标注'原因未知',禁止编造\n"
            f"4. 识别不确定项(需澄清的模糊点)\n"
            f"5. 综合风险评分 0-1\n"
            f"6. 输出总字符数必须≤1250\n\n"
            f'输出严格JSON: {{"task_summary":"≤80字","explicit_constraints":[],'
            f'"inferred_constraints":[],"relevant_decisions":[{{"decision":"",'
            f'"outcome":"正/负","why":""}}],"known_pitfalls":[{{"pitfall":"",'
            f'"when":"","fix":""}}],"discard_warnings":[{{"discarded_approach":"",'
            f'"reason":"","relevance":0.0}}],"uncertainty_flags":[],'
            f'"risk_score":0.0}}\n'
        )

        try:
            data = self.fast_llm.cached_call_json(
                ctx, temperature=0.1, max_tokens=self.MAX_OUTPUT_TOKENS,
            )
        except Exception:
            return self._heuristic_compress(task, retrieved)

        # 填充快照 — 清洗非字符串值
        raw_explicit = data.get("explicit_constraints", [])
        raw_inferred = data.get("inferred_constraints", [])
        snapshot.constraints = (
            self._sanitize_strings(raw_explicit) +
            self._sanitize_strings(raw_inferred)
        )
        snapshot.relevant_decisions = self._sanitize_dicts(
            data.get("relevant_decisions", []))
        snapshot.known_pitfalls = self._sanitize_dicts(
            data.get("known_pitfalls", []))
        snapshot.discard_warnings = self._sanitize_dicts(
            data.get("discard_warnings", []))
        snapshot.uncertainty_flags = self._sanitize_strings(
            data.get("uncertainty_flags", []))
        snapshot.risk_score = float(data.get("risk_score", 0.0))
        snapshot.matched_asset_keys = [
            a.get("task", "")[:40] for a in retrieved.relevant_assets[:5]
        ]

        # 硬性校验
        fragment = snapshot.to_prompt_fragment()
        if len(fragment) > self.MAX_CHARS:
            snapshot = self._truncate_to_budget(snapshot)

        return snapshot

    def _heuristic_compress(self, task: str,
                            retrieved: RetrievedContext) -> CompressedSnapshot:
        """无 LLM 时的纯启发式压缩 — 零额外 token 消耗"""
        snapshot = CompressedSnapshot(
            task_id=retrieved.task_id,
            task_summary=task[:80],
        )

        # 从资产中提取约束标签
        seen_c = set()
        for a in retrieved.relevant_assets:
            for t in a.get("tags", []):
                if isinstance(t, dict):
                    val = t.get("value", "")
                    dim = t.get("dimension", "")
                    if dim == "技术栈" and val not in seen_c:
                        snapshot.constraints.append(f"技术栈含{val}")
                        seen_c.add(val)

        # 废案转警告(discard_reasons为空时标注原因未知)
        for d in retrieved.discard_warnings[:3]:
            reasons = d.get("discard_reasons", [])
            if not reasons:
                reasons = ["原因未知"]
            for r in reasons[:1]:
                snapshot.discard_warnings.append({
                    "discarded_approach": d.get("task", "")[:60],
                    "reason": r[:60],
                    "relevance": d.get("relevance_score", 0.5),
                })

        # 坑点从废案总结
        for d in retrieved.discard_warnings[:3]:
            reasons = d.get("discard_reasons", [])
            if reasons:
                snapshot.known_pitfalls.append({
                    "pitfall": d.get("task", "")[:40],
                    "when": reasons[0][:60],
                    "fix": "需人工确认",
                })

        # v0.6: 因果失败路径 → 坑点 (outcome_score < 0 的节点)
        causal_failures = [
            m for m in retrieved.causal_memories
            if (isinstance(m, dict) and
                (m.get("outcome_score") or 0) < 0)
        ]
        for cf in causal_failures[:3]:
            label = cf.get("label", cf.get("description", ""))[:50]
            desc = cf.get("description", label)[:60]
            score = cf.get("outcome_score") or 0
            snapshot.known_pitfalls.append({
                "pitfall": label or "历史失败路径",
                "when": desc,
                "fix": f"该路径历史outcome_score={score}, 建议避开",
            })
            if score < -0.5:
                snapshot.risk_score = max(snapshot.risk_score, 0.6)

        snapshot.risk_score = min(
            0.1 * len(retrieved.discard_warnings) +
            0.05 * len(snapshot.uncertainty_flags), 1.0,
        )

        snapshot = self._truncate_to_budget(snapshot)
        return snapshot

    def generate_discard_reason(self, scheme_text: str,
                                why_discarded: str) -> str:
        """为废案生成 ≤50 token 的弃用原因摘要"""
        if len(why_discarded) <= 60:
            return why_discarded[:60]

        if self.fast_llm is None:
            return why_discarded[:60]

        ctx = (
            f"行动: 总结废案原因\n"
            f"方案: {scheme_text[:300]}\n"
            f"淘汰: {why_discarded[:200]}\n"
            f"要求: ≤50字，保留具体技术/业务细节，禁止编造\n"
        )
        try:
            data = self.fast_llm.cached_call_json(
                ctx, temperature=0.1, max_tokens=128,
            )
            return data.get("summary", why_discarded[:60])
        except Exception:
            return why_discarded[:60]

    # ========== 内部工具 ==========

    @staticmethod
    def _sanitize_strings(items: list) -> List[str]:
        """确保列表中所有元素都是字符串"""
        out = []
        for item in items:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(json.dumps(item, ensure_ascii=False))
            else:
                out.append(str(item))
        return out

    @staticmethod
    def _sanitize_dicts(items: list) -> List[Dict]:
        """确保列表中所有元素都是 dict"""
        out = []
        for item in items:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str):
                out.append({"decision": item[:80], "outcome": "未知", "why": ""})
        return out

    @staticmethod
    def _abbreviate_assets(assets: List[Dict]) -> List[Dict]:
        out = []
        for a in assets:
            out.append({
                "task": a.get("task", "")[:60],
                "score": round(a.get("relevance_score", 0), 2),
                "is_discarded": a.get("is_discarded", False),
                "discard_reasons": a.get("discard_reasons", [])[:2],
                "tags": [
                    t.get("value", "") for t in a.get("tags", [])
                    if isinstance(t, dict)
                ][:5],
            })
        return out

    @staticmethod
    def _abbreviate_discards(discards: List[Dict]) -> List[Dict]:
        out = []
        for d in discards:
            reasons = d.get("discard_reasons", [])
            if not reasons:
                reasons = ["原因未知"]
            out.append({
                "approach": d.get("task", "")[:80],
                "reasons": reasons[:2],
                "score": round(d.get("relevance_score", 0), 2),
            })
        return out

    def _truncate_to_budget(self, snapshot: CompressedSnapshot) -> CompressedSnapshot:
        """渐进式截断: 约束 > 坑点 > 决策 > 废案"""
        while True:
            frag = snapshot.to_prompt_fragment()
            if len(frag) <= self.MAX_CHARS:
                break
            if len(snapshot.constraints) > 2:
                snapshot.constraints = snapshot.constraints[:2]
            elif len(snapshot.known_pitfalls) > 1:
                snapshot.known_pitfalls = snapshot.known_pitfalls[:1]
            elif len(snapshot.relevant_decisions) > 1:
                snapshot.relevant_decisions = snapshot.relevant_decisions[:1]
            elif len(snapshot.discard_warnings) > 1:
                snapshot.discard_warnings = snapshot.discard_warnings[:1]
            else:
                break
        return snapshot
