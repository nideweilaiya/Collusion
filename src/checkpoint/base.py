"""检查点基类 — v0.6 核心抽象

架构原则:
  - 检查点无状态: run() 是纯函数，输出仅依赖于输入 snapshot + artifacts + 自身逻辑
  - provides/requires 声明: 引擎在执行前基于声明做拓扑排序和前置检查
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from enum import Enum
import time

from src.models import CompressedSnapshot


class CheckpointSeverity(str, Enum):
    PASS = "pass"
    ADVISORY = "advisory"
    WARNING = "warning"
    BLOCKING = "blocking"


class CheckpointCategory(str, Enum):
    CORE = "core"            # 始终运行的轻量检查
    DEEP = "deep"            # 高风险/高不确定性时激活
    DIAGNOSTIC = "diagnostic"  # 按需调查


@dataclass
class CheckpointResult:
    """检查点执行结果 — 纯输出，无外部依赖"""
    checkpoint_id: str = ""
    category: str = "core"
    severity: str = "pass"
    summary: str = ""              # <=80 chars
    findings: List[Dict] = field(default_factory=list)
    risk_score: float = 0.0        # 0.0 = 无风险, 1.0 = 最大风险
    confidence: float = 1.0        # 自身结论自信度 0-1
    requires: List[str] = field(default_factory=list)
    provides: List[str] = field(default_factory=list)
    activation_gate: bool = False  # True = 触发深度检查点
    uncertainty_flags: List[str] = field(default_factory=list)
    llm_calls: int = 0
    tokens_used: int = 0
    duration_ms: float = 0.0
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {}
        for f_name in self.__dataclass_fields__:
            d[f_name] = getattr(self, f_name)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointResult":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


class BaseCheckpoint(ABC):
    """检查点抽象基类

    契约:
      1. run() 是纯函数 — 只依赖输入的 snapshot + artifacts
      2. 子类覆盖 _analyze()，不覆盖 run()
      3. requires/provides 声明依赖与产出
    """

    checkpoint_id: str = ""
    category: CheckpointCategory = CheckpointCategory.CORE
    description: str = ""
    requires: List[str] = []      # 前置依赖能力清单
    provides: List[str] = []      # 产出能力清单

    def __init__(self, fast_llm=None, strong_llm=None, strict_mode: bool = False):
        self.fast_llm = fast_llm
        self.strong_llm = strong_llm
        self.strict_mode = strict_mode

    def run(self, snapshot: CompressedSnapshot,
            artifacts: dict = None) -> CheckpointResult:
        """模板方法: pre_check → analyze → post_check"""
        start = time.time()

        pre = self._pre_check(snapshot, artifacts or {})
        if pre is not None:
            pre.duration_ms = (time.time() - start) * 1000
            return pre

        result = self._analyze(snapshot, artifacts or {})
        result.duration_ms = (time.time() - start) * 1000
        result.checkpoint_id = self.checkpoint_id
        result.category = self.category.value
        result.requires = list(self.requires)
        result.provides = list(self.provides)
        result = self._post_check(result, snapshot)
        return result

    def _pre_check(self, snapshot: CompressedSnapshot,
                   artifacts: dict) -> Optional[CheckpointResult]:
        """返回 CheckpointResult 可短路跳过分析。返回 None 继续 _analyze()。"""
        return None

    @abstractmethod
    def _analyze(self, snapshot: CompressedSnapshot,
                 artifacts: dict) -> CheckpointResult:
        """核心逻辑 — 子类必须实现。依赖：只读 snapshot + artifacts。"""
        ...

    def _post_check(self, result: CheckpointResult,
                    snapshot: CompressedSnapshot) -> CheckpointResult:
        """调整严重性（strict_mode），追加元数据"""
        if self.strict_mode and result.severity == CheckpointSeverity.WARNING.value:
            result.severity = CheckpointSeverity.BLOCKING.value
        result.metadata["strict_mode"] = self.strict_mode
        return result

    def _llm_check(self, prompt: str, temperature: float = 0.1,
                   max_tokens: int = 2048) -> dict:
        """统一 LLM 调用 — 使用 cached_call_json + 固定 PREFIX"""
        if self.fast_llm is None:
            return {}
        return self.fast_llm.cached_call_json(
            prompt, temperature=temperature, max_tokens=max_tokens
        )
