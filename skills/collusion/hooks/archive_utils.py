"""archive_utils.py — Collusion 自动归档共用逻辑

L0-L3 分级判定、资产库读写、因果记忆读写、中断管理。
所有 Hook 脚本共用此模块，避免重复逻辑。

用法：
    from archive_utils import grade_experience, add_pending_entry, ...
"""

import json
import os
import re
import uuid
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 路径配置 ──────────────────────────────────────────────
# 优先从环境变量读取，否则向上找 Collusion 项目根目录下的 data/
_HERE = Path(__file__).resolve().parent
_COLLUSION_ROOT = _HERE.parent.parent.parent  # skills/collusion/hooks/ → 项目根

DATA_DIR = Path(os.environ.get(
    "COLLUSION_DATA_DIR",
    str(_COLLUSION_ROOT / "data")
))

ASSET_LIBRARY_DIR = DATA_DIR / "asset_library"
CAUSAL_MEMORY_DIR = DATA_DIR / "causal_memory"
INTERRUPTS_DIR = DATA_DIR / "interrupts"
SOLUTIONS_DIR = DATA_DIR / "solutions"

# ── 信号词表 ─────────────────────────────────────────────
CONFIRM_KEYWORDS = [
    "好了", "可以", "就这样", "OK", "works", "good",
    "不错", "通过了", "没问题", "可以了", "搞定", "完成",
    "done", "fixed", "great", "nice",
]

ABANDON_KEYWORDS = [
    "不要了", "换方向", "算了", "放弃", "不做了",
    "换一个方案", "重新来", "别做了", "停",
]

BUG_KEYWORDS = [
    "bug", "修复", "fix", "错误", "问题",
    "修好了", "fixed", "解决", "故障",
    "exception", "error",
]

MONITORED_TOOLS = {
    "write_file", "edit_file", "multi_edit",
    "run_command", "run_background",
}

# ── 公开 API ──────────────────────────────────────────────


def grade_experience(context: dict) -> int:
    """判定经验等级 L0-L3

    规则（按优先级）:
        L0 — 对话中出现放弃信号
        L1 — 有代码修改 + 编译/运行错误日志
        L2 — 有代码修改 + 无错误
        L3 — 用户明确确认
    """
    text = _get_text(context)

    # L0: 放弃信号 — 立即丢弃
    if _has_any(text, ABANDON_KEYWORDS):
        return 0

    # L1: 有错误日志
    has_code = _has_code_modification(context)
    has_error = _has_error_log(text)

    if has_code and has_error:
        return 1

    # L2: 有代码修改且无错误
    if has_code and not has_error:
        return 2

    # 兜底
    return 1


def has_triad(context: dict) -> bool:
    """检测文本是否包含"目标—行动—结果"三要素

    基于简单关键词启发式，不调用 LLM。
    如果后续需要更高精度，可升级为 LLM 分类调用。
    """
    text = _get_text(context).lower()

    # 目标信号: 想解决什么问题
    has_goal = bool(re.search(
        r'(要|想|需要|goal|目的|目标|fix|修复|实现|添加|'
        r'add|implement|feature|改进|优化|improve|optimize)',
        text
    ))

    # 行动信号: 做了什么
    has_action = bool(re.search(
        r'(写|改|修|create|update|modify|change|write|'
        r'edit|删|删除|添加|added|removed|改成|改为)',
        text
    ))

    # 如果上下文中有工具调用记录，视为有行动
    if not has_action:
        has_action = _has_code_modification(context)

    return has_goal and has_action


def is_confirmation(text: str) -> bool:
    """检测用户是否发出了确认信号（"好了""可以"等）"""
    return _has_any(text, CONFIRM_KEYWORDS)


def is_abandonment(text: str) -> bool:
    """检测用户是否发出了放弃信号"""
    return _has_any(text, ABANDON_KEYWORDS)


def is_bug_fix(text: str) -> bool:
    """检测是否在 Bug 修复上下文中"""
    return _has_any(text, BUG_KEYWORDS)


def is_monitored_tool(tool_name: str) -> bool:
    """工具是否在监控列表中"""
    return tool_name in MONITORED_TOOLS


# ── 资产库读写 ────────────────────────────────────────────


