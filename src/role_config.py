"""动态 Agent 角色配置 — YAML 驱动 + 自动检测

支持:
  1. YAML 配置文件定义自定义角色
  2. 根据任务关键词自动选择 Agent 数量和角色
  3. 预设模式: quick(1) / standard(3) / full(5)
  4. 用户可注册自定义角色
"""
import os
import re
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class AgentRoleDef:
    """单个 Agent 角色定义"""
    id: str
    name: str
    focus: str
    model: str = "flash"  # flash / strong
    enabled: bool = True

    def to_agent_prompt(self) -> str:
        return f"你是{self.name}。核心关注点：{self.focus}"


@dataclass
class PresetConfig:
    """预设模式"""
    agents: int
    roles: List[str]  # 角色 ID 列表


@dataclass
class AutoDetectRule:
    """自动检测规则"""
    keywords: List[str]
    add_roles: List[str] = field(default_factory=list)
    boost_complexity: int = 0


# ==================== 内置角色库 ====================

BUILTIN_ROLES: Dict[str, AgentRoleDef] = {
    "business_value": AgentRoleDef(
        id="business_value", name="UX/产品专家",
        focus="用户场景完整性、操作流畅度、部署门槛、开发者体验",
        model="flash",
    ),
    "architecture": AgentRoleDef(
        id="architecture", name="性能架构师",
        focus="技术选型合理性、性能瓶颈、缓存策略、扩展性、数据流设计",
        model="strong",
    ),
    "security": AgentRoleDef(
        id="security", name="安全专家",
        focus="数据安全（加密/脱敏/备份）、认证授权（JWT/OAuth/RBAC）、威胁建模（STRIDE/OWASP）、合规要求",
        model="flash",
    ),
    "cost_optimizer": AgentRoleDef(
        id="cost_optimizer", name="成本优化师",
        focus="云资源成本、许可费用、运维人力成本、第三方服务费用，在质量和成本间找平衡",
        model="flash", enabled=False,
    ),
    "frontend_expert": AgentRoleDef(
        id="frontend_expert", name="前端专家",
        focus="组件架构、状态管理、渲染性能、打包优化、浏览器兼容性、CSS方案",
        model="flash", enabled=False,
    ),
    "data_engineer": AgentRoleDef(
        id="data_engineer", name="数据工程师",
        focus="数据管道设计、ETL/ELT流程、数据仓库选型、数据质量管理、CDC/流处理",
        model="flash", enabled=False,
    ),
    "devops_advisor": AgentRoleDef(
        id="devops_advisor", name="DevOps顾问",
        focus="CI/CD流水线、容器化部署、监控告警、基础设施即代码、灾备方案",
        model="flash", enabled=False,
    ),
}

# ==================== 预设模式 ====================

BUILTIN_PRESETS: Dict[str, PresetConfig] = {
    "quick": PresetConfig(agents=1, roles=["business_value"]),
    "standard": PresetConfig(agents=3, roles=["business_value", "architecture", "security"]),
    "full": PresetConfig(
        agents=5,
        roles=["business_value", "architecture", "security", "cost_optimizer", "devops_advisor"],
    ),
}

# ==================== 自动检测规则 ====================

AUTO_DETECT_RULES: List[AutoDetectRule] = [
    AutoDetectRule(
        keywords=["高并发", "性能", "扩展", "cache", "缓存", "qps", "延迟", "throughput",
                   "scalability", "latency", "load", "并发", "吞吐", "scale", "瓶颈",
                   "optimization", "优化", "分布式", "distributed", "集群", "cluster"],
        add_roles=["architecture"], boost_complexity=1,
    ),
    AutoDetectRule(
        keywords=["安全", "加密", "认证", "auth", "合规", "gdpr", "权限", "审计",
                   "security", "encrypt", "authentication", "authorization", "audit",
                   "oauth", "jwt", "rbac", "token", "password", "漏洞", "注入", "xss",
                   "csrf", "威胁", "threat", "隐私", "privacy", "penetration"],
        add_roles=["security"], boost_complexity=1,
    ),
    AutoDetectRule(
        keywords=["前端", "ui", "页面", "组件", "react", "vue", "交互", "响应式",
                   "frontend", "component", "responsive", "css", "html", "browser",
                   "animation", "dom", "渲染", "render", "webpack", "vite", "angular",
                   "svelte", "next.js", "nuxt", "seo", "accessibility", "a11y"],
        add_roles=["frontend_expert"], boost_complexity=0,
    ),
    AutoDetectRule(
        keywords=["数据", "data", "etl", "仓库", "pipeline", "分析", "报表",
                   "database", "sql", "nosql", "warehouse", "lake", "streaming",
                   "analytics", "reporting", "bi", "clickhouse", "flink", "spark",
                   "hadoop", "kafka", "rabbitmq", "mq", "消息", "队列"],
        add_roles=["data_engineer"], boost_complexity=0,
    ),
    AutoDetectRule(
        keywords=["部署", "deploy", "docker", "k8s", "ci/cd", "运维", "监控",
                   "kubernetes", "helm", "terraform", "ansible", "jenkins", "actions",
                   "monitoring", "prometheus", "grafana", "alert", "logging",
                   "backup", "恢复", "灾备", "disaster", "sre", "devops", "gitops",
                   "容器", "container", "编排", "orchestration"],
        add_roles=["devops_advisor"], boost_complexity=0,
    ),
    AutoDetectRule(
        keywords=["成本", "省钱", "预算", "优化", "降本", "cost", "budget", "pricing",
                   "expensive", "cheap", "saving", "affordable", "roi", "economical"],
        add_roles=["cost_optimizer"], boost_complexity=0,
    ),
]


