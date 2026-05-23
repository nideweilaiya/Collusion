"""Collusion v0.6 — 检查点引擎

架构原则:
  1. 信息单向流: 数据从 CompressedSnapshot 流向各检查点，检查点绝不绕过快照访问原始数据
  2. 检查点无状态: BaseCheckpoint.run() 是纯函数，输出仅依赖输入快照和自身逻辑
"""

from src.checkpoint.base import BaseCheckpoint, CheckpointResult
from src.checkpoint.registry import CheckpointRegistry
from src.checkpoint.situation_compressor import SituationCompressor
from src.checkpoint.knowledge_retriever import KnowledgeRetriever

__all__ = [
    "BaseCheckpoint",
    "CheckpointResult",
    "CheckpointRegistry",
    "KnowledgeRetriever",
    "SituationCompressor",
]