def load_asset_index() -> dict:
    """加载资产库索引"""
    index_path = ASSET_LIBRARY_DIR / "index.json"
    if index_path.exists():
        return json.loads(index_path.read_text(encoding="utf-8"))
    return {}


def save_asset_index(index: dict):
    """保存资产库索引（带 30 秒锁超时保护）

    使用文件锁防止并发写入冲突。超过 30 秒自动清理残留锁文件。
    """
    ASSET_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    index_path = ASSET_LIBRARY_DIR / "index.json"
    lock_path = ASSET_LIBRARY_DIR / ".index.lock"

    timeout = 30.0
    start = time.time()

    # 等待锁或超时
    while lock_path.exists():
        if time.time() - start > timeout:
            # 超时 → 清理残留锁后继续（不阻塞）
            try:
                lock_path.unlink()
            except OSError:
                pass
            break
        time.sleep(0.1)

    # 创建锁文件（跨平台，非原子创建）
    try:
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
    except FileExistsError:
        pass

    try:
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def add_pending_entry(entry: dict) -> str:
    """添加一条 L2 待验证经验到资产库

    Args:
        entry: 经验字典, 至少包含 goal, action, result 字段

    Returns:
        entry_id: 新条目的 ID
    """
    index = load_asset_index()
    entry_id = _generate_id("pending")

    entry.setdefault("id", entry_id)
    entry.setdefault("verification_status", "pending")
    entry.setdefault("compiled", True)
    entry.setdefault("created_at", datetime.now().isoformat())
    entry.setdefault("verified_at", None)
    entry.setdefault("reuse_count", 0)
    entry.setdefault("tags", [])
    entry.setdefault("source_hook", "auto")

    index[entry_id] = entry
    save_asset_index(index)
    return entry_id


def upgrade_to_verified(entry_id: str) -> bool:
    """将一条 L2 待验证经验升级为 L3 已验证

    更新:
        verification_status → "verified"
        verified_at → 当前时间

    Returns:
        True 成功, False 未找到该 ID
    """
    index = load_asset_index()
    if entry_id not in index:
        return False

    entry = index[entry_id]
    entry["verification_status"] = "verified"
    entry["verified_at"] = datetime.now().isoformat()
    entry["verified_by"] = "user_confirmation"
    index[entry_id] = entry
    save_asset_index(index)

    # 同时写入 data/solutions/ 作为 Markdown 方案页
    write_solution_markdown(entry_id, entry)
    return True


def write_solution_markdown(entry_id: str, entry: dict):
    """将已验证的 L3 经验写为 Markdown 方案页

    输出到 data/solutions/{entry_id}.md，格式为人类可读的方案文档。
    """
    SOLUTIONS_DIR.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append(f"# {entry.get('goal', '未命名方案')}")
    lines.append("")
    lines.append(f"> 类型: {entry.get('type', 'modification')}  |  "
                 f"归档: {entry.get('verified_at', '')[:10]}  |  "
                 f"来源: {entry.get('source_hook', 'auto')}")
    lines.append("")

    if entry.get("root_cause"):
        lines.append("## 根因")
        lines.append("")
        lines.append(entry["root_cause"])
        lines.append("")

    lines.append("## 目标")
    lines.append("")
    lines.append(entry.get("goal", ""))
    lines.append("")

    lines.append("## 行动")
    lines.append("")
    lines.append(entry.get("action", ""))
    lines.append("")

    if entry.get("files_modified"):
        lines.append("### 修改的文件")
        for f in entry["files_modified"]:
            lines.append(f"- `{f}`")
        lines.append("")

    lines.append("## 结果")
    lines.append("")
    lines.append(entry.get("result", ""))
    lines.append("")

    if entry.get("tags"):
        lines.append("## 标签")
        lines.append("")
        lines.append(", ".join(entry["tags"]))
        lines.append("")

    path = SOLUTIONS_DIR / f"{entry_id}.md"
    path.write_text("\n".join(lines), encoding="utf-8")


def find_latest_pending() -> Optional[str]:
    """查找最近一条 L2 待验证经验的 ID

    Returns:
        entry_id 或 None
    """
    index = load_asset_index()
    pending = [
        (eid, e) for eid, e in index.items()
        if e.get("verification_status") == "pending"
    ]
    if not pending:
        return None
    pending.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    return pending[0][0]


