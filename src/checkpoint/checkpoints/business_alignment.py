"""深度检查点: 业务价值对齐

职责: 检查方案是否偏离核心需求、是否存在为技术而技术的过度设计
requires: []
provides: ["business_alignment"]
LLM 调用: 1 次

激活条件:
  - risk_score > 0.5 或
  - uncertainty_flags 包含"业务"/"需求"/"用户"关键词 或
  - constraints 中包含"用户"/"客户"/"业务"关键词
"""

from typing import Optional

from src.checkpoint.base import BaseCheckpoint, CheckpointResult, CheckpointCategory
from src.models import CompressedSnapshot


class BusinessAlignmentCheckpoint(BaseCheckpoint):
    """业务锚点扫描 — 检查方案是否偏离核心需求"""

    checkpoint_id = "business_alignment"
    category = CheckpointCategory.DEEP
    description = "检查方案是否偏离核心需求、是否存在过度设计"
    requires = []
    provides = ["business_alignment"]

    def _analyze(self, snapshot: CompressedSnapshot,
                 artifacts: dict) -> CheckpointResult:
        frag = snapshot.to_prompt_fragment()

        prompt = (
            f"角色: 业务价值评估专家\n"
            f"行动: 检查方案是否偏离核心用户需求，是否存在为技术而技术的过度设计\n\n"
            f"情境快照:\n{frag}\n\n"
            f"聚焦检查:\n"
            f"1. 方案是否解决了用户真正的问题，还是解决了一个更'有趣'的技术问题\n"
            f"2. 是否有可砍掉的非核心功能 (80/20原则)\n"
            f"3. 约束条件中是否有隐含的业务假设需要确认\n"
            f"4. 历史决策中是否有可复用的模式\n\n"
            f"要求: 标记过度设计的环节，给出简化建议\n"
        )

        data = self._llm_check(prompt, temperature=0.1, max_tokens=2048)

        return CheckpointResult(
            checkpoint_id=self.checkpoint_id,
            category=self.category.value,
            severity=data.get("severity", "advisory"),
            summary=data.get("summary", "业务对齐检查完成"),
            findings=data.get("findings", []),
            risk_score=data.get("risk_score", snapshot.risk_score),
            confidence=data.get("confidence", 0.8),
            uncertainty_flags=data.get("uncertainty_flags", []),
            activation_gate=data.get("activation_gate", False),
            llm_calls=1,
            tokens_used=data.get("_tokens", 2000),
        )
