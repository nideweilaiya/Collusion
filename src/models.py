"""Brainstorm Orchestrator v3.1 — 数据模型（对象化架构）
v0.5.0 新增: 知识标签、因果记忆、资产索引、关联度评分模型
"""
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
from enum import Enum
import uuid
import time


# ==================== v0.5.0 知识库系统 ====================

class TagDimension(str, Enum):
    """知识标签的五维分类"""
    TECH_STACK = "技术栈"          # #Go, #PostgreSQL, #Redis
    DOMAIN = "领域"                # #短链接, #文件分享, #实时协作
    ARCH_PATTERN = "架构模式"      # #事件驱动, #微服务, #单体
    SECURITY_FOCUS = "安全关注点"  # #OAuth2, #SQL注入, #GDPR
    PERFORMANCE = "性能特征"       # #高并发, #低延迟, #大文件传输


@dataclass
class KnowledgeTag:
    """结构化知识标签"""
    dimension: TagDimension       # 标签维度
    value: str                    # 标签值，如 "Go", "微服务", "高并发"
    confidence: float = 1.0       # 提取置信度 0-1
    source: str = "auto"          # 来源: "auto" / "llm" / "human"

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension.value,
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeTag":
        return cls(
            dimension=TagDimension(data.get("dimension", "技术栈")),
            value=data.get("value", ""),
            confidence=data.get("confidence", 1.0),
            source=data.get("source", "auto"),
        )


@dataclass
class AssetEntry:
    """资产库索引条目（增强版 v0.5.0）"""
    task_id: str = ""
    scheme_id: str = ""
    task: str = ""                 # 原始任务描述
    tags: List[Dict] = field(default_factory=list)  # KnowledgeTag 列表
    keywords: List[str] = field(default_factory=list)  # 兼容旧版
    object_name: str = ""          # 代言对象
    agent_role: str = ""           # Agent 角色
    total_score: float = 0.0       # 投票总分
    rank: int = 0                  # 排名 (1=Top1)
    is_top1: bool = False          # 是否为 Top1
    is_discarded: bool = False     # 是否为废案（被淘汰方案）
    discard_reasons: List[str] = field(default_factory=list)  # 淘汰原因
    summary: str = ""              # 方案摘要 (前500字)
    created_at: str = ""           # 创建时间 ISO
    success_outcome: Optional[bool] = None  # 方案最终是否被采纳（None=未知）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AssetEntry":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


# ==================== v0.5.0 因果记忆图 (Prism) ====================

class CausalNodeType(str, Enum):
    """因果图节点类型"""
    DECISION = "decision"          # 决策（如"选择 PostgreSQL"）
    CONSTRAINT = "constraint"      # 约束（如"Docker单命令部署"）
    OUTCOME = "outcome"            # 结果（如"部署复杂度增加"）
    RISK = "risk"                  # 风险（如"SQL注入风险"）
    TASK = "task"                  # 任务节点


class CausalRelation(str, Enum):
    """因果边类型"""
    LEADS_TO = "leads_to"          # 导致
    AVOIDS = "avoids"              # 规避
    ASSOCIATES = "associates"      # 关联
    CONFLICTS = "conflicts"        # 冲突
    ENABLES = "enables"            # 促成


