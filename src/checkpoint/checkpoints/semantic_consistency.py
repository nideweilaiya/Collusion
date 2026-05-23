"""核心检查点: 语义一致性扫描

职责: 检查任务需求自身是否存在矛盾、歧义或逻辑漏洞
requires: []
provides: ["semantic_gaps"]
LLM 调用: 1 次
"""

from typing import Optional

from src.checkpoint.base import BaseCheckpoint, CheckpointResult, CheckpointCategory
from src.models import CompressedSnapshot


class SemanticConsistencyCheckpoint(BaseCheckpoint):
    """检查任务描述与约束之间的语义一致性

    不需外部 artifacts — 只消费 CompressedSnapshot
    """

    checkpoint_id = "semantic_consistency"
    category = CheckpointCategory.CORE
    description = "检查任务需求与约束之间是否存在语义矛盾、歧义或逻辑漏洞"
    requires = []
    provides = ["semantic_gaps"]

    def _analyze(self, snapshot: CompressedSnapshot,
                 artifacts: dict) -> CheckpointResult:
        frag = snapshot.to_prompt_fragment()

        if len(frag.strip()) < 20:
            return CheckpointResult(
                checkpoint_id=self.checkpoint_id,
                category=self.category.value,
                severity="pass",
                summary="快照内容过短，跳过语义检查",
                confidence=0.5,
                uncertainty_flags=["快照信息不足"],
            )

        prompt = (
            f"角色: 语义一致性检查专家\n"
            f"行动: 检查任务需求与约束是否存在语义矛盾、歧义或逻辑漏洞\n\n"
            f"情境快照:\n{frag}\n\n"
            f"聚焦检查:\n"
            f"1. 需求自相矛盾 (如'无状态'+'保持会话')\n"
            f"2. 关键术语是否定义清晰 (同一词汇不同含义)\n"
            f"3. 非功能性需求是否有可度量指标\n"
            f"4. 约束之间是否存在冲突 (如'预算<500'+'需要GPU集群')\n\n"
            f"注意: 废案原因缺失时标注'原因未知'于uncertainty_flags,不编造\n"
        )

        data = self._llm_check(prompt, temperature=0.1, max_tokens=2048)

        result = CheckpointResult(
            checkpoint_id=self.checkpoint_id,
            category=self.category.value,
            severity=data.get("severity", "pass"),
            summary=data.get("summary", "语义一致性检查完成"),
            findings=data.get("findings", []),
            risk_score=data.get("risk_score", 0.0),
            confidence=data.get("confidence", 0.8),
            uncertainty_flags=data.get("uncertainty_flags", []),
            activation_gate=data.get("activation_gate", False),
            llm_calls=1,
            tokens_used=data.get("_tokens", 1500),
        )

        if not result.findings:
            result.summary = "未发现语义矛盾"

        return result
