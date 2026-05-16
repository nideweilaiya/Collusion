"""Brainstorm Orchestrator v3.1 — 数据模型（对象化架构）"""
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
from enum import Enum
import uuid
import time


class AgentRole(str, Enum):
    SECURITY = "安全专家"
    PERFORMANCE = "性能架构师"
    UX = "UX/产品专家"


class ObjectType(str, Enum):
    """v3.1: 每个Agent代言的对象类型"""
    BUSINESS = "业务价值对象"
    ARCHITECTURE = "技术架构对象"
    SECURITY = "安全与合规对象"
    ENGINEERING = "工程实现对象"


# Agent角色到代言对象的映射
ROLE_OBJECT_MAP = {
    AgentRole.UX: ObjectType.BUSINESS,
    AgentRole.PERFORMANCE: ObjectType.ARCHITECTURE,
    AgentRole.SECURITY: ObjectType.SECURITY,
}


class OrchestratorPhase(str, Enum):
    DECOMPOSE = "phase1_decompose"
    CONSENSUS = "phase2_consensus"
    PROPOSAL = "phase3_proposal"
    CROSS_REVIEW = "phase4_cross_review"
    FEASIBILITY_BRAKE = "phase4.5_feasibility_brake"     # v3.1新增
    OWNER_INTEGRATION = "phase4.6_owner_integration"      # v3.1新增
    OPTIONAL_ROUND2 = "phase5_optional_round2"
    VOTE = "phase6_vote"
    DONE = "done"
    ERROR = "error"


@dataclass
class Step:
    """任务环节定义"""
    id: str = field(default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}")
    index: int = 0
    name: str = ""
    description: str = ""
    dependencies: List[str] = field(default_factory=list)
    design_content: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ModificationRecord:
    """单次修改记录"""
    agent_id: str = ""
    agent_role: str = ""
    object_name: str = ""       # v3.1: 代言对象名称
    target_step: str = ""
    change_type: str = ""       # "enhancement" | "issue_flag" | "simplification"
    complexity_delta: int = 0   # v3.1: 复杂度增量 +1/+2/+3
    content: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlanScheme:
    """单个Agent的完整方案"""
    id: str = field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:8]}")
    agent_role: str = ""
    agent_name: str = ""
    object_name: str = ""                        # v3.1: 代言对象
    steps: Dict[str, str] = field(default_factory=dict)
    modified_steps: List[str] = field(default_factory=list)
    modification_history: List[Dict] = field(default_factory=list)
    missing_reports: List[Dict] = field(default_factory=list)
    is_paused: bool = False
    complexity_score: int = 0                    # v3.1: 复杂度累积值
    owner_agent_id: str = ""                     # v3.1: 整合Owner
    integrated_content: str = ""                 # v3.1: 深度整合后的完整方案文本
    simplification_applied: bool = False         # v3.1: 是否已收束

    def to_dict(self) -> dict:
        d = asdict(self)
        d["modification_history"] = self.modification_history
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "PlanScheme":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class VoteResult:
    """单个方案的评分结果"""
    plan_id: str = ""
    correctness: float = 0.0
    completeness: float = 0.0
    feasibility: float = 0.0
    innovation: float = 0.0
    business_alignment: float = 0.0
    total_score: float = 0.0
    rank: int = 0
    comment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrchestratorState:
    """编排器完整状态（可序列化持久化）"""
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    original_task: str = ""
    step_list: List[Dict] = field(default_factory=list)
    schemes: Dict[str, Dict] = field(default_factory=dict)
    current_round: int = 1
    max_rounds: int = 2
    agents: List[Dict] = field(default_factory=list)
    round_schedule: List[List] = field(default_factory=list)
    phase: str = "phase1_decompose"
    vote_results: List[Dict] = field(default_factory=list)
    top3_plans: List[Dict] = field(default_factory=list)
    total_cost_rmb: float = 0.0
    total_tokens: int = 0
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    # v3.1 新增字段
    object_coverage: Dict[str, float] = field(default_factory=dict)
    scheme_complexity: Dict[str, int] = field(default_factory=dict)
    business_alignment_warnings: List[Dict] = field(default_factory=list)
    feasibility_brake_records: List[Dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "OrchestratorState":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)
