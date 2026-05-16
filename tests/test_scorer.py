"""独立评委评分模块单元测试"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.scorer import Scorer, VOTE_WEIGHTS
from src.models import Step, PlanScheme, VoteResult
from tests.conftest import MockLLMAdapter, VOTE_RESPONSE


@pytest.fixture
def mock_scorer():
    mock_llm = MockLLMAdapter([VOTE_RESPONSE])
    return Scorer(mock_llm)


class TestVoteWeights:
    def test_weights_sum_to_one(self):
        total = sum(VOTE_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_feasibility_has_highest_weight(self):
        assert VOTE_WEIGHTS["feasibility"] == 0.25
        assert VOTE_WEIGHTS["feasibility"] >= VOTE_WEIGHTS["correctness"]
        assert VOTE_WEIGHTS["feasibility"] >= VOTE_WEIGHTS["completeness"]
        assert VOTE_WEIGHTS["feasibility"] >= VOTE_WEIGHTS["innovation"]
        assert VOTE_WEIGHTS["feasibility"] >= VOTE_WEIGHTS["business_alignment"]


class TestScorePlans:
    def test_returns_ranked_results(self, mock_scorer):
        steps = [
            Step(index=1, name="接口设计", description=""),
            Step(index=2, name="数据存储", description=""),
        ]
        plans = [
            PlanScheme(agent_role="UX/产品专家", object_name="业务价值对象", id="A",
                steps={steps[0].id: "详细设计：RESTful API + GraphQL 双协议支持，OpenAPI 3.0 文档自动生成"}),
            PlanScheme(agent_role="性能架构师", object_name="技术架构对象", id="B",
                steps={steps[0].id: "详细设计：异步非阻塞架构 + Redis 多级缓存 + CDN 加速"}),
            PlanScheme(agent_role="安全专家", object_name="安全与合规对象", id="C",
                steps={steps[0].id: "详细设计：JWT + OAuth2.0 + RBAC + 审计日志 + WAF 防护"}),
        ]

        results = mock_scorer.score_plans("测试任务", plans, steps)
        assert len(results) == 3
        assert results[0].rank == 1
        assert results[2].rank == 3

    def test_total_score_calculation(self, mock_scorer):
        """验证加权总分计算逻辑"""
        steps = [Step(index=1, name="接口设计", description="")]
        plans = [PlanScheme(agent_role="UX/产品专家", id="A",
            steps={steps[0].id: "RESTful API 设计，包含版本化端点、限流和 API Key 管理"})]

        results = mock_scorer.score_plans("测试任务", plans, steps)
        r = results[0]

        expected_total = (
            r.correctness * VOTE_WEIGHTS["correctness"]
            + r.completeness * VOTE_WEIGHTS["completeness"]
            + r.feasibility * VOTE_WEIGHTS["feasibility"]
            + r.innovation * VOTE_WEIGHTS["innovation"]
            + r.business_alignment * VOTE_WEIGHTS["business_alignment"]
        )
        assert abs(r.total_score - round(expected_total, 2)) < 0.01

    def test_plan_ids_are_single_letters(self, mock_scorer):
        """验证 plan_id 被清理为单个字母"""
        steps = [Step(index=1, name="接口设计", description="")]
        plans = [PlanScheme(agent_role="UX/产品专家", id="A",
            steps={steps[0].id: "完整的技术方案设计，包含 API 版本管理和认证机制"})]

        results = mock_scorer.score_plans("测试任务", plans, steps)
        for r in results:
            assert len(r.plan_id) == 1
            assert r.plan_id in "ABC"

    def test_empty_plan_list(self):
        """空方案列表返回空结果"""
        mock_llm = MockLLMAdapter([{"results": []}])
        scorer = Scorer(mock_llm)
        results = scorer.score_plans("测试任务", [], [])
        assert len(results) == 0

    def test_empty_content_guard(self):
        """方案内容为空时，拒绝评分并给出明确提示"""
        steps = [Step(index=1, name="接口设计", description="")]
        plans = [
            PlanScheme(agent_role="安全专家", id="A"),  # 无 steps 内容
            PlanScheme(agent_role="性能架构师", id="B"),  # 无 steps 内容
        ]
        mock_llm = MockLLMAdapter([])
        scorer = Scorer(mock_llm)
        results = scorer.score_plans("测试任务", plans, steps)
        assert len(results) == 2
        for r in results:
            assert r.total_score == 0
            assert r.rank == 0
            assert "API Key" in r.comment or "为空" in r.comment


class TestVoteResult:
    def test_vote_result_creation(self):
        v = VoteResult(
            plan_id="A",
            correctness=8.5,
            completeness=7.0,
            feasibility=9.0,
            innovation=6.0,
            business_alignment=8.0,
        )
        assert v.plan_id == "A"
        assert v.correctness == 8.5

    def test_vote_result_serialization(self):
        v = VoteResult(
            plan_id="B", correctness=9.0, completeness=9.0,
            feasibility=8.5, innovation=7.5, business_alignment=9.5,
            total_score=8.75, rank=1, comment="最佳方案",
        )
        d = v.to_dict()
        assert d["plan_id"] == "B"
        assert d["rank"] == 1
        assert d["total_score"] == 8.75
