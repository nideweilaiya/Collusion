#!/usr/bin/env python3
"""Collusion 子 Agent 进程 — 支持多阶段模式

用法:
  python child_agent.py --task-id bb_xxx --role ux --mode proposal  [Phase 3: 生成方案]
  python child_agent.py --task-id bb_xxx --role ux --mode review    [Phase 4: 交叉审查]
  python child_agent.py --task-id bb_xxx --role ux --mode brake     [Phase 4.5: 可行性收束]
  python child_agent.py --task-id bb_xxx --role ux --mode integrate [Phase 4.6: Owner整合]
  python child_agent.py --task-id bb_xxx --role ux --mode vote      [Phase 6: 投票评分]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.llm.deepseek import DeepSeekAdapter

ROLE_INFO = {
    "ux": {"name": "UX/产品专家", "object": "业务价值对象",
           "focus": "用户能否用起来？操作是否流畅？部署门槛低不低？"},
    "architecture": {"name": "性能架构师", "object": "技术架构对象",
                     "focus": "技术选型是否合理？性能瓶颈在哪？扩展性够不够？"},
    "security": {"name": "安全专家", "object": "安全与合规对象",
                 "focus": "数据安全、认证授权、威胁建模、合规要求"},
}


def update_status(agent_dir: Path, **kwargs):
    current = {}
    sp = agent_dir / "status.json"
    if sp.exists():
        try:
            current = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            pass
    current.update(kwargs)
    current["updated_at"] = datetime.now().isoformat()
    tmp = sp.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(sp)


def atomic_write(path: Path, content: str):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def read_other_proposals(agent_dir: Path, my_role: str) -> str:
    """读取其他 Agent 的方案"""
    others = []
    for role in ["ux", "architecture", "security"]:
        if role == my_role:
            continue
        p = agent_dir.parent / role / "proposal.md"
        if p.exists():
            others.append(f"=== 方案 {role} ===\n{p.read_text(encoding='utf-8')[:2500]}")
    return "\n\n".join(others)


def read_all_proposals(agent_dir: Path) -> str:
    """读取所有方案"""
    parts = []
    for role in ["ux", "architecture", "security"]:
        p = agent_dir.parent / role / "proposal.md"
        if p.exists():
            parts.append(f"=== 方案 {role} ===\n{p.read_text(encoding='utf-8')[:2000]}")
    return "\n\n".join(parts)


# ==================== 各阶段入口 ====================

def run_proposal(adapter, task_data: dict, agent_dir: Path, my_role: str):
    """Phase 3: 生成方案"""
    info = ROLE_INFO[my_role]
    steps = task_data.get("steps", [])
    steps_text = "\n".join(
        f"环节{i+1}: {s.get('name','')} — {s.get('description','')}"
        for i, s in enumerate(steps)
    ) if steps else "（由 Agent 自行判断）"

    prompt = (
        f"你是{info['name']}，代言{info['object']}。核心关注点：{info['focus']}\n\n"
        f"任务: {task_data.get('description','')}\n\n"
        f"环节清单:\n{steps_text}\n\n"
        f"请输出一份完整技术方案。每个环节都要有具体设计（技术选型、关键决策、代码示例）。"
        f"不要泛泛而谈，给出可落地的细节。Markdown 格式。"
    )
    proposal = adapter.cached_call(prompt, temperature=0.1, max_tokens=4096)
    atomic_write(agent_dir / "proposal.md", proposal)
    update_status(agent_dir, phase="proposal_done", proposal_length=len(proposal))


def run_review(adapter, task_data: dict, agent_dir: Path, my_role: str):
    """Phase 4: 交叉审查 — 审查其他两个方案"""
    info = ROLE_INFO[my_role]
    others = read_other_proposals(agent_dir, my_role)
    if not others.strip():
        update_status(agent_dir, phase="review_skipped", reason="no other proposals")
        return

    prompt = (
        f"你是{info['name']}，从{info['object']}视角审查以下两份方案。"
        f"对每份方案输出 1-2 条具体修改建议（enhancement/issue_flag/simplification），"
        f"附理由。JSON格式:\n"
        f'{{"reviews":[{{"target":"ux|architecture|security","type":"enhancement|issue_flag|simplification",'
        f'"content":"修改建议","reason":"理由"}}]}}\n\n'
        f"任务: {task_data.get('description','')}\n\n"
        f"其他方案:\n{others}"
    )
    try:
        data = adapter.cached_call_json(prompt, temperature=0.1, max_tokens=1536)
        atomic_write(agent_dir / "review.json",
                     json.dumps(data, ensure_ascii=False, indent=2))
        update_status(agent_dir, phase="review_done",
                      reviews=len(data.get("reviews", [])))
    except Exception as e:
        update_status(agent_dir, phase="review_error", error=str(e))


def run_brake(adapter, task_data: dict, agent_dir: Path, my_role: str):
    """Phase 4.5: 可行性收束 — 工程视角检查过度设计"""
    all_proposals = read_all_proposals(agent_dir)
    if not all_proposals.strip():
        update_status(agent_dir, phase="brake_skipped")
        return

    prompt = (
        f"你是工程实现专家。审查以下方案，检查是否存在过度设计。"
        f"对每个方案标注:\n"
        f"- 是否有不必要的微服务拆分？\n"
        f"- 是否有过度引入复杂中间件？\n"
        f"- 能否进一步简化？简化成什么？\n"
        f"- MVP 需要哪些环节？\n\n"
        f"输出 JSON:\n"
        f'{{"brakes":[{{"target":"ux|architecture|security","feasible":true|false,'
        f'"complexity":"low|medium|high","simplifications":["建议1","建议2"],'
        f'"mvp_steps":["环节1","环节2"]}}]}}\n\n'
        f"任务: {task_data.get('description','')}\n\n"
        f"方案:\n{all_proposals}"
    )
    try:
        data = adapter.cached_call_json(prompt, temperature=0.1, max_tokens=1536)
        atomic_write(agent_dir / "brake.json",
                     json.dumps(data, ensure_ascii=False, indent=2))
        update_status(agent_dir, phase="brake_done")
    except Exception as e:
        update_status(agent_dir, phase="brake_error", error=str(e))


def run_integrate(adapter, task_data: dict, agent_dir: Path, my_role: str):
    """Phase 4.6: Owner 整合 — 融合审查意见，生成最终版"""
    info = ROLE_INFO[my_role]
    my_proposal_path = agent_dir / "proposal.md"
    if not my_proposal_path.exists():
        update_status(agent_dir, phase="integrate_skipped")
        return
    my_proposal = my_proposal_path.read_text(encoding="utf-8")

    # 收集其他 Agent 的审查意见
    reviews = []
    for role in ["ux", "architecture", "security"]:
        rp = agent_dir.parent / role / "review.json"
        if rp.exists():
            data = json.loads(rp.read_text(encoding="utf-8"))
            for r in data.get("reviews", []):
                if r.get("target") == my_role:
                    reviews.append(f"[{role}] {r.get('type')}: {r.get('content')} — {r.get('reason')}")

    # 收集收束建议
    simplifications = []
    for role in ["ux", "architecture", "security"]:
        bp = agent_dir.parent / role / "brake.json"
        if bp.exists():
            data = json.loads(bp.read_text(encoding="utf-8"))
            for b in data.get("brakes", []):
                if b.get("target") == my_role:
                    simplifications.extend(b.get("simplifications", []))

    reviews_text = "\n".join(f"- {r}" for r in reviews) if reviews else "无"
    simps_text = "\n".join(f"- {s}" for s in simplifications) if simplifications else "无"

    prompt = (
        f"你是{info['name']}。你的原始方案收到了以下审查意见和简化建议。\n\n"
        f"审查意见:\n{reviews_text}\n\n"
        f"简化建议:\n{simps_text}\n\n"
        f"请综合所有意见，输出一份整合后的最终方案。保持你原有视角的特色，"
        f"同时融合合理的修改建议。Markdown 格式，控制在 3000 字以内。\n\n"
        f"原始方案:\n{my_proposal[:3000]}"
    )
    final = adapter.cached_call(prompt, temperature=0.1, max_tokens=4096)
    atomic_write(agent_dir / "proposal_final.md", final)
    update_status(agent_dir, phase="integrate_done", final_length=len(final))


def run_vote(adapter, task_data: dict, agent_dir: Path, my_role: str):
    """Phase 6: 投票评分 — 5 维打分"""
    all_proposals = []
    for role in ["ux", "architecture", "security"]:
        # 优先读最终版，否则读原始版
        fp = agent_dir.parent / role / "proposal_final.md"
        pp = agent_dir.parent / role / "proposal.md"
        path = fp if fp.exists() else pp
        if path.exists():
            all_proposals.append(f"=== 方案 {role} ===\n{path.read_text(encoding='utf-8')[:2000]}")

    if len(all_proposals) < 2:
        update_status(agent_dir, phase="vote_skipped", reason="need 2+ proposals")
        return

    proposals_text = "\n\n".join(all_proposals)
    prompt = (
        f"你是技术评审专家。对以下方案进行 5 维评分（每维 1-10），输出 JSON:\n"
        f'{{"votes":[{{"target":"ux|architecture|security","correctness":8.5,"completeness":8.0,'
        f'"feasibility":9.0,"innovation":7.0,"business_alignment":8.5,"comment":"一句话评语"}}],'
        f'"ranked":["architecture","ux","security"]}}\n\n'
        f"任务: {task_data.get('description','')}\n\n"
        f"方案:\n{proposals_text}"
    )
    try:
        data = adapter.cached_call_json(prompt, temperature=0.1, max_tokens=1536)
        atomic_write(agent_dir / "vote.json",
                     json.dumps(data, ensure_ascii=False, indent=2))
        update_status(agent_dir, phase="vote_done")
    except Exception as e:
        update_status(agent_dir, phase="vote_error", error=str(e))


# ==================== CLI ====================
MODES = {
    "proposal": run_proposal,
    "review": run_review,
    "brake": run_brake,
    "integrate": run_integrate,
    "vote": run_vote,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--mode", required=True, choices=list(MODES.keys()))
    parser.add_argument("--blackboard", required=True)

    args = parser.parse_args()

    agent_dir = Path(args.blackboard) / args.task_id / "agents" / args.role
    task_path = Path(args.blackboard) / args.task_id / "task.json"

    if not task_path.exists():
        update_status(agent_dir, phase="error", error="task.json not found")
        sys.exit(1)

    task_data = json.loads(task_path.read_text(encoding="utf-8"))
    update_status(agent_dir, phase=f"{args.mode}_start")

    try:
        adapter = DeepSeekAdapter(model="deepseek-chat")
    except ValueError as e:
        update_status(agent_dir, phase="error", error=str(e))
        sys.exit(1)

    handler = MODES[args.mode]
    handler(adapter, task_data, agent_dir, args.role)