# ==================== 配置管理器 ====================

class RoleConfigManager:
    """动态角色配置管理器"""

    def __init__(self, config_path: str = None):
        self.roles: Dict[str, AgentRoleDef] = dict(BUILTIN_ROLES)
        self.presets: Dict[str, PresetConfig] = dict(BUILTIN_PRESETS)
        self.auto_rules: List[AutoDetectRule] = list(AUTO_DETECT_RULES)
        self._config_path = config_path
        if config_path and Path(config_path).exists():
            self._load_yaml(config_path)

    def _load_yaml(self, path: str):
        """从 YAML 加载自定义配置"""
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except ImportError:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

        if not data:
            return

        # 加载自定义角色
        for r in data.get("roles", []):
            role = AgentRoleDef(
                id=r["id"], name=r["name"], focus=r.get("focus", ""),
                model=r.get("model", "flash"), enabled=r.get("enabled", True),
            )
            self.roles[role.id] = role

        # 加载预设
        for name, p in data.get("presets", {}).items():
            self.presets[name] = PresetConfig(
                agents=p.get("agents", 3), roles=p.get("roles", []),
            )

        # 加载检测规则
        if data.get("auto_detect", {}).get("enabled", True):
            for r in data.get("auto_detect", {}).get("rules", []):
                self.auto_rules.append(AutoDetectRule(
                    keywords=r.get("keywords", []),
                    add_roles=r.get("add_roles", []),
                    boost_complexity=r.get("boost_complexity", 0),
                ))

    # ==================== API ====================

    def detect(self, task: str, preset: str = "standard") -> tuple:
        """根据任务描述自动检测需要的 Agent 角色

        Returns:
            (role_ids, complexity_boost)
        """
        preset_config = self.presets.get(preset, self.presets["standard"])
        role_ids = list(preset_config.roles)
        complexity = 0
        task_lower = task.lower()

        for rule in self.auto_rules:
            matches = sum(1 for kw in rule.keywords if kw.lower() in task_lower)
            if matches >= 2:  # 至少匹配 2 个关键词
                for rid in rule.add_roles:
                    if rid in self.roles and rid not in role_ids:
                        # 自动检测命中时启用角色
                        self.roles[rid].enabled = True
                        role_ids.append(rid)
                complexity += rule.boost_complexity

        return role_ids, complexity

    def get_agent_count(self, role_ids: list) -> int:
        """根据角色数量映射实际并行 Agent 数"""
        n = len(role_ids)
        if n <= 1:
            return 1
        elif n <= 3:
            return n
        else:
            return min(n, 5)  # 最多 5 个

    def get_prompt_for_role(self, role_id: str) -> str:
        """获取角色的 Agent 提示词"""
        role = self.roles.get(role_id)
        if role:
            return role.to_agent_prompt()
        return f"你是技术顾问。请从多角度分析以下任务。"

    def get_model_for_role(self, role_id: str) -> str:
        """获取角色推荐的模型类型"""
        role = self.roles.get(role_id)
        return role.model if role else "flash"

    def register_role(self, role_id: str, name: str, focus: str, model: str = "flash"):
        """动态注册新角色"""
        self.roles[role_id] = AgentRoleDef(
            id=role_id, name=name, focus=focus, model=model, enabled=True,
        )


# 全局单例
role_manager = RoleConfigManager()
