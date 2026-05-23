"""Collusion v1.3-v1.7 — 验证门 + 蓝图复用 + 审查记忆 + 记忆巩固

v1.3: 协调验证门 — 阶段边界确定性检查
v1.4: task_graph 资产化 — 工作流蓝图复用 (99%降成本)
v1.6: 审查记忆结构化复用 — 注意力分配
v1.7: 短/长期记忆分离 + 睡眠巩固
"""
import json
import time
import math
from pathlib import Path
from typing import Dict, List, Optional, Set


# ============================================================
# v1.3: 协调验证门
# ============================================================

class VerificationGate:
    """阶段边界验证门 — 基于确定性的规则检查，不依赖LLM推理

    参考: Kerno (2026) Spec→Impl多门验证
          Verify-Gated Completion (2026) 验证门控准入控制
          Self-Healing Framework (Jeong, May 2026)
    """

    GATES = [
        {
            "id": "proposal_review",
            "name": "提案→审查",
            "checks": [
                {"type": "file_boundary", "desc": "修改是否越界？",
                 "rule": lambda ctx: ctx.get("files_modified", set()).issubset(
                     ctx.get("allowed_files", set())) if "allowed_files" in ctx else True},
                {"type": "proposal_completeness", "desc": "是否覆盖所有必需环节？",
                 "rule": lambda ctx: len(ctx.get("plan_steps", [])) >= ctx.get("min_steps", 3)},
            ],
        },
        {
            "id": "review_converge",
            "name": "审查→收束",
            "checks": [
                {"type": "test_pass", "desc": "关联测试是否通过？",
                 "rule": lambda ctx: not ctx.get("test_failures", [])},
                {"type": "no_conflict", "desc": "Agent间是否有文件修改冲突？",
                 "rule": lambda ctx: len(_get_conflicts(ctx.get("modifications", []))) == 0},
            ],
        },
        {
            "id": "converge_integrate",
            "name": "收束→整合",
            "checks": [
                {"type": "causal_warning", "desc": "因果记忆预警是否触发？",
                 "rule": lambda ctx: len(ctx.get("causal_warnings", [])) == 0},
                {"type": "complexity_threshold", "desc": "复杂度是否超阈值？",
                 "rule": lambda ctx: ctx.get("complexity_score", 0) <= ctx.get("max_complexity", 5)},
            ],
        },
        {
            "id": "integrate_execute",
            "name": "整合→执行",
            "checks": [
                {"type": "run_validate", "desc": "方案是否包含可执行步骤？",
                 "rule": lambda ctx: len(ctx.get("executable_steps", [])) > 0},
            ],
        },
    ]

    @classmethod
    def check(cls, gate_id: str, context: dict) -> dict:
        """执行验证门检查，返回通过/失败/警告"""
        gate = next((g for g in cls.GATES if g["id"] == gate_id), None)
        if not gate:
            return {"status": "pass", "reason": f"no gate {gate_id}"}

        results = []
        all_pass = True
        for check in gate["checks"]:
            try:
                passed = check["rule"](context)
                results.append({
                    "check": check["desc"],
                    "passed": passed,
                    "type": check["type"],
                })
                if not passed:
                    all_pass = False
            except Exception as e:
                results.append({"check": check["desc"], "passed": False, "error": str(e)})
                all_pass = False

        return {
            "gate": gate_id,
            "name": gate["name"],
            "passed": all_pass,
            "checks": results,
            "action": "continue" if all_pass else "rollback_and_retry",
        }


def _get_conflicts(modifications: list) -> list:
    """检测Agent间文件修改冲突"""
    seen = {}
    conflicts = []
    for mod in modifications:
        fname = mod.get("file", "")
        if fname in seen:
            conflicts.append({"file": fname, "agent1": seen[fname], "agent2": mod.get("agent")})
        else:
            seen[fname] = mod.get("agent")
    return conflicts


# ============================================================
# v1.4: 工作流蓝图复用
# ============================================================

