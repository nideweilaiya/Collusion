"""Brainstorm Orchestrator v3.1 — Agent 层（缓存优化版）

所有 LLM 调用使用 cached_call/cached_call_json：
- 全局固定 PREFIX 作为 system prompt（缓存命中 >80%）
- 只传最小用户上下文（200-500 tokens）
- 无对话历史累积
"""
from typing import List, Dict
from src.models import (
    AgentRole, ObjectType, ROLE_OBJECT_MAP, Step, PlanScheme,
)
from src.llm.base import BaseLLMAdapter
from src.cache_prefix import PREFIX


class OrchestratorAgent:
    """v3.1 编排器Agent — 对象代言人（缓存优化）"""

    def __init__(self, agent_id: str, role: AgentRole,
                 strong_llm: BaseLLMAdapter, fast_llm: BaseLLMAdapter):
        self.agent_id = agent_id
        self.role = role
        self.object_type = ROLE_OBJECT_MAP.get(role)
        self.object_name = self.object_type.value if self.object_type else role.value
        self.strong_llm = strong_llm
        self.fast_llm = fast_llm

    # ==================== 阶段3: 并行提案 ====================

    def generate_proposal(self, task: str, steps: List[Step]) -> PlanScheme:
        """从对象视角生成完整方案"""
        ctx = (
            f"角色: {self.object_name}代言人\n"
            f"任务: {task}\n"
            f"行动: 生成完整技术方案\n"
            f"需覆盖的环节:\n{self._steps_compact(steps)}\n"
        )
        data = self.fast_llm.cached_call_json(ctx, temperature=0.3, max_tokens=8192)

        scheme = PlanScheme(
            agent_role=self.role.value,
            agent_name=f"{self.agent_id} ({self.role.value})",
            object_name=self.object_name,
        )
        for s in data.get("steps", []):
            si = s.get("step_index", s.get("index", 0))
            step_id = self._find_step_id(steps, si)
            scheme.steps[step_id] = s.get("design_content", "")
        return scheme

    # ==================== 阶段4: 交叉修改（瘦身版） ====================

    def review_plan(self, task: str, target_plan: PlanScheme,
                    self_plan: PlanScheme, steps: List[Step]) -> dict:
        """审查其他方案。只传入目标环节片段（200-400字），不传全方案"""
        # 选一个与对象相关的环节作为审查重点
        target_step_info = self._pick_relevant_step(target_plan, steps)
        modified_str = ", ".join(target_plan.modified_steps[:3]) or "无"

        ctx = (
            f"角色: {self.object_name}代言人\n"
            f"任务: {task}\n"
            f"行动: 审查来自{target_plan.object_name or target_plan.agent_role}代言人的方案\n"
            f"审查焦点环节:\n{target_step_info}\n"
            f"已修改环节(不可重复): {modified_str}\n"
            f"复杂度增量规则: +1微小/+2中等/+3显著\n"
            f"输出: 按PREFIX中定义的交叉修改Schema输出JSON\n"
        )
        data = self.fast_llm.cached_call_json(ctx, temperature=0.2)

        if data.get("need_pause"):
            return {"type": "missing_step", "missing_step": data.get("missing_step", {})}
        if data.get("target_step_index", 0) == 0:
            return {"type": "no_change"}
        return {
            "type": "modification",
            "target_step_index": data["target_step_index"],
            "target_step_name": data.get("target_step_name", ""),
            "change_type": data.get("change_type", "enhancement"),
            "content": data.get("content", ""),
            "reason": data.get("reason", ""),
            "complexity_delta": data.get("complexity_delta", 1),
        }

    def _pick_relevant_step(self, plan: PlanScheme,
                            steps: List[Step]) -> str:
        """从方案中选出一个与当前对象最相关的环节片段"""
        # 简化策略: 返回方案前2个环节的内容(约200-400字)
        parts = []
        for s in steps[:2]:
            content = plan.steps.get(s.id, "")
            if content:
                parts.append(f"## {s.index}. {s.name}\n{content[:200]}")
        return "\n".join(parts) if parts else "(方案内容为空)"

    # ==================== 阶段2: 环节共识 ====================

    def review_steps(self, task: str, steps: List[Step]) -> dict:
        """审查环节清单，返回建议补充的环节 + 对象覆盖率"""
        ctx = (
            f"角色: {self.object_name}代言人\n"
            f"任务: {task}\n"
            f"行动: 审查环节清单完整性，评估对象覆盖率\n"
            f"现有环节:\n{self._steps_compact(steps)}\n"
            f"输出: 按PREFIX中定义的环节共识Schema输出JSON\n"
        )
        return self.fast_llm.cached_call_json(ctx, temperature=0.1)

    # ==================== v3.1: 业务锚点扫描 ====================

    def business_anchor_scan(self, task: str, plan: PlanScheme,
                             steps: List[Step]) -> dict:
        """业务价值对象代言人检查方案是否偏离核心需求"""
        summary = self._plan_compact(plan, steps)
        ctx = (
            f"角色: 业务价值对象代言人\n"
            f"任务: {task}\n"
            f"行动: 业务锚点扫描 — 检查方案是否偏离核心需求、是否过度设计\n"
            f"方案摘要:\n{summary}\n"
            f"输出: 按PREFIX中定义的业务锚点Schema输出JSON\n"
        )
        return self.fast_llm.cached_call_json(ctx, temperature=0.1)

    # ==================== v3.1: 可行性收束 ====================

    def feasibility_brake(self, task: str, plan: PlanScheme,
                          steps: List[Step], threshold: int) -> dict:
        """工程实现对象代言人进行现实检验"""
        summary = self._plan_compact(plan, steps)
        ctx = (
            f"角色: 工程实现对象代言人\n"
            f"任务: {task}\n"
            f"行动: 可行性收束 — 从成本/技术栈/团队能力/交付时间四个维度检验\n"
            f"当前复杂度累积值: {plan.complexity_score} (阈值: {threshold})\n"
            f"要求: 提出至少一处减法修改；若复杂度超阈值则强制简化\n"
            f"方案摘要:\n{summary}\n"
            f"输出: 按PREFIX中定义的可行性收束Schema输出JSON\n"
        )
        return self.fast_llm.cached_call_json(ctx, temperature=0.1)

    # ==================== v3.1: Owner 深度整合 ====================

    def owner_integration(self, task: str, plan: PlanScheme,
                          steps: List[Step]) -> str:
        """Owner Agent 深度整合 — Flash 初稿 + Strong 终审润色"""
        original = self._plan_compact(plan, steps)
        mods = self._format_mods_compact(plan.modification_history)
        simplified = "是" if plan.simplification_applied else "否"

        # 第一遍：Flash 模型做结构整合
        ctx_pass1 = (
            f"角色: {self.object_name}代言人(本方案Owner)\n"
            f"任务: {task}\n"
            f"行动: 深度整合 — 将原始设计、交叉修改、收束修改融合为完整文档\n"
            f"原始设计:\n{original}\n"
            f"修改记录:\n{mods}\n"
            f"已应用可行性收束: {simplified}\n"
            f"输出: 直接输出完整技术方案文档(纯文本，非JSON)\n"
        )
        draft = self.fast_llm.cached_call(ctx_pass1, temperature=0.3, max_tokens=16384)

        # 第二遍：Strong 模型终审润色 — 补充缺失技术细节，修正不一致
        ctx_pass2 = (
            f"角色: 技术校对专家\n"
            f"任务: {task}\n"
            f"行动: 对以下方案进行终审润色\n"
            f"要求:\n"
            f"1. 补充缺失的关键技术参数(如具体数字、配置值、算法名)\n"
            f"2. 修正任何技术不一致或逻辑矛盾\n"
            f"3. 确保每个环节的设计可独立阅读和执行\n"
            f"4. 保持原方案的视角和结构不变\n"
            f"5. 不要改变方案的核心设计方向\n"
            f"\n方案初稿:\n{draft}\n"
            f"输出: 润色后的完整方案文档(纯文本)\n"
        )
        return self.strong_llm.cached_call(ctx_pass2, temperature=0.1, max_tokens=16384)

    # ==================== 缺失环节补齐 ====================

    def fill_missing_step(self, task: str, plan: PlanScheme,
                          missing_step: dict, step_index: int) -> dict:
        """为方案补充缺失环节的设计内容"""
        ctx = (
            f"角色: {self.object_name}代言人\n"
            f"任务: {task}\n"
            f"行动: 补充缺失环节 — {missing_step.get('name', '')}\n"
            f"原因: {missing_step.get('description', '')}\n"
            f"输出: {{\"step_index\":{step_index},\"step_name\":\"{missing_step.get('name', '')}\",\"design_content\":\"...\"}}\n"
        )
        return self.fast_llm.cached_call_json(ctx, temperature=0.3)

    # ==================== 内部工具 ====================

    @staticmethod
    def _steps_compact(steps: List[Step]) -> str:
        """紧凑格式: 只保留名称和一行描述"""
        return "\n".join(
            f"{s.index}. {s.name}: {s.description[:60]}"
            for s in steps
        )

    @staticmethod
    def _plan_compact(plan: PlanScheme, steps: List[Step]) -> str:
        """紧凑方案摘要: 每环节只取前150字"""
        lines = [f"[{plan.object_name or plan.agent_role}视角]"]
        for s in steps:
            content = plan.steps.get(s.id, "")
            if content:
                lines.append(f"{s.index}. {s.name}: {content[:150]}...")
            else:
                lines.append(f"{s.index}. {s.name}: (未覆盖)")
        return "\n".join(lines)

    @staticmethod
    def _format_mods_compact(history: List[Dict]) -> str:
        """紧凑格式修改记录"""
        if not history:
            return "无"
        return "\n".join(
            f"[{m.get('object_name', m.get('agent_role', ''))}] "
            f"{m.get('target_step', '')}: {m.get('content', '')[:100]}"
            for m in history
        )

    @staticmethod
    def _find_step_id(steps: List[Step], index: int) -> str:
        for s in steps:
            if s.index == index:
                return s.id
        return f"step_{index}"
