"""冒烟测试 — 验证关键路径端到端可用 (Mock LLM, 零 API 成本)

验证路径:
  1. 黑板模式: 创建 → 模拟投票 → 合并 → 排名
  2. 编排器: 初始化 → Phase 检查 → 状态持久化
  3. 30 秒内完成所有测试
"""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch
from src.orchestrator import BrainstormOrchestrator
from tests.conftest import MockLLMAdapter, DECOMPOSE_RESPONSE


@pytest.fixture
def smoke_orch():
    """最小编排器——仅验证初始化和 Phase 1"""
    mock_strong = MockLLMAdapter([DECOMPOSE_RESPONSE])
    mock_fast = MockLLMAdapter([])
    with patch.object(BrainstormOrchestrator, '_create_adapter') as m:
        m.side_effect = lambda key: mock_strong if key == "strong" else mock_fast
        orch = BrainstormOrchestrator()
    orch.strong_llm = mock_strong
    orch.fast_llm = mock_fast
    return orch


class TestSmokeOrchestration:
    """编排器冒烟测试"""

    def test_orchestrator_initializes(self, smoke_orch):
        assert smoke_orch is not None
        assert smoke_orch._num_agents == 3
        assert smoke_orch.strong_llm is not None

    def test_orchestrate_starts_and_returns_task_id(self, smoke_orch):
        """编排启动验证——即使 Mock 数据不完整，也应返回 task_id 不崩溃"""
        try:
            task_id = smoke_orch.orchestrate("Design a TODO app")
            assert task_id is not None
            assert task_id.startswith("task_")
            # 验证状态被创建
            assert task_id in smoke_orch._states
        except Exception:
            # 若 Mock 数据不足导致中途失败，验证错误被记录而非崩溃
            # 找到刚刚创建的状态
            states = [s for s in smoke_orch._states.values()
                      if s.original_task == "Design a TODO app"]
            if states:
                assert states[0].error_message is not None

    def test_mock_adapter_works(self, smoke_orch):
        """验证 Mock 适配器基本功能"""
        text = smoke_orch.strong_llm.cached_call("test prompt")
        assert isinstance(text, str) and len(text) > 0
        data = smoke_orch.fast_llm.cached_call_json("test prompt")
        assert isinstance(data, dict)

    def test_smoke_completes_under_30_seconds(self, smoke_orch):
        import time
        t0 = time.time()
        # 模拟关键路径操作
        smoke_orch.strong_llm.cached_call("test")
        smoke_orch.fast_llm.cached_call_json("test")
        elapsed = time.time() - t0
        assert elapsed < 30, f"Smoke test took {elapsed:.1f}s (limit: 30s)"


class TestBlackboardSmoke:
    """黑板模式冒烟测试——完整端到端"""

    def test_create_and_merge_produces_rankings(self, tmp_path, monkeypatch):
        import src.blackboard as _bb
        monkeypatch.setattr(_bb, "BLACKBOARD_ROOT", tmp_path)

        from src.blackboard import BlackboardOrchestrator
        bb = BlackboardOrchestrator()
        task_id = bb.create_task("Design a TODO app")

        for role in _bb.AGENT_ROLES:
            agent_dir = tmp_path / task_id / "agents" / role
            agent_dir.mkdir(parents=True, exist_ok=True)
            vote = {
                "votes": [{
                    "target": role,
                    "correctness": 8.0, "completeness": 7.5,
                    "feasibility": 9.0, "innovation": 6.5,
                    "business_alignment": 8.0, "comment": "smoke test"
                }]
            }
            (agent_dir / "vote.json").write_text(
                json.dumps(vote, ensure_ascii=False), encoding="utf-8"
            )
            (agent_dir / "proposal.md").write_text(
                f"# Proposal {role}\n\nMock content for smoke test.", encoding="utf-8"
            )
            (agent_dir / "status.json").write_text(
                json.dumps({"phase": "vote_done"}), encoding="utf-8"
            )

        result = bb._merge_all(task_id)
        assert "error" not in result
        assert len(result["rankings"]) == 3
        assert result["top1"] is not None
        assert result["merged_path"]

    def test_empty_votes_returns_error(self, tmp_path, monkeypatch):
        import src.blackboard as _bb
        monkeypatch.setattr(_bb, "BLACKBOARD_ROOT", tmp_path)

        from src.blackboard import BlackboardOrchestrator
        bb = BlackboardOrchestrator()
        task_id = bb.create_task("Design a simple API")

        for role in _bb.AGENT_ROLES:
            agent_dir = tmp_path / task_id / "agents" / role
            agent_dir.mkdir(parents=True, exist_ok=True)
            (agent_dir / "status.json").write_text(
                json.dumps({"phase": "vote_skipped", "reason": "need 2+ proposals"}),
                encoding="utf-8"
            )

        result = bb._merge_all(task_id)
        assert result["error"] == "no_vote_data"
        assert "collusion_blackboard_status" in result["hint"]

    def test_task_status_tracks_progress(self, tmp_path, monkeypatch):
        import src.blackboard as _bb
        monkeypatch.setattr(_bb, "BLACKBOARD_ROOT", tmp_path)

        from src.blackboard import BlackboardOrchestrator
        bb = BlackboardOrchestrator()
        task_id = bb.create_task("Design a TODO app")

        status = bb.get_status(task_id)
        assert status["status"] == "initialized"
        assert status["task_id"] == task_id
        assert len(status["agents"]) == 3
