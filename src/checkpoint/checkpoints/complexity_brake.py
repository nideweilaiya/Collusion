"""深度检查点: 可行性/复杂度门控

职责: 从成本、技术栈成熟度、团队能力、交付时间四个维度检验方案可行性
requires: ["semantic_gaps"]
provides: ["complexity_assessment"]
LLM 调用: 1 次

激活条件: risk_score > 0.6 或快照中 uncertainty_flags 包含"复杂度"/"可行性"关键词
"""

from typing import Optional

from src.checkpoint.base import BaseCheckpoint, CheckpointResult, CheckpointCategory
from src.models import CompressedSnapshot


class ComplexityBrakeCheckpoint(BaseCheckpoint):
    """可行性收束 — 成本/技术栈/团队/交付时间四维现实检验

    不依赖多角色，可独立运行。
    """

    checkpoint_id = "complexity_brake"
    category = CheckpointCategory.DEEP
    description = "从成本、技术栈成熟度、团队能力、交付时间四维检验可行性"
    requires = ["semantic_gaps"]
    provides = ["complexity_assessment"]

    def _analyze(self, snapshot: CompressedSnapshot,
                 artifacts: dict) -> CheckpointResult:
        frag = snapshot.to_prompt_fragment()

        prompt = (
            f"角色: 工程可行性评估专家\n"
            f"行动: 从成本/技术栈/团队能力/交付时间四个维度检验任务可行性\n\n"
            f"情境快照:\n{frag}\n\n"
            f"聚焦检查:\n"
            f"1. 是否有过度设计——可以用更简单的方案达到同样目标\n"
            f"2. 技术栈选择是否与团队能力和已有约束匹配\n"
            f"3. 隐性成本——迁移、学习、维护成本是否被低估\n"
            f"4. 是否有至少一处可以简化的设计\n\n"
            f"要求: 每个发现必须附带具体的简化建议\n"
        )

        data = self._llm_check(prompt, temperature=0.1, max_tokens=2048)

        return CheckpointResult(
            checkpoint_id=self.checkpoint_id,
            category=self.category.value,
            severity=data.get("severity", "advisory"),
            summary=data.get("summary", "可行性评估完成"),
            findings=data.get("findings", []),
            risk_score=data.get("risk_score", snapshot.risk_score),
            confidence=data.get("confidence", 0.8),
            uncertainty_flags=data.get("uncertainty_flags", []),
            activation_gate=data.get("activation_gate", False),
            llm_calls=1,
            tokens_used=data.get("_tokens", 2000),
        )
