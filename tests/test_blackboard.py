"""黑板模式回归测试"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import src.blackboard as _bb
from src.blackboard import BlackboardOrchestrator, AGENT_ROLES


def _bb_root():
    """获取当前黑板根目录（尊重 monkeypatch）"""
    return _bb.BLACKBOARD_ROOT


@pytest.fixture
def bb(tmp_path, monkeypatch):
    """创建使用临时目录的黑板编排器"""
    monkeypatch.setattr(_bb, "BLACKBOARD_ROOT", tmp_path)
    orch = BlackboardOrchestrator()
    return orch


@pytest.fixture
def task_with_skipped_agent(bb):
    """创建一个任务，其中 UX Agent 处于 review_skipped 状态"""
    task_id = bb.create_task("设计一个文件分享服务")
    # 直接写入状态文件模拟 Agent 跳过
    for role in AGENT_ROLES:
        agent_dir = _bb_root() / task_id / "agents" / role
        agent_dir.mkdir(parents=True, exist_ok=True)
        if role == "ux":
            status = {"phase": "review_skipped", "reason": "no other proposals to review"}
        else:
            status = {"phase": "review_done"}
        (agent_dir / "status.json").write_text(
            json.dumps(status, ensure_ascii=False), encoding="utf-8"
        )
    return task_id


@pytest.fixture
def task_with_failed_agent(bb):
    """创建一个任务，其中 Security Agent 处于 review_error 状态"""
    task_id = bb.create_task("设计一个高并发短链接服务")
    for role in AGENT_ROLES:
        agent_dir = _bb_root() / task_id / "agents" / role
        agent_dir.mkdir(parents=True, exist_ok=True)
        if role == "security":
            status = {"phase": "vote_error", "error": "API connection timeout"}
        else:
            status = {"phase": "vote_done"}
        (agent_dir / "status.json").write_text(
            json.dumps(status, ensure_ascii=False), encoding="utf-8"
        )
    return task_id


@pytest.fixture
def task_with_no_votes(bb):
    """创建没有任何投票数据的任务"""
    task_id = bb.create_task("设计一个简单的 API")
    # 所有 Agent 完成但未生成 vote.json
    for role in AGENT_ROLES:
        agent_dir = _bb_root() / task_id / "agents" / role
        agent_dir.mkdir(parents=True, exist_ok=True)
        status = {"phase": "vote_skipped", "reason": "need 2+ proposals"}
        (agent_dir / "status.json").write_text(
            json.dumps(status, ensure_ascii=False), encoding="utf-8"
        )
    return task_id


@pytest.fixture
def task_with_corrupt_vote(bb):
    """创建包含损坏 vote.json 的任务"""
    task_id = bb.create_task("设计一个博客平台")
    for role in AGENT_ROLES:
        agent_dir = _bb_root() / task_id / "agents" / role
        agent_dir.mkdir(parents=True, exist_ok=True)
        status = {"phase": "vote_done"}
        (agent_dir / "status.json").write_text(
            json.dumps(status, ensure_ascii=False), encoding="utf-8"
        )
        if role == "architecture":
            # 写入损坏的 JSON
            (agent_dir / "vote.json").write_text("{invalid json!!!", encoding="utf-8")
        else:
            valid_vote = {
                "votes": [{
                    "target": role,
                    "correctness": 8.0, "completeness": 7.0,
                    "feasibility": 9.0, "innovation": 6.0,
                    "business_alignment": 8.0, "comment": "ok"
                }]
            }
            (agent_dir / "vote.json").write_text(
                json.dumps(valid_vote, ensure_ascii=False), encoding="utf-8"
            )
    return task_id


# ==================== Phase 检查测试 ====================

class TestPhaseCheckHandlesSkipped:
    """_check_agents_phase 应将 _skipped 视为完成"""

    def test_skipped_agent_counts_as_done(self, bb, task_with_skipped_agent):
        status = bb._check_agents_phase(task_with_skipped_agent, "review")
        assert status["all_done"] is True
        assert status["done"] == 3  # 所有 Agent 均视为完成

    def test_skipped_agent_generates_warning(self, bb, task_with_skipped_agent):
        status = bb._check_agents_phase(task_with_skipped_agent, "review")
        assert status["warnings"] is not None
        assert any("review_skipped" in w for w in status["warnings"])
        assert any("no other proposals" in w for w in status["warnings"])


class TestPhaseCheckHandlesError:
    """_check_agents_phase 应将 _error 视为完成"""

    def test_error_agent_counts_as_done(self, bb, task_with_failed_agent):
        status = bb._check_agents_phase(task_with_failed_agent, "vote")
        assert status["all_done"] is True
        assert status["done"] == 3

    def test_error_agent_generates_warning_with_reason(self, bb, task_with_failed_agent):
        status = bb._check_agents_phase(task_with_failed_agent, "vote")
        assert status["warnings"] is not None
        assert any("vote_error" in w for w in status["warnings"])
        assert any("API connection timeout" in w for w in status["warnings"])


# ==================== Merge 测试 ====================

class TestMergeAllEmptyVotes:
    """_merge_all 在无 vote 数据时应返回清晰错误"""

    def test_returns_error_field(self, bb, task_with_no_votes):
        result = bb._merge_all(task_with_no_votes)
        assert result["error"] == "no_vote_data"
        assert result["rankings"] == []
        assert result["top1"] is None

    def test_returns_actionable_hint(self, bb, task_with_no_votes):
        result = bb._merge_all(task_with_no_votes)
        assert "collusion_blackboard_status" in result["hint"]
        assert task_with_no_votes in result["hint"]


class TestMergeAllCorruptVote:
    """_merge_all 应容忍损坏的 vote.json 文件"""

    def test_skips_corrupt_vote_file(self, bb, task_with_corrupt_vote):
        result = bb._merge_all(task_with_corrupt_vote)
        # 应产生 2 个方案的排名（跳过损坏的 architecture）
        assert len(result["rankings"]) == 2
        assert "error" not in result  # 错误只用于完全无数据
        assert result["top1"] is not None


# ==================== 错误边界测试 ====================

class TestOrchestrateFullErrorBoundary:
    """orchestrate_full 不应让异常导致后台线程静默死亡"""

    def test_nonexistent_task_returns_error(self, bb):
        result = bb.orchestrate_full("bb_nonexistent")
        assert "error" in result
        assert "不存在" in result["error"] or "nonexistent" in result["error"].lower()

    def test_task_marked_as_error_on_exception(self, bb, monkeypatch):
        task_id = bb.create_task("设计一个简单的 TODO App")
        # 让 _spawn_agents 抛异常
        def blow_up(*args, **kwargs):
            raise RuntimeError("simulated subprocess failure")
        monkeypatch.setattr(bb, "_spawn_agents", blow_up)

        result = bb.orchestrate_full(task_id)
        assert "error" in result
        assert "simulated subprocess failure" in result["error"]

        # 验证 task 被标记为 error 状态 (持久化)
        task_data = bb.read_task(task_id)
        assert task_data["status"] == "error"