@dataclass
class CausalMemoryNode:
    """因果记忆图节点"""
    id: str = field(default_factory=lambda: f"cm_{uuid.uuid4().hex[:8]}")
    node_type: CausalNodeType = CausalNodeType.DECISION
    label: str = ""                # 简短标签，如"选择PostgreSQL"
    description: str = ""          # 详细描述
    tags: List[str] = field(default_factory=list)  # 关联的技术标签
    task_id: str = ""              # 来源任务ID
    created_at: float = field(default_factory=time.time)
    outcome_score: Optional[float] = None  # 结果评价: -1(坏) ~ +1(好), None=未知

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CausalMemoryNode":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class CausalEdge:
    """因果记忆图边"""
    source_id: str = ""            # 源节点ID
    target_id: str = ""            # 目标节点ID
    relation: CausalRelation = CausalRelation.LEADS_TO
    weight: float = 1.0            # 权重 0-1
    description: str = ""          # 关系描述
    task_id: str = ""              # 来源任务ID

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CausalEdge":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class CausalMemoryGraph:
    """因果记忆图（完整图结构）"""
    nodes: Dict[str, CausalMemoryNode] = field(default_factory=dict)
    edges: List[CausalEdge] = field(default_factory=list)
    version: str = "0.5.0"

    def to_dict(self) -> dict:
        return {
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CausalMemoryGraph":
        graph = cls()
        if "nodes" in data:
            graph.nodes = {
                k: CausalMemoryNode.from_dict(v)
                for k, v in data["nodes"].items()
            }
        if "edges" in data:
            graph.edges = [CausalEdge(**e) for e in data["edges"]]
        graph.version = data.get("version", "0.5.0")
        return graph


@dataclass
class RelevanceResult:
    """关联度查询结果"""
    asset_key: str = ""
    relevance_score: float = 0.0    # 综合关联度 0-1
    tag_overlap: float = 0.0        # 标签重合度分量
    tech_similarity: float = 0.0    # 技术栈相似度分量
    causal_match: float = 0.0       # 因果记忆匹配度分量
    entry: Optional[Dict] = None    # 完整资产条目

    def to_dict(self) -> dict:
        return {
            "asset_key": self.asset_key,
            "relevance_score": self.relevance_score,
            "tag_overlap": self.tag_overlap,
            "tech_similarity": self.tech_similarity,
            "causal_match": self.causal_match,
            "task": (self.entry or {}).get("task", ""),
            "keywords": (self.entry or {}).get("keywords", []),
            "tags": (self.entry or {}).get("tags", []),
            "is_top1": (self.entry or {}).get("is_top1", False),
            "is_discarded": (self.entry or {}).get("is_discarded", False),
            "discard_reasons": (self.entry or {}).get("discard_reasons", []),
            "total_score": (self.entry or {}).get("total_score", 0),
        }


# ==================== 原有模型 ====================

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
class ElicitationQuestion:
    """引导交互问题"""
    id: str = ""
    category: str = ""  # security/performance/ux/deployment/data/scale
    question: str = ""
    context: str = ""  # 为什么问这个问题
    answer: str = ""   # 用户回答
    answered: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ElicitationQuestion":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


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
    output_paths: Dict[str, str] = field(default_factory=dict)  # v3.2: HTML/MD 输出路径
    # v0.4.0 新增字段
    elicitation_questions: List[Dict] = field(default_factory=list)  # 引导交互问题列表
    elicitation_answered: bool = False  # 是否已全部回答
    # v1.0.0: 轻量预检模式结果
    precheck_result: Optional[Dict] = None  # check 模式的预检结果

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "OrchestratorState":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


# ==================== v0.6: 检查点引擎 + 决策支持系统 ====================

class CheckpointSeverity(str, Enum):
    PASS = "pass"
    ADVISORY = "advisory"
    WARNING = "warning"
    BLOCKING = "blocking"


class CheckpointCategory(str, Enum):
    CORE = "core"
    DEEP = "deep"
    DIAGNOSTIC = "diagnostic"


@dataclass
class EvidenceItem:
    source: str = ""           # "asset_library" | "causal_memory" | "checkpoint" | "agent"
    source_id: str = ""
    content: str = ""          # <=100 chars
    weight: float = 0.0       # -1.0 (strongly against) to +1.0 (strongly for)
    confidence: float = 0.0   # 0.0 to 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceItem":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class CompressedSnapshot:
    """情境压缩快照 — 硬预算 ≤500 token (~1250 chars UTF-8)"""
    task_id: str = ""
    task_summary: str = ""           # <=80 chars
    constraints: List[str] = field(default_factory=list)
    relevant_decisions: List[Dict] = field(default_factory=list)
    # [{"decision":"...", "outcome":"正/负", "why":"..."}]
    known_pitfalls: List[Dict] = field(default_factory=list)
    # [{"pitfall":"...", "when":"...", "fix":"..."}]
    discard_warnings: List[Dict] = field(default_factory=list)
    # [{"discarded_approach":"...", "reason":"...", "relevance":0.0}]
    uncertainty_flags: List[str] = field(default_factory=list)
    matched_asset_keys: List[str] = field(default_factory=list)
    risk_score: float = 0.0          # 0.0 = 无风险, 1.0 = 最大风险
    compression_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CompressedSnapshot":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    def to_prompt_fragment(self) -> str:
        lines = []
        if self.task_summary:
            lines.append(f"任务: {self.task_summary}")

        # 防御性: 清洗 constraints
        str_constraints = [
            str(c) if not isinstance(c, str) else c
            for c in self.constraints[:5]
        ]
        if str_constraints:
            lines.append(f"约束: {'; '.join(str_constraints)}")

        if self.relevant_decisions:
            dec_text = "; ".join(
                f"{str(d.get('decision',''))}({str(d.get('outcome',''))})"
                for d in self.relevant_decisions[:5] if isinstance(d, dict))
            if dec_text:
                lines.append(f"历史决策: {dec_text}")

        if self.known_pitfalls:
            pit_text = "; ".join(
                f"{str(p.get('pitfall',''))}->{str(p.get('fix','无解'))}"
                for p in self.known_pitfalls[:3] if isinstance(p, dict))
            if pit_text:
                lines.append(f"已知坑: {pit_text}")

        if self.discard_warnings:
            dis_text = "; ".join(
                f"避免{str(w.get('discarded_approach',''))}({str(w.get('reason',''))[:40]})"
                for w in self.discard_warnings[:3] if isinstance(w, dict))
            if dis_text:
                lines.append(f"废案警示: {dis_text}")

        # 防御性: 清洗 uncertainty_flags
        str_uncertainty = [
            str(u) if not isinstance(u, str) else u
            for u in self.uncertainty_flags[:5]
        ]
        if str_uncertainty:
            lines.append(f"不确定项: {'; '.join(str_uncertainty)}")

        fragment = "\n".join(lines)
        MAX_CHARS = 1250
        fragment = fragment[:MAX_CHARS]
        assert len(fragment) <= MAX_CHARS, f"快照超预算: {len(fragment)} > {MAX_CHARS}"
        return fragment


@dataclass
class RetrievedContext:
    """KnowledgeRetriever 输出 — 检索到的原始资产（未压缩）"""
    task_id: str = ""
    relevant_assets: List[Dict] = field(default_factory=list)
    # [{"task": "", "relevance_score": 0.0, "tags": [], "is_discarded": false,
    #   "discard_reasons": [], "summary": ""}]
    discard_warnings: List[Dict] = field(default_factory=list)
    # [{"task": "", "relevance_score": 0.0, "discard_reasons": []}]
    causal_memories: List[Dict] = field(default_factory=list)
    agent_graph_stats: Dict = field(default_factory=dict)
    retrieval_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RetrievedContext":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class CheckpointResult:
    """检查点执行结果"""
    checkpoint_id: str = ""
    category: str = "core"         # core / deep / diagnostic
    severity: str = "pass"         # pass / advisory / warning / blocking
    summary: str = ""              # <=80 chars
    findings: List[Dict] = field(default_factory=list)
    # [{"type":"gap|conflict|risk|pattern", "target":"", "detail":"", "suggestion":""}]
    risk_score: float = 0.0
    confidence: float = 1.0        # 检查点对自身结论的自信度 0-1
    requires: List[str] = field(default_factory=list)    # 前置依赖
    provides: List[str] = field(default_factory=list)    # 产出能力
    activation_gate: bool = False  # True = 触发深度检查点
    uncertainty_flags: List[str] = field(default_factory=list)
    llm_calls: int = 0
    tokens_used: int = 0
    duration_ms: float = 0.0
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointResult":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class DecisionCard:
    """决策支持卡片 — 核心交付物，不包含方案正文"""

    card_id: str = field(default_factory=lambda: f"dc_{uuid.uuid4().hex[:8]}")
    task_id: str = ""
    created_at: float = field(default_factory=time.time)

    task: str = ""
    task_summary: str = ""

    # 约束
    explicit_constraints: List[str] = field(default_factory=list)
    inferred_constraints: List[str] = field(default_factory=list)

    # 历史决策
    relevant_decisions: List[Dict] = field(default_factory=list)

    # 关键假设
    assumptions: List[Dict] = field(default_factory=list)
    # [{"assumption":"...", "impact_if_wrong":"high|medium|low", "validation":"..."}]

    # 已知坑
    pitfalls: List[Dict] = field(default_factory=list)

    # 风险评估
    overall_risk: str = "low"
    risk_breakdown: Dict[str, float] = field(default_factory=dict)

    # 建议
    suggested_approach: str = ""       # <=150 words
    alternative_approaches: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)

    # 证据链
    evidence: List[Dict] = field(default_factory=list)

    # 检查点摘要
    checkpoints_run: List[str] = field(default_factory=list)
    checkpoint_results: List[Dict] = field(default_factory=list)

    # 深度审查
    deep_review_recommended: bool = False
    deep_review_reason: str = ""

    # 总体置信度
    overall_confidence: float = 0.0    # 引擎汇总各检查点自信度

    # 预算使用
    total_llm_calls: int = 0
    total_tokens: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DecisionCard":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    def to_markdown(self) -> str:
        lines = [
            f"# 决策卡片: {self.task_summary or self.task[:80]}",
            f"**风险等级**: {self.overall_risk.upper()} | **置信度**: {self.overall_confidence:.0%}",
            f"**检查点**: {', '.join(self.checkpoints_run)} | **LLM调用**: {self.total_llm_calls} | **Token**: {self.total_tokens}",
            "",
            "## 约束条件",
        ]
        for c in self.explicit_constraints:
            lines.append(f"- **[显式]** {c}")
        for c in self.inferred_constraints:
            lines.append(f"- [推断] {c}")
        if not self.explicit_constraints and not self.inferred_constraints:
            lines.append("(无已知约束)")

        lines.append("\n## 历史决策参考")
        for d in self.relevant_decisions[:5]:
            icon = "+" if d.get("outcome") in ("正", "positive") else "-"
            lines.append(f"- {icon} {d.get('decision','')}: {d.get('why','')}")

        lines.append("\n## 已知风险与陷阱")
        for p in self.pitfalls[:5]:
            lines.append(f"- **{p.get('pitfall','')}** → {p.get('fix','待定')}")

        lines.append("\n## 关键假设（需验证）")
        for a in self.assumptions[:5]:
            lines.append(f"- {a.get('assumption','')} [影响: {a.get('impact_if_wrong','?')}]")

        lines.append(f"\n## 建议方向\n{self.suggested_approach}")

        lines.append("\n## 建议下一步")
        for s in self.next_steps:
            lines.append(f"- {s}")

        if self.deep_review_recommended:
            lines.append(f"\n## 深度审查建议\n{self.deep_review_reason}")

        return "\n".join(lines)


@dataclass
class CheckpointConfig:
    checkpoint_id: str = ""
    enabled: bool = True
    strict_mode: bool = False
    category: str = "core"


@dataclass
class EngineConfig:
    core_checkpoints: List[str] = field(default_factory=lambda: [
        "semantic_consistency", "interface_conflict", "pattern_match"
    ])
    deep_checkpoints: List[str] = field(default_factory=lambda: [
        "architecture_review", "security_audit", "business_alignment", "complexity_brake"
    ])
    activation_threshold: float = 0.4
    token_budget_light: int = 15000
    token_budget_deep: int = 25000
    max_concurrency: int = 2
    checkpoint_configs: Dict[str, CheckpointConfig] = field(default_factory=dict)
    discard_max_age_months: int = 6
