"""深度检查点: 多视角架构审查

职责: 从技术架构、扩展性、模块边界、数据流等视角审查方案
requires: ["semantic_gaps"]
provides: ["architecture_review"]
LLM 调用: 1-3 次 (取决于是否启用多角色)

激活条件:
  - risk_score > 0.4 或
  - uncertainty_flags 包含"架构"/"选型"/"模块"/"扩展"关键词

Phase 6 升级: 启用动态角色选择后，可并行 2-3 个 Agent 从不同视角审查
"""

from typing import Optional

from src.checkpoint.base import BaseCheckpoint, CheckpointResult, CheckpointCategory
from src.models import CompressedSnapshot


class ArchitectureReviewCheckpoint(BaseCheckpoint):
    """多视角架构审查 — 当前为单角色版本，Phase 6 升级为多角色并行"""

    checkpoint_id = "architecture_review"
    category = CheckpointCategory.DEEP
    description = "多视角审查技术架构、扩展性、模块边界、数据流"
    requires = ["semantic_gaps"]
    provides = ["architecture_review"]

    def _analyze(self, snapshot: CompressedSnapshot,
                 artifacts: dict) -> CheckpointResult:
        frag = snapshot.to_prompt_fragment()

        prompt = (
            f"角色: 技术架构审查专家\n"
            f"行动: 从架构、扩展性、模块边界、数据流多视角审查方案\n\n"
            f"情境快照:\n{frag}\n\n"
            f"聚焦检查:\n"
            f"1. 架构模式选择是否与任务约束匹配\n"
            f"2. 模块边界是否清晰——高内聚低耦合\n"
            f"3. 数据流设计是否有瓶颈或单点故障\n"
            f"4. 扩展性——水平/垂直扩展路径是否被阻塞\n"
            f"5. 技术选型是否存在锁定风险\n\n"
            f"要求: 每个架构风险给出具体的替代建议\n"
        )

        data = self._llm_check(prompt, temperature=0.1, max_tokens=2048)

        return CheckpointResult(
            checkpoint_id=self.checkpoint_id,
            category=self.category.value,
            severity=data.get("severity", "advisory"),
            summary=data.get("summary", "架构审查完成"),
            findings=data.get("findings", []),
            risk_score=data.get("risk_score", snapshot.risk_score),
            confidence=data.get("confidence", 0.8),
            uncertainty_flags=data.get("uncertainty_flags", []),
            activation_gate=data.get("activation_gate", False),
            llm_calls=1,
            tokens_used=data.get("_tokens", 2500),
        )
