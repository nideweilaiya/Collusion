"""CheckpointEngine — 检查点链编排器 + Token 预算控制器

核心职责:
  1. 基于 requires/provides 声明做拓扑排序
  2. 并发度控制 (max_concurrency=2)
  3. TokenBudgetController 硬上限
  4. 汇总 CheckpointResult → DecisionCard

架构原则:
  - 引擎不写检查逻辑，只做调度
  - 检查点输入仅来自 CompressedSnapshot (信息单向流)
"""

from typing import List, Dict, Optional
from collections import defaultdict
import time
import uuid

from src.models import (
    CompressedSnapshot, CheckpointResult, DecisionCard,
    EngineConfig,
)
from src.checkpoint.base import BaseCheckpoint


class TokenBudgetController:
    """Token 预算控制器 — 硬上限 + 累计追踪"""

    def __init__(self, hard_limit: int = 15000):
        self.hard_limit = hard_limit
        self.accumulated = 0
        self.skipped_checkpoints: List[str] = []

    def can_run(self, checkpoint: BaseCheckpoint) -> bool:
        """预估消耗 + 已累计 > 硬上限 → 跳过"""
        estimated = self._estimate_tokens(checkpoint)
        return (self.accumulated + estimated) <= self.hard_limit

    def record(self, result: CheckpointResult):
        """从检查点结果中读取实际消耗并累计"""
        used = result.tokens_used if result.tokens_used > 0 else 500
        self.accumulated += used

    def skip(self, checkpoint_id: str) -> CheckpointResult:
        """生成预算跳过的结果"""
        self.skipped_checkpoints.append(checkpoint_id)
        return CheckpointResult(
            checkpoint_id=checkpoint_id,
            severity="pass",
            summary=f"因 token 预算限制跳过 ({self.accumulated}/{self.hard_limit})",
            confidence=0.5,
            uncertainty_flags=[f"token预算超限,已跳过{checkpoint_id}"],
            llm_calls=0,
            tokens_used=0,
        )

    @staticmethod
    def _estimate_tokens(checkpoint: BaseCheckpoint) -> int:
        category = getattr(checkpoint, 'category', None)
        if category is not None:
            cat_val = category.value if hasattr(category, 'value') else str(category)
            if cat_val == 'core':
                return 2500
        return 5000  # deep checkpoints

    @property
    def remaining(self) -> int:
        return max(0, self.hard_limit - self.accumulated)


