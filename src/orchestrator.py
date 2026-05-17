"""Brainstorm Orchestrator v3.2 — 核心编排引擎（双文件输出 + 反馈回路）"""
import json
import os
import time
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

from src.models import (
    AgentRole, ObjectType, ROLE_OBJECT_MAP, Step, PlanScheme,
    OrchestratorState, OrchestratorPhase,
)
from src.llm.base import BaseLLMAdapter
from src.llm.deepseek import DeepSeekAdapter
from src.agents import OrchestratorAgent
from src.scorer import Scorer
from src.prompts import SYSTEM_DECOMPOSE
from src.role_config import role_manager

DEFAULT_AGENT_ROLES = [
    AgentRole.UX,          # → 业务价值对象
    AgentRole.PERFORMANCE,  # → 技术架构对象
    AgentRole.SECURITY,     # → 安全与合规对象
]


class BrainstormOrchestrator:
    """Brainstorm Orchestrator v3.1 核心引擎"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        self._num_agents = self.config.get("num_agents", 3)
        self.agents: List[OrchestratorAgent] = []
        self.business_agent = None
        self.engineering_agent = None
        self.max_consensus_rounds = self.config.get("max_consensus_rounds", 2)
        self.max_modification_rounds = self.config.get("max_modification_rounds", 1)
        self.enable_second_round = self.config.get("enable_second_round", False)
        self.enable_feasibility_brake = self.config.get("enable_feasibility_brake", True)
        self.enable_owner_integration = self.config.get("enable_owner_integration", True)
        self.complexity_threshold = self.config.get("complexity_threshold", 5)
        self.coverage_threshold = self.config.get("object_coverage_threshold", 0.5)
        self.data_dir = Path(self.config.get("data_dir", "data"))

        self.strong_llm = self._create_adapter("strong")
        self.fast_llm = self._create_adapter("fast")

        self._rebuild_agents()
        self.scorer = Scorer(self.strong_llm)
        self._states: Dict[str, OrchestratorState] = {}
        self._executor = ThreadPoolExecutor(max_workers=self.num_agents)
        self._template_env = None  # Jinja2 环境，延迟初始化

    @property
    def num_agents(self):
        return self._num_agents

    @num_agents.setter
    def num_agents(self, value):
        if value != self._num_agents:
            self._num_agents = value
            if hasattr(self, 'agents') and self._strong_has_adapters():
                self._rebuild_agents()

    def _strong_has_adapters(self):
        return hasattr(self, 'strong_llm') and hasattr(self, 'fast_llm')

    def _rebuild_agents(self):
        roles = DEFAULT_AGENT_ROLES[:self._num_agents]
        self.agents = []
        for i, role in enumerate(roles):
            self.agents.append(OrchestratorAgent(
                agent_id=f"agent_{i + 1}", role=role,
                strong_llm=self.strong_llm, fast_llm=self.fast_llm,
            ))
        self.business_agent = self._find_agent(AgentRole.UX)
        self.engineering_agent = self._find_agent(AgentRole.PERFORMANCE)

    # ==================== v0.4.0: Elicitation 引导交互 ====================

    ELICITATION_CATEGORIES = {
        "security": "安全与合规",
        "performance": "性能与扩展",
        "ux": "用户体验",
        "deployment": "部署与运维",
        "data": "数据与存储",
        "scale": "规模与预期",
    }

    def detect_elicitation_questions(self, task: str, steps: list) -> list:
        """检测用户需求中缺失的关键信息，生成引导问题"""
        ctx = (
            f"用户的任务描述:\n{task}\n\n"
            f"当前已识别的环节: {len(steps)} 个\n"
            f"请分析上述任务描述，判断以下6个维度是否有信息缺失，"
            f"对每个真正缺失的维度生成1个具体的引导问题。\n\n"
            f"安全保障 / 性能扩展 / 用户体验 / 部署运维 / 数据存储 / 规模预期\n\n"
            f"仅对真正缺失的维度生成问题，不要编造。"
            f"输出严格JSON格式:\n"
            f'{{"questions":[{{"category":"security","question":"...",'
            f'"context":"为什么需要了解这个"}}, ...]}}\n'
        )
        try:
            data = self.fast_llm.cached_call_json(ctx, temperature=0.1, max_tokens=1024)
            questions = []
            for i, q in enumerate(data.get("questions", [])):
                questions.append({
                    "id": f"elicit_{i}",
                    "category": q.get("category", ""),
                    "question": q.get("question", ""),
                    "context": q.get("context", ""),
                    "answer": "",
                    "answered": False,
                })
            return questions
        except Exception:
            return []

    def apply_elicitation_answers(self, state: OrchestratorState,
                                   answers: dict) -> OrchestratorState:
        """将用户的引导问题答案应用到方案中"""
        for q in state.elicitation_questions:
            if q["id"] in answers:
                q["answer"] = answers[q["id"]]
                q["answered"] = True
        state.elicitation_answered = all(
            q.get("answered", False) for q in state.elicitation_questions
        )
        return state

    # ==================== 公共 API ====================

    def detect_agents_for_task(self, task: str, preset: str = "auto") -> tuple:
        """根据任务自动检测需要的 Agent 配置

        Args:
            task: 任务描述
            preset: 预设模式 — auto(自动检测)/quick/standard/full

        Returns:
            (role_ids, agent_count, complexity_level)
        """
        if preset == "auto":
            role_ids, complexity = role_manager.detect(task, "standard")
            count = role_manager.get_agent_count(role_ids)
        elif preset in role_manager.presets:
            pc = role_manager.presets[preset]
            role_ids = pc.roles
            count = pc.agents
            complexity = 0
        else:
            role_ids, count, complexity = ["business_value", "architecture", "security"], 3, 0

        return role_ids, count, complexity

    def orchestrate(self, task: str, task_id: str = None, output_format: str = "md",
                    preset: str = "auto") -> str:
        """主入口：执行完整编排

        Args:
            task: 技术任务描述
            task_id: 预设任务 ID（异步模式用）
            output_format: 输出格式 — \"md\"(默认) / \"html\" / \"both\"
                md: 仅 Markdown（~800 token 增量）
                html: HTML 可视化报告 + Markdown（~2500 token 增量）
                both: 同 html
        """
        state = OrchestratorState(original_task=task)
        state.output_paths = {"format": output_format}
        if task_id:
            state.task_id = task_id
        self._states[state.task_id] = state

        try:
            # Phase 1: 任务解构
            self._update_phase(state, OrchestratorPhase.DECOMPOSE)
            steps = self._decompose(state)
            state.step_list = [s.to_dict() for s in steps]
            self._save_state(state)

            # Phase 2: 环节共识 (v3.1: 含对象覆盖率检查)
            self._update_phase(state, OrchestratorPhase.CONSENSUS)
            steps = self._consensus(state, steps)
            state.step_list = [s.to_dict() for s in steps]
            self._save_state(state)

            # Phase 2.5: Elicitation 引导交互检测
            if len(steps) < 3:
                questions = self.detect_elicitation_questions(task, steps)
                if questions:
                    state.elicitation_questions = questions
                    state.phase = "phase2.5_elicitation"
                    self._save_state(state)
                    # 不阻塞编排——继续执行，用户可异步回答问题
                    print(f"  [Elicitation] 检测到 {len(questions)} 个引导问题，"
                          f"用户可通过 brainstorm_elicit 回答")

            # Phase 3: 并行提案
            self._update_phase(state, OrchestratorPhase.PROPOSAL)
            schemes = self._proposals(state, steps)
            state.schemes = {k: v.to_dict() for k, v in schemes.items()}
            self._save_state(state)

            # Phase 4: 交叉修改 (v3.1: 含复杂度追踪 + 业务锚点)
            self._update_phase(state, OrchestratorPhase.CROSS_REVIEW)
            schemes = self._cross_review(state, schemes, steps)
            state.schemes = {k: v.to_dict() for k, v in schemes.items()}
            self._save_state(state)

            # Phase 4.5: 可行性收束 (v3.1 新增)
            if self.enable_feasibility_brake:
                self._update_phase(state, OrchestratorPhase.FEASIBILITY_BRAKE)
                schemes = self._feasibility_brake(state, schemes, steps)
                state.schemes = {k: v.to_dict() for k, v in schemes.items()}
                self._save_state(state)

            # Phase 4.6: Owner 深度整合 (v3.1 新增)
            if self.enable_owner_integration:
                self._update_phase(state, OrchestratorPhase.OWNER_INTEGRATION)
                schemes = self._owner_integration(state, schemes, steps)
                state.schemes = {k: v.to_dict() for k, v in schemes.items()}
                self._save_state(state)

            # Phase 5: 可选第二轮
            if self.enable_second_round:
                self._update_phase(state, OrchestratorPhase.OPTIONAL_ROUND2)
                state.current_round = 2
                schemes = self._cross_review(state, schemes, steps, round2=True)
                state.schemes = {k: v.to_dict() for k, v in schemes.items()}
                self._save_state(state)

            # Phase 6: 投票评分
            self._update_phase(state, OrchestratorPhase.VOTE)
            plan_list = list(schemes.values())
            results = self.scorer.score_plans(state.original_task, plan_list, steps)
            state.vote_results = [r.to_dict() for r in results]
            state.top3_plans = [r.to_dict() for r in results[:3]]
            self._save_state(state)

            # Phase 6.5: 资产库索引（所有方案，含未选中方案）
            try:
                self._index_scheme_assets(state)
            except Exception as e:
                print(f"  [资产库] 索引失败（不阻塞主流程）: {e}")

            # Phase 7: 渲染双文件输出（v3.2 新增）
            state.phase = "phase7_render"
            try:
                output_paths = self._render_outputs(state)
                state.output_paths = output_paths
            except Exception as render_err:
                state.output_paths = {"error": str(render_err)}
            self._save_state(state)

            self._update_phase(state, OrchestratorPhase.DONE)
            state.completed_at = time.time()
            self._save_state(state)

        except Exception as e:
            self._update_phase(state, OrchestratorPhase.ERROR)
            state.error_message = str(e)
            self._save_state(state)

        return state.task_id

    def get_state(self, task_id: str) -> Optional[dict]:
        if task_id in self._states:
            return self._states[task_id].to_dict()
        state = self._load_state(task_id)
        if state:
            self._states[task_id] = state
            return state.to_dict()
        return None

    def get_result(self, task_id: str) -> Optional[dict]:
        state_dict = self.get_state(task_id)
        if state_dict is None:
            return None
        return {
            "task_id": state_dict["task_id"],
            "original_task": state_dict["original_task"],
            "phase": state_dict["phase"],
            "top3": state_dict["top3_plans"],
            "vote_results": state_dict["vote_results"],
            "step_list": state_dict["step_list"],
            "schemes": state_dict["schemes"],
            "object_coverage": state_dict.get("object_coverage", {}),
            "scheme_complexity": state_dict.get("scheme_complexity", {}),
            "business_alignment_warnings": state_dict.get("business_alignment_warnings", []),
            "feasibility_brake_records": state_dict.get("feasibility_brake_records", []),
            "output_files": state_dict.get("output_paths", {}),
            "total_cost_rmb": state_dict["total_cost_rmb"],
            "total_tokens": state_dict["total_tokens"],
            "error": state_dict.get("error_message"),
        }

    # ==================== 阶段1: 任务解构 ====================

    def _decompose(self, state: OrchestratorState) -> List[Step]:
        ctx = (
            f"行动: 任务解构\n"
            f"任务: {state.original_task}\n"
            f"输出: {{\"steps\":[{{\"index\":1,\"name\":\"\",\"description\":\"\"}}]}}, 4-6个环节\n"
        )
        data = self.strong_llm.cached_call_json(ctx, temperature=0.1, max_tokens=4096)
        steps = []
        for s in data.get("steps", []):
            steps.append(Step(
                index=s.get("index", len(steps) + 1),
                name=s.get("name", ""),
                description=s.get("description", ""),
            ))
        return steps

    # ==================== 阶段2: 环节共识 (v3.1 对象覆盖率) ====================

    def _consensus(self, state: OrchestratorState, steps: List[Step]) -> List[Step]:
        current_steps = list(steps)
        all_coverage = {}

        for round_num in range(self.max_consensus_rounds):
            all_suggestions = []
            for agent in self.agents:
                data = agent.review_steps(state.original_task, current_steps)

                # v3.1: 收集对象覆盖率
                coverage = data.get("coverage", [])
                for c in coverage:
                    step_name = c.get("step_name", "")
                    level = c.get("level", "充分")
                    key = f"{agent.object_name}:{step_name}"
                    all_coverage[key] = level

                for ms in data.get("missing_steps", []):
                    name = ms.get("name", "")
                    if any(s.name == name for s in current_steps):
                        continue
                    all_suggestions.append(ms)

            if not all_suggestions:
                break

            validated = self._validate_new_steps(
                state.original_task, current_steps, all_suggestions,
            )
            for s in validated:
                current_steps.append(Step(
                    index=len(current_steps) + 1,
                    name=s.get("name", ""),
                    description=s.get("description", ""),
                ))

        # v3.1: 计算对象覆盖率并检查是否需要增补横切环节
        state.object_coverage = self._calc_coverage(current_steps, all_coverage)

        # 如果某对象覆盖率低于阈值，补充横切环节
        for object_name, cov in state.object_coverage.items():
            if cov < self.coverage_threshold:
                current_steps.append(Step(
                    index=len(current_steps) + 1,
                    name=f"全局{object_name}基线",
                    description=f"确保所有环节满足{object_name}的基本要求",
                ))
                # 重新计算覆盖率
                state.object_coverage[object_name] = min(cov + 0.3, 1.0)

        for i, s in enumerate(current_steps):
            s.index = i + 1
        return current_steps

    def _calc_coverage(self, steps: List[Step],
                       coverage_map: dict) -> Dict[str, float]:
        """计算每个对象在所有环节中的平均覆盖率"""
        obj_levels = {}
        for agent in self.agents:
            obj_name = agent.object_name
            covered = 0
            for s in steps:
                key = f"{obj_name}:{s.name}"
                level = coverage_map.get(key, "缺失")
                if level in ("充分", "不足"):
                    covered += 1 if level == "充分" else 0.5
            obj_levels[obj_name] = round(covered / max(len(steps), 1), 2)
        return obj_levels

    def _validate_new_steps(self, task: str, existing: List[Step],
                            suggestions: list) -> list:
        """用强模型判断新增环节的必要性，去重合并"""
        existing_text = "\n".join(f"- {s.name}: {s.description}" for s in existing)
        sug_text = "\n".join(
            f"[{i}] {s.get('name', '')}: {s.get('description', '')}"
            for i, s in enumerate(suggestions)
        )
        ctx = (
            f"行动: 审核新增环节建议\n"
            f"规则: 语义重复→skip 填补空白→keep 最多保留3个 相同建议只保留一个\n"
            f"任务: {task}\n"
            f"已有环节:\n{existing_text}\n"
            f"新增建议:\n{sug_text}\n"
            f"输出: {{\"decisions\":[{{\"index\":0,\"action\":\"keep或skip\",\"reason\":\"\"}}]}}\n"
        )
        data = self.strong_llm.cached_call_json(ctx, temperature=0.1, max_tokens=2048)
        kept = []
        for d in data.get("decisions", []):
            if d.get("action") == "keep":
                idx = d.get("index", -1)
                if 0 <= idx < len(suggestions):
                    kept.append(suggestions[idx])
        return kept[:3]

    # ==================== 阶段3: 并行提案 ====================

    def _proposals(self, state: OrchestratorState,
                   steps: List[Step]) -> Dict[str, PlanScheme]:
        """阶段3: 并行提案 (ThreadPoolExecutor, 兼容同步/异步环境)"""
        from concurrent.futures import as_completed
        futures = {}
        for agent in self.agents:
            future = self._executor.submit(
                agent.generate_proposal,
                state.original_task,
                steps,
            )
            futures[future] = agent

        schemes = {}
        for future in as_completed(futures):
            agent = futures[future]
            plan = future.result()
            plan.id = chr(ord("A") + len(schemes))
            plan.owner_agent_id = agent.agent_id
            schemes[plan.id] = plan
        return schemes

    # ==================== 阶段4: 交叉修改 (v3.1 复杂度追踪 + 业务锚点) ====================

    def _cross_review(self, state: OrchestratorState, schemes: Dict[str, PlanScheme],
                      steps: List[Step], round2: bool = False) -> Dict[str, PlanScheme]:
        scheme_ids = list(schemes.keys())
        state.scheme_complexity = {}

        for agent in self.agents:
            for target_id in scheme_ids:
                target = schemes[target_id]
                if target.agent_role == agent.role.value:
                    continue
                if target.is_paused:
                    continue

                self_plan = self._find_own_plan(schemes, agent.role.value)
                if self_plan is None:
                    continue

                if round2 and not target.modified_steps:
                    continue

                # v3.1: 业务锚点Agent在每个方案的开始/结束时执行锚点扫描
                if self.business_agent and agent == self.business_agent:
                    anchor_result = agent.business_anchor_scan(
                        state.original_task, target, steps,
                    )
                    if not anchor_result.get("aligned", True):
                        state.business_alignment_warnings.append({
                            "scheme_id": target.id,
                            "agent_role": agent.role.value,
                            "warnings": anchor_result.get("over_engineered_steps", []),
                            "simplification_score": anchor_result.get("simplification_score", 0),
                        })
                    continue  # 业务Agent不参与常规修改

                result = agent.review_plan(
                    state.original_task, target, self_plan, steps,
                )

                if result["type"] == "modification":
                    step_id = self._index_to_step_id(
                        steps, result.get("target_step_index", 0),
                    )
                    if step_id and step_id not in target.modified_steps:
                        target.modified_steps.append(step_id)
                        delta = result.get("complexity_delta", 1)
                        target.complexity_score += delta
                        target.modification_history.append({
                            "agent_id": agent.agent_id,
                            "agent_role": agent.role.value,
                            "object_name": agent.object_name,
                            "target_step": step_id,
                            "change_type": result.get("change_type", "enhancement"),
                            "complexity_delta": delta,
                            "content": result.get("content", ""),
                            "reason": result.get("reason", ""),
                        })
                        if step_id in target.steps:
                            target.steps[step_id] += (
                                f"\n\n[{agent.object_name}修改 | 复杂度+{delta}]: "
                                f"{result.get('content', '')}"
                            )

                elif result["type"] == "missing_step":
                    target.is_paused = True
                    missing = result.get("missing_step", {})
                    self._fill_missing_step(state, target, missing, steps)
                    target.is_paused = False

            # v3.1: 记录每个方案的复杂度
            for tid in scheme_ids:
                state.scheme_complexity[tid] = schemes[tid].complexity_score

        return schemes

    # ==================== 阶段4.5: 可行性收束 (v3.1 新增) ====================

    def _feasibility_brake(self, state: OrchestratorState,
                           schemes: Dict[str, PlanScheme],
                           steps: List[Step]) -> Dict[str, PlanScheme]:
        """工程实现对象代言人对每个方案进行现实检验"""
        records = []
        for scheme_id, scheme in schemes.items():
            brake_agent = self.engineering_agent or self.agents[0]
            result = brake_agent.feasibility_brake(
                state.original_task, scheme, steps, self.complexity_threshold,
            )
            records.append({
                "scheme_id": scheme_id,
                "feasible": result.get("feasible", True),
                "cost_estimate": result.get("cost_estimate", "中"),
                "team_requirements": result.get("team_requirements", ""),
                "simplifications": result.get("simplifications", []),
                "mandatory_simplify": result.get("mandatory_simplify", False),
            })

            # 应用简化建议
            if result.get("mandatory_simplify") or not result.get("feasible", True):
                scheme.simplification_applied = True
                for simpl in result.get("simplifications", []):
                    target_name = simpl.get("target_step", "")
                    simplified = simpl.get("simplified_approach", "")
                    # 找到对应环节并追加简化方案
                    for s in steps:
                        if s.name == target_name and s.id in scheme.steps:
                            scheme.steps[s.id] += (
                                f"\n\n[可行性收束 - 简化方案]: {simplified}"
                            )
                            break

                # 严重过度设计时要求简洁重构
                if scheme.complexity_score > self.complexity_threshold:
                    # 压缩方案：将各环节方案限制在300字以内
                    for sid, content in scheme.steps.items():
                        if len(content) > 500:
                            scheme.steps[sid] = content[:500] + (
                                "\n\n[已由可行性守门人精简，核心要点保留]"
                            )
                    scheme.complexity_score = self.complexity_threshold

        state.feasibility_brake_records = records
        return schemes

    # ==================== 阶段4.6: Owner 深度整合 (v3.1 新增) ====================

    def _owner_integration(self, state: OrchestratorState,
                           schemes: Dict[str, PlanScheme],
                           steps: List[Step]) -> Dict[str, PlanScheme]:
        """每个方案的Owner Agent深度整合所有修改为最终文档"""
        for scheme_id, scheme in schemes.items():
            # 找到Owner Agent
            owner = self._find_agent_by_id(scheme.owner_agent_id)
            if owner is None:
                owner = self.agents[0]

            integrated = owner.owner_integration(
                state.original_task, scheme, steps,
            )
            scheme.integrated_content = integrated

        return schemes

    # ==================== 内部工具 ====================

    def _fill_missing_step(self, state: OrchestratorState, plan: PlanScheme,
                           missing: dict, steps: List[Step]):
        step_name = missing.get("name", "")
        existing = next((s for s in steps if s.name == step_name), None)
        if existing:
            step_id = existing.id
            step_index = existing.index
        else:
            new_step = Step(
                index=len(steps) + 1,
                name=step_name,
                description=missing.get("description", ""),
            )
            steps.append(new_step)
            state.step_list = [s.to_dict() for s in steps]
            step_id = new_step.id
            step_index = new_step.index

        for agent in self.agents:
            data = agent.fill_missing_step(
                state.original_task, plan, missing, step_index,
            )
            plan.steps[step_id] = data.get("design_content", "")

    @staticmethod
    def _generate_mermaid_flow(task: str, steps: list) -> str:
        """生成 Collusion 编排流程 Mermaid 图"""
        lines = ["graph TD"]
        lines.append("  A[用户输入任务] --> B[Phase1: 任务解构]")
        lines.append("  B --> C[Phase2: 环节共识]")
        for i, step in enumerate(steps[:8], 1):
            step_name = step.get("name", f"步骤{i}")[:20]
            lines.append(f"  C --> D{i}[{step_name}]")
        lines.append("  D1 --> E[Phase3: 并行提案]")
        lines.append("  E --> F[Phase4: 交叉审查]")
        lines.append("  F --> G[可行性强制收束]")
        lines.append("  G --> H[Owner 整合]")
        lines.append("  H --> I[5维投票评分]")
        lines.append("  I --> J[输出 Top3 方案]")
        lines.append("  style A fill:#dbeafe")
        lines.append("  style J fill:#d1fae5")
        lines.append("  style G fill:#fee2e2")
        return "\n".join(lines)

    @staticmethod
    def _generate_scheme_mermaid(scheme_id: str, role: str,
                                  content: str) -> str:
        """从方案内容中提取/生成架构分层图"""
        lines = ["graph TB"]
        lines.append("  subgraph 方案架构")

        # 根据方案内容智能判断架构层级
        has_frontend = any(kw in content.lower() for kw in
                         ["前端", "frontend", "ui", "界面", "react", "vue",
                          "component", "page", "组件", "页面"])
        has_backend = any(kw in content.lower() for kw in
                        ["后端", "backend", "api", "server", "服务", "接口",
                         "controller", "handler", "route"])
        has_database = any(kw in content.lower() for kw in
                         ["数据库", "database", "sql", "mysql", "postgres",
                          "sqlite", "mongo", "redis", "cache", "缓存", "存储",
                          "storage", "持久化"])
        has_deploy = any(kw in content.lower() for kw in
                       ["部署", "deploy", "docker", "ci/cd", "kubernetes",
                        "k8s", "运维", "发布", "release"])
        has_security = any(kw in content.lower() for kw in
                        ["安全", "security", "认证", "auth", "授权",
                         "encrypt", "加密", "token", "jwt", "权限"])

        # 默认至少显示三层架构
        if not any([has_frontend, has_backend, has_database]):
            has_frontend = has_backend = has_database = True

        if has_frontend:
            lines.append('    FRONT["前端层<br/>UI / 交互 / 状态"]')
        if has_backend:
            lines.append('    BACK["后端服务层<br/>API / 业务逻辑 / 消息"]')
        if has_database:
            lines.append('    DB["数据层<br/>存储 / 缓存 / 持久化"]')
        if has_deploy:
            lines.append('    OPS["部署运维层<br/>CI/CD / 监控 / 日志"]')
        if has_security:
            lines.append('    SEC["安全层<br/>认证 / 加密 / 审计"]')

        # 连接层级
        if has_frontend and has_backend:
            lines.append("    FRONT --> BACK")
        if has_backend and has_database:
            lines.append("    BACK --> DB")
        if has_deploy:
            if has_frontend:
                lines.append(f"    OPS --> FRONT")
            if has_backend:
                lines.append(f"    OPS --> BACK")
            if has_database:
                lines.append(f"    OPS --> DB")
        if has_security:
            if has_frontend:
                lines.append(f"    SEC -.-> FRONT")
            if has_backend:
                lines.append(f"    SEC -.-> BACK")
            if has_database:
                lines.append(f"    SEC -.-> DB")

        lines.append("  end")
        lines.append(f'  style FRONT fill:#dbeafe')
        lines.append(f'  style BACK fill:#e9d5ff')
        lines.append(f'  style DB fill:#d1fae5')
        if has_deploy:
            lines.append(f'  style OPS fill:#fef3c7')
        if has_security:
            lines.append(f'  style SEC fill:#fee2e2')
        return "\n".join(lines)

    @staticmethod
    def _render_radar_svg(schemes: list, dimensions: list) -> str:
        """生成雷达图 SVG（纯 Python 计算坐标，无 JS 依赖）"""
        colors = {"A": "#dc2626", "B": "#2563eb", "C": "#059669"}
        cx, cy, r = 250, 240, 180
        n = len(dimensions)
        parts = ['<svg viewBox="0 0 500 500" xmlns="http://www.w3.org/2000/svg">']

        # 背景
        parts.append(f'<rect width="500" height="500" fill="#1e293b" rx="12"/>')
        # 网格
        for level in [0.2, 0.4, 0.6, 0.8, 1.0]:
            pts = []
            for i in range(n):
                angle = (i * 360 / n - 90) * math.pi / 180
                x = cx + r * level * math.cos(angle)
                y = cy - r * level * math.sin(angle)
                pts.append(f"{x:.0f},{y:.0f}")
            parts.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="#475569" stroke-width="1.5"/>')

        # 轴 + 标签
        for i, dim in enumerate(dimensions):
            angle = (i * 360 / n - 90) * math.pi / 180
            x2 = cx + r * 1.05 * math.cos(angle)
            y2 = cy - r * 1.05 * math.sin(angle)
            tx = cx + r * 1.18 * math.cos(angle)
            ty = cy - r * 1.18 * math.sin(angle) + 4
            parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="#64748b" stroke-width="1.5"/>')
            parts.append(f'<text x="{tx:.0f}" y="{ty:.0f}" text-anchor="middle" font-size="13" font-weight="600" fill="#94a3b8">{dim}</text>')

        # 数据多边形 + 顶点
        for scheme in schemes:
            c = colors.get(scheme.get("id", ""), "#6b7280")
            pts = []
            for i, dim in enumerate(dimensions):
                score = scheme.get("scores", {}).get(dim, 0) / 10.0
                angle = (i * 360 / n - 90) * math.pi / 180
                x = cx + r * score * math.cos(angle)
                y = cy - r * score * math.sin(angle)
                pts.append(f"{x:.0f},{y:.0f}")
            parts.append(f'<polygon points="{" ".join(pts)}" fill="{c}" fill-opacity="0.1" stroke="{c}" stroke-width="2"/>')
            for pt in pts:
                px, py = pt.split(",")
                parts.append(f'<circle cx="{px}" cy="{py}" r="4" fill="{c}"/>')

        # 图例
        for i, scheme in enumerate(schemes):
            c = colors.get(scheme.get("id", ""), "#6b7280")
            ly = 400 + i * 22
            parts.append(f'<rect x="20" y="{ly}" width="14" height="14" fill="{c}" opacity="0.4" rx="2"/>')
            parts.append(f'<rect x="20" y="{ly}" width="14" height="2" fill="{c}"/>')
            parts.append(f'<text x="40" y="{ly + 12}" font-size="12" font-weight="600" fill="#cbd5e1">方案 {scheme.get("id", "")} ({scheme.get("total_score", 0):.1f})</text>')

        parts.append('</svg>')
        return "\n".join(parts)

    # ==================== v3.2: 双文件输出 ====================

    def _get_template_env(self):
        if self._template_env is None:
            from jinja2 import Environment, FileSystemLoader
            template_dir = Path(__file__).parent / "templates"
            self._template_env = Environment(
                loader=FileSystemLoader(str(template_dir)),
                autoescape=False,
            )
        return self._template_env

    def _render_outputs(self, state: OrchestratorState) -> dict:
        """渲染输出，保存到 data/outputs/{task_id}/"""
        fmt = state.output_paths.get("format", "md")
        data = self._build_template_data(state, fmt)
        env = self._get_template_env()
        output_dir = self.data_dir / "outputs" / state.task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        paths = {"format": fmt}

        # Markdown（仅 md 和 both 模式生成）
        if fmt in ("md", "both"):
            md_template = env.get_template("report.md")
            md_content = md_template.render(**data)
            md_path = output_dir / "report.md"
            md_path.write_text(md_content, encoding="utf-8")
            paths["markdown"] = str(md_path)

        # HTML（html 和 both 模式生成。html 模式跳过 MD，直接输出 HTML + JSON）
        if fmt in ("html", "both"):
            html_template = env.get_template("report.html")
            html_content = html_template.render(**data)
            html_path = output_dir / "report.html"
            html_path.write_text(html_content, encoding="utf-8")
            paths["html"] = str(html_path)

        return paths

    def _build_template_data(self, state: OrchestratorState, fmt: str = "md") -> dict:
        """将编排状态转换为模板可用的数据结构"""
        import mistune
        md_renderer = mistune.create_markdown()
        schemes = state.schemes
        steps = state.step_list
        vote_results = state.vote_results

        # 维度列表
        dimensions = ["正确性", "完整性", "可行性", "创新性", "业务对齐"]

        # 各维度最高分（用于标绿）
        max_scores = {}
        for dim in dimensions:
            best = 0
            for r in vote_results:
                s = r.get(dim.lower(), r.get(dim, 0))
                if s > best:
                    best = s
            max_scores[dim] = best

        # 方案数据
        scheme_list = []
        for vr in sorted(vote_results, key=lambda x: x.get("rank", 99)):
            sid = vr.get("plan_id", "")
            s = schemes.get(sid, {})
            raw_content = s.get("integrated_content", "")
            obj_name = s.get("object_name", "")
            role = s.get("agent_role", "")
            scheme_list.append({
                "id": sid,
                "object_name": obj_name,
                "agent_role": role,
                "integrated_content": raw_content,
                "integrated_html": md_renderer(raw_content) if fmt in ("html", "both") else "",
                "total_score": vr.get("total_score", 0),
                "comment": vr.get("comment", ""),
                "scores": {
                    "正确性": vr.get("correctness", 0),
                    "完整性": vr.get("completeness", 0),
                    "可行性": vr.get("feasibility", 0),
                    "创新性": vr.get("innovation", 0),
                    "业务对齐": vr.get("business_alignment", 0),
                },
                "mermaid_arch": self._generate_scheme_mermaid(
                    sid, role, raw_content,
                ) if fmt in ("html", "both") else "",
            })

        # Top 方案
        top_scheme = scheme_list[0] if scheme_list else None

        # 步骤 + 各方案设计（含代码锚点和 MVP 检测所需数据）
        all_step_ids_map = {s.get("id", ""): s for s in steps}
        step_list = []
        for step in steps:
            step_id = step.get("id", "")
            step_idx = step.get("index", 0)
            designs = {}
            for sid, scheme in schemes.items():
                design = scheme.get("steps", {}).get(step_id, "")
                if design and len(design.strip()) > 10:
                    designs[sid] = design[:300]
            # 代码入口锚点：从整合正文 + 步骤设计中提取
            code_anchors = []
            import re as _re2
            for sid, scheme in schemes.items():
                # 优先从整合正文提取（含完整架构描述）
                integrated = scheme.get("integrated_content", "")
                paths = _re2.findall(
                    r'(?:(?:src|app|lib|components|pages|api|'
                    r'routes|middleware|utils|config|'
                    r'models|services|controllers|handlers|'
                    r'tests|docs|public|static|data|bin)/[\w/.-]+|'
                    r'[\w/.-]+\.(?:tsx?|jsx?|py|rs|go|java|'
                    r'yml|yaml|json|toml|sql|css|html|sh|md|'
                    r'dockerfile|env|cfg|ini|xml))',
                    integrated,
                )
                code_anchors.extend(paths[:5])
                # 也检查步骤级设计
                design = scheme.get("steps", {}).get(step_id, "")
                if design:
                    paths = _re2.findall(
                        r'(?:(?:src|app|lib|components|pages|api|'
                        r'routes|middleware|utils|config|'
                        r'models|services|controllers|handlers|'
                        r'tests|docs|public|static|data|bin)/[\w/.-]+|'
                        r'[\w/.-]+\.(?:tsx?|jsx?|py|rs|go|java|'
                        r'yml|yaml|json|toml|sql|css|html|sh|md|'
                        r'dockerfile|env|cfg|ini|xml))',
                        design,
                    )
                    code_anchors.extend(paths[:3])
            seen_p = set()
            unique_anchors = []
            for p in code_anchors:
                if p not in seen_p and len(unique_anchors) < 5:
                    seen_p.add(p)
                    unique_anchors.append(p)
            # MVP 检测
            deps = step.get("dependencies", [])
            has_deps = len(deps) > 0 and any(
                d in all_step_ids_map for d in deps
            )
            is_mvp = (not has_deps) and step_idx <= 3
            # 优先级
            priority = "高"
            if step_idx >= 5:
                priority = "中"
            if step_idx >= 8:
                priority = "低"
            est_time = "2-3小时" if step_idx <= 2 else ("3-4小时" if step_idx >= 5 else "1-2小时")
            step_list.append({
                "index": step_idx,
                "name": step.get("name", ""),
                "description": step.get("description", ""),
                "designs": designs,
                "code_anchors": unique_anchors,
                "dependencies": deps,
                "is_mvp": is_mvp,
                "priority": priority,
                "estimated_time": est_time,
            })

        # 风险标注
        risks = []
        for record in state.feasibility_brake_records:
            level = "low"
            if not record.get("feasible", True):
                level = "high"
            elif record.get("mandatory_simplify"):
                level = "mid"
            if level != "low" or record.get("cost_estimate") == "高":
                risks.append({
                    "scheme_id": record.get("scheme_id", ""),
                    "level": level,
                    "description": record.get("cost_estimate", "") +
                        (" 需强制简化" if record.get("mandatory_simplify") else ""),
                })

        # 修改历史
        modifications = []
        for sid, scheme in schemes.items():
            for mod in scheme.get("modification_history", []):
                modifications.append({
                    "scheme_id": sid,
                    "agent_role": mod.get("agent_role", ""),
                    "target_step": mod.get("target_step", ""),
                    "reason": mod.get("reason", ""),
                    "content": mod.get("content", ""),
                })

        # 任务清单 (从步骤生成) + 代码入口锚点 + MVP 检测
        task_list = []
        mvp_steps = []
        all_step_ids = {s.get("id", ""): s for s in steps}
        for step in steps:
            step_id = step.get("id", "")
            step_idx = step.get("index", 0)
            deps = step.get("dependencies", [])

            # ---- 代码入口锚点：从方案内容中提取文件路径 ----
            code_anchors = []
            for sid, scheme in schemes.items():
                design = scheme.get("steps", {}).get(step_id, "")
                if design:
                    # 提取文件名/路径模式
                    import re as _re
                    paths = _re.findall(
                        r'(?:(?:src|app|lib|components|pages|api|'
                        r'routes|middleware|utils|config|'
                        r'models|services|controllers|handlers|'
                        r'tests|docs|public|static|data|bin)/[\w/.-]+|'
                        r'[\w/.-]+\.(?:tsx?|jsx?|py|rs|go|java|'
                        r'yml|yaml|json|toml|sql|css|html|sh|md|'
                        r'dockerfile|env|cfg|ini|xml))',
                        design,
                    )
                    code_anchors.extend(paths[:3])  # 每方案每步骤最多3个
            # 去重并限制数量
            seen = set()
            unique_anchors = []
            for p in code_anchors:
                if p not in seen and len(unique_anchors) < 5:
                    seen.add(p)
                    unique_anchors.append(p)

            # ---- MVP 自动检测 ----
            has_deps = len(deps) > 0 and any(
                d in all_step_ids for d in deps
            )
            # 规则: 无依赖 + 前3步 → MVP
            is_mvp = (not has_deps) and step_idx <= 3
            if is_mvp:
                mvp_steps.append(str(step_idx))

            # 耗时和优先级推断
            est_time = "1-2小时"
            if step_idx <= 2:
                est_time = "2-3小时"
            elif step_idx >= 5:
                est_time = "3-4小时"

            priority = "高"
            if step_idx >= 5:
                priority = "中"
            if step_idx >= 8:
                priority = "低"

            task_list.append({
                "id": step_idx,
                "name": step.get("name", ""),
                "description": step.get("description", ""),
                "dependencies": deps,
                "estimated_time": est_time,
                "priority": priority,
                "is_mvp": is_mvp,
                "code_anchors": unique_anchors,
            })

        # 雷达图 SVG
        radar_svg = self._render_radar_svg(scheme_list, dimensions)

        # Mermaid 架构图
        mermaid_diagram = ""
        if fmt in ("html", "both"):
            mermaid_diagram = self._generate_mermaid_flow(
                state.original_task, steps,
            )

        return {
            "task_id": state.task_id,
            "task": state.original_task,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "radar_svg": radar_svg,
            "mermaid_diagram": mermaid_diagram,
            "cost": state.total_cost_rmb,
            "tokens": state.total_tokens,
            "agents": len(self.agents),
            "top_scheme": top_scheme,
            "schemes": scheme_list,
            "steps": step_list,
            "dimensions": dimensions,
            "max_scores": max_scores,
            "risks": risks,
            "modifications": modifications,
            "task_list": task_list,
            "mvp_steps": mvp_steps,
            "mvp_count": len(mvp_steps),
            "saved_mods": {},  # 用于 HTML 草稿恢复
        }

    # ==================== v0.5.0: 6 种新模式 ====================

    def enhance(self, plan: str, focus: str = "") -> dict:
        """多视角增强已有方案"""
        perspectives = ["业务价值", "技术架构", "安全合规"]
        if focus:
            mapping = {"business": "业务价值", "architecture": "技术架构", "security": "安全合规"}
            perspectives = [mapping.get(focus, "技术架构")]

        findings = []
        for p in perspectives:
            ctx = (
                f"你是一个{p}专家。请审查以下技术方案，输出JSON:\n"
                f'{{"strengths":["优势1","优势2"],"risks":["风险1","风险2"],'
                f'"suggestions":["建议1","建议2"],"score":7.5}}\n\n'
                f"方案:\n{plan[:4000]}"
            )
            try:
                data = self.fast_llm.cached_call_json(ctx, temperature=0.1, max_tokens=1024)
                findings.append({"perspective": p, **data})
            except Exception as e:
                findings.append({"perspective": p, "error": str(e)})

        # 融合增强
        all_suggestions = []
        for f in findings:
            all_suggestions.extend(f.get("suggestions", []))
        avg_score = sum(f.get("score", 5) for f in findings) / max(len(findings), 1)

        return {
            "mode": "enhance",
            "original_length": len(plan),
            "perspectives": len(findings),
            "average_score": round(avg_score, 1),
            "findings": findings,
            "merged_suggestions": all_suggestions[:10],
        }

    def review_code(self, code: str, language: str = "python") -> dict:
        """多视角代码审查"""
        perspectives = [
            ("安全专家", "检查注入漏洞、权限控制、敏感数据泄露、依赖安全"),
            ("性能架构师", "检查N+1查询、缓存策略、内存泄漏、算法复杂度"),
            ("代码质量", "检查命名规范、SOLID原则、错误处理、圈复杂度"),
        ]
        findings = []
        for role, focus in perspectives:
            ctx = (
                f"你是{role}，专注{language}代码的{focus}。审查以下代码，输出JSON:\n"
                f'{{"issues":[{{"severity":"high|medium|low","line":"描述位置",'
                f'"description":"问题描述","fix":"修复建议"}}],"score":7.5}}\n\n'
                f"代码:\n{code[:4000]}"
            )
            try:
                data = self.fast_llm.cached_call_json(ctx, temperature=0.1, max_tokens=1024)
                findings.append({"reviewer": role, **data})
            except Exception as e:
                findings.append({"reviewer": role, "error": str(e)})

        all_issues = []
        for f in findings:
            all_issues.extend(f.get("issues", []))
        high = [i for i in all_issues if i.get("severity") == "high"]
        mid = [i for i in all_issues if i.get("severity") == "medium"]
        low = [i for i in all_issues if i.get("severity") == "low"]
        avg_score = sum(f.get("score", 5) for f in findings) / max(len(findings), 1)

        return {
            "mode": "review",
            "language": language,
            "total_issues": len(all_issues),
            "high": high,
            "medium": mid,
            "low": low,
            "overall_score": round(avg_score, 1),
        }

    def decompose_task(self, task: str) -> dict:
        """多视角任务拆解"""
        perspectives = [
            ("产品经理", "关注用户故事、验收标准、优先级排序"),
            ("架构师", "关注技术依赖、模块边界、接口定义"),
            ("工程专家", "关注实现路径、风险预估、工时估算"),
        ]
        plans = []
        for role, focus in perspectives:
            ctx = (
                f"你是{role}，{focus}。将以下任务拆解为可执行的任务清单，输出JSON:\n"
                f'{{"tasks":[{{"id":1,"name":"任务名","description":"描述",'
                f'"estimated_hours":2,"priority":"high|medium|low",'
                f'"dependencies":[]}}],"total_hours":0}}\n\n'
                f"任务:\n{task}"
            )
            try:
                data = self.fast_llm.cached_call_json(ctx, temperature=0.1, max_tokens=2048)
                plans.append({"role": role, **data})
            except Exception as e:
                plans.append({"role": role, "error": str(e)})

        # 去重合并
        seen = set()
        merged_tasks = []
        for p in plans:
            for t in p.get("tasks", []):
                key = t.get("name", "")[:20]
                if key not in seen:
                    seen.add(key)
                    merged_tasks.append(t)

        total_hours = sum(t.get("estimated_hours", 0) for t in merged_tasks)
        mvp_tasks = [t for t in merged_tasks if t.get("priority") == "high"]

        return {
            "mode": "plan",
            "total_tasks": len(merged_tasks),
            "total_hours": total_hours,
            "mvp_tasks": len(mvp_tasks),
            "tasks": merged_tasks,
        }

    def diagnose(self, problem: str) -> dict:
        """多视角问题诊断"""
        perspectives = [
            ("用户操作链", "从用户操作角度分析问题可能出在哪个环节"),
            ("系统依赖链", "从系统组件依赖关系分析故障点"),
            ("数据流", "从数据流转和状态变化分析异常"),
        ]
        trees = []
        for angle, focus in perspectives:
            ctx = (
                f"你是故障诊断专家。从{angle}（{focus}）构建故障树，输出JSON:\n"
                f'{{"root_causes":[{{"hypothesis":"根因假设","probability":"high|medium|low",'
                f'"verification":"验证方法","fix":"修复方法","impact":"影响范围"}}],'
                f'"recommended_order":[1,2,3]}}\n\n'
                f"异常现象:\n{problem}"
            )
            try:
                data = self.fast_llm.cached_call_json(ctx, temperature=0.1, max_tokens=1536)
                trees.append({"angle": angle, **data})
            except Exception as e:
                trees.append({"angle": angle, "error": str(e)})

        return {
            "mode": "diagnose",
            "fault_trees": trees,
        }

    def evaluate_options(self, options: list, context: str = "") -> dict:
        """多维度技术选型评估"""
        ctx = (
            f"你是技术选型顾问。对以下候选方案从成本/性能/安全/维护四维评分，输出JSON:\n"
            f'{{"evaluations":[{{"option":"方案名","cost":8.0,"performance":7.5,'
            f'"security":9.0,"maintenance":8.5,"total":0,'
            f'"pros":["优点"],"cons":["缺点"]}}],"recommendation":"推荐理由"}}\n\n'
            f"候选方案: {', '.join(options)}\n"
            f"背景: {context or '通用技术选型'}"
        )
        try:
            data = self.fast_llm.cached_call_json(ctx, temperature=0.1, max_tokens=2048)
            # 计算加权总分
            for ev in data.get("evaluations", []):
                ev["total"] = round(
                    ev.get("cost", 5) * 0.3 +
                    ev.get("performance", 5) * 0.25 +
                    ev.get("security", 5) * 0.25 +
                    ev.get("maintenance", 5) * 0.2, 1
                )
            data["evaluations"].sort(key=lambda x: x.get("total", 0), reverse=True)
            data["mode"] = "choose"
            return data
        except Exception as e:
            return {"mode": "choose", "error": str(e)}

    def scout(self, project_path: str, files: list = None) -> dict:
        """多视角项目侦察"""
        import glob as _glob
        project_dir = Path(project_path)
        if not project_dir.exists():
            return {"mode": "scout", "error": f"项目路径不存在: {project_path}"}

        # 自动发现关键文件
        if not files:
            key_patterns = [
                "**/*.py", "**/*.js", "**/*.ts", "**/*.go", "**/*.rs",
                "**/package.json", "**/requirements.txt", "**/go.mod",
                "**/Cargo.toml", "**/Dockerfile", "**/docker-compose*.yml",
                "**/*.config.*", "**/.env*",
            ]
            found = set()
            for pat in key_patterns:
                for f in _glob.glob(str(project_dir / pat), recursive=True):
                    fpath = Path(f)
                    # 跳过忽略的目录
                    if any(skip in str(fpath) for skip in [
                        "node_modules", ".git", "__pycache__", ".venv", "venv",
                        "dist", "build", ".next", "target",
                    ]):
                        continue
                    # 限制文件大小
                    if fpath.stat().st_size < 102400:
                        found.add(str(fpath.relative_to(project_dir)))
            files = sorted(found)[:30]

        # 读取文件内容
        snippets = []
        for fname in files[:20]:
            fpath = project_dir / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")[:2000]
                snippets.append(f"--- {fname} ---\n{content}")
            except Exception:
                pass

        code_sample = "\n\n".join(snippets)[:12000]

        perspectives = [
            ("业务/UX", "分析用户流程、交互模式、API设计"),
            ("架构", "分析技术栈、模块结构、依赖关系、数据流"),
            ("安全/质量", "分析认证授权、安全配置、代码质量"),
        ]
        findings = []
        for role, focus in perspectives:
            ctx = (
                f"你是{role}专家。审查以下项目文件，{focus}，输出JSON:\n"
                f'{{"tech_stack":["发现的技术"],"strengths":["优势"],'
                f'"risks":["风险"],"suggestions":["建议"],'
                f'"key_files":["关键文件路径"]}}\n\n'
                f"项目: {project_path}\n文件内容:\n{code_sample}"
            )
            try:
                data = self.fast_llm.cached_call_json(ctx, temperature=0.1, max_tokens=1536)
                findings.append({"perspective": role, **data})
            except Exception as e:
                findings.append({"perspective": role, "error": str(e)})

        return {
            "mode": "scout",
            "project_path": str(project_path),
            "files_analyzed": len(files),
            "findings": findings,
        }

    # ==================== v0.4.0: 会话分支与合并 ====================

    def branch(self, parent_task_id: str, branch_point: str,
               direction: str = "") -> str:
        """从现有任务分叉出一个新分支，探索替代方案

        Args:
            parent_task_id: 父任务ID
            branch_point: 分叉点（步骤名或 'top1' / 'alternative'）
            direction: 替代方向描述。为空时自动从废案中选择最优废案

        Returns:
            新分支的 task_id
        """
        parent = self._load_state(parent_task_id)
        if parent is None and parent_task_id in self._states:
            parent = self._states[parent_task_id]
        if parent is None:
            return ""

        import uuid as _uuid
        branch_id = f"task_{_uuid.uuid4().hex[:12]}"

        # 复制父任务的核心结构
        branch_state = OrchestratorState(
            task_id=branch_id,
            original_task=parent.original_task,
            step_list=parent.step_list,
            max_rounds=parent.max_rounds,
        )

        # 如果指定了方向，用该方向；否则从未选中的方案中找最佳废案
        alt_task = parent.original_task
        if direction:
            alt_task = f"{parent.original_task}\n\n[分支探索方向]: {direction}"
        else:
            # 找非 Top1 中得分最高的方案
            non_top1 = [
                v for v in parent.vote_results
                if v.get("rank", 99) > 1
            ]
            non_top1.sort(key=lambda x: x.get("total_score", 0), reverse=True)
            if non_top1:
                best_alt = non_top1[0]
                alt_sid = best_alt.get("plan_id", "")
                alt_scheme = parent.schemes.get(alt_sid, {})
                alt_summary = alt_scheme.get("integrated_content", "")[:300]
                alt_task = (
                    f"{parent.original_task}\n\n"
                    f"[分支探索]: 以下为之前被淘汰的备选方案思路，"
                    f"请以此为基础重新设计:\n{alt_summary}"
                )

        branch_state.original_task = alt_task
        branch_state.phase = "branched"
        self._states[branch_id] = branch_state
        self._save_state(branch_state)

        # 记录分支关系
        branch_meta_path = self.data_dir / "states" / f"{branch_id}_meta.json"
        with open(branch_meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "parent_task_id": parent_task_id,
                "branch_point": branch_point,
                "direction": direction,
                "created_at": datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)

        return branch_id

    def merge_branches(self, task_ids: list, strategy: str = "best_per_step") -> dict:
        """合并多个分支的方案，提取每个环节的最优设计

        Args:
            task_ids: 要合并的任务ID列表
            strategy: 合并策略
                - "best_per_step": 每个步骤选最高分方案的设计
                - "vote": 让 Agent 投票决定每个步骤选哪个方案
                - "combine": 将所有方案的设计拼接在一起

        Returns:
            合并后的方案数据
        """
        all_states = []
        for tid in task_ids:
            state = self._load_state(tid)
            if state is None and tid in self._states:
                state = self._states[tid]
            if state:
                all_states.append(state)

        if len(all_states) < 2:
            return {"error": "需要至少2个有效任务进行合并", "merged_steps": []}

        # 收集所有步骤和方案
        merged_steps = []
        all_steps_map = {}
        for state in all_states:
            for step in state.step_list:
                name = step.get("name", "")
                if name not in all_steps_map:
                    all_steps_map[name] = []
                for sid, scheme in state.schemes.items():
                    design = scheme.get("steps", {}).get(step.get("id", ""), "")
                    if design:
                        score = 0
                        for v in state.vote_results:
                            if v.get("plan_id") == sid:
                                score = v.get("total_score", 0)
                                break
                        all_steps_map[name].append({
                            "task_id": state.task_id,
                            "scheme_id": sid,
                            "object_name": scheme.get("object_name", ""),
                            "design": design[:500],
                            "score": score,
                        })

        for step_name, options in all_steps_map.items():
            options.sort(key=lambda x: x["score"], reverse=True)
            best = options[0]
            merged_steps.append({
                "name": step_name,
                "best_design": best["design"],
                "source_scheme": f"{best['scheme_id']} ({best['object_name']})",
                "source_task": best["task_id"],
                "score": best["score"],
                "alternatives": len(options) - 1,
            })

        return {
            "strategy": strategy,
            "source_tasks": task_ids,
            "merged_steps": merged_steps,
            "summary": (
                f"合并了 {len(task_ids)} 个分支的 {len(merged_steps)} 个环节，"
                f"每个环节选取得分最高的方案设计。"
            ),
        }

    # ==================== v0.4.0: 废案资产库与语义检索 ====================

    def _index_scheme_assets(self, state: OrchestratorState):
        """编排完成后，将所有方案按关键词索引到资产库"""
        asset_dir = self.data_dir / "asset_library"
        asset_dir.mkdir(parents=True, exist_ok=True)
        index_path = asset_dir / "index.json"

        # 加载现有索引
        index = {}
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
            except Exception:
                index = {}

        # 提取关键词
        task_lower = state.original_task.lower()
        keywords = []
        kw_patterns = [
            ("api", "API设计"), ("微服务", "微服务"), ("microservice", "微服务"),
            ("数据库", "数据库"), ("database", "数据库"), ("sql", "SQL"),
            ("安全", "安全"), ("security", "安全"), ("前端", "前端"),
            ("frontend", "前端"), ("部署", "部署"), ("deploy", "部署"),
            ("docker", "Docker"), ("缓存", "缓存"), ("cache", "缓存"),
            ("redis", "Redis"), ("消息", "消息队列"), ("queue", "消息队列"),
            ("认证", "认证"), ("auth", "认证"), ("扩展", "扩展性"),
            ("scale", "扩展性"), ("高并发", "高并发"), ("博客", "博客"),
            ("blog", "博客"), ("后台", "后台管理"), ("admin", "后台管理"),
            ("移动", "移动端"), ("mobile", "移动端"), ("实时", "实时"),
            ("realtime", "实时"), ("搜索", "搜索"), ("search", "搜索"),
            ("支付", "支付"), ("payment", "支付"), ("短链接", "短链接"),
            ("cdn", "CDN"), ("监控", "监控"), ("monitoring", "监控"),
            ("文件", "文件处理"), ("上传", "文件上传"), ("upload", "文件上传"),
            ("分享", "分享服务"), ("share", "分享服务"), ("下载", "文件下载"),
            ("download", "文件下载"), ("存储", "存储"), ("storage", "存储"),
            ("链接", "链接管理"), ("待办", "待办事项"), ("todo", "待办事项"),
            ("crud", "CRUD"), ("增删改查", "CRUD"),
        ]
        for pattern, tag in kw_patterns:
            if pattern in task_lower and tag not in keywords:
                keywords.append(tag)

        # 为每个方案创建资产条目
        for sid, scheme in state.schemes.items():
            entry_key = f"{state.task_id}_{sid}"
            vote = next(
                (v for v in state.vote_results if v.get("plan_id") == sid),
                None,
            )
            entry = {
                "task_id": state.task_id,
                "scheme_id": sid,
                "task": state.original_task[:200],
                "keywords": keywords,
                "object_name": scheme.get("object_name", ""),
                "agent_role": scheme.get("agent_role", ""),
                "total_score": vote.get("total_score", 0) if vote else 0,
                "is_top1": (vote.get("rank", 99) == 1) if vote else False,
                "summary": scheme.get("integrated_content", "")[:500],
                "created_at": datetime.now().isoformat(),
            }
            index[entry_key] = entry

        # 保存索引
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        # 保存完整方案副本
        for sid, scheme in state.schemes.items():
            scheme_path = asset_dir / f"{state.task_id}_{sid}.json"
            scheme_data = {
                "task_id": state.task_id,
                "scheme_id": sid,
                "task": state.original_task,
                "keywords": keywords,
                "scheme": scheme,
                "vote": next(
                    (v for v in state.vote_results if v.get("plan_id") == sid),
                    None,
                ),
                "steps": state.step_list,
            }
            with open(scheme_path, "w", encoding="utf-8") as f:
                json.dump(scheme_data, f, ensure_ascii=False, indent=2)

        print(f"  [资产库] 索引了 {len(state.schemes)} 个方案 (关键词: {keywords})")
        return keywords

    def search_assets(self, query: str, top_k: int = 5) -> list:
        """语义检索资产库中的历史方案"""
        asset_dir = self.data_dir / "asset_library"
        index_path = asset_dir / "index.json"
        if not index_path.exists():
            return []

        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

        query_lower = query.lower()
        results = []
        for key, entry in index.items():
            # 关键词匹配 + 任务描述匹配
            kw_match = sum(
                1 for kw in entry.get("keywords", [])
                if kw.lower() in query_lower
            )
            task_match = sum(
                1 for word in query_lower.split()
                if word in entry.get("task", "").lower()
            )
            summary_match = sum(
                1 for word in query_lower.split()
                if word in entry.get("summary", "").lower()
            )
            score = kw_match * 3 + task_match * 2 + summary_match * 1
            if score > 0:
                results.append({
                    "key": key,
                    "score": score,
                    "task": entry.get("task", ""),
                    "scheme_id": entry.get("scheme_id", ""),
                    "object_name": entry.get("object_name", ""),
                    "keywords": entry.get("keywords", []),
                    "total_score": entry.get("total_score", 0),
                    "is_top1": entry.get("is_top1", False),
                    "summary": entry.get("summary", "")[:200],
                    "created_at": entry.get("created_at", ""),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    # ==================== v3.2: 反馈回路 ====================

    def refine(self, task_id: str, modifications: List[dict]) -> dict:
        """用户提交修改建议后，各 Agent 独立审查并给出反馈

        Args:
            task_id: 已有任务 ID
            modifications: [{"step_name": "...", "suggestion": "..."}, ...]

        Returns:
            {"feedback_matrix": [...], "auto_applied": [...], "needs_review": [...]}
        """
        state = self._load_state(task_id)
        if state is None and task_id in self._states:
            state = self._states[task_id]
        if state is None:
            return {"error": f"任务不存在: {task_id}"}

        feedback_matrix = []
        auto_applied = []
        needs_review = []
        modified_schemes = set()  # 追踪哪些方案被修改了

        for mod in modifications:
            step_name = mod.get("step_name", "")
            suggestion = mod.get("suggestion", "")
            agent_verdicts = []

            for agent in self.agents:
                ctx = (
                    f"角色: {agent.object_name}代言人\n"
                    f"原任务: {state.original_task}\n"
                    f"用户对环节「{step_name}」提出修改建议:\n"
                    f"「{suggestion}」\n\n"
                    f"请审查此建议并输出 JSON:\n"
                    f'{{"verdict":"认可"|"有隐患"|"高创新性",'
                    f'"reason":"具体理由(50字以内)"}}\n'
                )
                data = self.fast_llm.cached_call_json(
                    ctx, temperature=0.1, max_tokens=512)
                agent_verdicts.append({
                    "agent": agent.object_name,
                    "verdict": data.get("verdict", "有隐患"),
                    "reason": data.get("reason", ""),
                })

            feedback_matrix.append({
                "step_name": step_name,
                "suggestion": suggestion,
                "verdicts": agent_verdicts,
            })

            if all(v["verdict"] == "认可" for v in agent_verdicts):
                auto_applied.append(step_name)
                target_id = None
                for s in state.step_list:
                    if s.get("name") == step_name:
                        target_id = s.get("id")
                        break
                if not target_id:
                    # 步骤名不存在于方案中，无法合并
                    continue
                for sid, scheme in state.schemes.items():
                    if target_id in scheme.get("steps", {}):
                        scheme["steps"][target_id] = (
                            scheme["steps"][target_id] +
                            f"\n\n[用户修改（全票通过）]: {suggestion}"
                        )
                        modified_schemes.add(sid)
            else:
                needs_review.append(step_name)

        # 重新整合：对改过的方案重跑 Owner 集成
        updated_plan = ""
        if modified_schemes:
            steps = [Step.from_dict(s) for s in state.step_list]
            for sid in modified_schemes:
                scheme = state.schemes[sid]
                owner = self._find_agent_by_id(scheme.get("owner_agent_id", ""))
                if owner is None:
                    owner = self.agents[0]
                scheme_obj = PlanScheme(
                    id=sid,
                    agent_role=scheme.get("agent_role", ""),
                    agent_name=scheme.get("agent_name", ""),
                    object_name=scheme.get("object_name", ""),
                    steps=scheme.get("steps", {}),
                    owner_agent_id=scheme.get("owner_agent_id", ""),
                )
                integrated = owner.owner_integration(
                    state.original_task, scheme_obj, steps,
                )
                scheme["integrated_content"] = integrated
                updated_plan = integrated

        # 重新渲染输出文件
        output_paths = {}
        try:
            output_paths = self._render_outputs(state)
            state.output_paths = output_paths
            self._save_state(state)
        except Exception as e:
            output_paths = {"error": str(e)}
            print(f"[refine] 渲染失败: {e}")

        result = {
            "task_id": task_id,
            "feedback_matrix": feedback_matrix,
            "auto_applied": auto_applied,
            "needs_review": needs_review,
            "output_files": output_paths,
        }
        if updated_plan:
            result["updated_plan"] = updated_plan[:3000]
            result["note"] = "auto_applied 的修改已写入方案并重新渲染。updated_plan 为 Top1 方案的最新内容，可据此继续工作。"
        return result

    @staticmethod
    def _try_reasonix_key() -> str:
        """自动读取 Reasonix 已配置的 API Key，实现零配置启动"""
        reasonix_config = Path.home() / ".reasonix" / "config.json"
        if reasonix_config.exists():
            try:
                with open(reasonix_config, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("apiKey", "")
            except Exception:
                pass
        return ""

    def _create_adapter(self, key: str) -> BaseLLMAdapter:
        cfg = self.config["llm"].get(key, self.config["llm"]["strong"])
        provider = cfg.get("provider", "deepseek")

        # MCP Sampling 委托模式：LLM 调用委托宿主
        if (provider == "mcp_sampling"
                or os.environ.get("COLLUSION_SAMPLING_MODE") == "1"
                or self.config.get("sampling", {}).get("enabled", False)):
            from src.llm.mcp_sampling import MCPSamplingAdapter
            return MCPSamplingAdapter(
                model=cfg.get("model", "host-default"),
                base_url=cfg.get("base_url", ""),
            )

        # Key 解析链: config.json → 多环境变量 → Reasonix 配置 → 到 DeepSeekAdapter 内部再解析
        api_key = cfg.get("api_key", "")
        if not api_key:
            # 按优先级检查多个环境变量名
            for env_name in ["DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"]:
                api_key = os.environ.get(env_name, "")
                if api_key:
                    break
        # 零配置回退：自动读取 Reasonix 已存的 API Key
        if not api_key:
            api_key = self._try_reasonix_key()
        if provider == "deepseek":
            return DeepSeekAdapter(
                api_key=api_key,  # 允许为空，DeepSeekAdapter 内部还会再查一次
                model=cfg.get("model", "deepseek-chat"),
                base_url=cfg.get("base_url", "https://api.deepseek.com/v1"),
            )
        raise ValueError(f"Unsupported LLM provider: {provider}")

    def _update_phase(self, state: OrchestratorState, phase: OrchestratorPhase):
        state.phase = phase.value

    def _find_own_plan(self, schemes: Dict[str, PlanScheme],
                       role: str) -> Optional[PlanScheme]:
        for plan in schemes.values():
            if plan.agent_role == role:
                return plan
        return None

    def _find_agent(self, role: AgentRole) -> Optional[OrchestratorAgent]:
        for agent in self.agents:
            if agent.role == role:
                return agent
        return None

    def _find_agent_by_id(self, agent_id: str) -> Optional[OrchestratorAgent]:
        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        return None

    @staticmethod
    def _index_to_step_id(steps: List[Step], index: int) -> Optional[str]:
        for s in steps:
            if s.index == index:
                return s.id
        return None

    def _save_state(self, state: OrchestratorState):
        state.total_tokens = (self.strong_llm.total_input_tokens
                              + self.strong_llm.total_output_tokens
                              + self.fast_llm.total_input_tokens
                              + self.fast_llm.total_output_tokens)
        state.total_cost_rmb = self.strong_llm.total_cost_rmb + self.fast_llm.total_cost_rmb

        # v3.1: 轻量指标日志
        self._log_metrics(state)

        state_dir = self.data_dir / "states"
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / f"{state.task_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)

    def _log_metrics(self, state: OrchestratorState):
        """输出轻量级代理指标，用于提前判断方案质量"""
        schemes = state.schemes
        if not schemes:
            return

        # 方案多样性: 计算方案间文本相似度 (1 - 平均相似度)
        diversity = self._calc_diversity(schemes)

        # 环节覆盖率: 每个方案覆盖的环节比例
        total_steps = len(state.step_list)
        coverages = {}
        for sid, scheme in schemes.items():
            covered = sum(1 for s in state.step_list
                         if s.get("id", "") in scheme.get("steps", {}))
            coverages[sid] = round(covered / max(total_steps, 1), 2)

        # 复杂度
        complexities = state.scheme_complexity or {
            sid: scheme.get("complexity_score", 0)
            for sid, scheme in schemes.items()
        }

        # 输出
        phase = state.phase.split("_")[-1][:12]
        print(f"  [指标|{phase}] "
              f"多样性={diversity:.2f} "
              f"覆盖率={coverages} "
              f"复杂度={complexities} "
              f"成本=Y{state.total_cost_rmb:.4f} "
              f"Token={state.total_tokens}")

    @staticmethod
    def _calc_diversity(schemes: Dict) -> float:
        """计算方案间文本多样性 (0=完全相同, 1=完全不同)"""
        if len(schemes) < 2:
            return 0.0
        texts = []
        for scheme in schemes.values():
            parts = []
            for content in scheme.get("steps", {}).values():
                parts.append(content)
            texts.append(" ".join(parts))

        # 简化Jaccard: 词汇级相似度
        def word_sim(a, b):
            wa = set(a[:2000].split())
            wb = set(b[:2000].split())
            if not wa or not wb:
                return 0.0
            return len(wa & wb) / len(wa | wb)

        sims = []
        ids = list(schemes.keys())
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                sims.append(word_sim(texts[i], texts[j]))

        if not sims:
            return 0.5
        return round(1.0 - sum(sims) / len(sims), 2)

    def _load_state(self, task_id: str) -> Optional[OrchestratorState]:
        path = self.data_dir / "states" / f"{task_id}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return OrchestratorState.from_dict(json.load(f))
        return None
