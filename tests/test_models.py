"""数据模型单元测试"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import (
    Step, PlanScheme, ModificationRecord, VoteResult,
    OrchestratorState, AgentRole, OrchestratorPhase,
)


def test_step_creation():
    s = Step(index=1, name="接口设计", description="定义API端点")
    assert s.index == 1
    assert s.name == "接口设计"
    assert s.id.startswith("step_")
    d = s.to_dict()
    s2 = Step.from_dict(d)
    assert s2.name == "接口设计"


def test_plan_scheme():
    p = PlanScheme(agent_role="安全专家", agent_name="agent_1")
    p.steps["step_1"] = "设计方案内容"
    p.modified_steps.append("step_1")
    p.modification_history.append({
        "agent_id": "agent_2", "agent_role": "性能架构师",
        "target_step": "step_1", "change_type": "enhancement",
        "content": "增加缓存层", "reason": "提升响应速度",
    })
    assert p.is_paused is False
    d = p.to_dict()
    assert d["agent_role"] == "安全专家"


def test_vote_result():
    v = VoteResult(plan_id="A", correctness=8.5, completeness=7.0,
                   feasibility=9.0, innovation=6.0, business_alignment=8.0)
    v.total_score = (8.5 * 0.25 + 7.0 * 0.25 + 9.0 * 0.20
                     + 6.0 * 0.15 + 8.0 * 0.15)
    assert abs(v.total_score - 7.775) < 0.001


def test_orchestrator_state_roundtrip():
    s = OrchestratorState(
        original_task="设计用户登录系统",
        step_list=[Step(index=1, name="认证", description="...").to_dict()],
    )
    d = s.to_dict()
    s2 = OrchestratorState.from_dict(d)
    assert s2.original_task == "设计用户登录系统"
    assert s2.phase == "phase1_decompose"


def test_agent_roles():
    assert AgentRole.SECURITY.value == "安全专家"
    assert AgentRole.PERFORMANCE.value == "性能架构师"
    assert AgentRole.UX.value == "UX/产品专家"


def test_phases():
    assert OrchestratorPhase.DECOMPOSE.value == "phase1_decompose"
    assert OrchestratorPhase.VOTE.value == "phase6_vote"
    assert OrchestratorPhase.DONE.value == "done"


if __name__ == "__main__":
    test_step_creation()
    test_plan_scheme()
    test_vote_result()
    test_orchestrator_state_roundtrip()
    test_agent_roles()
    test_phases()
    print("All model tests passed")
