"""Brainstorm Orchestrator v3.0 — CLI 入口"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.orchestrator import BrainstormOrchestrator


def cmd_orchestrate(args):
    """执行编排任务"""
    orch = BrainstormOrchestrator()
    print(f"[启动] {args.task}")
    print(f"  Agent数量: {args.agents}")
    print(f"  修改轮数: {args.rounds}")
    print()

    orch.num_agents = args.agents
    orch.max_modification_rounds = args.rounds

    task_id = orch.orchestrate(task=args.task)
    print(f"[任务ID] {task_id}")

    result = orch.get_result(task_id)
    if result is None:
        print("[错误] 编排失败")
        return

    print(f"  阶段: {result['phase']}")
    print(f"  成本: Y{result['total_cost_rmb']:.4f}")
    print()

    if result.get("error"):
        print(f"[错误] {result['error']}")
        return

    if result["top3"]:
        print("=" * 60)
        print("Top 3 方案")
        print("=" * 60)
        for i, r in enumerate(result["top3"]):
            print(f"\n#{i + 1}  方案 {r.get('plan_id', '?')}")
            print(f"    正确性: {r.get('correctness', 0)}  完整性: {r.get('completeness', 0)}")
            print(f"    可行性: {r.get('feasibility', 0)}  创新性: {r.get('innovation', 0)}")
            print(f"    业务对齐: {r.get('business_alignment', 0)}")
            print(f"    总分: {r.get('total_score', 0)}")
            if r.get("comment"):
                print(f"    点评: {r['comment']}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n[输出] {output_path}")


def cmd_status(args):
    """查询任务状态"""
    orch = BrainstormOrchestrator()
    state = orch.get_state(args.task_id)
    if state is None:
        print(f"[错误] 任务不存在: {args.task_id}")
        return

    print(f"[任务] {state.get('original_task', '?')}")
    print(f"  阶段: {state.get('phase', '?')}")
    print(f"  轮次: {state.get('current_round', 0)}/{state.get('max_rounds', 0)}")
    print(f"  方案数: {len(state.get('schemes', {}))}")
    print(f"  环节数: {len(state.get('step_list', []))}")
    print(f"  成本: Y{state.get('total_cost_rmb', 0):.4f}")
    if state.get("error_message"):
        print(f"  [错误] {state['error_message']}")


def cmd_result(args):
    """获取任务结果"""
    orch = BrainstormOrchestrator()
    result = orch.get_result(args.task_id)
    if result is None:
        print(f"[错误] 任务不存在: {args.task_id}")
        return

    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Brainstorm Orchestrator v3.0 - 多Agent协作技术方案生成",
    )
    sub = parser.add_subparsers(dest="command")

    p_orch = sub.add_parser("orchestrate", help="启动方案编排")
    p_orch.add_argument("--task", "-t", required=True, help="任务描述")
    p_orch.add_argument("--agents", "-a", type=int, default=3, help="Agent数量 (默认3)")
    p_orch.add_argument("--rounds", "-r", type=int, default=1, help="修改轮数 (默认1)")
    p_orch.add_argument("--output", "-o", help="输出JSON文件路径")

    p_status = sub.add_parser("status", help="查询任务状态")
    p_status.add_argument("task_id", help="任务ID")

    p_result = sub.add_parser("result", help="获取任务结果")
    p_result.add_argument("task_id", help="任务ID")

    args = parser.parse_args()
    if args.command == "orchestrate":
        cmd_orchestrate(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "result":
        cmd_result(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
