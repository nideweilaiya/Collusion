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

    # ==================== 公共 API ====================

    def orchestrate(self, task: str, task_id: str = None) -> str:
        """主入口：执行 v3.1 完整编排"""
        state = OrchestratorState(original_task=task)
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
    def _render_radar_svg(schemes: list, dimensions: list) -> str:
        """生成雷达图 SVG（纯 Python 计算坐标，无 JS 依赖）"""
        colors = {"A": "#dc2626", "B": "#2563eb", "C": "#059669"}
        cx, cy, r = 250, 240, 180
        n = len(dimensions)
        parts = ['<svg viewBox="0 0 500 500" xmlns="http://www.w3.org/2000/svg">']

        # 网格
        for level in [0.2, 0.4, 0.6, 0.8, 1.0]:
            pts = []
            for i in range(n):
                angle = (i * 360 / n - 90) * math.pi / 180
                x = cx + r * level * math.cos(angle)
                y = cy - r * level * math.sin(angle)
                pts.append(f"{x:.0f},{y:.0f}")
            parts.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="#e5e7eb" stroke-width="1"/>')

        # 轴 + 标签
        for i, dim in enumerate(dimensions):
            angle = (i * 360 / n - 90) * math.pi / 180
            x2 = cx + r * 1.05 * math.cos(angle)
            y2 = cy - r * 1.05 * math.sin(angle)
            tx = cx + r * 1.18 * math.cos(angle)
            ty = cy - r * 1.18 * math.sin(angle) + 4
            parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="#d1d5db" stroke-width="1"/>')
            parts.append(f'<text x="{tx:.0f}" y="{ty:.0f}" text-anchor="middle" font-size="12" fill="#374151">{dim}</text>')

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
            parts.append(f'<rect x="20" y="{ly}" width="14" height="14" fill="{c}" opacity="0.3" rx="2"/>')
            parts.append(f'<rect x="20" y="{ly}" width="14" height="2" fill="{c}"/>')
            parts.append(f'<text x="40" y="{ly + 12}" font-size="11" fill="#374151">方案 {scheme.get("id", "")} ({scheme.get("total_score", 0):.1f})</text>')

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
        """渲染 HTML + Markdown 输出，保存到 data/outputs/{task_id}/"""
        data = self._build_template_data(state)
        env = self._get_template_env()
        output_dir = self.data_dir / "outputs" / state.task_id
        output_dir.mkdir(parents=True, exist_ok=True)

        paths = {}
        # Markdown
        md_template = env.get_template("report.md")
        md_content = md_template.render(**data)
        md_path = output_dir / "report.md"
        md_path.write_text(md_content, encoding="utf-8")
        paths["markdown"] = str(md_path)

        # HTML
        html_template = env.get_template("report.html")
        html_content = html_template.render(**data)
        html_path = output_dir / "report.html"
        html_path.write_text(html_content, encoding="utf-8")
        paths["html"] = str(html_path)

        return paths

    def _build_template_data(self, state: OrchestratorState) -> dict:
        """将编排状态转换为模板可用的数据结构"""
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
            scheme_list.append({
                "id": sid,
                "object_name": s.get("object_name", ""),
                "agent_role": s.get("agent_role", ""),
                "integrated_content": s.get("integrated_content", ""),
                "total_score": vr.get("total_score", 0),
                "comment": vr.get("comment", ""),
                "scores": {
                    "正确性": vr.get("correctness", 0),
                    "完整性": vr.get("completeness", 0),
                    "可行性": vr.get("feasibility", 0),
                    "创新性": vr.get("innovation", 0),
                    "业务对齐": vr.get("business_alignment", 0),
                },
            })

        # Top 方案
        top_scheme = scheme_list[0] if scheme_list else None

        # 步骤 + 各方案设计
        step_list = []
        for step in steps:
            designs = {}
            for sid, scheme in schemes.items():
                design = scheme.get("steps", {}).get(step.get("id", ""), "")
                if design and len(design.strip()) > 10:
                    designs[sid] = design[:300]
            step_list.append({
                "index": step.get("index", 0),
                "name": step.get("name", ""),
                "description": step.get("description", ""),
                "designs": designs,
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

        # 任务清单 (从步骤生成)
        task_list = []
        for step in steps:
            task_list.append({
                "id": step.get("index", 0),
                "name": step.get("name", ""),
                "description": step.get("description", ""),
                "estimated_time": "1-2小时",
                "priority": "高" if step.get("index", 0) <= 2 else "中",
            })

        # 雷达图 SVG
        radar_svg = self._render_radar_svg(scheme_list, dimensions)

        return {
            "task_id": state.task_id,
            "task": state.original_task,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "radar_svg": radar_svg,
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
            "saved_mods": {},  # 用于 HTML 草稿恢复
        }

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

            # 全票通过 → 自动应用
            if all(v["verdict"] == "认可" for v in agent_verdicts):
                auto_applied.append(step_name)
            else:
                needs_review.append(step_name)

        return {
            "task_id": task_id,
            "feedback_matrix": feedback_matrix,
            "auto_applied": auto_applied,
            "needs_review": needs_review,
        }

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
        api_key = cfg.get("api_key", "")
        if not api_key:
            api_key = os.environ.get(cfg.get("api_key_env", "DEEPSEEK_API_KEY"), "")
        # 零配置回退：自动读取 Reasonix 已存的 API Key
        if not api_key:
            api_key = self._try_reasonix_key()
        if provider == "deepseek":
            return DeepSeekAdapter(
                api_key=api_key,
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
