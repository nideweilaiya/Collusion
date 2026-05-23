#!/usr/bin/env python
"""Hook: PostToolUse — 关键行动归档

触发时机：Agent 成功调用关键工具（write_file, run_command 等）之后。

处理流程：
    1. 读取工具调用事件（stdin JSON）
    2. 检查工具名是否在监控列表中
    3. 检查对话上下文中是否有 Bug/修复相关关键词
    4. 如果是 Bug 修复 → 提取摘要，生成 L1/L2 经验
    5. 普通文件写入 → 跳过（由 Stop 钩子做全量检测）

关键设计：
    - 只关注"写了什么文件"和"运行了什么命令"
    - 完整的三要素检测交给 Stop 钩子，这里只做轻量级快照
    - 不输出到 stdout

安装（在 Reasonix settings.json 中配置）：
    {
      "hooks": {
        "PostToolUse": [{
          "command": "python",
          "args": ["skills/collusion/hooks/on_post_tool.py"]
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
    is_monitored_tool,
    is_bug_fix,
    extract_experience,
    add_pending_entry,
    add_failed_attempt,
    grade_experience,
    MONITORED_TOOLS,
)


def main():
    # ── 1. 读取事件 ──
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except json.JSONDecodeError:
        event = {"text": raw}
    except Exception:
        return

    # ── 2. 检查工具名 ──
    tool_name = event.get("tool_name") or event.get("name") or event.get("tool", "")
    if not is_monitored_tool(tool_name):
        return  # 不是监控工具，跳过

    # ── 3. 提取上下文文本 ──
    text = _get_text(event)

    # ── 4. 如果是文件写入工具，记录修改的文件 ──
    modified_file = None
    if tool_name in ("write_file", "edit_file", "multi_edit"):
        args = event.get("args") or event.get("arguments") or {}
        if isinstance(args, dict):
            modified_file = args.get("path") or args.get("file") or ""

    # ── 5. 检查是否是 Bug 修复上下文 ──
    if not is_bug_fix(text) and not is_bug_fix(str(event)):
        # 不是 Bug 修复 — 不在这里归档
        # 完整的经验提取交给 Stop 钩子处理
        _log(f"[Collusion] PostToolUse 检测到 {tool_name}，非修复上下文，跳过")
        return

    # ── 6. 是 Bug 修复 → 提取经验 ──
    entry = extract_experience(text)

    # 补充工具特定信息
    if modified_file:
        entry["files_modified"] = [modified_file]
    entry["source_hook"] = "PostToolUse"
    entry["tool_name"] = tool_name

    # ── 7. 判定等级并归档 ──
    level = grade_experience({"text": text, "tool_calls": [{"name": tool_name}]})

    if level == 1:
        add_failed_attempt(entry)
        _log(f"[Collusion] PostToolUse L1 causal_memory: "
             f"{entry.get('goal', '?')[:40]} | {modified_file or tool_name}")
    elif level >= 2:
        entry_id = add_pending_entry(entry)
        _log(f"[Collusion] PostToolUse L2 asset_library: {entry_id} | "
             f"{entry.get('goal', '?')[:40]} | {modified_file or tool_name}")


def _get_text(event) -> str:
    """从事件中提取连接文本（含对话上下文）"""
    parts = []

    # 工具参数
    args = event.get("args") or event.get("arguments") or {}
    if isinstance(args, dict):
        for k in ("text", "content", "command", "path"):
            if k in args and isinstance(args[k], str):
                parts.append(f"{k}={args[k][:200]}")

    # 工具结果
    result = event.get("result") or event.get("output") or ""
    if isinstance(result, str) and result:
        parts.append(f"result={result[:200]}")

    # 对话上下文（如果有）
    context = event.get("context") or event.get("conversation") or ""
    if isinstance(context, str) and context:
        parts.append(f"context={context[:500]}")

    return " | ".join(parts)


def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(f"[Collusion] Hook:PostToolUse 异常: {traceback.format_exc()}", file=sys.stderr, flush=True)
