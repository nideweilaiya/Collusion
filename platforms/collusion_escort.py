#!/usr/bin/env python3
"""Collusion Reasonix 分身护航脚本 (collusion_escort.py)

在 Reasonix 中实现"分身响应"：
  - 当用户请求方案设计时，自动触发 Collusion 编排
  - 编排完成后，自动通知用户结果

部署方式（Reasonix Hook 配置）：
  {
    "hooks": {
      "on_user_message": {
        "command": "python",
        "args": ["path/to/collusion_escort.py", "--check", "{message}"]
      },
      "on_task_complete": {
        "command": "python",
        "args": ["path/to/collusion_escort.py", "--notify", "{task_id}"]
      }
    }
  }

环境要求：
  - collusion-mcp 已安装 (pip install collusion-mcp)
  - DEEPSEEK_API_KEY 已设置（Reasonix 用户可跳过）
  - MCP Server 已启动 (collusion-mcp --sse --port 8020)
"""
import json
import sys
import os
import time
import urllib.request
import urllib.error

MCP_URL = os.environ.get("COLLUSION_MCP_URL", "http://localhost:8020")
TRIGGER_KEYWORDS = [
    "方案", "设计", "架构", "选型", "技术栈", "怎么做",
    "如何实现", "选哪个", "推荐", "评估", "对比",
    "design", "architecture", "implement", "how to",
    "choose", "recommend", "compare",
]


def should_trigger(message: str) -> bool:
    """判断用户消息是否应触发编排"""
    msg_lower = message.lower()
    score = sum(1 for kw in TRIGGER_KEYWORDS if kw in msg_lower)
    # 至少匹配 2 个关键词，或者消息长度 > 50 字
    return score >= 2 or len(message) > 50


def check_and_trigger(message: str) -> dict:
    """检测并触发编排"""
    if not should_trigger(message):
        return {"triggered": False, "reason": "未匹配触发关键词"}

    # 通过 HTTP API 触发编排（异步）
    try:
        req = urllib.request.Request(
            f"{MCP_URL}/api/orchestrate",
            data=json.dumps({
                "task": message[:500],
                "agents": 3,
                "format": "html",
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        return {"triggered": True, "task_id": data.get("task_id", "")}
    except urllib.error.URLError as e:
        return {"triggered": False, "error": f"MCP Server 连接失败: {e}"}
    except Exception as e:
        return {"triggered": False, "error": str(e)}


def notify_complete(task_id: str) -> dict:
    """通知用户编排完成"""
    try:
        req = urllib.request.Request(
            f"{MCP_URL}/api/status/{task_id}",
            method="GET",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        phase = data.get("phase", "unknown")

        if phase == "done":
            return {
                "notify": True,
                "phase": "done",
                "report_url": f"{MCP_URL}/outputs/{task_id}/report.html",
                "message": (
                    f"✅ Collusion 方案设计完成！\n"
                    f"📊 查看 HTML 报告: {MCP_URL}/outputs/{task_id}/report.html\n"
                    f"💬 获取方案详情: /brainstorm-result {task_id}"
                ),
            }
        elif phase == "error":
            return {
                "notify": True,
                "phase": "error",
                "error": data.get("error_message", "未知错误"),
            }
        else:
            wait_msg = f"⏳ 编排进行中 ({phase})..."
            if data.get("pending_questions"):
                wait_msg += f"\n❓ 有 {len(data['pending_questions'])} 个引导问题待回答"
            return {"notify": True, "phase": phase, "message": wait_msg}

    except Exception as e:
        return {"notify": False, "error": str(e)}


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Collusion Reasonix 分身护航脚本")
    parser.add_argument("--check", type=str,
                        help="检查用户消息是否触发编排")
    parser.add_argument("--trigger", type=str,
                        help="直接触发编排（传入任务描述）")
    parser.add_argument("--notify", type=str,
                        help="查询任务状态并通知")
    parser.add_argument("--wait", type=str,
                        help="等待任务完成（轮询模式）")

    args = parser.parse_args()

    if args.check:
        result = check_and_trigger(args.check)
        if result.get("triggered"):
            print(f"TRIGGERED:{result['task_id']}")
        else:
            print(f"SKIPPED:{result.get('reason', '')}")

    elif args.trigger:
        result = check_and_trigger(args.trigger)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.notify:
        result = notify_complete(args.notify)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.wait:
        task_id = args.wait
        print(f"等待任务 {task_id} 完成...")
        for _ in range(30):  # 最多等 5 分钟
            result = notify_complete(task_id)
            if result.get("phase") in ("done", "error"):
                print(result.get("message", json.dumps(result)))
                break
            time.sleep(10)
        else:
            print("⏰ 等待超时，请稍后手动查询")

    else:
        parser.print_help()
