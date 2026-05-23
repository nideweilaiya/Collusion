"""Collusion GoalRunner v2.0 — 自动化 Mod 开发闭环

核心架构: Reasonix Agent (Coder) + GoalRunner (验证+重试+审查+归档)

  L1: gradle build     (编译验证 — 全自动)
  L2: gradle test      (单元测试 — 全自动)
  L3: runGameTest      (游戏逻辑验证 — 全自动)
  L4: 用户进游戏测试    (行为体验 — 手动)

  只有 L1→L2→L3 全部通过，GoalRunner 才报告 "ready for L4"

使用方式 (在 Reasonix 会话中):
  from src.goal_runner import GoalRunner

  runner = GoalRunner(data_dir="D:/Reasonix/data")

  # 定义 Goal
  cfg = runner.create_goal(
      goal_id="fix_tree_cutting",
      description="修复砍树时中途跑去挖矿的问题",
      verification={
          "l1": {"command": "gradle build", "expected_exit_code": 0},
          "l2": {"command": "gradle test", "expected_exit_code": 0},
          "l3": {"command": "gradle runGameTest --tests *TreeCutting*", "expected_exit_code": 0},
      },
      allowed_files=["src/main/java/com/companion/skill/"],
      max_iterations=5,
  )

  # Agent 修改代码后...
  result = runner.verify_all(cfg.goal_id)
  if result["l3_passed"]:
      print("✅ L1+L2+L3 全部通过，可以进游戏测试了 (L4)")
"""

import json
import time
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
from collections import defaultdict


# ============================================================
# Goal 配置模型
# ============================================================

@dataclass
class GoalConfig:
    goal_id: str
    description: str
    # 三级验证: l1=编译, l2=单元测试, l3=游戏测试
    verification: dict = field(default_factory=lambda: {
        "l1": {"command": "", "expected_exit_code": 0},
        "l2": {"command": "", "expected_exit_code": 0},
        "l3": {"command": "", "expected_exit_code": 0},
    })
    # 文件约束
    allowed_files: list = field(default_factory=list)
    forbidden_files: list = field(default_factory=list)
    # 重试策略
    max_iterations: int = 5
    strategy_switch_threshold: int = 3  # 同错连3次 → 切换策略
    # 冷却 (秒)
    cooldown_seconds: int = 0
    # 标签 (用于检索复用)
    tags: list = field(default_factory=list)


@dataclass
class GoalState:
    goal_id: str
    config: dict
    status: str = "idle"  # idle | l1_running | l2_running | l3_running | ready_for_l4 | failed | abandoned
    current_verification: str = "l1"  # l1 / l2 / l3
    iteration: int = 0
    error_history: dict = field(default_factory=lambda: defaultdict(list))  # {error_hash: [iteration, ...]}
    started_at: float = 0.0
    completed_at: Optional[float] = None
    history: list = field(default_factory=list)
    last_error: str = ""


# ============================================================
# GoalRunner
# ============================================================

