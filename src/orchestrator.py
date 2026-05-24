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

        # v0.5.0: 知识库配置
        from src.prompts import DEFAULT_KNOWLEDGE_CONFIG
        knowledge_cfg = self.config.get("knowledge", {})
        self.knowledge_config = {**DEFAULT_KNOWLEDGE_CONFIG, **knowledge_cfg}

        # v0.7.0: 向量语义索引（零新依赖）
        self.vector_index = None
        self._enable_vector = self.knowledge_config.get("enable_vector_search", False)

        # v0.8.0: MAGE 自进化引擎
        self.evolution = None
        self._enable_evolution = self.knowledge_config.get("enable_evolution", True)
        if self._enable_evolution:
            try:
                from src.evolution import EvolutionEngine
                self.evolution = EvolutionEngine(str(self.data_dir))
            except Exception:
                self.evolution = None

        # v1.0.0: Agent-as-a-Graph 知识图谱路由
        self.agent_graph = None
        self._enable_agent_graph = self.knowledge_config.get("enable_agent_graph", True)
        if self._enable_agent_graph:
            try:
                from src.agent_graph import AgentGraph
                self.agent_graph = AgentGraph(str(self.data_dir))
            except Exception:
                self.agent_graph = None

        # v1.3-v1.7: 验证门 + 蓝图 + 审查记忆 + 记忆巩固
        self.verification_gate = None
        self.task_graph_store = None
        self.review_memory = None
        self.memory_consolidation = None
        if self.knowledge_config.get("enable_advanced", True):
            try:
                from src.verification import (VerificationGate, TaskGraphStore,
                                               ReviewMemory, MemoryConsolidation)
                self.verification_gate = VerificationGate
                self.task_graph_store = TaskGraphStore(str(self.data_dir))
                self.review_memory = ReviewMemory(str(self.data_dir))
                self.memory_consolidation = MemoryConsolidation(str(self.data_dir))
            except Exception:
                pass

        # v1.5-v2.0: Agent 能力自评与自组织进化
        self.agent_evolution = None
        if self.knowledge_config.get("enable_agent_evolution", True):
            try:
                from src.agent_evolution import AgentEvolutionEngine
                self.agent_evolution = AgentEvolutionEngine(str(self.data_dir))
            except Exception:
                self.agent_evolution = None

        # GoalRunner: 自动化执行闭环
        self.goal_runner = None
        if self.knowledge_config.get("enable_goal_runner", True):
            try:
                from src.goal_runner import GoalRunner
                self.goal_runner = GoalRunner(
                    data_dir=str(self.data_dir),
                    on_success=lambda gid, desc: self._on_goal_success(gid, desc),
                )
            except Exception:
                self.goal_runner = None
        if self._enable_vector:
            try:
                from src.vector_index import VectorIndex
                self.vector_index = VectorIndex()
                vi_path = self.data_dir / "vector_index"
                if (vi_path / "metadata.json").exists():
                    self.vector_index.load(str(vi_path))
                    print(f"  [向量索引] 加载 {self.vector_index.size} 篇文档")
            except Exception:
                self.vector_index = None

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
        """v1.0.0: 使用 Agent-as-a-Graph 优化 Agent 选择"""
        # 先获取默认检测结果
        if preset == "auto":
            role_ids, complexity = role_manager.detect(task, "standard")
            count = role_manager.get_agent_count(role_ids)
        elif preset in role_manager.presets:
            pc = role_manager.presets[preset]
            role_ids = pc["roles"]
            count = pc["agents"]
            complexity = 0
        else:
            role_ids = role_manager.detect(task, "standard")[0]
            count = len(role_ids)
            complexity = 0

        # v1.0.0: 如果 Agent 图有数据，用历史数据优化选择
        if self._enable_agent_graph and self.agent_graph is not None:
            try:
                # 提取任务标签
                task_lower = task.lower()
                kw_patterns = ["短链接", "微服务", "API", "数据库", "安全", "前端",
                               "后端", "高并发", "文件", "博客", "实时", "搜索"]
                task_tags = [kw for kw in kw_patterns if kw in task_lower]

                graph_roles = self.agent_graph.select_agents(
                    task_tags=task_tags,
                    available_roles=[r.value for r in DEFAULT_AGENT_ROLES],
                    top_k=count,
                )
                if graph_roles:
                    # 把前端 Agent 映射回 role_ids
                    role_map = {
                        "业务价值对象": "ux",
                        "技术架构对象": "performance",
                        "安全与合规对象": "security",
                    }
                    mapped = [role_map.get(r, r) for r in graph_roles]
                    if mapped:
                        role_ids = mapped[:count]
            except Exception:
                pass

        return role_ids, count, complexity

    def orchestrate(self, task: str, task_id: str = None, output_format: str = "md",
                    preset: str = "auto", mode: str = "design") -> str:
        """主入口：执行编排（v1.0.0: 支持 check/design 双模式）

        Args:
            task: 技术任务描述
            task_id: 预设任务 ID（异步模式用）
            output_format: 输出格式
            preset: Agent 预设
            mode: "check" = 只做预检（0.5ms）不编排
                  "design" = 完整编排（预检+注入+3Agent+归档）
        """
        if mode == "check":
            # 轻量模式：只做知识预检，不启动编排
            import uuid
            check_id = f"check_{uuid.uuid4().hex[:8]}"
            precheck = self.pre_check_knowledge(task)
            # 存到临时状态
            check_state = OrchestratorState(
                task_id=check_id, original_task=task, phase="precheck_done"
            )
            check_state.precheck_result = precheck
            self._states[check_id] = check_state
            return check_id
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

            # Phase 6.6: 因果记忆图记录
            try:
                self._record_causal_memory(state)
            except Exception as e:
                print(f"  [因果记忆] 记录失败（不阻塞主流程）: {e}")

            # Phase 6.7: Agent Graph 自动记录
            try:
                if self._enable_agent_graph and self.agent_graph is not None:
                    roles_used = [
                        a.get("object_name", a.get("agent_role", ""))
                        for a in state.agents
                    ]
                    # 提取任务标签
                    task_lower = state.original_task.lower()
                    kw_tags = [kw for kw in
                        ["短链接","微服务","API","数据库","安全","前端","后端",
                         "高并发","文件","博客","实时","搜索","Docker","部署"]
                        if kw.lower() in task_lower]
                    self.agent_graph.record_task(
                        task_id=state.task_id,
                        task_desc=state.original_task[:100],
                        roles=roles_used or ["业务价值对象","技术架构对象","安全与合规对象"],
                        success=True,
                        tags=kw_tags,
                    )
            except Exception as e:
                print(f"  [Agent图] 记录失败（不阻塞）: {e}")

            # Phase 6.8: 工作流蓝图保存 (v1.4)
            try:
                if self.task_graph_store is not None and state.steps:
                    blueprint = {
                        "steps": [
                            {"index": s.index, "name": s.name, "description": s.description}
                            for s in state.steps
                        ],
                        "task_desc": state.original_task[:200],
                    }
                    self.task_graph_store.save(
                        task_id=state.task_id,
                        task_desc=state.original_task[:200],
                        task_graph=blueprint,
                        tags=[t.get("value", "") for t in getattr(state, "all_tags", [])],
                    )
            except Exception:
                pass

            # Phase 6.9: 睡眠巩固 (v1.7)
            try:
                if self.memory_consolidation is not None:
                    self.memory_consolidation.sleep_consolidate()
            except Exception:
                pass

            # Phase 6.10: Agent 能力记录 (v1.5-v2.0 基础数据)
            try:
                if self.agent_evolution is not None:
                    # 提取任务标签
                    task_lower = state.original_task.lower()
                    task_tags = [kw for kw in
                        ["短链接","微服务","API","数据库","安全","前端","后端",
                         "高并发","文件","博客","实时","搜索","Docker","部署",
                         "认证","缓存","架构","选型"]
                        if kw.lower() in task_lower]

                    # 为每个Agent记录执行结果
                    for agent in self.agents:
                        top1_score = 0
                        if state.vote_results:
                            top1 = next((r for r in state.vote_results if r.get("rank")==1), None)
                            if top1:
                                top1_score = top1.get("total_score", 0)

                        self.agent_evolution.record_execution(
                            agent_id=agent.agent_id,
                            role=agent.role.value,
                            task_desc=state.original_task[:100],
                            success=top1_score > 7.0,
                            score=top1_score,
                            tags=task_tags,
                        )

                    # 记录团队组建
                    agent_roles = [a.role.value for a in self.agents]
                    self.agent_evolution.record_team(
                        task_desc=state.original_task[:100],
                        agents=agent_roles,
                        success=state.phase == "done",
                        task_tags=task_tags,
                    )
            except Exception:
                pass

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

            # v0.6: GoalRunner 自动触发 — 方案→执行闭环
            if self.goal_runner is not None:
                try:
                    goal_config = self._generate_goal_config(state)
                    if goal_config and goal_config.get("goal_id"):
                        from src.goal_runner import GoalConfig
                        cfg = GoalConfig.from_dict(goal_config)
                        gid = self.goal_runner.start_goal(cfg)
                        print(f"  [闭环] GoalRunner 自动启动: {gid}")
                        state.output_paths["goal_id"] = gid
                        self._save_state(state)
                except Exception as e:
                    print(f"  [闭环] GoalRunner 启动失败（不阻塞）: {e}")

        except Exception as e:
            self._update_phase(state, OrchestratorPhase.ERROR)
            state.error_message = str(e)
            self._save_state(state)

        return state.task_id

    # ==================== v0.6: 决策评估 (轻量入口) ====================

    def assess(self, task: str, task_id: str = "",
               deep: str = "auto", strict_mode: bool = False) -> dict:
        """薄适配器: 检索→压缩→检查点链→DecisionCard

        不写任何检查逻辑。所有逻辑在 CheckpointEngine 内。

        Args:
            task: 技术任务描述
            task_id: 任务ID（可选）
            deep: "auto"(默认) / "force" / "never"
            strict_mode: True = warning 升级为 blocking

        Returns:
            {"task_id": str, "decision_card": dict, "mode": str, ...}
        """
        import uuid as _uuid
        from src.checkpoint.engine import create_engine
        from src.checkpoint.knowledge_retriever import KnowledgeRetriever
        from src.checkpoint.situation_compressor import SituationCompressor

        if not task_id:
            task_id = f"assess_{_uuid.uuid4().hex[:8]}"

        try:
            # 1. 检索
            retriever = KnowledgeRetriever(orchestrator=self)
            retrieved = retriever.retrieve(
                task=task, task_id=task_id,
                top_k=self.knowledge_config.get("asset_retrieval_top_k", 3),
                max_age_months=self.knowledge_config.get("discard_max_age_months", 6),
            )

            # 2. 压缩
            compressor = SituationCompressor(fast_llm=self.fast_llm)
            snapshot = compressor.compress(task, retrieved)

            # 3. 检查点引擎
            engine = create_engine(orchestrator=self)
            card = engine.run_light(
                snapshot=snapshot,
                strict_mode=strict_mode,
            )

            # 4. 深度模式判断
            mode = "light"
            if deep == "force" or (deep == "auto" and card.deep_review_recommended):
                mode = "deep"
                card = engine.run_deep(
                    snapshot=snapshot,
                    core_results=[
                        type('R', (), r)() if isinstance(r, dict)
                        else r
                        for r in card.checkpoint_results
                    ],
                    strict_mode=strict_mode,
                )

            # 5. 保存到状态（兼容 brainstorm_status / collusion_render 等工具）
            from src.models import OrchestratorState
            state = OrchestratorState(
                task_id=task_id,
                original_task=task,
                phase="done",
            )
            state_dict = state.to_dict()
            state_dict["decision_card"] = card.to_dict()
            state.decision_card = card.to_dict()  # type: ignore
            self._states[task_id] = state
            self._save_state(state)

            # 6. v0.6.1: 决策卡片归档到资产库 + 因果记忆
            try:
                self._archive_assess_result(task_id, task, card, snapshot)
            except Exception as e:
                print(f"  [归档] assess结果归档失败（不阻塞）: {e}")

            return {
                "task_id": task_id,
                "decision_card": card.to_dict(),
                "mode": mode,
                "llm_calls": card.total_llm_calls,
                "tokens_used": card.total_tokens,
                "deep_review_recommended": card.deep_review_recommended,
                "deep_review_reason": card.deep_review_reason,
            }

        except Exception as e:
            return {
                "task_id": task_id,
                "error": str(e),
                "decision_card": None,
            }

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

        # v1.0.0: check 模式直接返回预检结果
        if state_dict.get("phase") == "precheck_done" or task_id.startswith("check_"):
            precheck = state_dict.get("precheck_result")
            if precheck:
                return {
                    "task_id": task_id,
                    "mode": "check",
                    "task": state_dict.get("original_task", ""),
                    "phase": "precheck_done",
                    "precheck": precheck,
                    "tip": "关联度 >0.5 可参考历史方案。使用 mode=design 启动完整编排。",
                }

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
        """阶段3: 并行提案 (ThreadPoolExecutor, 兼容同步/异步环境)
        v0.5.0: 提案前自动注入知识上下文（如果有关联历史资产）
        """
        # 知识预检：检索关联历史资产
        knowledge_context = ""
        from src.prompts import SYSTEM_KNOWLEDGE_CONTEXT, DEFAULT_KNOWLEDGE_CONFIG
        try:
            precheck = self.pre_check_knowledge(state.original_task)
            relevant = precheck.get("relevant_assets", [])
            warnings = precheck.get("discarded_warnings", [])
            threshold = self.knowledge_config.get("relevance_threshold", 0.3)

            if relevant:
                entries_text = []
                for a in relevant[:3]:
                    score = a.get("relevance_score", 0)
                    if score >= threshold:
                        tag_str = ", ".join(a.get("keywords", [])[:4])
                        entries_text.append(
                            f"- [{score:.2f}] {a.get('task', '')[:80]} [{tag_str}]"
                        )
                warn_text = []
                for w in warnings[:2]:
                    reasons = "; ".join(w.get("discard_reasons", [])) or "被Top1方案淘汰"
                    warn_text.append(
                        f"- ⚠️ {w.get('task', '')[:60]} (原因: {reasons})"
                    )

                if entries_text:
                    knowledge_context = SYSTEM_KNOWLEDGE_CONTEXT.format(
                        relevance_threshold=threshold,
                        relevance_entries="\n".join(entries_text),
                        discarded_warnings="\n".join(warn_text) if warn_text else "无",
                    )
                    print(f"  [知识注入] 注入 {len(entries_text)} 条历史参考 + {len(warn_text)} 条废案警告")
        except Exception as e:
            print(f"  [知识注入] 跳过（不阻塞主流程）: {e}")

        from concurrent.futures import as_completed
        futures = {}
        for agent in self.agents:
            future = self._executor.submit(
                agent.generate_proposal,
                state.original_task,
                steps,
                knowledge_context,
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
        """工程实现对象代言人对每个方案进行现实检验

        v1.3: 添加收敛→整合验证门
        """
        records = []
        for scheme_id, scheme in schemes.items():
            brake_agent = self.engineering_agent or self.agents[0]
            result = brake_agent.feasibility_brake(
                state.original_task, scheme, steps, self.complexity_threshold,
            )
            # v1.3: 检查因果预警和复杂度
            if self.verification_gate is not None:
                gate_ctx = {
                    "causal_warnings": [],  # TODO: wire causal query
                    "complexity_score": scheme.complexity_score,
                    "max_complexity": self.complexity_threshold,
                }
                gate_result = self.verification_gate.check("converge_integrate", gate_ctx)
                if not gate_result["passed"]:
                    records.append(f"[验证门] {scheme_id}: {gate_result['checks']}")
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

    def _generate_goal_config(self, state: OrchestratorState) -> dict:
        """从 Top1 方案自动生成 GoalRunner 可直接消费的 Goal 配置 JSON (v1.3)

        从整合方案中提取文件路径、任务描述，生成结构化配置。
        用户可直接将返回的 JSON 传给 GoalRunner.create_goal()。
        """
        # 找 Top1 方案
        top1_sid = None
        if state.vote_results:
            top1 = next((r for r in state.vote_results if r.get("rank") == 1), None)
            if top1:
                top1_sid = top1.get("plan_id")

        if not top1_sid or top1_sid not in state.schemes:
            return {}

        scheme = state.schemes[top1_sid]
        integrated = scheme.get("integrated_content", "")

        # 从方案文本中提取文件路径（复用品牌锚点正则）
        import re
        file_patterns = re.findall(
            r'(?:(?:src|app|lib|components|pages|api|'
            r'routes|middleware|utils|config|models|services|'
            r'controllers|handlers|tests|docs|public|static|data|bin)/[\w/.-]+|'
            r'[\w/.-]+\.(?:tsx?|jsx?|py|rs|go|java|'
            r'yml|yaml|json|toml|sql|css|html|sh|md|'
            r'dockerfile|env|cfg|ini|xml))',
            integrated
        )
        # 去重，限制最多 10 个文件路径
        seen = set()
        unique_files = []
        for f in file_patterns:
            if f not in seen and len(unique_files) < 10:
                seen.add(f)
                unique_files.append(f)

        # 推断 allowed_files（取公共前缀目录）
        allowed = []
        if unique_files:
            parts = unique_files[0].split("/")
            if len(parts) >= 2:
                allowed = ["/".join(parts[:2]) + "/"]

        # MVP 步骤的文件入口
        mvp_code_anchors = []
        for step in state.step_list[:3]:
            step_id = step.get("id", "")
            if step_id in scheme.get("steps", {}):
                code_files = re.findall(
                    r'(?:(?:src|app|lib)/[\w/.-]+)',
                    scheme["steps"][step_id]
                )
                mvp_code_anchors.extend(code_files[:2])
        mvp_allowed = list(dict.fromkeys(mvp_code_anchors))[:5] if mvp_code_anchors else allowed

        return {
            "goal_id": f"auto_{state.task_id}",
            "description": f"基于方案{top1_sid}的架构设计，实现：{state.original_task[:80]}",
            "verification": {
                "l1": {"command": "gradle build", "expected_exit_code": 0, "timeout_seconds": 300},
                "l2": {"command": "gradle test", "expected_exit_code": 0, "timeout_seconds": 300},
                "l3": {"command": "gradle runGameTest", "expected_exit_code": 0, "timeout_seconds": 600},
            },
            "constraints": {
                "allowed_files": mvp_allowed or ["src/"],
                "forbidden_files": [],
            },
            "max_iterations": 5,
        }

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
            # Goal 配置 JSON（v1.3 新增：从 Top1 方案自动生成可执行 Goal 配置）
            "goal_config": self._generate_goal_config(state),
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
        """多视角代码审查（结果自动归档到 asset_library）"""
        perspectives_def = [
            ("安全专家", "检查注入漏洞、权限控制、敏感数据泄露、依赖安全"),
            ("性能架构师", "检查N+1查询、缓存策略、内存泄漏、算法复杂度"),
            ("代码质量", "检查命名规范、SOLID原则、错误处理、圈复杂度"),
        ]
        findings = []
        raw_perspectives = []
        for role, focus in perspectives_def:
            ctx = (
                f"你是{role}，专注{language}代码的{focus}。审查以下代码，输出JSON:\n"
                f'{{"issues":[{{"severity":"high|medium|low","line":"描述位置",'
                f'"description":"问题描述","fix":"修复建议"}}],"score":7.5}}\n\n'
                f"代码:\n{code[:4000]}"
            )
            try:
                data = self.fast_llm.cached_call_json(ctx, temperature=0.1, max_tokens=1024)
                findings.append({"reviewer": role, **data})
                raw_perspectives.append({"role": role, "raw_data": data})
            except Exception as e:
                findings.append({"reviewer": role, "error": str(e)})
                raw_perspectives.append({"role": role, "raw_data": {"error": str(e)}})

        all_issues = []
        for f in findings:
            all_issues.extend(f.get("issues", []))
        high = [i for i in all_issues if i.get("severity") == "high"]
        mid = [i for i in all_issues if i.get("severity") == "medium"]
        low = [i for i in all_issues if i.get("severity") == "low"]
        avg_score = sum(f.get("score", 5) for f in findings) / max(len(findings), 1)

        merged = {
            "mode": "review",
            "language": language,
            "total_issues": len(all_issues),
            "high": high,
            "medium": mid,
            "low": low,
            "overall_score": round(avg_score, 1),
        }
        # v0.7.0: 自动归档
        try:
            task_id = self._save_tool_result(
                "review", f"[{language}] {code[:200]}", raw_perspectives, merged)
            merged["task_id"] = task_id
        except Exception:
            pass
        return merged

    def decompose_task(self, task: str) -> dict:
        """多视角任务拆解（结果自动归档到 asset_library）"""
        perspectives_def = [
            ("产品经理", "关注用户故事、验收标准、优先级排序"),
            ("架构师", "关注技术依赖、模块边界、接口定义"),
            ("工程专家", "关注实现路径、风险预估、工时估算"),
        ]
        plans = []
        raw_perspectives = []
        for role, focus in perspectives_def:
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
                raw_perspectives.append({"role": role, "raw_data": data})
            except Exception as e:
                plans.append({"role": role, "error": str(e)})
                raw_perspectives.append({"role": role, "raw_data": {"error": str(e)}})

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

        merged = {
            "mode": "plan",
            "total_tasks": len(merged_tasks),
            "total_hours": total_hours,
            "mvp_tasks": len(mvp_tasks),
            "tasks": merged_tasks,
        }
        # v0.7.0: 自动归档
        try:
            task_id = self._save_tool_result(
                "plan", task, raw_perspectives, merged)
            merged["task_id"] = task_id
        except Exception:
            pass
        return merged

    def diagnose(self, problem: str) -> dict:
        """多视角问题诊断（结果自动归档到 asset_library）"""
        perspectives_def = [
            ("用户操作链", "从用户操作角度分析问题可能出在哪个环节"),
            ("系统依赖链", "从系统组件依赖关系分析故障点"),
            ("数据流", "从数据流转和状态变化分析异常"),
        ]
        trees = []
        raw_perspectives = []
        for angle, focus in perspectives_def:
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
                raw_perspectives.append({"role": angle, "raw_data": data})
            except Exception as e:
                trees.append({"angle": angle, "error": str(e)})
                raw_perspectives.append({"role": angle, "raw_data": {"error": str(e)}})

        merged = {
            "mode": "diagnose",
            "fault_trees": trees,
        }
        # v0.7.0: 自动归档
        try:
            task_id = self._save_tool_result(
                "diagnose", problem, raw_perspectives, merged)
            merged["task_id"] = task_id
        except Exception:
            pass
        return merged

    # ==================== 工具结果持久化 (v0.7.0) ====================

    def _archive_assess_result(self, task_id: str, task: str,
                               card, snapshot) -> str:
        """v0.6.1: 将 assess() 的决策卡片归档到资产库 + 因果记忆

        与 orchestrate() 的归档互补：assess 归档轻量评估结果，
        orchestrate 归档完整方案。
        """
        from datetime import datetime

        # 1. 资产库索引
        asset_dir = self.data_dir / "asset_library"
        asset_dir.mkdir(parents=True, exist_ok=True)

        keywords = self._extract_keywords_fallback(task)
        index_path = asset_dir / "index.json"
        index = {}
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
            except Exception:
                pass

        # 保存决策卡片摘要
        card_summary = (
            card.suggested_approach[:300] if hasattr(card, 'suggested_approach')
            else str(card.to_dict().get('suggested_approach', ''))[:300]
        )
        index[f"{task_id}_assess"] = {
            "task_id": task_id,
            "scheme_id": "assess",
            "task": task[:200],
            "keywords": keywords,
            "tags": [],
            "object_name": "决策评估",
            "agent_role": "CheckpointEngine",
            "total_score": card.overall_confidence if hasattr(card, 'overall_confidence') else 0.5,
            "is_top1": True,
            "is_discarded": False,
            "discard_reasons": [],
            "rank": 1,
            "summary": card_summary[:500],
            "created_at": datetime.now().isoformat(),
        }
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        # 保存完整决策卡片
        card_path = asset_dir / f"{task_id}_assess.json"
        with open(card_path, "w", encoding="utf-8") as f:
            json.dump(card.to_dict(), f, ensure_ascii=False, indent=2)

        # 2. 因果记忆记录
        if self.knowledge_config.get("enable_causal_memory", True):
            causal_dir = self.data_dir / "causal_memory"
            causal_dir.mkdir(parents=True, exist_ok=True)

            # 记录检查点发现作为因果节点
            pending = {
                "task_id": task_id,
                "task": task[:200],
                "checkpoint_results": card.checkpoint_results if hasattr(card, 'checkpoint_results') else [],
                "overall_risk": card.overall_risk if hasattr(card, 'overall_risk') else "unknown",
                "confidence": card.overall_confidence if hasattr(card, 'overall_confidence') else 0.0,
                "status": "pending_analysis",
                "created_at": datetime.now().isoformat(),
            }
            with open(causal_dir / f"pending_assess_{task_id}.json", "w", encoding="utf-8") as f:
                json.dump(pending, f, ensure_ascii=False, indent=2)

        n_pitfalls = len(card.pitfalls) if hasattr(card, 'pitfalls') else 0
        print(f"  [归档] assess → {task_id} "
              f"(风险:{card.overall_risk}, 坑点:{n_pitfalls}, "
              f"检查点:{len(getattr(card, 'checkpoints_run', []))})")
        return task_id

    def _save_tool_result(self, tool_name: str, input_text: str,
                          perspectives: List[dict], merged_result: dict) -> str:
        """将 review/diagnose/plan 结果持久化到磁盘。

        保存路径:
          data/states/{task_id}.json          — 会话状态
          data/asset_library/{task_id}_{A/B/C}.json — 三Agent原始输出
          data/outputs/{task_id}/report.md    — 合并报告
          data/asset_library/index.json       — 增量更新索引

        废案判定: 三Agent各自独立输出均保存为变体。
          排名第1的Agent变体标记为 is_top1。
          其余变体标记为 is_discarded=True（废案备选）。
        """
        import uuid
        task_id = f"{tool_name}_{uuid.uuid4().hex[:12]}"

        # 1. 保存会话状态
        state_dir = self.data_dir / "states"
        state_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "task_id": task_id,
            "tool": tool_name,
            "input": input_text[:500],
            "perspectives": [p.get("role", "") for p in perspectives],
            "merged_result": merged_result,
            "created_at": datetime.now().isoformat(),
        }
        with open(state_dir / f"{task_id}.json", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        # 2. 增量更新资产库索引
        asset_dir = self.data_dir / "asset_library"
        asset_dir.mkdir(parents=True, exist_ok=True)
        index_path = asset_dir / "index.json"
        index = {}
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
            except Exception:
                index = {}

        keywords = self._extract_keywords_fallback(input_text)

        # 废案判定：第一个视角默认为主方案，其余为废案备选
        for i, p in enumerate(perspectives):
            sid = chr(65 + i)  # A, B, C
            entry_key = f"{task_id}_{sid}"
            role = p.get("role", p.get("angle", f"视角{i+1}"))

            # 保存原始输出副本
            agent_data = {
                "task_id": task_id,
                "scheme_id": sid,
                "tool": tool_name,
                "input": input_text[:200],
                "perspective": role,
                "result": p.get("raw_data", p),
            }
            with open(asset_dir / f"{task_id}_{sid}.json", "w", encoding="utf-8") as f:
                json.dump(agent_data, f, ensure_ascii=False, indent=2)

            is_discarded = (i > 0)
            index[entry_key] = {
                "task_id": task_id,
                "scheme_id": sid,
                "task": input_text[:200],
                "keywords": keywords,
                "object_name": role,
                "agent_role": role,
                "total_score": 0,
                "is_top1": (i == 0),
                "is_discarded": is_discarded,
                "discard_reasons": ["非主方案，保留为备选参考"] if is_discarded else [],
                "rank": i + 1,
                "summary": json.dumps(p.get("raw_data", p), ensure_ascii=False)[:500],
                "created_at": datetime.now().isoformat(),
                "success_outcome": None,
            }

        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        # 3. 保存合并报告
        output_dir = self.data_dir / "outputs" / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        report_lines = [
            f"# {tool_name} 报告",
            f"Task ID: `{task_id}`",
            f"创建时间: {datetime.now().isoformat()}",
            "",
            "## 输入",
            input_text[:2000],
            "",
            "## 参与视角",
        ]
        for p in perspectives:
            role = p.get("role", p.get("angle", "?"))
            report_lines.append(f"- {role}")
        report_lines += [
            "",
            "## 合并结果",
            "```json",
            json.dumps(merged_result, ensure_ascii=False, indent=2),
            "```",
        ]
        with open(output_dir / "report.md", "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))

        # v0.7.0: 尝试同步向量索引
        try:
            if getattr(self, '_enable_vector', False) and self.vector_index is not None:
                self._build_vector_index(full_index=index)
        except Exception:
            pass

        n_discarded = sum(1 for v in index.values()
                         if v.get("task_id") == task_id and v.get("is_discarded"))
        print(f"  [归档] {tool_name} → {task_id} "
              f"(3个变体, {n_discarded}个废案) → "
              f"states/ + asset_library/ + outputs/")
        return task_id

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

    # ==================== v0.5.0: 废案资产库 — 增强版（5维标签+关联度+废案警告） ====================

    def _extract_tags_llm(self, task: str, plan_text: str) -> List[dict]:
        """标签提取（v0.5.5: 不再主动调 LLM，由调用方通过 collusion_asset_tag 注入）
        保留此方法作为兼容接口，但返回空列表让调用方写入。
        """
        return []

    def _extract_keywords_fallback(self, task: str) -> List[str]:
        """关键词回退提取（兼容旧版和LLM失败时）"""
        task_lower = task.lower()
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
        return keywords

    def _build_yaml_frontmatter(self, entry: dict) -> str:
        """为资产条目生成 YAML frontmatter（方案E：渐进式元数据）"""
        tags = entry.get("tags", [])
        keywords = entry.get("keywords", [])
        # 从 tags 中按维度提取
        tech_stack = [t["value"] for t in tags if isinstance(t, dict) and t.get("dimension") == "技术栈"]
        domain = next((t["value"] for t in tags if isinstance(t, dict) and t.get("dimension") == "领域"), "")
        arch = next((t["value"] for t in tags if isinstance(t, dict) and t.get("dimension") == "架构模式"), "")
        security = [t["value"] for t in tags if isinstance(t, dict) and t.get("dimension") == "安全关注点"]
        perf = [t["value"] for t in tags if isinstance(t, dict) and t.get("dimension") == "性能特征"]

        parts = ["---"]
        parts.append(f"name: {entry.get('task_id', '')}_{entry.get('scheme_id', '')}")
        parts.append(f"description: {entry.get('task', '')[:120]}")
        if tech_stack:
            parts.append(f"tech_stack: [{', '.join(tech_stack)}]")
        if domain:
            parts.append(f"domain: {domain}")
        if arch:
            parts.append(f"arch_pattern: {arch}")
        if security:
            parts.append(f"security_focus: [{', '.join(security)}]")
        if perf:
            parts.append(f"performance: [{', '.join(perf)}]")
        if keywords:
            parts.append(f"keywords: [{', '.join(keywords)}]")
        parts.append(f"created_at: {entry.get('created_at', '')}")
        parts.append("---")
        return "\n".join(parts)

    def _migrate_old_entry(self, entry: dict) -> dict:
        """将旧版 index.json 条目迁移到新版 5 维标签格式"""
        if "tags" in entry and entry["tags"]:
            return entry  # 已经是最新版
        keywords = entry.get("keywords", [])
        tags = []
        # 从旧 keywords 推断标签维度
        for kw in keywords:
            dim = "领域"  # 默认
            if kw in ("Go", "Python", "Redis", "PostgreSQL", "Docker", "SQL", "CDN"):
                dim = "技术栈"
            elif kw in ("微服务", "事件驱动", "单体"):
                dim = "架构模式"
            elif kw in ("安全", "认证", "OAuth"):
                dim = "安全关注点"
            elif kw in ("高并发", "低延迟", "扩展性"):
                dim = "性能特征"
            tags.append({"dimension": dim, "value": kw, "confidence": 0.6, "source": "migrated"})
        entry["tags"] = tags
        if "is_discarded" not in entry:
            entry["is_discarded"] = False
        if "discard_reasons" not in entry:
            entry["discard_reasons"] = []
        if "rank" not in entry:
            entry["rank"] = 1 if entry.get("is_top1", False) else 0
        return entry

    def _index_scheme_assets(self, state: OrchestratorState):
        """编排完成后，将所有方案按 5维标签索引到资产库 (v0.5.0)"""
        asset_dir = self.data_dir / "asset_library"
        asset_dir.mkdir(parents=True, exist_ok=True)
        index_path = asset_dir / "index.json"

        # 加载现有索引并迁移旧条目
        index = {}
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k, v in raw.items():
                    index[k] = self._migrate_old_entry(v)
            except Exception:
                index = {}

        # 用 LLM 提取 5 维标签（仅在开启 auto_tagging 时）
        auto_tagging = getattr(self, 'knowledge_config', {}).get('auto_tagging', True)
        all_tags = []
        if auto_tagging and state.top3_plans:
            # 用 Top1 方案的集成内容提取标签
            top1_sid = None
            for r in state.vote_results:
                if r.get("rank") == 1:
                    top1_sid = r.get("plan_id")
                    break
            if top1_sid and top1_sid in state.schemes:
                plan_text = state.schemes[top1_sid].get("integrated_content", "")
                if len(plan_text) > 200:
                    all_tags = self._extract_tags_llm(state.original_task, plan_text)

        # 回退关键词
        keywords = self._extract_keywords_fallback(state.original_task)

        # 为每个方案创建增强版资产条目
        for sid, scheme in state.schemes.items():
            entry_key = f"{state.task_id}_{sid}"
            vote = next(
                (v for v in state.vote_results if v.get("plan_id") == sid),
                None,
            )
            rank = vote.get("rank", 99) if vote else 99
            is_top1 = rank == 1
            # 废案判定：非Top1 且 非最高分代理人
            is_discarded = not is_top1 and rank > 1

            entry = {
                "task_id": state.task_id,
                "scheme_id": sid,
                "task": state.original_task[:200],
                "tags": all_tags,  # 5维结构化标签
                "keywords": keywords,  # 兼容旧版
                "object_name": scheme.get("object_name", ""),
                "agent_role": scheme.get("agent_role", ""),
                "total_score": vote.get("total_score", 0) if vote else 0,
                "rank": rank,
                "is_top1": is_top1,
                "is_discarded": is_discarded,
                "discard_reasons": [],  # 待用户在 refine 时填写
                "summary": scheme.get("integrated_content", "")[:500],
                "created_at": datetime.now().isoformat(),
                "success_outcome": None,  # 用户后续更新
                # v0.5.0+: YAML frontmatter（方案E渐进式元数据）
                "yaml_meta": self._build_yaml_frontmatter({
                    "task_id": state.task_id,
                    "scheme_id": sid,
                    "task": state.original_task[:200],
                    "tags": all_tags,
                    "keywords": keywords,
                    "created_at": datetime.now().isoformat(),
                }),
                # v1.3: 置信度分级（新归档资产默认 L1）
                "confidence_level": "L1",
                "execution_timestamp": None,
                "verified_by_user": None,
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
                "tags": all_tags,
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

        # v0.7.0: 同步更新向量语义索引
        try:
            if self._enable_vector and self.vector_index is not None:
                self._build_vector_index(full_index=index)
        except Exception as e:
            print(f"  [向量索引] 构建失败（不阻塞）: {e}")

        tag_summary = [t["value"] for t in all_tags[:8]] if all_tags else keywords[:4]
        print(f"  [资产库] 索引了 {len(state.schemes)} 个方案 (标签: {tag_summary})")
        return all_tags if all_tags else keywords

    def _build_vector_index(self, full_index: dict = None):
        """从资产库重建向量语义索引"""
        if self.vector_index is None:
            return
        if full_index is None:
            # 从磁盘加载
            idx_path = self.data_dir / "asset_library" / "index.json"
            if not idx_path.exists():
                return
            with open(idx_path, "r", encoding="utf-8") as f:
                full_index = json.load(f)

        documents = []
        for key, entry in full_index.items():
            # 构建搜索文本：任务 + 标签 + 关键词 + 摘要
            tags_text = " ".join(
                t.get("value", "") for t in entry.get("tags", []) if isinstance(t, dict)
            )
            kw_text = " ".join(entry.get("keywords", []))
            task_text = entry.get("task", "")
            summary_text = (entry.get("summary", "") or "")[:300]
            combined = f"{task_text} {tags_text} {kw_text} {summary_text}".strip()
            if combined:
                documents.append((key, combined))

        if documents:
            n = self.vector_index.build(documents)
            vi_path = self.data_dir / "vector_index"
            self.vector_index.save(str(vi_path))
            print(f"  [向量索引] 构建 {n} 篇文档")

    def _compute_relevance_score(self, query_tags: List[str],
                                  entry: dict, weights: dict = None) -> float:
        """计算查询与资产条目的关联度分数 (0-1)

        公式 (Sanity.io + LLM Wiki 混合):
          关联度 = w1 * 标签重合度(Sanity.io) + w2 * 技术栈相似度 + w3 * 因果记忆匹配度

        标签重合度子公式 (Sanity.io Dropbox Dash 验证):
          shared_tags = 查询词 ∩ 资产可搜索文本 的匹配数
          relevance_tag = (shared_tags × 2) ÷ (len(query_tags) + len(asset_effective_tags))

        取值范围 0-1，值越接近 1 代表关联越强。
        """
        if weights is None:
            # v0.8.0: 使用 MAGE 自适应权重（如有）
            if self._enable_evolution and self.evolution is not None:
                weights = self.evolution.get_adaptive_weights()
            else:
                weights = {"tag_overlap": 0.4, "tech_similarity": 0.35, "causal_match": 0.25}

        query_lower = [q.lower() for q in query_tags]
        entry_tags = entry.get("tags", [])
        entry_keywords = [k.lower() for k in entry.get("keywords", [])]
        entry_task = entry.get("task", "").lower()
        entry_summary = entry.get("summary", "").lower()

        # 构建资产的有效标签集合（用于 Sanity.io 分母）
        tag_values = [t.get("value", "").lower() for t in entry_tags if isinstance(t, dict)]
        asset_effective = list(dict.fromkeys(tag_values + entry_keywords))  # 去重

        # 构建可搜索文本（中文用子串匹配，不用分词）
        searchable_text = " ".join(tag_values + entry_keywords) + " " + entry_task + " " + entry_summary

        # 1. 标签重合度 — Sanity.io 公式
        if query_lower and searchable_text:
            shared = sum(1 for qt in query_lower if qt and qt in searchable_text)
            # (shared × 2) ÷ (len(query) + len(asset_effective))
            denominator = max(len(query_lower) + len(asset_effective), 1)
            tag_overlap = min((shared * 2) / denominator, 1.0)
        else:
            tag_overlap = 0.0

        # 2. 技术栈相似度（从 tags 中筛选 tech_stack 维度的标签）
        tech_tags = [t.get("value", "").lower() for t in entry_tags
                     if isinstance(t, dict) and t.get("dimension") == "技术栈"]
        tech_overlap = 0.0
        if tech_tags and query_lower:
            tech_searchable = " ".join(tech_tags)
            shared_tech = sum(1 for qt in query_lower if qt and qt in tech_searchable)
            tech_denom = max(len(query_lower) + len(tech_tags), 1)
            tech_overlap = min((shared_tech * 2) / tech_denom, 1.0)

        # 3. 因果记忆匹配度
        causal_match = 0.0
        causal_nodes = entry.get("_causal_nodes", [])
        if causal_nodes and query_lower:
            entry_kw_lower = [k.lower() for k in entry.get("keywords", [])]
            entry_searchable = " ".join(entry_kw_lower) + " " + entry_task + " " + entry_summary
            shared = 0
            for node in causal_nodes[:5]:
                node_tags = [t.lower() for t in node.get("tags", [])]
                node_label = node.get("label", "").lower()
                # 节点标签是否出现在条目的可搜索文本中
                for nt in node_tags:
                    if nt and nt in entry_searchable:
                        shared += 0.5
                if node_label and node_label in entry_searchable:
                    shared += 1
                # outcome_score < 0 表示失败，权重加倍
                oscore = node.get("outcome_score") or 0
                if oscore < 0:
                    shared *= 2
            causal_match = min(shared / max(len(causal_nodes[:5]), 1), 1.0)

        # 综合评分
        relevance = (
            weights["tag_overlap"] * tag_overlap
            + weights["tech_similarity"] * tech_overlap
            + weights["causal_match"] * causal_match
        )

        return min(relevance, 1.0)

    # ==================== 方案B: 四信号复合关联度 (LLM Wiki 模型) ====================

    def _compute_adamic_adar(self, query_tags: set, entry_tags: set,
                              full_index: dict, entry_key: str) -> float:
        """Adamic-Adar 共同邻居信号：共享邻居越稀有，信号越强

        公式: Σ 1/log(degree(neighbor))
        """
        score = 0.0
        common = query_tags & entry_tags
        for tag in common:
            # degree = 拥有该标签的资产数
            degree = sum(1 for e in full_index.values()
                         if any(t.get("value", "").lower() == tag
                                for t in e.get("tags", []) if isinstance(t, dict))
                         or tag in [k.lower() for k in e.get("keywords", [])])
            if degree > 1:
                score += 1.0 / __import__("math").log(degree + 1)
        return min(score / max(len(common), 1), 1.0) if common else 0.0

    def _compute_four_signal_relevance(self, query_key: str,
                                        query_tags: List[str],
                                        entry_key: str,
                                        entry: dict,
                                        full_index: dict) -> float:
        """四信号复合关联度（LLM Wiki 开源实现 v0.3.1）

        信号1: 直接链接 (weight=3.0) — 两个任务同源时触发
        信号2: 来源重叠 (weight=4.0) — 来自同一份原始任务
        信号3: Adamic-Adar (weight=1.5) — 共享罕见标签
        信号4: 类型亲和 (weight=1.0) — 相同 Agent 角色
        """
        # 提取 task_id 前缀
        q_task_id = query_key.split("_")[0] + "_" + query_key.split("_")[1] if "_" in query_key else ""
        e_task_id = entry.get("task_id", "")

        query_set = set(q.lower() for q in query_tags)
        entry_tag_vals = set(
            t.get("value", "").lower()
            for t in entry.get("tags", []) if isinstance(t, dict)
        )
        entry_kw_set = set(k.lower() for k in entry.get("keywords", []))
        entry_all_tags = entry_tag_vals | entry_kw_set

        # Signal 1: 直接链接 (3.0)
        direct_link_score = 0.0
        if q_task_id and e_task_id and q_task_id == e_task_id:
            direct_link_score = 3.0

        # Signal 2: 来源重叠 (4.0) — 同 task_id
        source_overlap = 0.0
        if q_task_id and e_task_id and q_task_id == e_task_id:
            source_overlap = 4.0

        # Signal 3: Adamic-Adar (1.5)
        aa_score = self._compute_adamic_adar(query_set, entry_all_tags,
                                              full_index, entry_key)
        signal_3 = aa_score * 1.5

        # Signal 4: 类型亲和 (1.0)
        q_role = ""  # query 没有 agent_role，可从 context 推断
        e_role = entry.get("agent_role", "")
        type_affinity = 1.0 if (q_role and e_role and q_role == e_role) else 0.0
        signal_4 = type_affinity * 1.0

        # 复合
        raw = direct_link_score + source_overlap + signal_3 + signal_4
        # 归一化到 0-1
        max_possible = 3.0 + 4.0 + 1.5 + 1.0  # = 9.5
        return min(raw / max_possible, 1.0)

    def search_assets(self, query: str, top_k: int = 5) -> list:
        """增强版语义检索 — 支持 5维标签 + 关联度评分 + 废案标记"""
        asset_dir = self.data_dir / "asset_library"
        index_path = asset_dir / "index.json"
        if not index_path.exists():
            return []

        with open(index_path, "r", encoding="utf-8") as f:
            raw_index = json.load(f)

        # 迁移旧条目
        index = {}
        for k, v in raw_index.items():
            index[k] = self._migrate_old_entry(v)

        from src.prompts import DEFAULT_KNOWLEDGE_CONFIG
        weights = DEFAULT_KNOWLEDGE_CONFIG["relevance_weights"]

        # 提取查询关键词（支持中文：整句原文 + 常用中文关键词提取 + 英文技术词）
        query_lower = query.lower()

        # 中文关键词自动提取：匹配预定义的中文技术/领域关键词
        chinese_kw_patterns = [
            "短链接", "微服务", "数据库", "前端", "后端", "安全", "认证",
            "部署", "缓存", "消息队列", "高并发", "博客", "文件分享", "文件上传",
            "待办事项", "API", "实时", "搜索", "支付", "CDN", "监控",
            "容器", "Docker", "Kubernetes", "负载均衡", "网关",
        ]
        query_kw = []
        for cp in chinese_kw_patterns:
            if cp.lower() in query_lower and cp.lower() not in query_kw:
                query_kw.append(cp.lower())

        # 提取技术标记词（英文关键词独立提取）
        tech_markers = ["go", "python", "rust", "java", "node", "react", "vue",
                        "docker", "k8s", "redis", "postgresql", "mysql", "mongodb",
                        "sqlite", "nginx", "aws", "gcp", "azure", "api", "crud",
                        "oauth", "jwt", "sql", "cdn", "etl", "css", "html"]
        query_tech = [w for w in query_lower.replace(",", " ").replace("#", " ").split()
                      if w in tech_markers]

        # 构建查询标签: 整句原文 + 中文关键词 + 英文技术词
        query_tags = [query_lower] + query_kw + query_tech

        # v0.6: 因果记忆匹配 — 查询一次，注入各条目
        causal_nodes = []
        try:
            causal_nodes = self.query_causal_memory(
                query_kw + query_tech, top_k=10,
            )
        except Exception:
            pass

        # 预处理中文条目文本库用于更快匹配

        results = []
        for key, entry in index.items():
            entry["_causal_nodes"] = causal_nodes  # 注入因果上下文
            # 关联度评分（主分数）
            relevance = self._compute_relevance_score(query_tags, entry, weights)

            # 兼容旧版：保留关键词匹配加分
            kw_match = sum(
                1 for kw in entry.get("keywords", [])
                if kw.lower() in query_lower
            )
            legacy_boost = kw_match * 0.05  # 小幅度加分

            # v0.5.5: 四信号 Adamic-Adar 稀有标签加分
            four_signal_boost = 0.0
            query_set = set(q.lower() for q in query_tags)
            entry_tag_vals = set(
                t.get("value", "").lower()
                for t in entry.get("tags", []) if isinstance(t, dict)
            )
            entry_kw_set = set(k.lower() for k in entry.get("keywords", []))
            entry_all_tags = entry_tag_vals | entry_kw_set
            common = query_set & entry_all_tags
            if common:
                # 稀有标签加分：只出现1次的标签 → x2, 出现2次 → x1.5, 出现3次 → x1
                rarity_bonus = 0.0
                for tag in common:
                    freq = sum(1 for e in index.values()
                               if any(t.get("value", "").lower() == tag
                                      for t in e.get("tags", []) if isinstance(t, dict))
                               or tag in [k.lower() for k in e.get("keywords", [])])
                    if freq == 1:
                        rarity_bonus += 0.10  # 极稀有标签
                    elif freq == 2:
                        rarity_bonus += 0.05  # 稀有标签
                four_signal_boost = min(rarity_bonus, 0.3)

            score = round(relevance + legacy_boost + four_signal_boost, 4)

            if score > 0.05:  # 低阈值也要返回
                # 找一下废案原因
                discard_reasons = entry.get("discard_reasons", [])
                is_discarded = entry.get("is_discarded", False)

                result = {
                    "key": key,
                    "relevance_score": score,
                    "tag_overlap": relevance,
                    "task": entry.get("task", ""),
                    "scheme_id": entry.get("scheme_id", ""),
                    "object_name": entry.get("object_name", ""),
                    "keywords": entry.get("keywords", []),
                    "tags": entry.get("tags", []),
                    "total_score": entry.get("total_score", 0),
                    "is_top1": entry.get("is_top1", False),
                    "is_discarded": is_discarded,
                    "discard_reasons": discard_reasons,
                    "rank": entry.get("rank", 0),
                    "summary": entry.get("summary", "")[:200],
                    "created_at": entry.get("created_at", ""),
                    # 来源标注（v1.3 新增）
                    "source_task_id": entry.get("task_id", ""),
                    "review_status": (
                        "verified"
                        if entry.get("verification_status") == "verified"
                        else ("deprecated" if is_discarded else "unreviewed")
                    ),
                    # 置信度分级（v1.3 新增）
                    "confidence_level": entry.get("confidence_level", "L1"),
                }
                if is_discarded:
                    result["warning"] = (
                        f"⚠️ 这是一个废案（排名 #{entry.get('rank', '?')}），"
                        f"被Top1方案淘汰"
                        + (f"。原因: {'; '.join(discard_reasons)}" if discard_reasons else "")
                    )
                results.append(result)

        # v0.7.0: 向量语义搜索融合（如启用）
        if self._enable_vector and self.vector_index is not None and self.vector_index.size > 0:
            try:
                vec_results = self.vector_index.query(query_lower, top_k=top_k * 2)
                vec_scores = {r["doc_id"]: r["score"] for r in vec_results}

                for r in results:
                    key = r["key"]
                    if key in vec_scores:
                        # 向量分融合：0.3 权重
                        r["vector_score"] = vec_scores[key]
                        r["relevance_score"] = round(
                            r["relevance_score"] * 0.7 + vec_scores[key] * 0.3, 4
                        )
                    else:
                        r["vector_score"] = 0.0

                # 重新排序
                results.sort(key=lambda x: x["relevance_score"], reverse=True)
            except Exception as e:
                print(f"  [向量索引] 查询失败（优雅降级）: {e}")

        # v0.8.0: MAGE Bandit 探索 — epsilon-greedy 提升低排名但高历史回报的资产
        if self._enable_evolution and self.evolution is not None and len(results) >= 2:
            try:
                if self.evolution.should_explore():
                    # 从排名 3+ 的资产中选一个 bandit 评分最高的提升到第 2 位
                    tail = results[min(2, len(results)-1):]
                    if tail:
                        best_tail = max(tail, key=lambda r: self.evolution.get_asset_score(r["key"]))
                        if best_tail in results:
                            results.remove(best_tail)
                            results.insert(1, best_tail)
            except Exception:
                pass

        results.sort(key=lambda x: x["relevance_score"], reverse=True)

        # v1.3: 默认过滤 L1 低置信度资产（除非显式包含 L1）
        # 通过 special query param 控制：用户说"包括低置信度"时返回 L1
        # 简单处理：只返回 confidence_level != "L1" 的资产，L1 需主动查询"包括低置信度"才返回
        # 由于 search_assets 目前没有这个参数，暂时只过滤掉完全无 confidence_level 标记且 L1 状态的条目
        # 实际使用场景：GoalRunner 归档时会更新 confidence_level，届时 L2+ 资产才会被检索
        pass  # 置信度过滤在 search_assets 返回前统一处理，见下

        top_results = results[:top_k]

        # v1.3: 最终过滤 — 排除从未执行过（execution_timestamp=None）且置信度为 L1 的资产
        # 但保留有高关联度（>0.4）的 L1 资产，避免矫枉过正
        filtered = []
        for r in top_results:
            conf = r.get("confidence_level", "L1")
            score = r.get("relevance_score", 0)
            if conf != "L1":
                filtered.append(r)
            elif score >= 0.4:
                # 高关联度 L1 资产降级显示，不完全排除
                r["confidence_level"] = "L1_low_confidence"
                filtered.append(r)
        top_results = filtered

        # v0.8.0: 自动记录到 MAGE 自进化引擎
        if self._enable_evolution and self.evolution is not None:
            try:
                self.evolution.record_search(query, top_results)
            except Exception:
                pass

        return top_results

    def check_discarded_warnings(self, query: str, top_k: int = 3) -> list:
        """专门检索废案，返回类似方案的警告"""
        asset_dir = self.data_dir / "asset_library"
        index_path = asset_dir / "index.json"
        if not index_path.exists():
            return []

        with open(index_path, "r", encoding="utf-8") as f:
            raw_index = json.load(f)

        # 迁移旧条目
        index = {}
        for k, v in raw_index.items():
            index[k] = self._migrate_old_entry(v)

        query_lower = query.lower()

        # 中文关键词预提取
        chinese_kw_patterns = [
            "短链接", "微服务", "数据库", "前端", "后端", "安全", "认证",
            "部署", "缓存", "消息队列", "高并发", "博客", "文件分享", "文件上传",
            "待办事项", "API", "实时", "搜索", "支付", "CDN", "监控",
        ]
        query_kw = [cp for cp in chinese_kw_patterns if cp.lower() in query_lower]

        warnings = []
        for key, entry in index.items():
            is_discarded = entry.get("is_discarded", False)
            rank = entry.get("rank", 0)
            total_score = entry.get("total_score", 0)

            # 只关注：明确标记废案 或 排名>1但有评分 或 未评分但非Top1
            if not is_discarded and rank <= 1:
                continue
            if not is_discarded and total_score == 0:
                continue

            entry_task = entry.get("task", "").lower()
            entry_kw = [k.lower() for k in entry.get("keywords", [])]

            # 检查与查询的相关性（子串匹配，支持中文）
            kw_match = [k for k in entry_kw if k and k in query_lower]
            kw_match += [k for k in query_kw if k in entry_task]
            task_match = query_lower in entry_task or any(k in entry_task for k in [query_lower])

            if kw_match or task_match:
                warnings.append({
                    "key": key,
                    "task": entry.get("task", ""),
                    "keywords": entry.get("keywords", []),
                    "discard_reasons": entry.get("discard_reasons", []),
                    "rank": rank,
                    "total_score": total_score,
                    "matched_terms": list(set(kw_match)) if kw_match else ["(task match)"],
                    "warning_type": is_discarded and "explicit_discard" or "alternative",
                })

        warnings.sort(key=lambda x: len(x.get("matched_terms", [])), reverse=True)
        return warnings[:top_k]

    def update_asset_tags(self, task_id: str, tags: List[dict]) -> dict:
        """由调用方（Reasonix AI）直接写入 5 维标签到资产库

        Args:
            task_id: 任务ID (如 task_abc123)
            tags: 标签列表，格式同 KnowledgeTag.to_dict()
                   [{"dimension":"技术栈","value":"Go","confidence":0.95}, ...]

        Returns:
            {"updated": n_entries, "task_id": task_id}
        """
        asset_dir = self.data_dir / "asset_library"
        index_path = asset_dir / "index.json"
        if not index_path.exists():
            return {"error": "资产库不存在", "task_id": task_id}

        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

        updated = 0
        for key, entry in index.items():
            if entry.get("task_id") == task_id:
                entry["tags"] = tags
                entry["keywords"] = list(dict.fromkeys(
                    [t["value"] for t in tags if isinstance(t, dict)]
                    + entry.get("keywords", [])
                ))
                # 刷新 YAML 元数据
                entry["yaml_meta"] = self._build_yaml_frontmatter(entry)
                updated += 1

        if updated > 0:
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            return {"updated": updated, "task_id": task_id}
        return {"error": f"任务 {task_id} 未找到", "task_id": task_id}

    def record_search_evolution(self, query: str, results: list):
        """记录一次搜索到 MAGE 自进化引擎"""
        if self._enable_evolution and self.evolution is not None:
            try:
                self.evolution.record_search(query, results)
            except Exception:
                pass

    def _on_goal_success(self, goal_id: str, description: str):
        """GoalRunner L1+L2+L3 全部通过后的采纳回调"""
        if self._enable_evolution and self.evolution is not None:
            try:
                updated = self.evolution.mark_adopted(goal_id, adopted=True)
                if updated > 0:
                    print(f"  [进化] Goal {goal_id} 成功 → 已标记采纳, 权重已更新")
                else:
                    print(f"  [进化] Goal {goal_id} 成功 → (无匹配的 feedback 条目)")
            except Exception as e:
                print(f"  [进化] 采纳标记失败: {e}")

    def get_knowledge_stats(self) -> dict:
        """获取知识库综合统计"""
        stats = {"version": "1.0.0"}

        # 资产库统计
        idx_path = self.data_dir / "asset_library" / "index.json"
        if idx_path.exists():
            import json
            with open(idx_path, "r", encoding="utf-8") as f:
                idx = json.load(f)
            stats["assets"] = {
                "total": len(idx),
                "top1": sum(1 for e in idx.values() if e.get("is_top1")),
                "discarded": sum(1 for e in idx.values() if e.get("is_discarded")),
                "with_tags": sum(1 for e in idx.values() if e.get("tags")),
            }
            # 标签种类
            all_dims = set()
            all_vals = set()
            for e in idx.values():
                for t in e.get("tags", []):
                    if isinstance(t, dict):
                        all_dims.add(t.get("dimension", ""))
                        all_vals.add(t.get("value", ""))
            stats["assets"]["tag_dimensions"] = len(all_dims)
            stats["assets"]["tag_values"] = len(all_vals)

        # 因果图统计
        causal_path = self.data_dir / "causal_memory" / "graph.json"
        if causal_path.exists():
            import json
            with open(causal_path, "r", encoding="utf-8") as f:
                graph = json.load(f)
            node_types = {}
            for n in graph.get("nodes", {}).values():
                nt = n.get("node_type", "unknown")
                node_types[nt] = node_types.get(nt, 0) + 1
            stats["causal"] = {
                "total_nodes": len(graph.get("nodes", {})),
                "total_edges": len(graph.get("edges", [])),
                "node_types": node_types,
            }

        # Agent 图统计
        if self._enable_agent_graph and self.agent_graph is not None:
            ag = self.agent_graph.get_stats()
            stats["agent_graph"] = ag

        # 进化统计
        if self._enable_evolution and self.evolution is not None:
            ev = self.evolution.get_stats()
            stats["evolution"] = {
                "total_searches": ev["total_searches"],
                "adoption_rate": ev["adoption_rate"],
                "epsilon": ev["epsilon"],
                "weight_version": ev["weights"].get("version", 1),
            }

        # 向量索引统计
        if self._enable_vector and self.vector_index is not None:
            stats["vector_index"] = {"documents": self.vector_index.size}

        return stats

    def get_evolution_stats(self) -> dict:
        """获取 MAGE 自进化统计"""
        if self._enable_evolution and self.evolution is not None:
            return self.evolution.get_stats()
        return {"status": "disabled"}

    def search_project_kb(self, query: str, top_k: int = 5) -> list:
        """搜索项目知识库 (Collusion_知识库/) 中的文档

        使用文件名和内容的子串匹配，返回匹配的文件列表
        """
        vault_path = self.knowledge_config.get("obsidian_vault_path", "")
        if not vault_path or not os.path.isdir(vault_path):
            return []

        query_lower = query.lower()
        results = []

        for root, dirs, files in os.walk(vault_path):
            # 跳过 .git, 参考材料中的代码库
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
            for f in files:
                if not f.endswith('.md'):
                    continue
                fpath = os.path.join(root, f)
                rel = os.path.relpath(fpath, vault_path)

                # 文件名匹配
                fname_score = 1.0 if query_lower in f.lower().replace('.md', '') else 0

                # 内容匹配 (只读前 2000 字符)
                try:
                    with open(fpath, 'r', encoding='utf-8') as fh:
                        head = fh.read(2000)
                    content_score = 0
                    for word in query_lower.split():
                        if word in head.lower():
                            content_score += 0.5
                    content_score = min(content_score, 2.0)
                except Exception:
                    content_score = 0

                total = fname_score + content_score
                if total > 0:
                    results.append({
                        "path": rel,
                        "filename": f,
                        "score": round(total, 2),
                        "match_type": "filename" if fname_score > 0 else "content",
                    })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def pre_check_knowledge(self, task: str) -> dict:
        """新任务启动时的知识预检：检索关联资产 + 废案警告

        返回:
            {"relevant_assets": [...], "discarded_warnings": [...], "relevance_summary": "..."}
        """
        relevant = self.search_assets(task, top_k=5)
        discarded = self.check_discarded_warnings(task, top_k=3)

        # 生成摘要
        parts = []
        if relevant:
            top = relevant[0]
            parts.append(f"发现 {len(relevant)} 个相关历史方案")
            if top["relevance_score"] > 0.3:
                parts.append(f"最高关联度: {top['relevance_score']:.2f} ({top['task'][:40]}...)")

        if discarded:
            parts.append(f"⚠️ 发现 {len(discarded)} 个类似废案需注意")

        return {
            "relevant_assets": relevant,
            "discarded_warnings": discarded,
            "relevance_summary": " | ".join(parts) if parts else "无匹配历史资产",
        }

    # ==================== Phase 4: 因果记忆图 Prism ====================

    def _load_causal_graph(self) -> dict:
        """加载因果记忆图"""
        path = self.data_dir / "causal_memory" / "graph.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"nodes": {}, "edges": [], "version": "0.5.0"}

    def _save_causal_graph(self, graph: dict):
        """保存因果记忆图"""
        path = self.data_dir / "causal_memory"
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "graph.json", "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)

    def _record_causal_memory(self, state):
        """编排完成后，保存 Top1 方案原文供调用方分析（v0.7.0: 不再主动调 LLM）
        调用方通过 collusion_causal_record 写入已分析的数据。
        """
        if not self.knowledge_config.get("enable_causal_memory", True):
            return

        top1_sid = None
        for r in state.vote_results:
            if r.get("rank") == 1:
                top1_sid = r.get("plan_id")
                break
        if not top1_sid or top1_sid not in state.schemes:
            return

        plan_text = state.schemes[top1_sid].get("integrated_content", "")
        if len(plan_text) < 200:
            return

        # 保存方案原文到临时文件，供调用方后续分析
        causal_dir = self.data_dir / "causal_memory"
        causal_dir.mkdir(parents=True, exist_ok=True)
        pending = {
            "task_id": state.task_id,
            "original_task": state.original_task[:200],
            "plan_text": plan_text[:5000],
            "status": "pending_analysis",
        }
        with open(causal_dir / f"pending_{state.task_id}.json", "w", encoding="utf-8") as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)
        print(f"  [因果记忆] 待分析: {state.task_id}（调用方可使用 collusion_causal_record 写入）")

    def save_causal_data(self, task_id: str, decisions: list, constraints: list = None,
                          outcomes: list = None, risks: list = None) -> dict:
        """由调用方（Reasonix AI）直接写入因果记忆数据

        Args:
            task_id: 任务ID
            decisions: [{"label","description","tags","outcome_score"}, ...]
            constraints: [{"label","description","tags"}, ...]
            outcomes: [{"label","description","tags","score"}, ...]
            risks: [{"label","description","tags","severity"}, ...]

        Returns:
            {"saved": n_nodes, "task_id": task_id}
        """
        graph = self._load_causal_graph()
        constraints = constraints or []
        outcomes = outcomes or []
        risks = risks or []
        n_initial = len(graph["nodes"])

        # 决策节点
        for d in decisions:
            nid = f"dec_{task_id}_{len(graph['nodes'])}"
            graph["nodes"][nid] = {
                "id": nid, "node_type": "decision",
                "label": d.get("label", ""), "description": d.get("description", ""),
                "tags": d.get("tags", []), "task_id": task_id,
                "created_at": time.time(), "outcome_score": d.get("outcome_score"),
            }

        # 约束节点
        for c in constraints:
            nid = f"con_{task_id}_{len(graph['nodes'])}"
            graph["nodes"][nid] = {
                "id": nid, "node_type": "constraint",
                "label": c.get("label", ""), "description": c.get("description", ""),
                "tags": c.get("tags", []), "task_id": task_id,
                "created_at": time.time(),
            }

        # 结果节点
        for o in outcomes:
            nid = f"out_{task_id}_{len(graph['nodes'])}"
            graph["nodes"][nid] = {
                "id": nid, "node_type": "outcome",
                "label": o.get("label", ""), "description": o.get("description", ""),
                "tags": o.get("tags", []), "task_id": task_id,
                "created_at": time.time(), "outcome_score": o.get("score"),
            }

        # 风险节点
        for r in risks:
            nid = f"risk_{task_id}_{len(graph['nodes'])}"
            graph["nodes"][nid] = {
                "id": nid, "node_type": "risk",
                "label": r.get("label", ""), "description": r.get("description", ""),
                "tags": r.get("tags", []), "task_id": task_id,
                "created_at": time.time(), "severity": r.get("severity", "中"),
            }

        # 自动添加因果边：决策 → 结果
        new_node_ids = list(graph["nodes"].keys())[n_initial:]
        dec_ids = [n for n in new_node_ids if graph["nodes"][n]["node_type"] == "decision"]
        out_ids = [n for n in new_node_ids if graph["nodes"][n]["node_type"] == "outcome"]
        for d_id in dec_ids:
            for o_id in out_ids:
                graph["edges"].append({
                    "source_id": d_id, "target_id": o_id,
                    "relation": "leads_to", "weight": 1.0,
                    "description": f"{graph['nodes'][d_id]['label']} → {graph['nodes'][o_id]['label']}",
                    "task_id": task_id,
                })

        self._save_causal_graph(graph)
        n_saved = len(graph["nodes"]) - n_initial

        # 清理待分析标记
        causal_dir = self.data_dir / "causal_memory"
        pending_file = causal_dir / f"pending_{task_id}.json"
        if pending_file.exists():
            pending_file.unlink()

        return {"saved": n_saved, "task_id": task_id, "total_nodes": len(graph["nodes"])}

    def query_causal_memory(self, query_tags: List[str], top_k: int = 5) -> list:
        """查询因果记忆图，返回与给定标签相关的历史决策-结果信息
        """
        graph = self._load_causal_graph()
        if not graph["nodes"]:
            return []

        query_lower = [q.lower() for q in query_tags]
        scored = []

        for nid, node in graph["nodes"].items():
            node_tags = [t.lower() for t in node.get("tags", [])]
            node_label = node.get("label", "").lower()
            node_desc = node.get("description", "").lower()

            # 标签重叠（Sanity.io 风格）
            shared = sum(1 for qt in query_lower if qt in node_tags or qt in node_label or qt in node_desc)
            if shared == 0:
                continue

            total = max(len(query_lower) + max(len(node_tags), 1), 1)
            score = (shared * 2) / total

            scored.append({
                "node_id": nid,
                "node_type": node.get("node_type", ""),
                "label": node.get("label", ""),
                "description": node.get("description", "")[:100],
                "tags": node.get("tags", []),
                "outcome_score": node.get("outcome_score"),
                "severity": node.get("severity", ""),
                "task_id": node.get("task_id", ""),
                "relevance": round(score, 4),
            })

        scored.sort(key=lambda x: x["relevance"], reverse=True)
        return scored[:top_k]

    def causal_risk_warning(self, task: str) -> list:
        """为新任务检查因果记忆中的风险预警
        返回: [{"risk": "...", "past_decision": "...", "relevance": 0.xx}, ...]
        """
        # 提取查询标签
        query_lower = task.lower()
        chinese_kw = ["短链接", "微服务", "数据库", "安全", "认证", "部署", "缓存",
                       "高并发", "文件分享", "API", "实时", "搜索", "支付"]
        query_kw = [cp for cp in chinese_kw if cp.lower() in query_lower]
        query_tags = [query_lower] + query_kw

        results = self.query_causal_memory(query_tags, top_k=10)
        warnings = []
        for r in results:
            if r["node_type"] == "risk":
                warnings.append({
                    "risk": r["label"],
                    "description": r["description"],
                    "severity": r["severity"],
                    "past_task": r["task_id"],
                    "relevance": r["relevance"],
                })
            elif r["node_type"] == "outcome" and r.get("outcome_score", 0) is not None and r["outcome_score"] < 0:
                warnings.append({
                    "risk": f"负面结果: {r['label']}",
                    "description": r["description"],
                    "severity": "中",
                    "past_task": r["task_id"],
                    "relevance": r["relevance"],
                })

        warnings.sort(key=lambda x: x["relevance"], reverse=True)
        return warnings[:5]

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
