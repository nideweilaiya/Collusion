"""GoalRunner CLI — 从终端直接运行，调用本地 gradle build/test

用法:
  cd D:/BrainstormOrchestrator
  python run_goal.py --project D:/AI_Workbench/integrations/minecraft/forge-mod --goal config/goals/batch_fix_all.json
  python run_goal.py --project D:/AI_Workbench/integrations/minecraft/forge-mod --goal-id fix_return_null
  python run_goal.py --project D:/AI_Workbench/integrations/minecraft/forge-mod --list-goals
"""
import sys
import os
import json
import time
import subprocess
import re
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ORCHESTRATOR_ROOT = Path(__file__).parent
CONFIG_DIR = ORCHESTRATOR_ROOT / "config" / "goals"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def run(project_dir: str, goal_config: dict):
    """在本地环境执行 GoalRunner 闭环

    Args:
        project_dir: MC Mod 项目根目录
        goal_config: Goal 配置 (来自 JSON 文件)
    """
    print(f"\n{'='*60}")
    print(f"  GoalRunner — 本地执行")
    print(f"  项目: {project_dir}")
    print(f"  Goal: {goal_config.get('goal_id', 'unknown')}")
    print(f"{'='*60}")

    description = goal_config.get("description", "")
    sub_goals = goal_config.get("sub_goals", [])
    verification = goal_config.get("verification", {})
    cmd = verification.get("command", "gradle build")
    max_iter = verification.get("max_iterations", 5)

    print(f"\n📋 任务: {description}")
    print(f"   验证命令: {cmd}")
    print(f"   最大迭代: {max_iter}")
    print(f"   子任务数: {len(sub_goals)}")

    # Step 1: 输出要修改的文件列表
    print(f"\n1️⃣  审计问题:")
    for sg in sub_goals:
        print(f"   [{sg.get('type','?')}] {sg['id']}: {sg.get('pattern','')}")

    # Step 2: 启动编译验证循环
    print(f"\n2️⃣  编译验证循环:")
    for i in range(max_iter):
        print(f"\n   ⏳ 第 {i+1}/{max_iter} 次编译...")
        t_start = time.time()

        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            cwd=project_dir, timeout=300
        )

        elapsed = time.time() - t_start
        passed = result.returncode == 0

        if passed:
            print(f"   ✅ 编译通过 (用时 {elapsed:.1f}s)")
            break
        else:
            # 提取错误信息
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            errors = _extract_errors(stdout + stderr)
            print(f"   ❌ 编译失败 (用时 {elapsed:.1f}s)")
            print(f"      错误数: {len(errors)} 个")
            for e in errors[:5]:
                print(f"      → {e}")
            if i < max_iter - 1:
                print(f"      准备修复...")
            else:
                print(f"      已达最大迭代次数")

    # Step 3: 运行测试
    print(f"\n3️⃣  测试 ...")
    test_cmd = "gradle test"
    result = subprocess.run(test_cmd, shell=True, capture_output=True, text=True,
                            cwd=project_dir, timeout=300)
    test_passed = result.returncode == 0
    if test_passed:
        print(f"   ✅ 测试全部通过")
    else:
        failures = _extract_test_failures((result.stdout or "") + (result.stderr or ""))
        print(f"   ⚠️  测试失败: {len(failures)} 个")
        for f in failures[:5]:
            print(f"      ❌ {f}")

    # Step 4: 汇总
    success = passed or test_passed
    print(f"\n{'='*60}")
    print(f"  {'✅ 完成' if success else '❌ 异常'}")
    print(f"{'='*60}")
    return success


def _extract_errors(output: str) -> list:
    """从 build 输出提取错误信息"""
    errors = []
    for line in output.split("\n"):
        if "error:" in line.lower() and not line.strip().startswith("["):
            errors.append(line.strip()[:120])
    for m in re.finditer(r"([\w/]+\.java:\d+): error", output):
        error_str = output[m.start():m.start()+120].strip()
        if error_str not in errors:
            errors.append(error_str)
    return errors[:10]


def _extract_test_failures(output: str) -> list:
    failures = []
    for m in re.finditer(r"([\w.]+>\w+)\s+FAILED", output):
        failures.append(m.group(1))
    return failures[:10]


def list_goals():
    """列出所有可用的 Goal 配置"""
    print("可用的 Goal 配置:")
    for f in sorted(CONFIG_DIR.glob("*.json")):
        with open(f) as fh:
            cfg = json.load(fh)
        sub = cfg.get("sub_goals", [])
        print(f"  {f.stem:<30} {cfg.get('description',''):<40} ({len(sub)} 子任务)")


# ==================== CLI ====================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GoalRunner — 本地自动化执行闭环")
    parser.add_argument("--project", default=".", help="MC Mod 项目目录")
    parser.add_argument("--goal", help="Goal 配置文件路径")
    parser.add_argument("--goal-id", help="Goal ID (从 config/goals/ 加载)")
    parser.add_argument("--list-goals", action="store_true", help="列出可用 Goal 配置")

    args = parser.parse_args()

    if args.list_goals:
        list_goals()
        sys.exit(0)

    if args.goal:
        with open(args.goal) as f:
            config = json.load(f)
        run(args.project, config)
    elif args.goal_id:
        path = CONFIG_DIR / f"{args.goal_id}.json"
        if path.exists():
            with open(path) as f:
                config = json.load(f)
            run(args.project, config)
        else:
            print(f"❌ Goal 配置不存在: {path}")
            sys.exit(1)
    else:
        parser.print_help()
