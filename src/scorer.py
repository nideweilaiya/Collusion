"""Brainstorm Orchestrator v3.0 — 独立评委评分模块"""
from typing import List
from src.models import PlanScheme, Step, VoteResult
from src.llm.base import BaseLLMAdapter
from src.prompts import SYSTEM_VOTE


VOTE_WEIGHTS = {
    "correctness": 0.20,
    "completeness": 0.20,
    "feasibility": 0.25,         # v3.1: 提升可行性权重
    "innovation": 0.15,
    "business_alignment": 0.20,  # v3.1: 提升业务对齐权重
}


class Scorer:
    """独立评委 — 不参与提案和修改，只做最终评分排名"""

    def __init__(self, strong_llm: BaseLLMAdapter):
        self.llm = strong_llm

    def score_plans(self, task: str, plans: List[PlanScheme],
                    steps: List[Step]) -> List[VoteResult]:
        """对所有方案评分+排名

        Args:
            task: 原始任务描述
            plans: 所有待评分方案
            steps: 全局环节清单

        Returns:
            按总分降序排列的评分结果列表
        """
        # 防御：检查方案内容是否为空（API Key 缺失等异常场景）
        empty_plans = []
        for plan in plans:
            has_content = any(
                v and len(v.strip()) > 20
                for v in plan.steps.values()
            )
            if not has_content:
                empty_plans.append(plan.id)
        if empty_plans and len(empty_plans) == len(plans):
            return [VoteResult(
                plan_id=pid, total_score=0, rank=0,
                comment="方案内容为空，无法评分。请检查 API Key 是否正确配置。",
            ) for pid in empty_plans]

        plans_text = self._format_all_plans(plans, steps)
        ctx = (
            f"行动: 独立评委投票评分\n"
            f"任务: {task}\n"
            f"各方案详情:\n{plans_text}\n"
            f"请按PREFIX中定义的投票评分Schema输出，plan_id只需单个字母(A/B/C)\n"
        )
        data = self.llm.cached_call_json(ctx, temperature=0.1, max_tokens=16384)

        results = []
        for r in data.get("results", []):
            total = (
                r.get("correctness", 5) * VOTE_WEIGHTS["correctness"]
                + r.get("completeness", 5) * VOTE_WEIGHTS["completeness"]
                + r.get("feasibility", 5) * VOTE_WEIGHTS["feasibility"]
                + r.get("innovation", 5) * VOTE_WEIGHTS["innovation"]
                + r.get("business_alignment", 5) * VOTE_WEIGHTS["business_alignment"]
            )
            # 清理 plan_id: 模型可能输出 "方案 A" 而不是 "A"
            raw_id = r.get("plan_id", "")
            clean_id = raw_id.strip()
            # 提取单个字母ID (A/B/C)
            import re as _re
            m = _re.search(r'[A-C]', clean_id)
            if m:
                clean_id = m.group(0)
            results.append(VoteResult(
                plan_id=clean_id,
                correctness=round(r.get("correctness", 5), 1),
                completeness=round(r.get("completeness", 5), 1),
                feasibility=round(r.get("feasibility", 5), 1),
                innovation=round(r.get("innovation", 5), 1),
                business_alignment=round(r.get("business_alignment", 5), 1),
                total_score=round(total, 2),
                comment=r.get("comment", ""),
            ))

        # 按总分降序排列
        results.sort(key=lambda x: x.total_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        return results

    # ==================== 内部工具 ====================

    @staticmethod
    def _format_all_plans(plans: List[PlanScheme], steps: List[Step]) -> str:
        """格式化所有方案为评委可读的文本"""
        blocks = []
        for plan in plans:
            lines = [f"### 方案 {plan.id}（来自{plan.agent_role}）"]
            for s in steps:
                content = plan.steps.get(s.id, "")
                if not content or len(content.strip()) < 10:
                    content = "⚠️ 此步骤内容缺失（LLM 调用可能失败）"
                lines.append(f"\n#### {s.index}. {s.name}\n{content}")
            # 附加修改历史
            if plan.modification_history:
                lines.append("\n修改记录：")
                for mod in plan.modification_history:
                    lines.append(f"  - [{mod.get('agent_role', '')}] {mod.get('target_step', '')}: "
                                 f"{mod.get('content', '')[:100]}")
            blocks.append("\n".join(lines))
        return "\n\n---\n\n".join(blocks)
