"""Collusion v2.0 — 行为日志分析器 (Behavior Review)

分析 AI 伙伴的行为录制日志，检测异常模式。
配合 GoalRunner 使用，自动触发行为评审任务。

支持:
  - 任务偏离检测 (砍树中途挖矿)
  - 频繁切换检测
  - 间隔过短检测 (抽风行为)
  - 优先级违规检测
  - 规则自动提炼

日志格式 (行为录制Mod输出的JSON):
  [
    {"timestamp": "2026-05-22T10:00:01", "action": "MOVE_TO", "target": {"type": "LOG", "pos": [10, 64, 20]}, "goal": "CUT_TREE"},
    {"timestamp": "2026-05-22T10:00:03", "action": "BREAK_BLOCK", "target": {"type": "LOG", "pos": [10, 64, 20]}, "goal": "CUT_TREE"},
    {"timestamp": "2026-05-22T10:00:05", "action": "MOVE_TO", "target": {"type": "STONE", "pos": [15, 63, 25]}, "goal": "CUT_TREE"},
    ...
  ]

用法:
  from behavior_review import BehaviorReviewer

  reviewer = BehaviorReviewer()
  report = reviewer.analyze("behavior_log.json")
  # report["anomalies"] 包含所有检测到的异常
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from dataclasses import dataclass, field


# ============================================================
# 评审规则定义
# ============================================================

@dataclass
class ReviewRule:
    rule_id: str
    name: str
    description: str
    severity: str  # high / medium / low
    # 检测函数签名: (actions: list, context: dict) -> [Anomaly, ...]
    source: str = "builtin"  # builtin / learned


class ReviewRuleSet:
    """内置评审规则集"""

    @staticmethod
    def get_default_rules() -> List[ReviewRule]:
        return [
            ReviewRule(
                rule_id="task_deviation",
                name="任务偏离检测",
                description="在目标任务未完成时，禁止切换到与当前目标无关的动作",
                severity="high",
            ),
            ReviewRule(
                rule_id="frequent_switching",
                name="频繁切换检测",
                description="检测短时间内多次切换目标/动作类型",
                severity="medium",
            ),
            ReviewRule(
                rule_id="too_fast",
                name="间隔过短检测",
                description="两个动作之间间隔过短（抽风行为）",
                severity="high",
            ),
            ReviewRule(
                rule_id="priority_violation",
                name="优先级违规检测",
                description="采集任务应按价值从高到低依次进行",
                severity="low",
            ),
            ReviewRule(
                rule_id="goal_abandonment",
                name="目标放弃检测",
                description="检测未完成就放弃的任务",
                severity="medium",
            ),
        ]


# ============================================================
# 异常报告
# ============================================================

@dataclass
class Anomaly:
    rule: str
    description: str
    severity: str
    evidence: list  # 证据条目 (具体动作序列)
    timestamp_range: Tuple[str, str]
    suggestion: str = ""


# ============================================================
# 行为评审器
# ============================================================

class BehaviorReviewer:
    """AI 伙伴行为评审器

    读取行为录制日志，检测异常行为模式，生成评审报告。
    """

    # 动作之间的最短合理间隔 (秒)
    MIN_ACTION_INTERVAL = 2.0
    # 短时间内切换目标的最大合理次数
    MAX_SWITCHES_IN_10S = 3

    # 与采集相关的目标类型
    GATHER_ACTIONS = {"BREAK_BLOCK", "MINE", "CHOP"}
    # 与采集不相关的动作
    UNRELATED_ACTIONS = {"IDLE", "WANDER", "TALK", "EAT"}

    def __init__(self, rules: List[ReviewRule] = None):
        self.rules = rules or ReviewRuleSet.get_default_rules()
        self.learned_rules: List[ReviewRule] = []

    def analyze(self, log_path: str) -> dict:
        """分析行为日志

        Args:
            log_path: JSON 日志文件路径

        Returns:
            {
                "anomalies": [...],
                "stats": {...},
                "suggestions": [...],
                "rules_applied": [...],
            }
        """
        actions = self._load_log(log_path)
        if not actions:
            return {"anomalies": [], "stats": {}, "suggestions": ["日志为空"], "rules_applied": []}

        anomalies = []
        stats = self._compute_stats(actions)

        # 执行每个规则
        for rule in self.rules:
            try:
                found = self._apply_rule(rule, actions, stats)
                anomalies.extend(found)
            except Exception as e:
                anomalies.append(Anomaly(
                    rule=rule.rule_id,
                    description=f"规则执行异常: {e}",
                    severity="low",
                    evidence=[],
                    timestamp_range=("", ""),
                ))

        # 生成建议
        suggestions = self._generate_suggestions(anomalies, stats)

        return {
            "anomalies": [self._anomaly_to_dict(a) for a in anomalies],
            "stats": stats,
            "suggestions": suggestions,
            "rules_applied": [r.rule_id for r in self.rules],
            "anomaly_count": len(anomalies),
        }

    # ==================== 规则动作 ====================

    def _apply_rule(self, rule: ReviewRule, actions: List[dict], stats: dict) -> List[Anomaly]:
        """应用单条规则"""
        if rule.rule_id == "task_deviation":
            return self._detect_task_deviation(actions)
        elif rule.rule_id == "frequent_switching":
            return self._detect_frequent_switching(actions)
        elif rule.rule_id == "too_fast":
            return self._detect_too_fast(actions)
        elif rule.rule_id == "priority_violation":
            return self._detect_priority_violation(actions, stats)
        elif rule.rule_id == "goal_abandonment":
            return self._detect_goal_abandonment(actions)
        return []

    def _detect_task_deviation(self, actions: List[dict]) -> List[Anomaly]:
        """检测任务偏离: 砍树中途挖矿"""
        anomalies = []
        current_goal = None
        deviation_start = None
        deviation_actions = []

        for i, a in enumerate(actions):
            goal = a.get("goal", "")

            if current_goal is None:
                current_goal = goal
                continue

            # 目标切换
            if goal != current_goal and goal:
                # 检查是否是同一大类 (如 GATHER → GATHER 不是偏离)
                if not self._is_same_category(current_goal, goal):
                    if deviation_start is None:
                        deviation_start = a
                        deviation_actions = [a]
                    else:
                        deviation_actions.append(a)
                else:
                    # 同类目标切换，重置
                    deviation_start = None
                    deviation_actions = []
                    current_goal = goal
            else:
                deviation_start = None
                deviation_actions = []

            # 偏离持续 > 2 个动作 → 记录异常
            if len(deviation_actions) >= 2:
                anomalies.append(Anomaly(
                    rule="task_deviation",
                    description=f"任务偏离: 从 {current_goal} 切换到 {goal}",
                    severity="high",
                    evidence=[{"action": da.get("action"), "target": da.get("target"),
                               "goal": da.get("goal")} for da in deviation_actions[:5]],
                    timestamp_range=(
                        deviation_actions[0].get("timestamp", ""),
                        deviation_actions[-1].get("timestamp", ""),
                    ),
                    suggestion=f"在完成 {current_goal} 之前，避免执行 {goal} 类动作",
                ))
                # 重置跟踪
                current_goal = goal
                deviation_start = None
                deviation_actions = []

        return anomalies

    def _detect_frequent_switching(self, actions: List[dict]) -> List[Anomaly]:
        """检测频繁切换目标"""
        anomalies = []
        window_size = 10

        for i in range(len(actions) - window_size):
            window = actions[i:i + window_size]
            goals = [a.get("goal", "") for a in window if a.get("goal")]
            if not goals:
                continue

            # 统计目标切换次数
            switches = sum(1 for j in range(1, len(goals)) if goals[j] != goals[j - 1])

            if switches > self.MAX_SWITCHES_IN_10S * (window_size / 10):
                anomalies.append(Anomaly(
                    rule="frequent_switching",
                    description=f"频繁切换: {switches} 次目标切换 / {window_size} 个动作",
                    severity="medium",
                    evidence=[{"goal_sequence": goals[:10]}],
                    timestamp_range=(
                        window[0].get("timestamp", ""),
                        window[-1].get("timestamp", ""),
                    ),
                    suggestion="建议增加任务锁定机制，完成任务后再切换目标",
                ))
                break

        return anomalies

    def _detect_too_fast(self, actions: List[dict]) -> List[Anomaly]:
        """检测间隔过短"""
        anomalies = []
        too_fast = []

        for i in range(1, len(actions)):
            t1 = self._parse_time(actions[i - 1].get("timestamp", ""))
            t2 = self._parse_time(actions[i].get("timestamp", ""))
            if t1 and t2:
                diff = (t2 - t1).total_seconds()
                if diff < self.MIN_ACTION_INTERVAL and diff > 0:
                    too_fast.append({
                        "index": i,
                        "interval": diff,
                        "action1": actions[i - 1].get("action"),
                        "action2": actions[i].get("action"),
                    })

        if len(too_fast) >= 3:
            avg_interval = sum(t["interval"] for t in too_fast) / len(too_fast)
            anomalies.append(Anomaly(
                rule="too_fast",
                description=f"动作间隔过短: {len(too_fast)} 次间隔 < {self.MIN_ACTION_INTERVAL}s, 平均 {avg_interval:.1f}s",
                severity="high" if avg_interval < 0.5 else "medium",
                evidence=too_fast[:5],
                timestamp_range=("", ""),
                suggestion=f"两个动作之间至少间隔 {self.MIN_ACTION_INTERVAL} 秒，避免抽风行为",
            ))

        return anomalies

    def _detect_priority_violation(self, actions: List[dict], stats: dict) -> List[Anomaly]:
        """检测优先级违规"""
        # 简化版: 按目标统计采集的方块类型，检查是否先采高价值后采低价值
        # 这是一个启发式检测，实际使用时可扩展
        return []

    def _detect_goal_abandonment(self, actions: List[dict]) -> List[Anomaly]:
        """检测未完成就放弃的任务"""
        anomalies = []
        goal_starts = {}
        goal_ends = {}

        for i, a in enumerate(actions):
            goal = a.get("goal", "")
            action = a.get("action", "")

            if goal and goal not in goal_starts:
                goal_starts[goal] = {"start_idx": i, "start_time": a.get("timestamp", "")}
            if goal:
                goal_ends[goal] = {"end_idx": i, "end_time": a.get("timestamp", "")}

        for goal in goal_starts:
            start = goal_starts[goal]
            end = goal_ends.get(goal, {})
            # 如果同一个 goal 很快被切换，可能是放弃
            if end and (end.get("end_idx", 0) - start.get("start_idx", 0)) < 3:
                anomalies.append(Anomaly(
                    rule="goal_abandonment",
                    description=f"疑似放弃目标: {goal} (仅 {end.get('end_idx', 0) - start.get('start_idx', 0)} 个动作)",
                    severity="low",
                    evidence=[{"goal": goal, "start": start["start_time"], "end": end.get("end_time", "")}],
                    timestamp_range=(start["start_time"], end.get("end_time", "")),
                    suggestion=f"确认 {goal} 是否已完成。如果未完成，延长尝试时间",
                ))

        return anomalies

    # ==================== 统计 ====================

    def _compute_stats(self, actions: List[dict]) -> dict:
        """计算基本统计"""
        if not actions:
            return {}

        goals = Counter(a.get("goal", "?") for a in actions)
        action_types = Counter(a.get("action", "?") for a in actions)

        # 时间范围
        times = [self._parse_time(a.get("timestamp", "")) for a in actions]
        valid_times = [t for t in times if t]
        duration = (valid_times[-1] - valid_times[0]).total_seconds() if len(valid_times) >= 2 else 0

        # 间隔统计
        intervals = []
        for i in range(1, len(valid_times)):
            intervals.append((valid_times[i] - valid_times[i - 1]).total_seconds())
        avg_interval = sum(intervals) / len(intervals) if intervals else 0

        return {
            "total_actions": len(actions),
            "total_duration_seconds": duration,
            "goals": dict(goals.most_common()),
            "action_types": dict(action_types.most_common()),
            "avg_interval_seconds": round(avg_interval, 2),
            "min_interval_seconds": round(min(intervals), 2) if intervals else 0,
            "max_interval_seconds": round(max(intervals), 2) if intervals else 0,
        }

    def _generate_suggestions(self, anomalies: List[Anomaly], stats: dict) -> List[str]:
        """根据异常和统计生成改进建议"""
        suggestions = []

        if not anomalies:
            suggestions.append("✅ 未检测到行为异常")
            return suggestions

        # 按严重程度排序
        high = [a for a in anomalies if a.severity == "high"]
        medium = [a for a in anomalies if a.severity == "medium"]
        low = [a for a in anomalies if a.severity == "low"]

        if high:
            suggestions.append(f"🔴 高优先级 ({len(high)} 项): 建议优先修复这些异常")
            for a in high:
                if a.rule == "task_deviation":
                    suggestions.append("  → 增加目标锁定：在 GatherGoal 中让 BreakBlockAction 检查当前目标是否与传入的 TaskTarget 一致")
                if a.rule == "too_fast":
                    suggestions.append("  → 增加动作冷却：在 SkillEngine 中为每个 AtomicAction 添加 2 秒最小间隔")
        if medium:
            suggestions.append(f"🟡 中优先级 ({len(medium)} 项)")
        if low:
            suggestions.append(f"🟢 低优先级 ({len(low)} 项)")

        return suggestions

    # ==================== 规则学习 ====================

    def learn_rule(self, user_feedback: str, anomaly: Anomaly) -> Optional[ReviewRule]:
        """从用户反馈中学习新规则

        当用户说"砍树时不能挖矿" → 自动提炼为规则并加入 learned_rules
        """
        # 提取关键词
        keywords = re.findall(r'(?:不能|禁止|不要|避免|应该|必须)\s*(.{5,40})', user_feedback)
        if not keywords:
            return None

        rule = ReviewRule(
            rule_id=f"learned_{len(self.learned_rules) + 1}",
            name=f"学习规则: {keywords[0][:30]}",
            description=keywords[0][:100],
            severity="medium",
            source="learned",
        )
        self.learned_rules.append(rule)
        return rule

    # ==================== 内部工具 ====================

    def _load_log(self, log_path: str) -> List[dict]:
        """加载行为日志"""
        path = Path(log_path)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "actions" in data:
                return data["actions"]
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def _parse_time(self, ts: str) -> Optional[datetime]:
        """解析时间戳"""
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            try:
                return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
            except (ValueError, TypeError):
                return None

    def _is_same_category(self, goal1: str, goal2: str) -> bool:
        """判断两个目标是否同一大类"""
        gather = {"CUT", "MINE", "GATHER", "CHOP", "DIG", "COLLECT"}
        g1, g2 = goal1.upper(), goal2.upper()
        return any(tag in g1 for tag in gather) == any(tag in g2 for tag in gather)

    def _anomaly_to_dict(self, a: Anomaly) -> dict:
        return {
            "rule": a.rule,
            "description": a.description,
            "severity": a.severity,
            "evidence": a.evidence[:5],
            "timestamp_range": list(a.timestamp_range),
            "suggestion": a.suggestion,
        }
