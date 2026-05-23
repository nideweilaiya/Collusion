#!/usr/bin/env python
"""Hook: Stop — 任务完成归档

触发时机：Reasonix Agent 完成一轮完整的工具调用循环后。

处理流程：
    1. 读取 stdin 中的对话上下文（JSON）
    2. 检测本轮是否包含"目标-行动-结果"三要素
    3. 判定经验等级 L1/L2
    4. L2 → 写入 data/asset_library/，标记"待验证"
    5. L1 → 提取错误摘要，写入 data/causal_memory/ 作为已知陷阱
    6. 检测中断信号 → 保存中断摘要
    7. 静默退出，不输出任何内容

设计原则：
    - 不打断用户：归档是静默的，只在日志留一条记录
    - 不输出到 stdout：避免污染 Reasonix 的正常对话流
    - 异常不传播：内部错误只写 stderr，不冒泡到宿主编排器

安装（在 Reasonix settings.json 中配置）：
    {
      "hooks": {
        "Stop": [{
          "command": "python",
          "args": ["skills/collusion/hooks/on_stop.py"]
        }]
      }
    }
"""

import json
import sys
import traceback

# 将 hooks 目录加入 path，方便 import archive_utils
_HOOKS_DIR = __file__.rsplit("\\", 1)[0] if "\\" in __file__ else __file__.rsplit("/", 1)[0]
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import re

from archive_utils import (
    has_triad,
    grade_experience,
    extract_experience,
    add_pending_entry,
    add_failed_attempt,
    save_interrupt,
    is_abandonment,
)



# ── 可选联动：每次 Stop 顺便清理过期 L2 ──
_AUTO_DOWNGRADE = True  # 设为 False 可关闭

if _AUTO_DOWNGRADE:
    try:
        from downgrade_pending import downgrade_pending
        _has_downgrade = True
    except ImportError:
        _has_downgrade = False
else:
    _has_downgrade = False


def main():
    # ── 1. 读取上下文 ──
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return  # 无输入，跳过
        context = json.loads(raw)
    except json.JSONDecodeError:
        # 如果不是 JSON，尝试当纯文本处理
        context = {"text": raw}
    except Exception:
        # 任何读取异常 → 静默跳过，不干扰宿主编排
        return

    text = _get_text(context)

    # ── 2. 先检测中断信号（独立于三要素） ──
    interrupt_signals = ["先这样", "明天", "下次", "暂停", "pause", "break", "继续"]
    if any(sig in text.lower() for sig in interrupt_signals):
        save_interrupt(
            goal=_extract_goal_quick(text),
            completed=["本轮工具调用已完成"],
            blocked=text[-200:] if len(text) > 200 else text,
            next_steps="（待用户指示）"
        )
        _log(f"[Collusion] 中断摘要已保存")
        # 中断场景也可能有经验，继续执行下面的检测

    # ── 3. 检测三要素 ──
    if not has_triad(context):
        return  # 没有可归档的经验单元

    # ── 4. 检查放弃信号 ──
    if is_abandonment(text):
        # L0: 记录废案元数据（简化版——仅打日志）
        _log(f"[Collusion] L0 废弃方案，跳过归档")
        return

    # ── 4. 判定等级 ──
    level = grade_experience(context)

    # ── 5. 提取经验 ──
    entry = extract_experience(context)

    # ── 6. 归档 ──
    if level == 1:
        add_failed_attempt(entry)
        _log(f"[Collusion] L1 已归档到 causal_memory: {entry.get('goal', '?')[:40]}")
    elif level >= 2:
        entry_id = add_pending_entry(entry)
        _log(f"[Collusion] L2 已归档到 asset_library: {entry_id} | {entry.get('goal', '?')[:40]}")

    # ── 7. 联动：顺便清理过期 L2 ──
    if _has_downgrade:
        try:
            count = downgrade_pending()
            if count > 0:
                _log(f"[Collusion] 联动降级: {count} 条过期 L2 → L1")
        except Exception:
            pass  # 降级失败不应影响主归档逻辑


def _get_text(context) -> str:
    """从上下文中提取文本"""
    if isinstance(context, str):
        return context
    if isinstance(context, dict):
        for key in ("text", "conversation", "message", "input", "content"):
            if key in context and isinstance(context[key], str):
                return context[key]
        return str(context)
    return str(context)


def _extract_goal_quick(text: str) -> str:
    """快速提取目标描述（用于中断摘要）"""
    patterns = [
        r'(?:要|需要|想|打算)(.{5,60}?)(?:[，。；]|$)',
        r'(?:修|做|改)(.{5,60}?)(?:[，。；]|$)',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()[:80]
    return text[:80]


def _log(msg: str):
    """写日志到 stderr（不污染 stdout）

    stdout 可能被 Reasonix 用作协议通道，
    所有日志必须走 stderr。
    """
    print(msg, file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 任何未捕获异常 → 静默，避免 Hook 崩溃影响宿主
        print(f"[Collusion] Hook:Stop 异常: {traceback.format_exc()}", file=sys.stderr, flush=True)
