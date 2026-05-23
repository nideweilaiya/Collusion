#!/usr/bin/env python
"""Hook: UserPromptSubmit — 用户确认归档

触发时机：用户发送消息给 Agent 之前。

处理流程：
    1. 读取用户即将发送的消息（stdin JSON）
    2. 检测是否包含确认性关键词（"好了""可以""OK"等）
    3. 命中 → 找到最近一条 L2 待验证经验
    4. 升级为 L3 已验证（更新 asset_library/ 元数据）
    5. 静默退出

关键设计：
    - 只升级最近一条 L2，不批量操作
    - 用户说的"好了"通常只针对刚完成的任务
    - 不输出到 stdout，不打断消息发送

安装（在 Reasonix settings.json 中配置）：
    {
      "hooks": {
        "UserPromptSubmit": [{
          "command": "python",
          "args": ["skills/collusion/hooks/on_user_submit.py"]
        }]
      }
    }
"""

import json
import sys
import traceback

_HOOKS_DIR = __file__.rsplit("\\", 1)[0] if "\\" in __file__ else __file__.rsplit("/", 1)[0]
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from archive_utils import (
    is_confirmation,
    find_latest_pending,
    upgrade_to_verified,
    get_asset_count,
)


def main():
    # ── 1. 读取用户消息 ──
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        context = json.loads(raw)
    except json.JSONDecodeError:
        context = {"text": raw}
    except Exception:
        return

    # ── 2. 提取用户文本 ──
    text = _get_text(context)
    if not text:
        return

    # ── 3. 检测确认信号 ──
    if not is_confirmation(text):
        return  # 不是确认消息，跳过

    # ── 4. 找到最近一条 L2 ──
    latest_id = find_latest_pending()
    if latest_id is None:
        _log(f"[Collusion] 检测到确认信号，但无待验证经验可升级")
        return

    # ── 5. 升级为 L3 ──
    success = upgrade_to_verified(latest_id)
    if success:
        stats = get_asset_count()
        _log(f"[Collusion] ↑ {latest_id} 已升级为 L3 已验证 | "
             f"资产库: {stats['verified']} 已验证 / {stats['pending']} 待验证 / {stats['total']} 总计")
    else:
        _log(f"[Collusion] 升级失败: {latest_id} 未找到")


def _get_text(context) -> str:
    if isinstance(context, str):
        return context
    if isinstance(context, dict):
        for key in ("text", "conversation", "message", "input", "content"):
            if key in context and isinstance(context[key], str):
                return context[key]
        return str(context)
    return str(context)


def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(f"[Collusion] Hook:UserSubmit 异常: {traceback.format_exc()}", file=sys.stderr, flush=True)
