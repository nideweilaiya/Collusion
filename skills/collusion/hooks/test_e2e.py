#!/usr/bin/env python
"""端到端测试：模拟 Reasonix 触发三个 Hook 的完整流程

测试场景：
  1. PostToolUse — Bug 修复 write_file → L2 轻量归档
  2. PostToolUse — 普通写入 → 跳过（不是修复上下文）
  3. Stop — 完整 Bug 修复对话 → L2 完整归档
  4. Stop — 中断信号 → 保存中断摘要
  5. UserPromptSubmit — "好了"确认 → 升级 L3 + 写方案页
  6. 验证所有输出
  7. 清理测试数据

用法：
    python skills/collusion/hooks/test_e2e.py
"""

import json
import sys
import os
from pathlib import Path

# 将 hooks 目录加入 path
_HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOKS_DIR))

from archive_utils import (
    ASSET_LIBRARY_DIR, CAUSAL_MEMORY_DIR, INTERRUPTS_DIR, SOLUTIONS_DIR,
    get_asset_count,
)


def simulate_posttooluse_bugfix():
    """场景 1：PostToolUse — Bug 修复后的文件写入"""
    print("\n" + "=" * 60)
    print("🧪 场景 1: PostToolUse — Bug 修复 write_file")
    print("=" * 60)

    event = {
        "tool_name": "write_file",
        "name": "write_file",
        "args": {
            "path": "src/auth.py",
            "content": "def login(username, password):\n    if not username:\n        return None\n    ..."
        },
        "result": "edit blocks: 1/1 applied",
        "conversation": "用户说 auth.py 有个 bug，login 返回 null 时没有检查"
    }

    _run_hook("on_post_tool.py", event)


def simulate_posttooluse_normal_write():
    """场景 2：PostToolUse — 普通写入（非修复上下文）"""
    print("\n" + "=" * 60)
    print("🧪 场景 2: PostToolUse — 普通文件写入（应跳过）")
    print("=" * 60)

    event = {
        "tool_name": "write_file",
        "name": "write_file",
        "args": {
            "path": "src/utils.py",
            "content": "def helper():\n    pass"
        },
        "result": "edit blocks: 1/1 applied",
        "conversation": "添加一个工具函数"
    }

    _run_hook("on_post_tool.py", event)


def simulate_stop_bugfix():
    """场景 3：Stop — 完整 Bug 修复对话周期"""
    print("\n" + "=" * 60)
    print("🧪 场景 3: Stop — 完整 Bug 修复对话")
    print("=" * 60)

    context = {
        "text": (
            "要修复 auth.py 中 login 函数返回 None 时前端报错的问题。"
            "修改了 auth.py 的 login 函数，添加了 null 检查。"
            "测试通过了，没有错误。"
        ),
        "tool_calls": [
            {"name": "read_file", "args": {"path": "src/auth.py"}},
            {"name": "edit_file", "args": {"path": "src/auth.py"}},
            {"name": "run_command", "args": {"command": "pytest tests/test_auth.py"}},
        ]
    }

    _run_hook("on_stop.py", context)


def simulate_stop_interruption():
    """场景 4：Stop — 中断信号"""
    print("\n" + "=" * 60)
    print("🧪 场景 4: Stop — 中断信号（先这样）")
    print("=" * 60)

    context = {
        "text": "先这样，auth.py 的修改已经做了一半，明天继续修 register 函数",
        "tool_calls": [
            {"name": "edit_file", "args": {"path": "src/auth.py"}},
        ]
    }

    _run_hook("on_stop.py", context)


def simulate_user_confirmation():
    """场景 5：UserPromptSubmit — 用户确认"""
    print("\n" + "=" * 60)
    print("🧪 场景 5: UserPromptSubmit — 用户说“好了”")
    print("=" * 60)

    # 注意：这个输入应该包含"好了"关键词
    # 为了测试准确，我们先看一下当前有多少 L2
    before = _count_pending()
    print(f"  确认前 L2 数量: {before}")

    event = {
        "text": "好了，这个修好了，测试通过了",
        "message": "好了，这个修好了，测试通过了"
    }

    _run_hook("on_user_submit.py", event)