def add_failed_attempt(entry: dict):
    """将 L1 尝试记录写入因果记忆

    写入 data/causal_memory/graph.json，类型为 failure 节点。
    """
    graph_path = CAUSAL_MEMORY_DIR / "graph.json"
    if graph_path.exists():
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    else:
        graph = {"nodes": {}, "edges": [], "version": "0.5.0"}

    node_id = f"fail_{_generate_id('fail', length=8)}"
    graph["nodes"][node_id] = {
        "id": node_id,
        "node_type": "failure",
        "label": entry.get("goal", "unknown"),
        "description": entry.get("error_summary", ""),
        "error_log": entry.get("error_log", ""),
        "tags": entry.get("tags", []),
        "created_at": datetime.now().isoformat(),
    }

    CAUSAL_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(graph, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_asset_count() -> dict:
    """获取资产库统计"""
    index = load_asset_index()
    total = len(index)
    pending = sum(1 for e in index.values()
                  if e.get("verification_status") == "pending")
    verified = sum(1 for e in index.values()
                   if e.get("verification_status") == "verified")
    return {
        "total": total,
        "pending": pending,
        "verified": verified,
    }


# ── 中断管理 ──────────────────────────────────────────────


def save_interrupt(goal: str, completed: list, blocked: str, next_steps: str):
    """保存中断摘要到 data/interrupts/

    用户说"先这样""明天继续"等暂停信号时自动保存。
    中断摘要有效期 72 小时。
    """
    INTERRUPTS_DIR.mkdir(parents=True, exist_ok=True)

    interrupt = {
        "goal": goal,
        "completed": completed,
        "blocked": blocked,
        "next_steps": next_steps,
        "status": "pending_recovery",
        "created_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(hours=72)).isoformat(),
    }

    path = INTERRUPTS_DIR / f"interrupt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(
        json.dumps(interrupt, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return path.name


def check_pending_interrupts() -> Optional[dict]:
    """检查是否有未过期的中断需要恢复

    遍历 interrupts/ 目录，返回最新的未过期中断。
    超过 72h 的自动标记为 expired 并降级为 L1。
    """
    if not INTERRUPTS_DIR.exists():
        return None

    now = datetime.now()
    latest = None
    latest_path = None

    for f in sorted(INTERRUPTS_DIR.glob("interrupt_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        expires_at = datetime.fromisoformat(data.get("expires_at", "2000-01-01"))

        if now < expires_at and data.get("status") == "pending_recovery":
            latest = data
            latest_path = f
            break
        elif now >= expires_at and data.get("status") == "pending_recovery":
            # 超时 → 标记过期
            data["status"] = "expired"
            f.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    return latest


# ── 经验提取 ──────────────────────────────────────────────


def extract_experience(context: dict) -> dict:
    """从上下文中提取经验三要素

    用关键词启发式提取 goal / action / result / tags。
    返回一个结构化的经验字典，可直接传给 add_pending_entry。
    """
    text = _get_text(context)

    goal = _extract_goal(text)
    action = _extract_action(text, context)
    result = _extract_result(text)

    tags = _extract_tags(text, context)

    entry = {
        "goal": goal,
        "action": action,
        "result": result,
        "tags": tags,
        "compiled": not _has_error_log(text),
    }

    # 如果是 Bug 修复，额外提取根因
    if is_bug_fix(text):
        entry["type"] = "fix"
        entry["root_cause"] = _extract_root_cause(text)
    elif _is_decision(text):
        entry["type"] = "decision"
    else:
        entry["type"] = "modification"

    # 记录工具列表
    if isinstance(context, dict):
        tools = context.get("tool_calls", [])
        entry["tools_used"] = [t.get("name", "?") for t in tools if isinstance(t, dict)]

    return entry


# ── 内部工具 ──────────────────────────────────────────────


def _get_text(context) -> str:
    """从各种可能的上下文格式中提取文本"""
    if isinstance(context, str):
        return context
    if isinstance(context, dict):
        for key in ("text", "conversation", "message", "input", "content"):
            if key in context and isinstance(context[key], str):
                return context[key]
        return str(context)
    return str(context)


def _has_any(text: str, keywords: list) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _has_error_log(text: str) -> bool:
    patterns = [
        r"error",
        r"exception",
        r"traceback",
        r"failed",
        r"失败",
        r"编译错误",
        r"exit code [1-9]",
        r"Error ",
        r"Traceback",
    ]
    return any(re.search(p, text) for p in patterns)


def _has_code_modification(context) -> bool:
    if not isinstance(context, dict):
        return False
    actions = context.get("tool_calls", []) if isinstance(context, dict) else []
    if isinstance(actions, list):
        write_tools = {"write_file", "edit_file", "multi_edit"}
        return any(
            isinstance(a, dict) and a.get("name") in write_tools
            for a in actions
        )
    return False


def _generate_id(prefix: str = "exp", length: int = 12) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:length]}"


def _extract_goal(text: str) -> str:
    """提取目标描述"""
    # 尝试从"要/需要/想..."句型中提取
    patterns = [
        r'(?:要|需要|想|打算)(.{5,60}?)(?:[，。；]|$)',
        r'(?:goal|purpose|目的|目标)[：:]\s*(.{5,60})',
        r'(?:fix|修复|实现|添加)(.{5,60}?)(?:[，。；]|$)',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()[:80]
    return "（未自动提取）"


def _extract_action(text: str, context) -> str:
    """提取行动描述"""
    # 优先从工具调用中提取
    if isinstance(context, dict):
        calls = context.get("tool_calls", [])
        if calls and isinstance(calls, list):
            actions = []
            for c in calls[:3]:
                if isinstance(c, dict):
                    name = c.get("name", "?")
                    args = c.get("args", {})
                    if isinstance(args, dict) and "path" in args:
                        actions.append(f"{name}: {args['path']}")
                    else:
                        actions.append(name)
            if actions:
                return "; ".join(actions)

    # 从文本中提取
    patterns = [
        r'(?:修改了|创建了|删除了|更新了|写了|改了)(.{5,80}?)(?:[，。；]|$)',
        r'(?:write|edit|update|create|modify|add|remove)(?:d)?\s+(.{5,80}?)(?:[，。；\n]|$)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:80]
    return "（未自动提取）"


def _extract_result(text: str) -> str:
    """提取结果描述"""
    patterns = [
        r'(?:好了|完成了|搞定|成功了|通过了|done|finished|passed|succeeded)',
        r'(?:测试|编译|构建)(?:通过|成功|passed|succeeded)',
        r'(?:修好了|fixed|resolved|closed)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)[:80]
    return "（未自动提取）"


def _extract_root_cause(text: str) -> str:
    """提取 Bug 根因"""
    patterns = [
        r'(?:原因是|根因|root cause|原因在于|因为)(.{10,100}?)(?:[。；]|$)',
        r'(?:was|were)\s+(?:caused by|due to)\s+(.{10,100}?)(?:[。；\n]|$)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:120]
    return ""


def _extract_tags(text: str, context) -> list:
    """提取标签"""
    tags = []

    # 技术栈关键词
    tech_map = {
        "python": "Python", "java": "Java", "go": "Go",
        "javascript": "JavaScript", "typescript": "TypeScript",
        "rust": "Rust", "react": "React", "vue": "Vue",
        "docker": "Docker", "redis": "Redis", "postgresql": "PostgreSQL",
        "mysql": "MySQL", "sqlite": "SQLite", "mongodb": "MongoDB",
        "kubernetes": "K8s", "aws": "AWS",
    }
    text_lower = text.lower()
    for kw, tag in tech_map.items():
        if kw in text_lower:
            tags.append(f"#{tag}")

    # 领域关键词
    domain_map = {
        "bug": "#Bug修复", "fix": "#Bug修复",
        "feature": "#功能", "添加": "#功能",
        "refactor": "#重构", "重构": "#重构",
        "test": "#测试", "测试": "#测试",
        "api": "#API", "ui": "#UI",
        "config": "#配置", "配置": "#配置",
    }
    for kw, tag in domain_map.items():
        if kw in text_lower:
            if tag not in tags:
                tags.append(tag)

    return tags[:5]


def _is_decision(text: str) -> bool:
    """检测是否为技术决策"""
    patterns = [
        r'(?:决定|就用|确定用|选|选择|采用|使用)\s*(.{5,40}?)',
        r'(?:decided|chose|selected|using)\s*(.{5,40}?)',
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)
