"""核心检查点: 废案模式快速匹配

职责: 对比当前任务与历史上已弃用方案的模式相似度
requires: []
provides: ["pattern_warnings"]
LLM 调用: 0-1 次 (规则优先: 文本相似度过滤 → 仅摘要模糊时才调 LLM)

优先路径 (0 次 LLM):
  1. 快照中已有 discard_warnings 直接使用
  2. 废案原因明确且有具体技术/业务细节 → 直接输出
  3. 无废案命中 → 直接 PASS

LLM 路径 (1 次 LLM):
  只有当废案原因缺失或过于模糊 (长度<15 chars 或含"原因未知") 时才调用 LLM 做原因比对
"""

from typing import Optional, List, Dict

from src.checkpoint.base import BaseCheckpoint, CheckpointResult, CheckpointCategory
from src.models import CompressedSnapshot


class PatternMatchCheckpoint(BaseCheckpoint):
    """废案模式匹配 — 规则优先，零 LLM 调用在多数场景"""

    checkpoint_id = "pattern_match"
    category = CheckpointCategory.CORE
    description = "对比当前任务与历史废案的模式相似度，输出适用的弃用原因"
    requires = []
    provides = ["pattern_warnings"]

    # 规则路径: 弃用原因 >= 此长度且不含"原因未知"视为明确原因
    MIN_REASON_LENGTH = 15

    def _analyze(self, snapshot: CompressedSnapshot,
                 artifacts: dict) -> CheckpointResult:
        discard_warnings = snapshot.discard_warnings

        # 无废案 → 直接 PASS
        if not discard_warnings:
            return CheckpointResult(
                checkpoint_id=self.checkpoint_id,
                category=self.category.value,
                severity="pass",
                summary="无匹配的历史废案",
                confidence=1.0,
                llm_calls=0,
                tokens_used=0,
            )

        # 分类: 原因明确 vs 原因模糊
        clear_warnings = []
        vague_warnings = []
        for w in discard_warnings:
            reason = w.get("reason", "")
            if self._is_reason_clear(reason):
                clear_warnings.append(w)
            else:
                vague_warnings.append(w)

        # 构建 findings
        findings = self._build_findings(clear_warnings)
        uncertainty = []

        # 原因模糊的废案: 尝试验证/补充原因
        if vague_warnings:
            vague_findings, vague_uncertainty = self._handle_vague_warnings(
                vague_warnings, snapshot
            )
            findings.extend(vague_findings)
            uncertainty.extend(vague_uncertainty)

        # 时效性标注
        for w in discard_warnings:
            if w.get("relevance", 0) > 0.5:
                findings.append({
                    "type": "pattern",
                    "target": w.get("discarded_approach", "未知废案")[:60],
                    "detail": (
                        f"历史废案(关联度{w.get('relevance',0):.0%})，"
                        f"需人工确认时效性"
                    ),
                    "suggestion": "确认弃用原因在当前上下文中是否仍然成立",
                })

        # v0.6: 因果失败模式增强 — 已知坑点中来自因果图的失败路径提升严重度
        causal_pitfalls = [
            p for p in snapshot.known_pitfalls
            if "outcome_score" in p.get("fix", "") or "历史失败" in p.get("pitfall", "")
        ]
        if causal_pitfalls:
            for cp in causal_pitfalls[:3]:
                findings.append({
                    "type": "pattern",
                    "target": cp.get("pitfall", "因果失败路径")[:60],
                    "detail": f"因果记忆: {cp.get('when', '')[:80]}",
                    "suggestion": cp.get("fix", "建议避开此路径")[:100],
                })

        severity = "advisory"
        risk_score = 0.0
        if findings:
            severity = "warning"
            risk_score = min(0.12 * len(findings), 0.85)
        if causal_pitfalls:
            severity = "warning"
            risk_score = max(risk_score, 0.4)

        return CheckpointResult(
            checkpoint_id=self.checkpoint_id,
            category=self.category.value,
            severity=severity,
            summary=(
                f"命中{len(clear_warnings)}个明确废案"
                + (f", {len(vague_warnings)}个原因模糊" if vague_warnings else "")
            ),
            findings=findings,
            risk_score=risk_score,
            confidence=0.85 if not vague_warnings else 0.6,
            uncertainty_flags=uncertainty,
            activation_gate=bool(vague_warnings or len(findings) > 2),
            llm_calls=1 if vague_warnings else 0,
            tokens_used=500 if vague_warnings else 0,
        )

    @classmethod
    def _is_reason_clear(cls, reason: str) -> bool:
        """判断弃用原因是否足够明确可用"""
        if not reason:
            return False
        if "原因未知" in reason:
            return False
        if len(reason) < cls.MIN_REASON_LENGTH:
            return False
        return True

    @staticmethod
    def _build_findings(warnings: List[Dict]) -> List[Dict]:
        """构建规则路径的 findings"""
        out = []
        for w in warnings:
            reason = w.get("reason", "")
            approach = w.get("discarded_approach", "未知方案")[:60]
            out.append({
                "type": "pattern",
                "target": approach,
                "detail": f"弃用原因: {reason[:120]}",
                "suggestion": (
                    "验证此弃用原因在当前技术栈和约束下是否仍然成立"
                ),
            })
        return out

    def _handle_vague_warnings(self, warnings: List[Dict],
                               snapshot: CompressedSnapshot) -> tuple:
        """处理原因模糊的废案 — 规则推断或 LLM 比对"""
        if self.fast_llm is None:
            # 无 LLM: 标注原因未知
            findings = []
            for w in warnings:
                findings.append({
                    "type": "pattern",
                    "target": w.get("discarded_approach", "未知方案")[:60],
                    "detail": "弃用原因未知或过于模糊，无法自动评估",
                    "suggestion": "建议人工审查此历史废案是否与当前任务相关",
                })
            return findings, ["部分废案原因未知，已标注需人工确认"]

        # LLM 路径: 让 LLM 对比快照与废案, 推断可能的风险
        frag = snapshot.to_prompt_fragment()
        vague_text = "\n".join(
            f"- {w.get('discarded_approach', '?')[:80]}: "
            f"{w.get('reason', '原因未知')[:80]}"
            for w in warnings[:3]
        )

        prompt = (
            f"角色: 历史模式匹配专家\n"
            f"行动: 评估模糊的废案原因在当前上下文中是否可能成立\n\n"
            f"当前任务:\n{frag[:600]}\n\n"
            f"原因模糊的废案:\n{vague_text}\n\n"
            f"要求: 仅基于快照中的约束和技术栈信息，判断这些废案的弃用原因\n"
            f"在当前上下文中是否可能仍然相关。不确定时标注 uncertainty_flags。\n"
            f"不要编造原因。\n"
        )

        try:
            data = self._llm_check(prompt, temperature=0.1, max_tokens=1024)
        except Exception:
            data = {}

        findings = []
        for finding in data.get("findings", []):
            findings.append({
                "type": "pattern",
                "target": finding.get("target", "")[:60],
                "detail": finding.get("detail", "")[:120],
                "suggestion": finding.get("suggestion", "建议人工确认"),
            })
        uncertainty = data.get("uncertainty_flags", [])
        return findings, uncertainty