class CheckpointEngine:
    """检查点链编排器

    使用方法:
        engine = CheckpointEngine(registry, fast_llm, strong_llm, config)
        snapshot = compressor.compress(task, retrieved)
        card = engine.run_light(snapshot)
        if card.deep_review_recommended:
            card = engine.run_deep(snapshot, card)
    """

    def __init__(self, registry=None, fast_llm=None, strong_llm=None,
                 config: EngineConfig = None):
        self.registry = registry
        self.fast_llm = fast_llm
        self.strong_llm = strong_llm
        self.config = config or EngineConfig()

    # ==================== 公共 API ====================

    def run_light(self, snapshot: CompressedSnapshot,
                  artifacts: dict = None,
                  strict_mode: bool = False) -> DecisionCard:
        """轻量模式: 只跑核心检查点子集"""
        return self._run(
            snapshot=snapshot,
            checkpoint_ids=self.config.core_checkpoints,
            artifacts=artifacts or {},
            strict_mode=strict_mode,
            budget_limit=self.config.token_budget_light,
            mode="light",
        )

    def run_deep(self, snapshot: CompressedSnapshot,
                 core_results: List[CheckpointResult] = None,
                 artifacts: dict = None,
                 strict_mode: bool = False) -> DecisionCard:
        """深度模式: 核心检查点 + 按风险激活的深度检查点"""
        activated = self._select_deep_checkpoints(snapshot, core_results or [])
        all_ids = self.config.core_checkpoints + activated
        return self._run(
            snapshot=snapshot,
            checkpoint_ids=all_ids,
            artifacts=artifacts or {},
            strict_mode=strict_mode,
            budget_limit=self.config.token_budget_deep,
            mode="deep",
        )

    # ==================== 核心调度 ====================

    def _run(self, snapshot: CompressedSnapshot,
             checkpoint_ids: List[str],
             artifacts: dict,
             strict_mode: bool,
             budget_limit: int,
             mode: str) -> DecisionCard:
        """统一调度器: 拓扑排序 → 分批执行 → 汇总 → DecisionCard"""
        budget = TokenBudgetController(hard_limit=budget_limit)
        card = DecisionCard(
            task_id=snapshot.task_id,
            task=snapshot.task_summary,
            task_summary=snapshot.task_summary,
            checkpoints_run=[],
        )

        # 1. 创建检查点实例
        instances = self._instantiate(checkpoint_ids, strict_mode)
        if not instances:
            card.total_llm_calls = 0
            card.total_tokens = 0
            card.next_steps = ["无可用检查点，请检查引擎配置"]
            return card

        # 2. 拓扑排序
        ordered, skipped = self._topological_sort(instances)
        for s in skipped:
            result = CheckpointResult(
                checkpoint_id=s.checkpoint_id,
                severity="pass",
                summary=f"前置检查点未执行，跳过",
                confidence=1.0,
                uncertainty_flags=[f"前置条件不满足: {s.requires}"],
            )
            self._apply_result(card, result, budget)

        # 3. 分批执行 (并发度 2)
        batches = self._partition(ordered, budget)
        for batch in batches:
            budget.record(self._execute_batch(batch, snapshot, artifacts, card, budget))

        # 4. 汇总
        self._finalize_card(card, snapshot, budget, mode)
        return card

    # ==================== 拓扑排序 ====================

    def _topological_sort(self,
                          checkpoints: List[BaseCheckpoint]) -> tuple:
        """基于 requires/provides 声明做拓扑排序

        已经执行过的 provides 集合从 artifacts 中推断（轻量模式下基本为空）。

        Returns:
            (ordered_list, skipped_list)
        """
        # 构建 provides 索引
        all_provides: Dict[str, str] = {}  # capability → checkpoint_id
        for cp in checkpoints:
            for p in cp.provides:
                all_provides[p] = cp.checkpoint_id

        ordered = []
        skipped = []
        satisfied = set()  # 已满足的能力

        for cp in checkpoints:
            can_run = True
            missing = []
            for req in cp.requires:
                if req not in satisfied and req not in all_provides:
                    can_run = False
                    missing.append(req)

            if can_run:
                ordered.append(cp)
                for p in cp.provides:
                    satisfied.add(p)
            else:
                skipped.append(cp)

        return ordered, skipped

    # ==================== 深度检查点选择 ====================

    def _select_deep_checkpoints(self, snapshot: CompressedSnapshot,
                                 core_results: List[CheckpointResult]) -> List[str]:
        """基于风险评分和不确定项选择 1-2 个最相关的深度检查点"""
        candidates = []

        # 汇总风险信号
        max_risk = snapshot.risk_score
        uncertainty_count = len(snapshot.uncertainty_flags)
        for r in core_results:
            max_risk = max(max_risk, r.risk_score)
            uncertainty_count += len(r.uncertainty_flags)

        if max_risk < self.config.activation_threshold and uncertainty_count == 0:
            return []

        # 选激活条件匹配的深度检查点
        for d_id in self.config.deep_checkpoints:
            cfg = self.config.checkpoint_configs.get(d_id)
            if cfg and not cfg.enabled:
                continue
            if self._should_activate_deep(d_id, snapshot, max_risk, uncertainty_count):
                candidates.append(d_id)

        return candidates[:2]

    def _should_activate_deep(self, checkpoint_id: str,
                              snapshot: CompressedSnapshot,
                              risk_score: float,
                              uncertainty_count: int) -> bool:
        """判断是否应激活特定深度检查点"""
        # 高架构不确定性 → architecture_review
        if checkpoint_id == "architecture_review":
            return (
                risk_score > 0.4 or
                any("架构" in u for u in snapshot.uncertainty_flags)
            )
        # 安全相关 → security_audit
        if checkpoint_id == "security_audit":
            return (
                any("安全" in u or "认证" in u or "auth" in u.lower()
                    for u in snapshot.uncertainty_flags) or
                any("安全" in c or "认证" in c for c in snapshot.constraints)
            )
        # 高风险或高不确定性 → business_alignment
        if checkpoint_id == "business_alignment":
            return risk_score > 0.5
        # 高复杂度或预算紧张 → complexity_brake
        if checkpoint_id == "complexity_brake":
            return risk_score > 0.6

        return False

    # ==================== 内部方法 ====================

    def _instantiate(self, checkpoint_ids: List[str],
                     strict_mode: bool) -> List[BaseCheckpoint]:
        """从注册表实例化检查点"""
        instances = []
        if self.registry is None:
            return instances
        for cid in checkpoint_ids:
            cls = self.registry.get(cid)
            if cls is not None:
                instances.append(cls(
                    fast_llm=self.fast_llm,
                    strong_llm=self.strong_llm,
                    strict_mode=strict_mode,
                ))
        return instances

    def _partition(self, checkpoints: List[BaseCheckpoint],
                   budget: TokenBudgetController) -> List[List[BaseCheckpoint]]:
        """按并发度分批，且每批受预算控制"""
        batches = []
        current = []
        for cp in checkpoints:
            if budget.can_run(cp):
                current.append(cp)
                if len(current) >= self.config.max_concurrency:
                    batches.append(current)
                    current = []
            else:
                # 当前批次先提交
                if current:
                    batches.append(current)
                    current = []
                # 超预算的检查点标记跳过
                budget.skip(cp.checkpoint_id)
        if current:
            batches.append(current)
        return batches

    def _execute_batch(self, batch: List[BaseCheckpoint],
                       snapshot: CompressedSnapshot,
                       artifacts: dict,
                       card: DecisionCard,
                       budget: TokenBudgetController) -> CheckpointResult:
        """执行一批检查点（使用简单顺序执行）"""
        batch_tokens = 0
        for cp in batch:
            try:
                result = cp.run(snapshot, artifacts)
            except Exception as e:
                result = CheckpointResult(
                    checkpoint_id=cp.checkpoint_id,
                    severity="pass",
                    summary=f"执行异常: {str(e)[:60]}",
                    confidence=0.0,
                    uncertainty_flags=[f"检查点{cp.checkpoint_id}执行失败"],
                )
            self._apply_result(card, result, budget)
            batch_tokens += result.tokens_used
        return CheckpointResult(tokens_used=batch_tokens)

    def _apply_result(self, card: DecisionCard,
                      result: CheckpointResult,
                      budget: TokenBudgetController):
        """将一个检查点结果应用到决策卡片"""
        card.checkpoint_results.append(result.to_dict())
        card.checkpoints_run.append(result.checkpoint_id)
        card.total_llm_calls += result.llm_calls
        card.total_tokens += result.tokens_used
        budget.record(result)

    def _finalize_card(self, card: DecisionCard,
                       snapshot: CompressedSnapshot,
                       budget: TokenBudgetController,
                       mode: str):
        """汇总所有检查点结果 → 填充卡片字段"""
        results = [
            CheckpointResult.from_dict(r)
            for r in card.checkpoint_results
        ]

        # 约束
        card.explicit_constraints = [
            c for c in snapshot.constraints
            if not c.startswith("推断")
        ]
        card.inferred_constraints = [
            c for c in snapshot.constraints
            if c.startswith("推断")
        ]

        # 历史决策 + 坑点
        card.relevant_decisions = snapshot.relevant_decisions[:5]
        card.pitfalls = snapshot.known_pitfalls[:5]

        # 假设
        assumptions = []
        for r in results:
            for uf in r.uncertainty_flags:
                assumptions.append({
                    "assumption": uf,
                    "impact_if_wrong": "medium",
                    "validation": "建议人工确认",
                })
        card.assumptions = assumptions[:5]

        # 风险
        risk_scores = [r.risk_score for r in results if r.risk_score > 0]
        max_risk = max(risk_scores) if risk_scores else snapshot.risk_score
        if max_risk < 0.2:
            card.overall_risk = "low"
        elif max_risk < 0.5:
            card.overall_risk = "medium"
        elif max_risk < 0.8:
            card.overall_risk = "high"
        else:
            card.overall_risk = "critical"

        # 置信度
        confs = [r.confidence for r in results if r.confidence > 0]
        card.overall_confidence = (
            sum(confs) / len(confs) if confs else 0.5
        )

        # 建议方向
        card.suggested_approach = self._generate_approach(snapshot, results, mode)

        # 下一步
        card.next_steps = self._generate_next_steps(results, snapshot)

        # 深度审查建议
        should_deep = (
            card.overall_risk in ("high", "critical") or
            any(r.activation_gate for r in results) or
            len(card.assumptions) >= 3
        )
        if should_deep and mode == "light":
            card.deep_review_recommended = True
            card.deep_review_reason = (
                f"风险等级{card.overall_risk}、"
                f"{len(card.assumptions)}个未验证假设，建议深度审查"
            )

        # 预算跳过
        if budget.skipped_checkpoints:
            card.next_steps.append(
                f"以下检查点因 token 预算限制跳过: "
                f"{', '.join(budget.skipped_checkpoints)}"
            )

        card.total_tokens += budget.accumulated

    def _generate_approach(self, snapshot: CompressedSnapshot,
                           results: List[CheckpointResult],
                           mode: str) -> str:
        """生成建议方向"""
        warnings = [r for r in results if r.severity == "warning"]
        blocking = [r for r in results if r.severity == "blocking"]

        if blocking:
            return "检测到阻塞性问题，建议先处理高危发现后再继续"
        if warnings:
            names = ", ".join(r.checkpoint_id for r in warnings)
            return f"在{names}方面存在需关注的风险，建议人工审查后决定是否深度展开"
        if mode == "light":
            return "核心检查未发现高风险项，可直接进入实现"
        return "深度检查已完成，建议按照决策卡片中的约束和已知坑点推进"

    def _generate_next_steps(self, results: List[CheckpointResult],
                             snapshot: CompressedSnapshot) -> List[str]:
        """从检查结果中提取下一步建议"""
        steps = []

        # 从 findings 中提取 suggestion
        for r in results:
            for f in r.findings:
                sug = f.get("suggestion", "")
                if sug and sug not in steps:
                    steps.append(str(sug))

        # 如果有不确定项，建议 clarify
        all_uncertainty = []
        for r in results:
            for u in (r.uncertainty_flags or []):
                all_uncertainty.append(str(u))
        if all_uncertainty:
            steps.append(f"澄清不确定项: {'; '.join(all_uncertainty[:3])}")

        if not steps:
            steps.append("无阻塞性问题，可进入实现阶段")

        return steps[:5]


# ============================================================
# 引擎工厂函数
# ============================================================

def create_engine(orchestrator=None, config: EngineConfig = None) -> CheckpointEngine:
    """创建 CheckpointEngine 实例并注册核心检查点"""
    from src.checkpoint.registry import CheckpointRegistry
    from src.checkpoint.checkpoints.semantic_consistency import SemanticConsistencyCheckpoint
    from src.checkpoint.checkpoints.interface_conflict import InterfaceConflictCheckpoint
    from src.checkpoint.checkpoints.pattern_match import PatternMatchCheckpoint

    registry = CheckpointRegistry()
    registry.register(SemanticConsistencyCheckpoint)
    registry.register(InterfaceConflictCheckpoint)
    registry.register(PatternMatchCheckpoint)

    fast_llm = orchestrator.fast_llm if orchestrator else None
    strong_llm = orchestrator.strong_llm if orchestrator else None

    return CheckpointEngine(
        registry=registry,
        fast_llm=fast_llm,
        strong_llm=strong_llm,
        config=config or EngineConfig(),
    )
