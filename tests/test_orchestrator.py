"""编排器核心流程单元测试（Mock LLM）"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock
from src.orchestrator import BrainstormOrchestrator
from src.models import (
    Step, PlanScheme, OrchestratorState, OrchestratorPhase,
    AgentRole, ObjectType, ROLE_OBJECT_MAP,
)
from tests.conftest import (
    MockLLMAdapter,
    DECOMPOSE_RESPONSE,
    CONSENSUS_UX_RESPONSE,
    CONSENSUS_SEC_RESPONSE,
    CONSENSUS_PERF_RESPONSE,
    VALIDATE_STEPS_RESPONSE,
    PROPOSAL_RESPONSE,
    CROSS_REVIEW_RESPONSE,
    FEASIBILITY_BRAKE_RESPONSE,
    OWNER_INTEGRATION_DRAFT,
    OWNER_INTEGRATION_FINAL,
    VOTE_RESPONSE,
)


@pytest.fixture
def mock_orchestrator():
    """创建使用 mock LLM 的编排器"""
    mock_strong = MockLLMAdapter()
    mock_fast = MockLLMAdapter()

    with patch.object(BrainstormOrchestrator, '_create_adapter') as mock_create:
        def create_adapter(key):
            return mock_strong if key == "strong" else mock_fast
        mock_create.side_effect = create_adapter

        orch = BrainstormOrchestrator()

    orch.strong_llm = mock_strong
    orch.fast_llm = mock_fast
    return orch


# ============================================================
# 初始化测试
# ============================================================

class TestOrchestratorInit:
    def test_creates_correct_number_of_agents(self, mock_orchestrator):
        assert len(mock_orchestrator.agents) == 3

    def test_agents_have_distinct_roles(self, mock_orchestrator):
        roles = [a.role for a in mock_orchestrator.agents]
        assert AgentRole.UX in roles
        assert AgentRole.PERFORMANCE in roles
        assert AgentRole.SECURITY in roles

    def test_agents_map_to_objects(self, mock_orchestrator):
        for agent in mock_orchestrator.agents:
            assert agent.object_type is not None
            assert agent.object_name is not None
            # 每个 agent 的 object_name 应该与其 role 对应
            expected_object = ROLE_OBJECT_MAP.get(agent.role)
            assert agent.object_type == expected_object

    def test_num_agents_setter_rebuilds_agents(self, mock_orchestrator):
        assert len(mock_orchestrator.agents) == 3
        mock_orchestrator.num_agents = 2
        assert len(mock_orchestrator.agents) == 2

    def test_business_and_engineering_agents_assigned(self, mock_orchestrator):
        assert mock_orchestrator.business_agent is not None
        assert mock_orchestrator.engineering_agent is not None
        assert mock_orchestrator.business_agent.role == AgentRole.UX
        assert mock_orchestrator.engineering_agent.role == AgentRole.PERFORMANCE


# ============================================================
# 阶段1: 任务解构测试
# ============================================================

class TestDecompose:
    def test_decompose_produces_steps(self, mock_orchestrator):
        mock_orchestrator.strong_llm.set_responses([DECOMPOSE_RESPONSE])
        state = OrchestratorState(original_task="设计一个开发者 API 工具平台")
        steps = mock_orchestrator._decompose(state)
        assert len(steps) == 4
        assert steps[0].name == "接口设计"
        assert isinstance(steps[0], Step)

    def test_decompose_steps_have_ids(self, mock_orchestrator):
        mock_orchestrator.strong_llm.set_responses([DECOMPOSE_RESPONSE])
        state = OrchestratorState(original_task="测试任务")
        steps = mock_orchestrator._decompose(state)
        for s in steps:
            assert s.id.startswith("step_")
            assert len(s.id) == 13  # "step_" + 8 hex chars


# ============================================================
# 阶段2: 环节共识 — 补齐能力测试（核心）
# ============================================================

class TestConsensusAndGapFilling:
    def test_detects_missing_steps_from_multiple_agents(self, mock_orchestrator):
        """测试补齐能力：多个 Agent 各自发现缺失环节"""
        mock_orchestrator.fast_llm.set_responses([
            CONSENSUS_UX_RESPONSE,    # UX: 提议开发者体验
            CONSENSUS_PERF_RESPONSE,  # Perf: 无缺失
            CONSENSUS_SEC_RESPONSE,   # Sec: 提议威胁建模
        ])
        mock_orchestrator.strong_llm.set_responses([
            VALIDATE_STEPS_RESPONSE,  # 验证新增（_validate_new_steps 使用 strong_llm）
        ])

        steps = [Step.from_dict(s) for s in DECOMPOSE_RESPONSE["steps"]]
        state = OrchestratorState(original_task="设计一个开发者 API 工具平台")

        result = mock_orchestrator._consensus(state, steps)

        # 应该从 4 个环节增长到至少 6 个
        assert len(result) >= 6
        step_names = [s.name for s in result]
        assert "开发者体验与入门引导" in step_names
        assert "威胁建模与安全风险评估" in step_names

    def test_no_missing_steps_when_all_covered(self, mock_orchestrator):
        """当所有 Agent 都认为无缺失时，环节数不变"""
        mock_orchestrator.fast_llm.set_responses([
            CONSENSUS_PERF_RESPONSE,
            CONSENSUS_PERF_RESPONSE,
            CONSENSUS_PERF_RESPONSE,
        ])

        steps = [Step.from_dict(s) for s in DECOMPOSE_RESPONSE["steps"]]
        state = OrchestratorState(original_task="测试任务")

        result = mock_orchestrator._consensus(state, steps)
        assert len(result) == 4  # 无新增

    def test_coverage_below_threshold_adds_baseline_step(self, mock_orchestrator):
        """覆盖率低于阈值时自动补充横切环节"""
        # 用只有 1 个环节的情境触发低覆盖率
        mock_orchestrator.fast_llm.set_responses([
            {
                "has_gap": False, "missing_steps": [],
                "coverage": [
                    {"step_index": 1, "step_name": "接口设计",
                     "level": "缺失", "note": ""}
                ]
            },
            {
                "has_gap": False, "missing_steps": [],
                "coverage": [
                    {"step_index": 1, "step_name": "接口设计",
                     "level": "缺失", "note": ""}
                ]
            },
            {
                "has_gap": False, "missing_steps": [],
                "coverage": [
                    {"step_index": 1, "step_name": "接口设计",
                     "level": "缺失", "note": ""}
                ]
            },
        ])

        steps = [Step(index=1, name="接口设计", description="API 设计")]
        state = OrchestratorState(original_task="测试任务")

        result = mock_orchestrator._consensus(state, steps)

        # 应该补了 3 个横切基线环节（每个对象一个）
        baseline_names = [s.name for s in result if "基线" in s.name]
        assert len(baseline_names) == 3


# ============================================================
# 阶段3: 并行提案测试
# ============================================================

class TestProposals:
    def test_generates_parallel_proposals(self, mock_orchestrator):
        mock_orchestrator.fast_llm.set_responses([
            PROPOSAL_RESPONSE,
            {**PROPOSAL_RESPONSE, "object_name": "技术架构对象"},
            {**PROPOSAL_RESPONSE, "object_name": "安全与合规对象"},
        ])

        steps = [Step.from_dict(s) for s in DECOMPOSE_RESPONSE["steps"]]
        state = OrchestratorState(original_task="测试任务")

        schemes = mock_orchestrator._proposals(state, steps)
        assert len(schemes) == 3
        assert "A" in schemes
        assert "B" in schemes
        assert "C" in schemes


# ============================================================
# 阶段4.5: 可行性收束测试
# ============================================================

class TestFeasibilityBrake:
    def test_brake_applied_to_all_schemes(self, mock_orchestrator):
        mock_orchestrator.fast_llm.set_responses([
            FEASIBILITY_BRAKE_RESPONSE,
            FEASIBILITY_BRAKE_RESPONSE,
            FEASIBILITY_BRAKE_RESPONSE,
        ])

        steps = [Step.from_dict(s) for s in DECOMPOSE_RESPONSE["steps"]]
        schemes = {
            "A": PlanScheme(agent_role="UX/产品专家", agent_name="agent_1"),
            "B": PlanScheme(agent_role="性能架构师", agent_name="agent_2"),
            "C": PlanScheme(agent_role="安全专家", agent_name="agent_3"),
        }
        state = OrchestratorState(original_task="测试任务")

        result = mock_orchestrator._feasibility_brake(state, schemes, steps)
        assert len(state.feasibility_brake_records) == 3
        for record in state.feasibility_brake_records:
            assert "feasible" in record
            assert "cost_estimate" in record

    def test_mandatory_simplify_when_over_threshold(self, mock_orchestrator):
        """复杂度超阈值时强制简化"""
        brake_with_mandatory = {
            **FEASIBILITY_BRAKE_RESPONSE,
            "feasible": False,
            "mandatory_simplify": True,
            "simplifications": [
                {"target_step": "接口设计",
                 "original_approach": "微服务架构",
                 "simplified_approach": "单体 + 模块化",
                 "impact": "降低部署复杂度"}
            ]
        }
        mock_orchestrator.fast_llm.set_responses([
            brake_with_mandatory,
            FEASIBILITY_BRAKE_RESPONSE,
            FEASIBILITY_BRAKE_RESPONSE,
        ])

        steps = [Step.from_dict(s) for s in DECOMPOSE_RESPONSE["steps"]]
        schemes = {
            "A": PlanScheme(agent_role="UX/产品专家",
                           complexity_score=10),  # 远超阈值
            "B": PlanScheme(agent_role="性能架构师"),
            "C": PlanScheme(agent_role="安全专家"),
        }
        state = OrchestratorState(original_task="测试任务")

        result = mock_orchestrator._feasibility_brake(state, schemes, steps)

        # 方案 A 应被标记为已简化，且复杂度被压回阈值
        assert result["A"].simplification_applied is True
        assert result["A"].complexity_score <= mock_orchestrator.complexity_threshold


# ============================================================
# 阶段6: 投票评分测试
# ============================================================

class TestVoting:
    def test_vote_produces_ranked_results(self, mock_orchestrator):
        mock_orchestrator.strong_llm.set_responses([VOTE_RESPONSE])

        steps = [Step.from_dict(s) for s in DECOMPOSE_RESPONSE["steps"]]
        schemes = {
            "A": PlanScheme(agent_role="UX/产品专家", object_name="业务价值对象"),
            "B": PlanScheme(agent_role="性能架构师", object_name="技术架构对象"),
            "C": PlanScheme(agent_role="安全专家", object_name="安全与合规对象"),
        }
        state = OrchestratorState(original_task="测试任务")

        plan_list = list(schemes.values())
        results = mock_orchestrator.scorer.score_plans(
            state.original_task, plan_list, steps)

        assert len(results) == 3
        # 应按总分降序
        assert results[0].total_score >= results[1].total_score
        assert results[1].total_score >= results[2].total_score
        # rank 应为 1, 2, 3
        assert results[0].rank == 1
        assert results[1].rank == 2
        assert results[2].rank == 3


# ============================================================
# 状态持久化测试
# ============================================================

class TestStatePersistence:
    def test_state_roundtrip(self, tmp_path, mock_orchestrator):
        """状态序列化后反序列化应保持数据一致"""
        mock_orchestrator.data_dir = tmp_path

        state = OrchestratorState(
            original_task="设计用户登录系统",
            step_list=[Step(index=1, name="认证", description="...").to_dict()],
            phase="phase1_decompose",
        )
        state.object_coverage = {"业务价值对象": 0.8, "安全与合规对象": 0.6}
        state.scheme_complexity = {"A": 3, "B": 4, "C": 2}

        mock_orchestrator._save_state(state)

        # 从文件加载
        loaded = mock_orchestrator._load_state(state.task_id)
        assert loaded is not None
        assert loaded.original_task == "设计用户登录系统"
        assert loaded.object_coverage == state.object_coverage
        assert loaded.scheme_complexity == state.scheme_complexity

    def test_get_state_returns_none_for_unknown_id(self, mock_orchestrator):
        result = mock_orchestrator.get_state("nonexistent_id")
        assert result is None
