#!/usr/bin/env python
"""downgrade_pending.py — L2→L1 定时降级脚本

功能：扫描 asset_library 中超过 24 小时未确认的 L2 待验证经验，
      自动降级为 L1，移入 causal_memory 作为已知陷阱。

使用方式：
    # 手动执行
    python skills/collusion/hooks/downgrade_pending.py

    # 只统计，不执行（预览模式）
    python skills/collusion/hooks/downgrade_pending.py --dry-run

    # 设置超时阈值（默认 24 小时）
    python skills/collusion/hooks/downgrade_pending.py --max-age-hours 48

设计原则：
    - 幂等：多次执行结果相同，不会重复降级
    - 可预览：--dry-run 只报告不修改
    - 可配置：--max-age-hours 自定义超时时间
    - 静默：无过期条目时无输出（适合定时任务）
"""

import json
import sys
from datetime import datetime, timezone, timedelta

# 将 hooks 目录加入 path
_HOOKS_DIR = __file__.rsplit("\\", 1)[0] if "\\" in __file__ else __file__.rsplit("/", 1)[0]
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from archive_utils import (
    ASSET_LIBRARY_DIR,
    CAUSAL_MEMORY_DIR,
    load_asset_index,
    save_asset_index,
)


# 默认超时阈值
DEFAULT_MAX_AGE_HOURS = 24


def downgrade_pending(max_age_hours: int = DEFAULT_MAX_AGE_HOURS, dry_run: bool = False) -> int:
    """降级超时未确认的 L2 条目

    Args:
        max_age_hours: 超时阈值（小时）
        dry_run: True = 只报告不修改

    Returns:
        降级条目数量
    """
    index = load_asset_index()
    if not index:
        return 0

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)

    # 加载因果记忆图
    graph_path = CAUSAL_MEMORY_DIR / "graph.json"
    if graph_path.exists():
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    else:
        graph = {"nodes": {}, "edges": [], "version": "0.5.0"}

    downgraded = 0

    # 找出所有超时的 L2
    pending_ids = list(index.keys())
    for entry_id in pending_ids:
        entry = index[entry_id]
        if entry.get("verification_status") != "pending":
            continue

        created_at_str = entry.get("created_at")
        if not created_at_str:
            continue

        try:
            # 处理可能缺少时区信息的时间戳
            if created_at_str.endswith("Z"):
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            elif "+" not in created_at_str and not created_at_str.endswith("Z"):
                created_at = datetime.fromisoformat(created_at_str).replace(tzinfo=timezone.utc)
            else:
                created_at = datetime.fromisoformat(created_at_str)
        except (ValueError, TypeError):
            continue

        age = now - created_at

        if age < timedelta(hours=max_age_hours):
            continue  # 未超时

        # ── 超时 → 降级 ──
        if not dry_run:
            # 1. 创建 causal_memory 节点
            node_id = f"downgrade_{entry_id}"
            graph["nodes"][node_id] = {
                "id": node_id,
                "node_type": "failure",
                "label": entry.get("goal", "未命名"),
                "description": entry.get("action", "")[:200],
                "error_summary": f"超过{max_age_hours}小时未确认的修复尝试",
                "original_entry_id": entry_id,
                "downgraded_at": now.isoformat(),
                "downgrade_reason": f"超过{max_age_hours}小时未确认",
                "tags": entry.get("tags", []),
                "created_at": entry.get("created_at", ""),
            }

            # 2. 从 asset_library 移除
            del index[entry_id]

        downgraded += 1

        # 报告
        goal_preview = entry.get("goal", "?")[:50]
        age_hours = age.total_seconds() / 3600
        _log(f"  {'[DRY-RUN] ' if dry_run else ''}"
             f"↓ {entry_id}: \"{goal_preview}\" "
             f"(已过 {age_hours:.1f} 小时)")

    # 保存（仅非预览模式）
    if not dry_run and downgraded > 0:
        save_asset_index(index)
        CAUSAL_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        graph_path.write_text(
            json.dumps(graph, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    return downgraded


def run_integrity_check() -> dict:
    """完整性检查：报告当前 asset_library 和 causal_memory 的状态

    Returns:
        {
            "asset_total": N,
            "pending": N,
            "pending_overdue": N,  # 超时的
            "verified": N,
        }
    """
    index = load_asset_index()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=DEFAULT_MAX_AGE_HOURS)

    pending = 0
    pending_overdue = 0
    verified = 0

    for entry in index.values():
        status = entry.get("verification_status")
        if status == "pending":
            pending += 1
            created_at_str = entry.get("created_at")
            if created_at_str:
                try:
                    if created_at_str.endswith("Z"):
                        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    elif "+" not in created_at_str:
                        created_at = datetime.fromisoformat(created_at_str).replace(tzinfo=timezone.utc)
                    else:
                        created_at = datetime.fromisoformat(created_at_str)
                    if created_at < cutoff:
                        pending_overdue += 1
                except (ValueError, TypeError):
                    pass
        elif status == "verified":
            verified += 1

    return {
        "asset_total": len(index),
        "pending": pending,
        "pending_overdue": pending_overdue,
        "verified": verified,
    }


def main():
    # ── 解析参数 ──
    dry_run = "--dry-run" in sys.argv
    max_age_hours = DEFAULT_MAX_AGE_HOURS

    for arg in sys.argv:
        if arg.startswith("--max-age-hours="):
            try:
                max_age_hours = int(arg.split("=")[1])
            except (ValueError, IndexError):
                pass

    # ── 完整性检查 ──
    stats = run_integrity_check()

    if stats["pending"] == 0:
        # 无待处理条目，静默退出（适合定时任务）
        return

    # ── 降级执行 ──
    if dry_run:
        _log(f"[Collusion] 降级预览模式 (超时: {max_age_hours}h)")
    else:
        _log(f"[Collusion] 开始降级 (超时: {max_age_hours}h)")

    _log(f"  资产库: {stats['asset_total']} 总计, "
         f"{stats['pending']} 待验证 (其中 {stats['pending_overdue']} 已超时), "
         f"{stats['verified']} 已验证")

    count = downgrade_pending(max_age_hours=max_age_hours, dry_run=dry_run)

    if count > 0:
        if dry_run:
            _log(f"[Collusion] 预览: {count} 条将被降级 (执行 --dry-run 移除此项以实际执行)")
        else:
            # 降级后重新统计
            after = run_integrity_check()
            _log(f"[Collusion] 降级完成: {count} 条已移至 causal_memory")
            _log(f"  降级后: {after['asset_total']} 总计, "
                 f"{after['pending']} 待验证, {after['verified']} 已验证")
    else:
        _log(f"[Collusion] 无超时条目需要降级")


def _log(msg: str):
    """写日志到 stderr"""
    print(msg, file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _log(f"[Collusion] 降级脚本异常: {e}")
