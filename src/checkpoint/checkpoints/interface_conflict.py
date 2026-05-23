"""核心检查点: 接口/契约冲突扫描

职责: 检查拟定的接口定义、数据契约是否存在冲突
requires: ["interface_definition"]
provides: ["contract_conflicts"]
LLM 调用: 1 次 (有 artifacts), 0 次 (无 artifacts 时降级)

设计决策 A: 轻量模式默认不传 artifacts → 优雅降级, 返回 PASS + uncertainty_flags
"""

from typing import Optional

from src.checkpoint.base import BaseCheckpoint, CheckpointResult, CheckpointCategory
from src.models import CompressedSnapshot


class InterfaceConflictCheckpoint(BaseCheckpoint):
    """扫描接口定义与数据契约之间的冲突

    轻量模式 (无 artifacts): 优雅降级, 返回 PASS + uncertainty_flags
    深度模式 (有 artifacts): 对用户提供的接口定义做 diff 检查
    """

    checkpoint_id = "interface_conflict"
    category = CheckpointCategory.CORE
    description = "检查组件间接口定义、数据契约是否存在冲突"
    requires = ["interface_definition"]
    provides = ["contract_conflicts"]

    def _pre_check(self, snapshot: CompressedSnapshot,
                   artifacts: dict) -> Optional[CheckpointResult]:
        """无设计草案时优雅降级 — 不消耗 LLM 调用"""
        has_draft = bool(
            artifacts.get("interface_definition") or
            artifacts.get("schemas") or
            artifacts.get("api_specs")
        )
        if not has_draft:
            return CheckpointResult(
                checkpoint_id=self.checkpoint_id,
                category=self.category.value,
                severity="pass",
                summary="无设计草案，接口冲突扫描跳过",
                confidence=1.0,
                uncertainty_flags=["无设计草案，接口定义未提供"],
                llm_calls=0,
                tokens_used=0,
            )
        return None

    def _analyze(self, snapshot: CompressedSnapshot,
                 artifacts: dict) -> CheckpointResult:
        frag = snapshot.to_prompt_fragment()

        # 收集 artifacts
        interface_def = artifacts.get("interface_definition", "")
        schemas = artifacts.get("schemas", {})
        api_specs = artifacts.get("api_specs", {})

        artifacts_text = []
        if interface_def:
            artifacts_text.append(f"接口定义:\n{str(interface_def)[:1500]}")
        if schemas:
            artifacts_text.append(f"数据Schema:\n{str(schemas)[:1000]}")
        if api_specs:
            artifacts_text.append(f"API规范:\n{str(api_specs)[:1000]}")

        prompt = (
            f"角色: 接口/契约冲突检查专家\n"
            f"行动: 检查组件间接口定义和数据契约是否存在冲突\n\n"
            f"情境快照:\n{frag}\n\n"
            f"设计草案:\n{chr(10).join(artifacts_text)}\n\n"
            f"聚焦检查:\n"
            f"1. 数据格式不一致 (一处定义string另一处期望int)\n"
            f"2. API签名不兼容 (字段名/类型/必需性不一致)\n"
            f"3. 组件间假设冲突 (同步vs异步, 推vs拉)\n"
            f"4. 快照约束与接口定义是否存在矛盾\n"
        )

        data = self._llm_check(prompt, temperature=0.1, max_tokens=2048)

        result = CheckpointResult(
            checkpoint_id=self.checkpoint_id,
            category=self.category.value,
            severity=data.get("severity", "pass"),
            summary=data.get("summary", "接口冲突检查完成"),
            findings=data.get("findings", []),
            risk_score=data.get("risk_score", 0.0),
            confidence=data.get("confidence", 0.8),
            uncertainty_flags=data.get("uncertainty_flags", []),
            activation_gate=data.get("activation_gate", False),
            llm_calls=1,
            tokens_used=data.get("_tokens", 2000),
        )

        if not result.findings:
            result.summary = "未发现接口/契约冲突"

        return result
