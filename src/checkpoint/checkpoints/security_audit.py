"""深度检查点: 安全威胁建模

职责: 识别安全威胁、合规风险、数据保护隐患
requires: []
provides: ["security_threats"]
LLM 调用: 1 次 (单角色版本，Phase 6 后可扩展为多视角)

激活条件:
  - uncertainty_flags 包含"安全"/"认证"/"合规"关键词 或
  - constraints 中包含"安全"/"认证"/"auth"/"PCI"/"GDPR"
"""

from typing import Optional

from src.checkpoint.base import BaseCheckpoint, CheckpointResult, CheckpointCategory
from src.models import CompressedSnapshot


class SecurityAuditCheckpoint(BaseCheckpoint):
    """安全威胁建模 — 识别安全漏洞、合规风险、数据保护隐患

    当前为单角色版本。Phase 6 后扩展为安全+合规+渗透三视角。
    """

    checkpoint_id = "security_audit"
    category = CheckpointCategory.DEEP
    description = "识别安全威胁、合规风险、数据保护隐患"
    requires = []
    provides = ["security_threats"]

    def _analyze(self, snapshot: CompressedSnapshot,
                 artifacts: dict) -> CheckpointResult:
        frag = snapshot.to_prompt_fragment()

        prompt = (
            f"角色: 安全审计专家\n"
            f"行动: 对任务上下文进行威胁建模和安全风险识别\n\n"
            f"情境快照:\n{frag}\n\n"
            f"聚焦检查:\n"
            f"1. 认证与授权——是否有未受保护的端点或数据\n"
            f"2. 数据保护——敏感数据是否加密存储和传输\n"
            f"3. 注入风险——SQL注入、命令注入、XSS\n"
            f"4. 依赖安全——使用的技术栈是否有已知漏洞\n"
            f"5. 合规要求——PCI/GDPR/等保是否适用\n\n"
            f"要求: 不确定时标注 uncertainty_flags，不要编造风险\n"
        )

        data = self._llm_check(prompt, temperature=0.1, max_tokens=2048)

        return CheckpointResult(
            checkpoint_id=self.checkpoint_id,
            category=self.category.value,
            severity=data.get("severity", "advisory"),
            summary=data.get("summary", "安全审计完成"),
            findings=data.get("findings", []),
            risk_score=data.get("risk_score", snapshot.risk_score),
            confidence=data.get("confidence", 0.8),
            uncertainty_flags=data.get("uncertainty_flags", []),
            activation_gate=data.get("activation_gate", False),
            llm_calls=1,
            tokens_used=data.get("_tokens", 2000),
        )