class TaskGraphStore:
    """工作流蓝图资产管理 — 将 task_graph 从一次性消耗品变为可复用资产

    参考: MCP Workflow Engine (2026) — 每次执行仅需一次run_workflow调用
          99%降成本, 67步45秒完成
          GraSP (Xia et al., 2026) — 图结构化技能组合
    """

    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "task_graphs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self._index: dict = {}
        self._load()

    def save(self, task_id: str, task_desc: str, task_graph: dict,
             tags: list = None, parameters: dict = None):
        """保存工作流蓝图，支持参数化模板"""
        blueprint = {
            "task_id": task_id,
            "task_desc": task_desc[:200],
            "task_graph": task_graph,
            "tags": tags or [],
            "parameters": parameters or {},
            "created_at": time.time(),
            "reuse_count": 0,
        }
        fname = f"{task_id}.json"
        with open(self.dir / fname, "w", encoding="utf-8") as f:
            json.dump(blueprint, f, ensure_ascii=False, indent=2)

        self._index[task_id] = {
            "file": fname,
            "desc": task_desc[:80],
            "tags": tags or [],
            "params": list((parameters or {}).keys()),
        }
        self._save()

    def find_similar(self, task_desc: str, tags: list = None, top_k: int = 3) -> list:
        """查找相似蓝图"""
        desc_lower = task_desc.lower()
        tag_set = set(t.lower() for t in (tags or []))
        scored = []

        for tid, meta in self._index.items():
            score = 0
            # 描述文本匹配
            meta_desc = meta.get("desc", "").lower()
            for word in desc_lower.split():
                if word in meta_desc:
                    score += 1
            # 标签重叠 (Sanity.io)
            meta_tags = set(t.lower() for t in meta.get("tags", []))
            shared = len(tag_set & meta_tags)
            if shared > 0:
                total = len(tag_set) + len(meta_tags)
                score += (shared * 2) / max(total, 1) * 3

            if score > 0:
                scored.append({"task_id": tid, "score": round(score, 2), "meta": meta})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def load(self, task_id: str) -> Optional[dict]:
        """加载蓝图"""
        meta = self._index.get(task_id)
        if not meta:
            return None
        fpath = self.dir / meta["file"]
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                bp = json.load(f)
            bp["reuse_count"] = bp.get("reuse_count", 0) + 1
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(bp, f, ensure_ascii=False, indent=2)
            return bp
        return None

    def get_stats(self) -> dict:
        return {"n_blueprints": len(self._index)}

    def _load(self):
        if self.index_path.exists():
            with open(self.index_path, "r", encoding="utf-8") as f:
                self._index = json.load(f)

    def _save(self):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)


# ============================================================
# v1.6: 审查记忆结构化复用
# ============================================================

class ReviewMemory:
    """审查记忆 — 结构化记录审查发现，驱动注意力分配

    参考: VERDICT (Mar 2026) — 辩论有效性存在能力阈值
          DAR (Apr 2026) — 多样性感知保留
          The Cost of Consensus (Apr 2026) — 辩论消耗2.1-3.4倍token
    """

    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "review_memory.json"
        self.records: list = []
        self._load()

    def record(self, reviewer_role: str, reviewed_plan: str,
               finding_type: str, severity: str, step_name: str, description: str):
        """记录一次审查发现"""
        self.records.append({
            "time": time.time(),
            "reviewer": reviewer_role,
            "plan": reviewed_plan,
            "finding_type": finding_type,  # security/perf/ux/feasibility
            "severity": severity,          # high/medium/low
            "step": step_name,
            "description": description[:200],
        })
        if len(self.records) > 1000:
            self.records = self.records[-1000:]
        self._save()

    def get_attention_weights(self, reviewer_role: str) -> dict:
        """获取审查注意力权重 — 基于历史高频问题类型"""
        relevant = [r for r in self.records if r["reviewer"] == reviewer_role]
        if len(relevant) < 5:
            return {}

        counts = {}
        for r in relevant:
            counts[r["finding_type"]] = counts.get(r["finding_type"], 0) + 1

        total = sum(counts.values())
        return {k: round(v / total, 2) for k, v in counts.items()}

    def should_debate(self, agent_strength: str) -> bool:
        """根据VERDICT论文: 强模型辩论有效，弱模型可能被带偏"""
        return agent_strength == "strong"  # R1

    def get_stats(self) -> dict:
        return {"n_records": len(self.records)}

    def _load(self):
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                self.records = json.load(f)

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False, indent=2)