def verify_results():
    """场景 6：验证所有输出"""
    print("\n" + "=" * 60)
    print("📊 场景 6: 验证结果")
    print("=" * 60)

    passed = 0
    failed = 0

    # 检查 asset_library
    index_path = ASSET_LIBRARY_DIR / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        pending = [k for k, v in index.items() if v.get("verification_status") == "pending"]
        verified = [k for k, v in index.items() if v.get("verification_status") == "verified"]
        print(f"\n  ✅ asset_library: {len(index)} 条总计")
        print(f"     ├─ pending (L2): {len(pending)}")
        print(f"     └─ verified (L3): {len(verified)}")

        # 检查是否有最近来自测试的新条目
        test_entries = [k for k in index if k.startswith("pending_")]
        if test_entries:
            print(f"     📌 测试新增 pending: {test_entries[-1][:20]}...")
            passed += 1
        else:
            print("     ⚠️  没有找到测试新增的 pending 条目")
            failed += 1
    else:
        print("  ❌ asset_library/index.json 不存在")
        failed += 1

    # 检查 solutions
    if SOLUTIONS_DIR.exists():
        solution_files = list(SOLUTIONS_DIR.glob("*.md"))
        print(f"\n  ✅ solutions: {len(solution_files)} 个方案页")
        if solution_files:
            print(f"     📄 {solution_files[-1].name}")
            passed += 1
    else:
        print("\n  ⚠️  solutions/ 目录不存在")
        # 不一定失败，可能还没有已验证的条目

    # 检查 causal_memory（看是否有新增）
    causal_path = CAUSAL_MEMORY_DIR / "graph.json"
    if causal_path.exists():
        causal = json.loads(causal_path.read_text(encoding="utf-8"))
        failure_nodes = [k for k, v in causal.get("nodes", {}).items()
                         if v.get("node_type") == "failure"]
        print(f"\n  ✅ causal_memory: {len(causal.get('nodes', {}))} 节点, "
              f"{len(causal.get('edges', []))} 边")
        print(f"     failure 节点: {len(failure_nodes)}")
    else:
        print("\n  ⚠️  causal_memory/graph.json 不存在")

    # 检查 interrupts
    if INTERRUPTS_DIR.exists():
        interrupts = list(INTERRUPTS_DIR.glob("interrupt_*.json"))
        print(f"\n  ✅ interrupts: {len(interrupts)} 个中断摘要")
        if interrupts:
            data = json.loads(interrupts[0].read_text(encoding="utf-8"))
            print(f"     📝 goal: {data.get('goal', '?')[:40]}")
            print(f"     status: {data.get('status', '?')}")
            passed += 1

    # 最终统计
    stats = get_asset_count()
    print(f"\n  📊 资产库统计: {stats}")

    print(f"\n{'─' * 40}")
    print(f"结果: ✅ {passed} 通过  {'❌ ' + str(failed) + ' 失败' if failed else '🎉 全部通过'}")
    return failed == 0


def cleanup_test_data():
    """场景 7：清理测试数据"""
    print("\n" + "=" * 60)
    print("🧹 场景 7: 清理测试数据")
    print("=" * 60)

    # 清理 asset_library 中的测试条目
    index_path = ASSET_LIBRARY_DIR / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        test_keys = [k for k in index if k.startswith("pending_")]
        for k in test_keys:
            del index[k]
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✅ 资产库: 删除 {len(test_keys)} 条测试 pending 条目")

    # 清理 solutions 中的测试文件
    if SOLUTIONS_DIR.exists():
        test_solutions = list(SOLUTIONS_DIR.glob("pending_*.md"))
        for f in test_solutions:
            f.unlink()
        print(f"  ✅ solutions: 删除 {len(test_solutions)} 个测试方案页")

    # 清理 interrupts 中的测试摘要
    if INTERRUPTS_DIR.exists():
        for f in sorted(INTERRUPTS_DIR.glob("interrupt_*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("goal", "").startswith("auth.py"):
                f.unlink()
                print(f"  ✅ interrupts: 删除 {f.name}")


# ── 内部工具 ──

def _run_hook(script_name: str, input_data: dict):
    """通过子进程调用 Hook 脚本，模拟 Reasonix 的 stdin 管道"""
    import subprocess
    script_path = _HOOKS_DIR / script_name

    input_json = json.dumps(input_data, ensure_ascii=False)
    print(f"  输入: {input_json[:120]}...")

    result = subprocess.run(
        [sys.executable, str(script_path)],
        input=input_json,
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8"
    )

    if result.returncode == 0:
        print(f"  退出: 0 (正常)")
    else:
        print(f"  退出: {result.returncode} ⚠️")

    # stderr 输出是 Hook 的日志
    if result.stderr.strip():
        for line in result.stderr.strip().split("\n"):
            if "[Collusion]" in line:
                print(f"  📝 {line.strip()}")


def _count_pending() -> int:
    index_path = ASSET_LIBRARY_DIR / "index.json"
    if not index_path.exists():
        return 0
    index = json.loads(index_path.read_text(encoding="utf-8"))
    return sum(1 for v in index.values()
               if v.get("verification_status") == "pending")


# ── 主流程 ──

if __name__ == "__main__":
    print("🏁 Collusion 自动归档 Hook — 端到端测试")
    print(f"   数据目录: {ASSET_LIBRARY_DIR.parent}")
    print(f"   测试时间: {__import__('datetime').datetime.now().strftime('%H:%M:%S')}")

    # 执行前备份计数
    before = get_asset_count()
    print(f"\n   测试前资产库: {before}")

    # 依次执行
    simulate_posttooluse_bugfix()
    simulate_posttooluse_normal_write()
    simulate_stop_bugfix()
    simulate_stop_interruption()
    simulate_user_confirmation()

    # 验证
    all_ok = verify_results()

    # 清理
    cleanup_test_data()

    # 确认已恢复
    after = get_asset_count()
    print(f"\n   清理后资产库: {after}")

    if all_ok:
        print(f"\n{'🎉' * 5} 端到端测试通过！{'🎉' * 5}")
    else:
        print(f"\n{'⚠️' * 5} 部分测试未通过，请检查上面的失败项。{'⚠️' * 5}")
