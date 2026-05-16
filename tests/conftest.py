"""共享测试夹具 — Mock LLM 适配器 + 测试数据"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Dict, List
from src.llm.base import BaseLLMAdapter


class MockLLMAdapter(BaseLLMAdapter):
    """Mock LLM 适配器 — 返回预设 JSON 响应，用于单元测试"""

    def __init__(self, responses: List[Dict] = None):
        self.responses = responses or []
        self.call_index = 0
        self.call_history = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._model = "mock"

    def set_responses(self, responses: List[Dict]):
        self.responses = responses
        self.call_index = 0

    def chat(self, messages: List[Dict], temperature: float = 0.1,
             max_tokens: int = 4096) -> str:
        return self._next_response(raw=True)

    def _do_chat(self, messages: List[Dict], temperature: float,
                 max_tokens: int) -> tuple:
        text = self._next_response(raw=True)
        inp = sum(len(m.get("content", "")) for m in messages)
        out = len(text)
        self.total_input_tokens += inp // 4
        self.total_output_tokens += out // 4
        return text, inp // 4, out // 4

    def cached_call(self, user_context: str, temperature: float = 0.1,
                    max_tokens: int = 4096) -> str:
        self.call_history.append({"method": "cached_call", "context": user_context})
        return self._next_response(raw=True)

    def cached_call_json(self, user_context: str, temperature: float = 0.1,
                         max_tokens: int = 4096, retries: int = 3) -> Dict:
        self.call_history.append({"method": "cached_call_json", "context": user_context})
        return self._next_response(raw=False)

    def _next_response(self, raw: bool = False):
        if self.call_index < len(self.responses):
            resp = self.responses[self.call_index]
            self.call_index += 1
        else:
            resp = {}
        if raw:
            import json
            return json.dumps(resp, ensure_ascii=False)
        return resp

    @property
    def total_cost_rmb(self) -> float:
        return (self.total_input_tokens / 1000 * self.cost_per_1k_input
                + self.total_output_tokens / 1000 * self.cost_per_1k_output)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def cost_per_1k_input(self) -> float:
        return 0.001

    @property
    def cost_per_1k_output(self) -> float:
        return 0.004


# ============================================================
# 标准 Mock 响应模板
# ============================================================

# Phase 1: 任务解构响应
DECOMPOSE_RESPONSE = {
    "steps": [
        {"index": 1, "name": "接口设计",
         "description": "设计 RESTful API 端点，定义请求/响应格式"},
        {"index": 2, "name": "数据存储",
         "description": "设计数据库模型和存储方案"},
        {"index": 3, "name": "认证与授权",
         "description": "设计用户认证和权限控制方案"},
        {"index": 4, "name": "部署方案",
         "description": "设计容器化部署和 CI/CD 流程"},
    ]
}

# Phase 2: 环节共识响应 — UX Agent 提议补齐开发者体验
CONSENSUS_UX_RESPONSE = {
    "has_gap": True,
    "missing_steps": [
        {"name": "开发者体验与入门引导",
         "description": "提供 API 文档、SDK、快速开始指南"}
    ],
    "coverage": [
        {"step_index": 1, "step_name": "接口设计", "level": "充分",
         "note": "接口设计直接影响开发者使用体验"},
        {"step_index": 2, "step_name": "数据存储", "level": "不足",
         "note": "需要关注数据迁移的开发者体验"},
        {"step_index": 3, "step_name": "认证与授权", "level": "充分",
         "note": ""},
        {"step_index": 4, "step_name": "部署方案", "level": "不足",
         "note": "一键部署对开发者体验至关重要"},
    ]
}

# Phase 2: 安全专家响应 — 提议补齐安全环节
CONSENSUS_SEC_RESPONSE = {
    "has_gap": True,
    "missing_steps": [
        {"name": "威胁建模与安全风险评估",
         "description": "识别潜在威胁向量，设计安全防护措施"}
    ],
    "coverage": [
        {"step_index": 1, "step_name": "接口设计", "level": "充分",
         "note": "API 安全在接口设计中已考虑"},
        {"step_index": 2, "step_name": "数据存储", "level": "不足",
         "note": "缺少数据加密和访问审计"},
        {"step_index": 3, "step_name": "认证与授权", "level": "充分",
         "note": ""},
        {"step_index": 4, "step_name": "部署方案", "level": "缺失",
         "note": "缺少安全部署清单"},
    ]
}

# Phase 2: 性能架构师响应 — 无缺失
CONSENSUS_PERF_RESPONSE = {
    "has_gap": False,
    "missing_steps": [],
    "coverage": [
        {"step_index": 1, "step_name": "接口设计", "level": "充分", "note": ""},
        {"step_index": 2, "step_name": "数据存储", "level": "充分", "note": ""},
        {"step_index": 3, "step_name": "认证与授权", "level": "充分", "note": ""},
        {"step_index": 4, "step_name": "部署方案", "level": "充分", "note": ""},
    ]
}

# Phase 2 验证新增步骤的响应
VALIDATE_STEPS_RESPONSE = {
    "decisions": [
        {"index": 0, "action": "keep", "reason": "填补了开发者体验空白"},
        {"index": 1, "action": "keep", "reason": "填补了安全空白"},
    ]
}

# Phase 3: 并行提案响应模板
PROPOSAL_RESPONSE = {
    "object_name": "业务价值对象",
    "steps": [
        {"step_index": 1, "step_name": "接口设计",
         "design_content": "RESTful API，版本化端点，OpenAPI 3.0 文档"},
        {"step_index": 2, "step_name": "数据存储",
         "design_content": "PostgreSQL 主库 + Redis 缓存层"},
        {"step_index": 3, "step_name": "认证与授权",
         "design_content": "JWT + OAuth2.0，RBAC 权限模型"},
        {"step_index": 4, "step_name": "部署方案",
         "design_content": "Docker Compose 编排，GitHub Actions CI/CD"},
        {"step_index": 5, "step_name": "开发者体验与入门引导",
         "design_content": "自动生成 API 文档和 SDK"},
        {"step_index": 6, "step_name": "威胁建模与安全风险评估",
         "design_content": "STRIDE 威胁建模，OWASP Top 10 防护"},
    ],
    "overall_architecture": "前后端分离，RESTful API 驱动",
    "key_risks": ["高并发下数据库瓶颈", "API 版本管理复杂度"],
    "alternatives_considered": ["GraphQL 替代 REST", "NoSQL 替代 PostgreSQL"],
}

# Phase 4: 交叉审查响应
CROSS_REVIEW_RESPONSE = {
    "target_step_index": 2,
    "target_step_name": "数据存储",
    "change_type": "enhancement",
    "content": "作为安全与合规对象代言人，要求增加字段级加密和访问审计日志",
    "reason": "数据存储缺少安全审计能力，不符合合规要求",
    "complexity_delta": 2,
    "need_pause": False,
    "missing_step": None,
}

# Phase 4.5: 可行性收束响应
FEASIBILITY_BRAKE_RESPONSE = {
    "feasible": True,
    "cost_estimate": "中",
    "team_requirements": "2 后端 + 1 前端 + 1 DevOps",
    "simplifications": [
        {"target_step": "数据存储",
         "original_approach": "PostgreSQL + Redis",
         "simplified_approach": "SQLite + 内置缓存",
         "impact": "降低运维复杂度，单机 10w QPS 以内足够"}
    ],
    "mandatory_simplify": False,
}

# Phase 4.6: Owner 整合响应 (pass 1 + pass 2)
OWNER_INTEGRATION_DRAFT = "综合方案文档（Flash 初稿）..."
OWNER_INTEGRATION_FINAL = "# 最终技术方案\n\n## 1. 接口设计\nRESTful API，版本化端点...\n\n## 2. 数据存储\nSQLite + 内置缓存，字段级加密...\n\n## 3. 认证与授权\nJWT + OAuth2.0，RBAC 权限模型...\n\n## 4. 部署方案\nDocker Compose 编排，GitHub Actions CI/CD...\n\n## 5. 开发者体验\n自动生成 API 文档和 SDK...\n\n## 6. 威胁建模\nSTRIDE 威胁建模，OWASP Top 10 防护..."

# Phase 6: 投票评分响应
VOTE_RESPONSE = {
    "results": [
        {"plan_id": "A", "correctness": 8.5, "completeness": 9.0,
         "feasibility": 8.0, "innovation": 7.5, "business_alignment": 9.0,
         "total_score": 8.35, "comment": "业务对齐度高，安全性突出"},
        {"plan_id": "B", "correctness": 9.0, "completeness": 8.0,
         "feasibility": 9.5, "innovation": 7.0, "business_alignment": 8.5,
         "total_score": 8.45, "comment": "可行性最高，技术方案务实"},
        {"plan_id": "C", "correctness": 8.0, "completeness": 8.5,
         "feasibility": 7.5, "innovation": 8.5, "business_alignment": 8.0,
         "total_score": 8.05, "comment": "创新性强但落地风险略高"},
    ],
    "ranked_plan_ids": ["A", "B", "C"],
    "summary": "三方各有侧重，方案B可行性领先",
}
