"""v0.4.0 新增功能测试：资产库、分支合并、Mermaid、引导交互"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock
from src.orchestrator import BrainstormOrchestrator
from src.models import (
    Step, PlanScheme, OrchestratorState, OrchestratorPhase,
    ElicitationQuestion,
)
from src.llm.mcp_sampling import MCPSamplingAdapter
from tests.conftest import MockLLMAdapter


# ============================================================
# 1. Mermaid 图生成测试
# ============================================================

class TestMermaidGeneration:
    """测试架构图生成"""

    def test_generate_mermaid_flow(self):
        steps = [
            {"index": 1, "name": "接口设计", "description": "RESTful API"},
            {"index": 2, "name": "数据存储", "description": "SQLite"},
        ]
        result = BrainstormOrchestrator._generate_mermaid_flow(
            "测试任务", steps,
        )
        assert "graph TD" in result
        assert "Phase1" in result or "任务解构" in result
        assert "Phase4" in result or "交叉审查" in result
        assert "Top3" in result
        # 不应超过合理长度
        assert len(result) < 5000

    def test_generate_scheme_mermaid_frontend_backend(self):
        content = (
            "前端使用 React + TypeScript，后端使用 Go 语言。"
            "数据库选择 SQLite，通过 Docker 部署。"
            "安全性使用 JWT 认证和 HTTPS 加密。"
        )
        result = BrainstormOrchestrator._generate_scheme_mermaid(
            "A", "业务价值对象", content,
        )
        assert "graph TB" in result
        assert "FRONT" in result
        assert "BACK" in result
        assert "DB" in result
        assert "OPS" in result  # 检测到 Docker
        assert "SEC" in result   # 检测到 JWT

    def test_generate_scheme_mermaid_fallback(self):
        """没有明确架构关键词时，生成默认三层"""
        result = BrainstormOrchestrator._generate_scheme_mermaid(
            "B", "性能架构师", "这是一个通用方案，没有具体技术栈描述。",
        )
        assert "FRONT" in result
        assert "BACK" in result
        assert "DB" in result

    def test_generate_radar_svg(self):
        schemes = [
            {"id": "A", "total_score": 8.5, "scores": {
                "正确性": 8.5, "完整性": 9.0, "可行性": 8.0,
                "创新性": 7.5, "业务对齐": 9.0,
            }},
            {"id": "B", "total_score": 9.0, "scores": {
                "正确性": 9.0, "完整性": 8.0, "可行性": 9.5,
                "创新性": 7.0, "业务对齐": 8.5,
            }},
        ]
        dims = ["正确性", "完整性", "可行性", "创新性", "业务对齐"]
        svg = BrainstormOrchestrator._render_radar_svg(schemes, dims)
        assert "<svg" in svg
        assert "</svg>" in svg
        assert "polygon" in svg.lower()
        # 应有图例
        assert "方案 A" in svg
        assert "方案 B" in svg


# ============================================================
# 2. 资产库索引与检索测试
# ============================================================

class TestAssetLibrary:
    """测试废案资产库的索引和检索"""

    def test_index_scheme_assets(self, tmp_path):
        orchestrator = BrainstormOrchestrator()
        orchestrator.data_dir = tmp_path / "data"

        state = OrchestratorState(
            task_id="task_test_001",
            original_task="设计一个高并发短链接服务，使用 Redis 做缓存",
            step_list=[
                {"index": 1, "name": "API 设计"},
                {"index": 2, "name": "数据库选型"},
            ],
            schemes={
                "A": {"object_name": "业务价值", "agent_role": "UX",
                      "integrated_content": "方案A正文..."},
                "B": {"object_name": "技术架构", "agent_role": "性能",
                      "integrated_content": "方案B正文..."},
            },
            vote_results=[
                {"plan_id": "A", "rank": 1, "total_score": 8.5},
                {"plan_id": "B", "rank": 2, "total_score": 7.8},
            ],
        )

        keywords = orchestrator._index_scheme_assets(state)

        # 验证关键词提取
        assert "高并发" in keywords
        assert "Redis" in keywords

        # 验证文件创建
        index_path = orchestrator.data_dir / "asset_library" / "index.json"
        assert index_path.exists()

        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

        assert len(index) == 2
        for key in ("task_test_001_A", "task_test_001_B"):
            assert key in index
            assert index[key]["is_top1"] == (key.endswith("A"))

    def test_search_assets(self, tmp_path):
        orchestrator = BrainstormOrchestrator()
        orchestrator.data_dir = tmp_path / "data"

        # 手动写入资产库数据
        asset_dir = orchestrator.data_dir / "asset_library"
        asset_dir.mkdir(parents=True)
        index = {
            "t1_A": {
                "task_id": "t1", "scheme_id": "A",
                "task": "设计一个博客平台",
                "keywords": ["博客", "Docker"],
                "object_name": "业务价值",
                "total_score": 8.5, "is_top1": True,
                "summary": "使用 Go+SQLite 单二进制部署",
                "created_at": "2026-01-01",
            },
            "t2_A": {
                "task_id": "t2", "scheme_id": "A",
                "task": "设计一个高并发短链接服务",
                "keywords": ["高并发", "Redis", "短链接"],
                "object_name": "技术架构",
                "total_score": 9.0, "is_top1": True,
                "summary": "使用 Redis 存储短链接映射",
                "created_at": "2026-02-01",
            },
        }
        with open(asset_dir / "index.json", "w", encoding="utf-8") as f:
            json.dump(index, f)

        # 检索关键词匹配
        results = orchestrator.search_assets("博客", top_k=5)
        assert len(results) >= 1
        assert results[0]["task"] == "设计一个博客平台"

        # 检索短链接
        results = orchestrator.search_assets("短链接高并发", top_k=5)
        assert len(results) >= 1
        assert "短链接" in str(results[0]["keywords"])

        # 无匹配
        results = orchestrator.search_assets("区块链", top_k=5)
        assert len(results) == 0

    def test_search_assets_empty_library(self, tmp_path):
        orchestrator = BrainstormOrchestrator()
        orchestrator.data_dir = tmp_path / "data"
        results = orchestrator.search_assets("任意")
        assert results == []


# ============================================================
# 3. 会话分支与合并测试
# ============================================================

class TestBranchAndMerge:
    """测试分支和合并功能"""

    def test_branch_creates_new_state(self, tmp_path):
        orchestrator = BrainstormOrchestrator()
        orchestrator.data_dir = tmp_path / "data"

        # 创建一个父任务
        parent = OrchestratorState(
            task_id="task_parent_001",
            original_task="设计一个博客平台",
            step_list=[{"index": 1, "name": "接口设计"}],
            schemes={
                "A": {
                    "object_name": "业务价值",
                    "integrated_content": "使用 Go+SQLite 方案",
                },
            },
            vote_results=[
                {"plan_id": "A", "rank": 1, "total_score": 8.5},
            ],
            phase="done",
        )
        orchestrator._save_state(parent)

        # 从废案中分支
        branch_id = orchestrator.branch("task_parent_001", "alternative")
        assert branch_id
        assert branch_id.startswith("task_")
        assert branch_id != "task_parent_001"

        # 验证分支状态
        branch_state = orchestrator._load_state(branch_id)
        assert branch_state is not None
        assert branch_state.phase == "branched"
        # 分支任务描述应包含原任务
        assert "博客平台" in branch_state.original_task

    def test_branch_unknown_parent(self):
        orchestrator = BrainstormOrchestrator()
        branch_id = orchestrator.branch("nonexistent", "top1")
        assert branch_id == ""

    def test_merge_branches_best_per_step(self, tmp_path):
        orchestrator = BrainstormOrchestrator()
        orchestrator.data_dir = tmp_path / "data"

        # 创建两个分支
        step1_id = "step_aaa"
        step2_id = "step_bbb"

        branch_a = OrchestratorState(
            task_id="task_branch_a",
            original_task="设计博客",
            step_list=[
                {"id": step1_id, "index": 1, "name": "接口设计"},
                {"id": step2_id, "index": 2, "name": "数据存储"},
            ],
            schemes={
                "A": {
                    "object_name": "业务价值",
                    "agent_role": "UX",
                    "steps": {
                        step1_id: "RESTful API 设计",
                        step2_id: "PostgreSQL 存储",
                    },
                },
            },
            vote_results=[
                {"plan_id": "A", "total_score": 8.5},
            ],
        )
        branch_b = OrchestratorState(
            task_id="task_branch_b",
            original_task="设计博客",
            step_list=[
                {"id": step1_id, "index": 1, "name": "接口设计"},
                {"id": step2_id, "index": 2, "name": "数据存储"},
            ],
            schemes={
                "B": {
                    "object_name": "技术架构",
                    "agent_role": "性能",
                    "steps": {
                        step1_id: "GraphQL API 设计",
                        step2_id: "SQLite 存储",
                    },
                },
            },
            vote_results=[
                {"plan_id": "B", "total_score": 9.0},
            ],
        )

        orchestrator._save_state(branch_a)
        orchestrator._save_state(branch_b)

        result = orchestrator.merge_branches(
            ["task_branch_a", "task_branch_b"],
        )

        assert "merged_steps" in result
        assert len(result["merged_steps"]) == 2
        # 应选取得分最高的（B 得分 9.0）
        assert result["merged_steps"][0]["best_design"] == "GraphQL API 设计"
        assert result["merged_steps"][1]["best_design"] == "SQLite 存储"

    def test_merge_insufficient_branches(self):
        orchestrator = BrainstormOrchestrator()
        result = orchestrator.merge_branches(["single_branch"])
        assert "error" in result


# ============================================================
# 4. Elicitation 引导交互测试
# ============================================================

class TestElicitation:
    """测试引导交互检测和应用"""

    def test_detect_elicitation_questions(self):
        orchestrator = BrainstormOrchestrator()
        # 注入 mock LLM
        mock = MockLLMAdapter([{
            "questions": [
                {"category": "security",
                 "question": "需要支持哪些认证方式？",
                 "context": "用于确定安全方案"},
                {"category": "scale",
                 "question": "预期并发用户数是多少？",
                 "context": "用于确定架构容量"},
            ],
        }])
        orchestrator.fast_llm = mock

        steps = [{"index": 1, "name": "接口设计"}]
        questions = orchestrator.detect_elicitation_questions(
            "设计一个 API 服务", steps,
        )

        assert len(questions) == 2
        assert questions[0]["id"] == "elicit_0"
        assert questions[0]["category"] == "security"
        assert not questions[0]["answered"]

    def test_detect_elicitation_empty_when_enough_steps(self):
        """步骤足够多时不应弹出引导问题"""
        orchestrator = BrainstormOrchestrator()
        mock = MockLLMAdapter([{"questions": []}])
        orchestrator.fast_llm = mock

        steps = [
            {"index": 1, "name": "接口"},
            {"index": 2, "name": "数据"},
            {"index": 3, "name": "安全"},
            {"index": 4, "name": "部署"},
        ]
        questions = orchestrator.detect_elicitation_questions(
            "设计一个完整系统", steps,
        )
        assert questions == []

    def test_apply_elicitation_answers(self):
        orchestrator = BrainstormOrchestrator()
        state = OrchestratorState(
            task_id="test_elicit",
            elicitation_questions=[
                {"id": "elicit_0", "category": "security",
                 "question": "认证方式？", "context": "", "answer": "", "answered": False},
                {"id": "elicit_1", "category": "scale",
                 "question": "并发数？", "context": "", "answer": "", "answered": False},
            ],
        )

        answers = {"elicit_0": "OAuth2.0 + JWT", "elicit_1": "10万"}
        updated = orchestrator.apply_elicitation_answers(state, answers)

        assert updated.elicitation_questions[0]["answer"] == "OAuth2.0 + JWT"
        assert updated.elicitation_questions[0]["answered"]
        assert updated.elicitation_questions[1]["answer"] == "10万"
        assert updated.elicitation_answered  # 全部已回答


# ============================================================
# 5. MCPSamplingAdapter 测试
# ============================================================

class TestMCPSamplingAdapter:
    """测试 MCP Sampling 适配器"""

    def test_adapter_creation(self):
        adapter = MCPSamplingAdapter(model="host-default")
        assert adapter.model_name == "host-default"
        assert adapter.total_input_tokens == 0
        assert adapter.total_output_tokens == 0

    def test_missing_callback_raises(self):
        adapter = MCPSamplingAdapter()
        # 清除类级回调
        MCPSamplingAdapter._sampling_callback = None
        with pytest.raises(RuntimeError, match="回调未设置"):
            adapter._do_chat([{"role": "user", "content": "test"}], 0.1, 100)

    def test_cost_calculation(self):
        adapter = MCPSamplingAdapter()
        adapter.total_input_tokens = 10000
        adapter.total_output_tokens = 5000
        cost = adapter.total_cost_rmb
        assert cost > 0
        # 委托模式成本应极低（仅统计）
        assert cost < 0.1


# ============================================================
# 6. ElicitationQuestion 模型测试
# ============================================================

class TestElicitationQuestionModel:
    """测试引导问题数据模型序列化"""

    def test_to_dict(self):
        q = ElicitationQuestion(
            id="eq_01", category="security",
            question="认证方式？", context="用于安全方案",
        )
        d = q.to_dict()
        assert d["id"] == "eq_01"
        assert d["category"] == "security"
        assert not d["answered"]

    def test_from_dict(self):
        d = {
            "id": "eq_02", "category": "scale",
            "question": "并发数？", "context": "",
            "answer": "1000", "answered": True,
        }
        q = ElicitationQuestion.from_dict(d)
        assert q.id == "eq_02"
        assert q.answered

    def test_roundtrip(self):
        q = ElicitationQuestion(
            id="eq_03", category="deployment",
            question="部署环境？", context="确定基础设施",
            answer="Docker", answered=True,
        )
        q2 = ElicitationQuestion.from_dict(q.to_dict())
        assert q2.id == q.id
        assert q2.answer == q.answer
        assert q2.answered == q.answered