# ============================================================
# v1.7: 记忆巩固边界
# ============================================================

class MemoryConsolidation:
    """短/长期记忆分离 + 睡眠巩固

    参考: Microsoft记忆架构 (May 2026) — 97.2%保留, 58%存储缩减
          CraniMem (Mar 2026) — 目标条件门控+有界情景缓冲
          FadeMem (Feb 2026) — 模拟人类遗忘曲线
    """

    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.short_term: list = []  # 会话级，自动清理
        self._load_long_term()

    def add_short_term(self, entry: dict):
        """添加到短期缓冲"""
        entry["entered_at"] = time.time()
        self.short_term.append(entry)
        if len(self.short_term) > 100:
            self.short_term = self.short_term[-100:]

    def consolidate(self, quality_gate: dict = None) -> int:
        """将短期记忆巩固到长期知识库 (质量门控)"""
        if not self.short_term:
            return 0

        consolidated = 0
        for entry in self.short_term[-20:]:  # 只处理最近20条
            if quality_gate:
                score = quality_gate.get("min_score", 0)
                if entry.get("score", 0) < score:
                    continue

            # 去重: 相似度 >85% 合并
            merged = False
            for existing in self._long_term:
                if self._similarity(entry, existing) > 0.85:
                    existing["count"] = existing.get("count", 1) + 1
                    existing["updated_at"] = time.time()
                    merged = True
                    break

            if not merged:
                entry["count"] = 1
                entry["consolidated_at"] = time.time()
                self._long_term.append(entry)

            consolidated += 1

        self.short_term = self.short_term[-20:]  # 清理已处理的
        self._save_long_term()
        return consolidated

    def sleep_consolidate(self) -> dict:
        """离线睡眠巩固 — 去重 + 衰减 + 强化

        在编排完成后或定期触发
        """
        stats = {"removed": 0, "decayed": 0, "strengthened": 0}
        now = time.time()

        # 1. 移除被标记为"过时"的资产
        new_lt = []
        for e in self._long_term:
            if e.get("obsolete"):
                stats["removed"] += 1
                continue
            # 2. 强度衰减 — 长期未检索降低权重
            last_access = e.get("last_accessed", e.get("consolidated_at", now))
            days_since = (now - last_access) / 86400
            if days_since > 30:
                e["strength"] = e.get("strength", 1.0) * 0.9
                stats["decayed"] += 1
            # 3. 因果强化 — 多次验证加强权重
            if e.get("verified", 0) >= 3:
                e["strength"] = min(e.get("strength", 1.0) * 1.1, 2.0)
                stats["strengthened"] += 1
            new_lt.append(e)

        self._long_term = new_lt
        self._save_long_term()
        return stats

    def get_stats(self) -> dict:
        return {
            "short_term": len(self.short_term),
            "long_term": len(self._long_term),
        }

    def _similarity(self, a: dict, b: dict) -> float:
        """简单标签重叠相似度"""
        ta = set(a.get("tags", []))
        tb = set(b.get("tags", []))
        if not ta or not tb:
            return 0
        return len(ta & tb) / max(len(ta | tb), 1)

    def _load_long_term(self):
        path = self.dir / "long_term.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                self._long_term = json.load(f)
        else:
            self._long_term = []

    def _save_long_term(self):
        with open(self.dir / "long_term.json", "w", encoding="utf-8") as f:
            json.dump(self._long_term, f, ensure_ascii=False, indent=2)