class GoalRunner:
    """Goal 驱动自动化验证闭环

    Agent (Reasonix) 负责编码，GoalRunner 负责验证+审查+归档。
    """

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.results_dir = self.data_dir / "goal_results"
        self.task_graphs_dir = self.data_dir / "task_graphs"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.task_graphs_dir.mkdir(parents=True, exist_ok=True)
        self._goals: Dict[str, GoalState] = {}

        # 延迟导入，避免循环依赖
        self._hook_available = False
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "collusion" / "hooks"))
            from archive_utils import add_pending_entry, upgrade_to_verified
            self._add_pending_entry = add_pending_entry
            self._upgrade_to_verified = upgrade_to_verified
            self._hook_available = True
        except ImportError:
            pass

    # ==================== 公开 API ====================

    def create_goal(self, goal_id: str, description: str,
                    verification: dict = None,
                    allowed_files: list = None,
                    forbidden_files: list = None,
                    max_iterations: int = 5,
                    cooldown_seconds: int = 0,
                    tags: list = None) -> GoalConfig:
        """创建一个新 Goal

        verification 格式:
          {"l1": {"command": "gradle build", "expected_exit_code": 0},
           "l2": {"command": "gradle test", "expected_exit_code": 0},
           "l3": {"command": "gradle runGameTest --tests *TreeCutting*", "expected_exit_code": 0}}
        """
        ver = verification or {}

        cfg = GoalConfig(
            goal_id=goal_id,
            description=description,
            verification={
                "l1": ver.get("l1", {"command": "", "expected_exit_code": 0}),
                "l2": ver.get("l2", {"command": "", "expected_exit_code": 0}),
                "l3": ver.get("l3", {"command": "", "expected_exit_code": 0}),
            },
            allowed_files=allowed_files or [],
            forbidden_files=forbidden_files or [],
            max_iterations=max_iterations,
            cooldown_seconds=cooldown_seconds,
            tags=tags or [],
        )

        state = GoalState(
            goal_id=goal_id,
            config=asdict(cfg),
            status="idle",
            current_verification="l1",
            started_at=time.time(),
        )
        self._goals[goal_id] = state
        self._save_state(state)
        return cfg

    def load_goal(self, goal_id: str) -> Optional[GoalState]:
        """从磁盘加载之前创建的 Goal"""
        path = self.results_dir / f"{goal_id}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            # 重新构造 state
            state = GoalState(
                goal_id=data["goal_id"],
                config=data.get("config", {}),
                status=data.get("status", "idle"),
                current_verification=data.get("current_verification", "l1"),
                iteration=data.get("iteration", 0),
                started_at=data.get("started_at", 0),
                completed_at=data.get("completed_at"),
                history=data.get("history", []),
                last_error=data.get("last_error", ""),
            )
            self._goals[goal_id] = state
            return state
        return None

    def verify_one(self, goal_id: str) -> dict:
        """运行单级验证 (当前 level 的验证命令)

        调用时机: Agent 修改代码后，调用此方法验证当前 level。
        如果通过 → 自动晋级到下一级。
        如果失败 → 记录错误，返回错误信息供 Agent 修复。

        Returns:
          {"passed": bool, "level": "l1", "output": "...", "next_level": "l2", "ready_for_l4": bool}
        """
        state = self._get_state(goal_id)
        if not state:
            return {"passed": False, "error": f"goal {goal_id} not found"}

        level = state.current_verification
        state.iteration += 1
        state.status = f"{level}_running"

        # 运行命令
        ver = state.config.get("verification", {}).get(level, {})
        cmd = ver.get("command", "")
        expected_ec = ver.get("expected_exit_code", 0)
        timeout = ver.get("timeout_seconds", 300)

        if not cmd:
            # 无命令 → 假通过（跳到下一级）
            return self._auto_pass(goal_id, state, level)

        result = self._run_cmd(cmd, expected_ec, timeout)

        # 记录
        entry = {
            "iteration": state.iteration,
            "level": level,
            "command": cmd[:120],
            "passed": result["passed"],
            "exit_code": result.get("exit_code"),
            "output": result.get("output", "")[:3000],
            "timestamp": time.time(),
        }
        state.history.append(entry)
        state.last_error = "" if result["passed"] else result.get("output", "")[:500]

        if result["passed"]:
            # → 晋级
            return self._advance_level(goal_id, state, level, entry)
        else:
            # → 记录失败，检查重试
            return self._handle_failure(goal_id, state, level, result, entry)

    def verify_all(self, goal_id: str) -> dict:
        """运行所有未通过的验证 (L1→L2→L3 顺序)

        从当前的 verification level 开始，逐级运行直到全部通过或某级失败。

        Returns:
          {"l1_passed": bool, "l2_passed": bool, "l3_passed": bool,
           "ready_for_l4": bool, "history": [...]}
        """
        state = self._get_state(goal_id)
        if not state:
            return {"error": f"goal {goal_id} not found"}

        results = {"l1_passed": False, "l2_passed": False, "l3_passed": False, "ready_for_l4": False}

        levels = ["l1", "l2", "l3"]
        for level in levels:
            if state.status == "ready_for_l4" or state.status == "failed":
                break
            if state.status == "abandoned":
                break

            # 跳过已通过的级
            if self._level_already_passed(state, level):
                results[f"{level}_passed"] = True
                continue

            r = self.verify_one(goal_id)
            results[f"{state.current_verification}_passed"] = r.get("passed", False)

            if not r.get("passed"):
                break

            # 重新加载 state (verify_one 可能已更新)
            state = self._get_state(goal_id) or state

        # 最终检查
        if state and state.status == "ready_for_l4":
            results["l1_passed"] = True
            results["l2_passed"] = True
            results["l3_passed"] = True
            results["ready_for_l4"] = True

        results["history"] = state.history if state else []
        return results

    def get_last_error(self, goal_id: str) -> str:
        """获取最后一次验证错误 (供 Agent 用于修复代码)"""
        state = self._get_state(goal_id)
        return state.last_error if state else ""

    def get_status(self, goal_id: str) -> Optional[dict]:
        """获取 Goal 完整状态"""
        state = self._get_state(goal_id)
        if not state:
            return None
        return {
            "goal_id": state.goal_id,
            "status": state.status,
            "current_verification": state.current_verification,
            "iteration": state.iteration,
            "max_iterations": state.config.get("max_iterations", 5),
            "should_switch_strategy": self._should_switch_strategy(state),
            "progress": self._calc_progress(state),
            "last_error": state.last_error[:500],
            "history_count": len(state.history),
        }

    def archive(self, goal_id: str) -> dict:
        """归档已通过的 Goal (全部 L1+L2+L3 通过后调用)

        1. 保存蓝图到 task_graphs/
        2. 联动 Hook 系统创建 L2 待验证条目
        """
        state = self._get_state(goal_id)
        if not state:
            return {"error": "goal not found"}

        if state.status != "ready_for_l4":
            return {"error": f"goal not ready for archive, status={state.status}"}

        # 蓝图归档
        blueprint = {
            "goal_id": state.goal_id,
            "description": state.config.get("description", "")[:200],
            "verification": state.config.get("verification", {}),
            "constraints": {
                "allowed_files": state.config.get("allowed_files", []),
                "forbidden_files": state.config.get("forbidden_files", []),
            },
            "iterations": state.iteration,
            "execution_path": state.history,
            "tags": state.config.get("tags", []),
            "verified_at": time.time(),
        }

        bp_path = self.task_graphs_dir / f"{state.goal_id}.json"
        bp_path.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")

        # Hook 联动
        hook_result = None
        if self._hook_available:
            try:
                entry = {
                    "type": "fix" if "fix" in state.goal_id.lower() or "bug" in state.goal_id.lower() else "modification",
                    "goal": state.config.get("description", state.goal_id),
                    "action": f"Goal {state.goal_id}: {state.iteration} 次迭代, L1+L2+L3 通过",
                    "result": f"✅ 所有自动验证通过，等待 L4 人工测试",
                    "compiled": True,
                    "tags": state.config.get("tags", []),
                    "verification_profile": "l1_l2_l3",
                    "goal_id": state.goal_id,
                }
                eid = self._add_pending_entry(entry)
                hook_result = {"entry_id": eid, "status": "L2_pending"}
            except Exception as e:
                hook_result = {"error": str(e)}

        return {
            "archived": True,
            "blueprint": str(bp_path),
            "hook": hook_result,
        }

    # ==================== 内部: 验证执行 ====================

    def _run_cmd(self, cmd: str, expected_ec: int, timeout: int) -> dict:
        """运行命令并检查退出码"""
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout
            )
            passed = proc.returncode == expected_ec
            output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            return {
                "passed": passed,
                "exit_code": proc.returncode,
                "expected": expected_ec,
                "output": output.strip()[:3000],
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "exit_code": -1, "output": f"⏰ 超时 ({timeout}s)"}
        except FileNotFoundError:
            return {"passed": False, "exit_code": -1, "output": f"❌ 命令未找到: {cmd[:80]}"}

    def _auto_pass(self, goal_id: str, state: GoalState, level: str) -> dict:
        """无验证命令 → 自动通过"""
        entry = {
            "iteration": state.iteration,
            "level": level,
            "command": "(auto-pass: no command)",
            "passed": True,
            "exit_code": 0,
            "timestamp": time.time(),
        }
        state.history.append(entry)
        return self._advance_level(goal_id, state, level, entry)

    def _advance_level(self, goal_id: str, state: GoalState, level: str, entry: dict) -> dict:
        """晋级到下一级验证"""
        level_order = ["l1", "l2", "l3"]
        idx = level_order.index(level)

        if idx >= 2:  # l3 通过 → ready for L4
            state.status = "ready_for_l4"
            state.completed_at = time.time()
            state.current_verification = "done"
            self._save_state(state)
            print(f"  [GoalRunner] 🎉 {goal_id}: L1+L2+L3 全部通过 → 可以进游戏测试了 (L4)")
            return {
                "passed": True,
                "level": level,
                "next_level": "done",
                "ready_for_l4": True,
                "output": entry.get("output", ""),
            }
        else:
            next_level = level_order[idx + 1]
            state.current_verification = next_level
            state.status = f"{next_level}_running"
            state.iteration -= 1  # 不占重试次数 (晋级是正向)
            self._save_state(state)
            return {
                "passed": True,
                "level": level,
                "next_level": next_level,
                "ready_for_l4": False,
                "output": entry.get("output", ""),
            }

    def _handle_failure(self, goal_id: str, state: GoalState, level: str,
                        result: dict, entry: dict) -> dict:
        """处理验证失败"""
        # 记录错误哈希 (用于检测同一错误重复)
        error_snippet = result.get("output", "")[:200]
        error_hash = str(hash(error_snippet))
        state.error_history[error_hash].append(state.iteration)
        state.last_error = result.get("output", "")[:500]

        # 检查是否超过最大迭代
        max_iter = state.config.get("max_iterations", 5)
        if state.iteration >= max_iter:
            state.status = "failed"
            self._save_state(state)
            print(f"  [GoalRunner] ❌ {goal_id}: 超过最大迭代次数 ({max_iter}), 已归档")
            return {
                "passed": False,
                "level": level,
                "max_iterations_reached": True,
                "output": result.get("output", ""),
                "hint": "最大重试次数已耗尽，建议缩小改动范围或重启 Goal",
            }

        # 检查是否为根本性错误（同一错误连续 3 次）
        if self._is_repeated_error_stuck(state):
            state.status = "user_intervention_required"
            self._save_state(state)
            print(f"  [GoalRunner] ⛔ {goal_id}: 检测到同一错误连续出现 ≥3 次，暂停请求人工介入")
            return {
                "passed": False,
                "level": level,
                "repeated_error_detected": True,
                "output": result.get("output", ""),
                "hint": "⚠️ 同一错误连续出现 3 次，请检查：测试命令是否正确 / 代码是否根本性错误 / 测试环境是否正常",
            }

        # 检查是否需要切换策略
        switch = self._should_switch_strategy(state)

        state.status = f"{level}_failed"
        self._save_state(state)

        print(f"  [GoalRunner] ❌ {level} FAILED (迭代 {state.iteration}/{max_iter})"
              f"{' ⚡ 建议切换策略' if switch else ''}")

        return {
            "passed": False,
            "level": level,
            "iteration": state.iteration,
            "max_iterations": max_iter,
            "should_switch_strategy": switch,
            "output": result.get("output", ""),
            "hint": self._get_retry_hint(state, level, result),
        }

    def _should_switch_strategy(self, state: GoalState) -> bool:
        """同一错误连续 3 次 → 建议切换策略"""
        for err_hash, iterations in state.error_history.items():
            if len(iterations) >= state.config.get("strategy_switch_threshold", 3):
                return True
        return False

    def _is_repeated_error_stuck(self, state: GoalState) -> bool:
        """同一错误连续出现 3 次 → 疑似根本性错误，暂停请求人工介入"""
        for err_hash, iterations in state.error_history.items():
            if len(iterations) >= 3:
                return True
        return False

    def _get_retry_hint(self, state: GoalState, level: str, result: dict) -> str:
        """根据失败类型生成重试提示"""
        output = result.get("output", "").lower()
        if "cannot find symbol" in output or "找不到符号" in output:
            return "编译错误：检查导入和类型名称是否拼写正确"
        if "undefined reference" in output:
            return "链接错误：检查方法/字段是否存在"
        if "test failed" in output or "failures" in output:
            return "测试失败：检查测试断言和业务逻辑"
        if "timeout" in output or "超时" in output:
            return "超时：检查性能或死锁"
        return f"{level.upper()} 验证失败，请检查上面错误日志"

    # ==================== 内部: 状态管理 ====================

    def _get_state(self, goal_id: str) -> Optional[GoalState]:
        if goal_id in self._goals:
            return self._goals[goal_id]
        return self.load_goal(goal_id)

    def _save_state(self, state: GoalState):
        self._goals[state.goal_id] = state
        path = self.results_dir / f"{state.goal_id}.json"
        data = {
            "goal_id": state.goal_id,
            "config": state.config,
            "status": state.status,
            "current_verification": state.current_verification,
            "iteration": state.iteration,
            "error_history": {k: v for k, v in state.error_history.items()},
            "started_at": state.started_at,
            "completed_at": state.completed_at,
            "history": state.history,
            "last_error": state.last_error[:2000],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _level_already_passed(self, state: GoalState, level: str) -> bool:
        """检查某级是否已通过"""
        level_order = ["l1", "l2", "l3"]
        current_idx = level_order.index(state.current_verification) if state.current_verification in level_order else 999
        check_idx = level_order.index(level)
        return check_idx < current_idx

    def _calc_progress(self, state: GoalState) -> dict:
        return {
            "level": state.current_verification,
            "iteration": state.iteration,
            "max_iterations": state.config.get("max_iterations", 5),
            "history_count": len(state.history),
            "ready_for_l4": state.status == "ready_for_l4",
        }


# ============================================================
# GradleVerifier — Gradle 专用验证命令生成器
# ============================================================

class GradleVerifier:
    """生成 Gradle 验证命令模板

    用法:
      gv = GradleVerifier(project_dir="D:/MC_AI_Companion")
      cfg = runner.create_goal(
          goal_id="fix_tree_cutting",
          description="修复砍树中途挖矿",
          verification=gv.all("com.companion.skill.TreeCuttingTest"),
          ...
      )
    """

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)

    def l1_build(self) -> dict:
        return {"command": f"gradle -p \"{self.project_dir}\" build -x test", "expected_exit_code": 0, "timeout_seconds": 300}

    def l2_test(self) -> dict:
        return {"command": f"gradle -p \"{self.project_dir}\" test", "expected_exit_code": 0, "timeout_seconds": 300}

    def l3_gametest(self, test_class: str = None) -> dict:
        if test_class:
            return {"command": f"gradle -p \"{self.project_dir}\" runGameTest --tests \"*{test_class}*\"", "expected_exit_code": 0, "timeout_seconds": 600}
        return {"command": f"gradle -p \"{self.project_dir}\" runGameTest", "expected_exit_code": 0, "timeout_seconds": 600}

    def all(self, gametest_class: str = None) -> dict:
        return {"l1": self.l1_build(), "l2": self.l2_test(), "l3": self.l3_gametest(gametest_class)}
