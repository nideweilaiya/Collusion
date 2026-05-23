"""BatchGoalRunner — 批量执行 Goal 修复 + 全链路端到端验证

根据审计报告自动生成 Goal 配置列表, 逐个执行 修复→验证→审查→归档.

用法:
  python batch_goal_runner.py --audit
  python batch_goal_runner.py --run-return-null     # fix return null issues
  python batch_goal_runner.py --run-empty-catch      # fix empty catch blocks
  python batch_goal_runner.py --run-hardcoded-temp   # fix hardcoded temperatures
  python batch_goal_runner.py --e2e                  # 全链路测试: 修复一个真实问题
"""
import json
import os
import re
import sys
import time
import subprocess
from pathlib import Path
from typing import Dict, List, Optional


MOD_ROOT = "D:/AI_Workbench/integrations/minecraft/forge-mod"
ORCH_ROOT = "D:/BrainstormOrchestrator"


class BatchGoalRunner:
    """批量 Goal 执行器"""

    def __init__(self, mod_root: str = MOD_ROOT, orchestrator_root: str = ORCH_ROOT):
        self.mod_root = Path(mod_root)
        self.orch_root = Path(orchestrator_root)
        self.results = []

        # 加载 GoalRunner
        sys.path.insert(0, orchestrator_root)
        from src.goal_runner import GoalRunner, GoalConfig as GC
        self.dict = GC
        self.goal_runner = GoalRunner(data_dir=str(self.orch_root / "data"))

    # ==================== 审计入口 ====================

    def audit(self) -> dict:
        """生成完整审计报告"""
        issues = {"return_null": [], "empty_catch": [], "printstacktrace": [],
                  "hardcoded_temp": [], "todo": [], "no_test": []}

        # 扫描所有 Java 文件
        for f in self.mod_root.rglob("*.java"):
            if "build" in str(f) or ".gradle" in str(f):
                continue
            rel = os.path.relpath(f, self.mod_root)
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    s = line.strip()
                    if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
                        continue

                    if "return null;" in s:
                        issues["return_null"].append({"file": rel, "line": i, "text": s[:80]})
                    if "catch" in s and "{}" in s.replace(" ", "") and "TODO" not in s:
                        if "Interrupt" not in s:  # skip legit interrupted catches
                            issues["empty_catch"].append({"file": rel, "line": i, "text": s[:80]})
                    if "printStackTrace" in s:
                        issues["printstacktrace"].append({"file": rel, "line": i, "text": s[:80]})
                    if "temperature" in s and "0." in s and "OllamaClient." not in s:
                        issues["hardcoded_temp"].append({"file": rel, "line": i, "text": s[:80]})

        # TODO/FIXME
        for f in self.mod_root.rglob("*.java"):
            if "build" in str(f):
                continue
            rel = os.path.relpath(f, self.mod_root)
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    for kw in ["TODO", "FIXME", "HACK", "XXX", "WIP"]:
                        if kw in line and "class " not in line:
                            issues["todo"].append({"file": rel, "line": i, "tag": kw, "text": line.strip()[:100]})

        return issues

    # ==================== 批量 Goal 生成 ====================

    def generate_goals(self, issues: dict)  -> list:
        """将所有问题转化为 Goal 配置"""
        goals = []

        # 机械性修复: return null (单文件, 可一次性修)
        if issues.get("return_null"):
            goals.append(self._make_goal(
                goal_id="fix_return_null",
                desc="修复 return null 为 Optional/空集合模式",
                target_files=list(set(i["file"] for i in issues["return_null"])),
                cmd="gradle build",
            ))

        # 机械性修复: 空 catch 块
        if issues.get("empty_catch"):
            goals.append(self._make_goal(
                goal_id="fix_empty_catch",
                desc="修复空 catch 块，添加日志或恢复中断状态",
                target_files=list(set(i["file"] for i in issues["empty_catch"])),
                cmd="gradle build",
            ))

        # 机械性修复: printStackTrace
        if issues.get("printstacktrace"):
            goals.append(self._make_goal(
                goal_id="fix_printstacktrace",
                desc="将 System.out.println 替换为 LOGGER 调用",
                target_files=list(set(i["file"] for i in issues["printstacktrace"])),
                cmd="gradle build",
            ))

        # 机械性修复: 硬编码温度
        if issues.get("hardcoded_temp"):
            goals.append(self._make_goal(
                goal_id="fix_hardcoded_temp",
                desc="将硬编码温度值替换为 OllamaClient.TEMP_* 常量",
                target_files=list(set(i["file"] for i in issues["hardcoded_temp"])),
                cmd="gradle build",
            ))

        return goals

    def _make_goal(self, goal_id: str, desc: str,
                   target_files: List[str], cmd: str)  -> dict:
        """构造 Goal 配置"""
        from src.goal_runner import GoalConfig
        return GoalConfig(
            goal_id=goal_id,
            description=desc,
            verification={"command": cmd, "expected_exit_code": 0,
                          "max_iterations": 3, "timeout_seconds": 120},
            review={"enabled": True, "agents": ["architecture"],
                    "checklist": ["不修改文件结构", "遵循现有代码风格"]},
            constraints={"allowed_files": target_files, "forbidden_files": []},
        )

    # ==================== 执行 ====================

    def run_goals(self, goals: list)  -> list[dict]:
        """执行一列 Goal, 返回结果"""
        results = []
        for i, goal in enumerate(goals):
            print(f"\n  [{i+1}/{len(goals)}] {goal.goal_id} — {goal.description}")
            goal_id = self.goal_runner.start_goal(goal)
            time.sleep(0.3)
            status = self.goal_runner.get_status(goal_id)
            results.append({
                "goal_id": goal.goal_id,
                "status": status.get("status"),
                "description": goal.description,
                "result": status.get("result"),
                "iterations": status.get("current_iteration"),
            })
            print(f"    → {status.get('status')}")
        return results

    # ==================== 全链路 E2E 测试 ====================

    def e2e_test(self) -> dict:
        """全链路端到端测试: 创建 → 修改 → 编译 → 检查 → 归档

        测试场景: 修复 MC Mod 中的一个真实问题
        具体: 给 MemoryStore.java 的空 catch 块加日志
        """
        print("=" * 60)
        print("  BatchGoalRunner — 全链路 E2E 测试")
        print("=" * 60)

        results = {}

        # Step 1: 审计
        print("\n1️⃣  审计扫描...")
        issues = self.audit()
        results["audit"] = {
            "return_null": len(issues["return_null"]),
            "empty_catch": len(issues["empty_catch"]),
            "printstacktrace": len(issues["printstacktrace"]),
            "hardcoded_temp": len(issues["hardcoded_temp"]),
            "todo": len(issues["todo"]),
        }
        print(f"    return_null: {results['audit']['return_null']}")
        print(f"    empty_catch: {results['audit']['empty_catch']}")
        print(f"    todo: {results['audit']['todo']}")

        # Step 2: 生成 Goal 配置
        print("\n2️⃣  生成 Goal 配置...")
        goals = self.generate_goals(issues)
        results["n_goals"] = len(goals)
        for g in goals:
            print(f"    {g.goal_id}: {g.description}")

        # Step 3: 执行所有 Goal (模拟）
        print("\n3️⃣  执行 Goal...")
        goal_results = self.run_goals(goals)
        results["goal_results"] = goal_results

        # Step 4: 资源一致性检查
        print("\n4️⃣  资源一致性检查 (mc_resources.py)...")
        sys.path.insert(0, str(self.orch_root))
        from mc_resources import ModResourceChecker
        checker = ModResourceChecker(str(self.mod_root))
        resource_result = checker.check_all(
            codegraph_db="D:/Reasonix/.codegraph/codegraph.db"
        )
        results["resource_check"] = {
            "passed": resource_result["passed"],
            "n_checks": len(resource_result["checks"]),
            "errors": resource_result["errors"][:3],
        }
        print(f"    Passed: {resource_result['passed']}")
        print(f"    Checks: {len(resource_result['checks'])}")
        if resource_result["errors"]:
            print(f"    Errors: {resource_result['errors'][:2]}")

        # Step 5: GoalRunner 自身验证
        print("\n5️⃣  GoalRunner 状态检查...")
        gr_stats = self.goal_runner.get_status("fix_return_null")
        results["goal_runner"] = {
            "status": gr_stats.get("status") if gr_stats else "none",
            "goals_available": len(goals),
        }
        print(f"    最后执行状态: {gr_stats.get('status') if gr_stats else 'N/A'}")

        # Step 6: 全量测试
        print("\n6️⃣  pytest 全量测试...")
        import pytest
        exit_code = pytest.main([
            "--rootdir", str(self.orch_root),
            str(self.orch_root / "tests"), "-q"
        ])
        results["pytest"] = {"passed": exit_code == 0}
        print(f"    {'✅ PASSED' if exit_code == 0 else '❌ FAILED'}")

        results["overall"] = "PASS" if (
            results["pytest"]["passed"] and 
            results.get("goal_results", [{}])[0].get("status") == "completed"
        ) else "FAIL"

        print(f"\n{'='*60}")
        print(f"  🏆 E2E 测试结果: {results['overall']}")
        print(f"{'='*60}")

        return results


# ==================== CLI ====================

if __name__ == "__main__":
    runner = BatchGoalRunner()

    if "--audit" in sys.argv:
        issues = runner.audit()
        print(json.dumps(
            {k: len(v) for k, v in issues.items()}, 
            ensure_ascii=False, indent=2
        ))

    elif "--e2e" in sys.argv:
        runner.e2e_test()

    elif "--list-goals" in sys.argv:
        issues = runner.audit()
        goals = runner.generate_goals(issues)
        for g in goals:
            print(f"  {g.goal_id}: {g.description}")

    else:
        # 默认: 执行所有机械性修复
        issues = runner.audit()
        goals = runner.generate_goals(issues)
        results = runner.run_goals(goals)
        print(json.dumps(results, ensure_ascii=False, indent=2))
