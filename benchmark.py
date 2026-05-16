"""Brainstorm Orchestrator v3.0 — 基准线对比测试 (成对比较版)

对照组A: 单次 LLM 调用生成方案
实验组B: Brainstorm Orchestrator (3 Agent × 6阶段)
评委: 盲评成对比较，5维度逐项判断谁更优
"""
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.llm.deepseek import DeepSeekAdapter
from src.orchestrator import BrainstormOrchestrator

# ============================================================
# 测试任务池（5个不同领域）
# ============================================================
TASKS = [
    {
        "id": "task_1",
        "domain": "Web后端",
        "task": "设计一个高并发短链接服务，支持每秒10万+短链生成、自定义短码、过期清理、访问统计",
    },
    {
        "id": "task_2",
        "domain": "移动端",
        "task": "设计一个iOS/Android跨平台即时通讯系统，支持文本/图片/语音消息、已读回执、离线推送",
    },
    {
        "id": "task_3",
        "domain": "数据工程",
        "task": "设计一个实时数据管道，从Kafka消费行为日志经流处理清洗聚合后写入ClickHouse，支持每天100亿条",
    },
    {
        "id": "task_4",
        "domain": "安全",
        "task": "设计一个OAuth2.0+OIDC统一认证网关，支持SSO单点登录、RBAC权限管理、多租户隔离",
    },
    {
        "id": "task_5",
        "domain": "AI/ML",
        "task": "设计一个在线模型推理服务平台，支持A/B测试、模型版本管理、GPU资源调度、推理延迟<50ms",
    },
]

# ============================================================
# 成对比较评分提示词
# ============================================================
PAIRWISE_JUDGE = """你是一个独立技术评委。下面有两个技术方案（方案1和方案2），请从以下5个维度逐项判断哪个方案更优。

评分规则：
- 如果方案1在该维度明显更好，输出 "1"
- 如果方案2在该维度明显更好，输出 "2"
- 如果两者相当，输出 "="

输出格式（严格JSON）：
{{
  "正确性": "1或2或=",
  "完整性": "1或2或=",
  "可行性": "1或2或=",
  "创新性": "1或2或=",
  "业务对齐": "1或2或=",
  "总体评价": "一句话说明哪个方案更好及理由(50字以内)"
}}"""


def baseline_generate(task_desc: str, adapter: DeepSeekAdapter) -> str:
    """对照组A: 单次 LLM 调用生成方案"""
    system = """你是一个资深技术架构师。请为以下任务设计一个完整的技术方案。
覆盖：接口设计、数据模型、核心流程、安全策略、性能优化、部署方案。
直接输出方案，不需要JSON格式。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task_desc},
    ]
    return adapter.chat(messages, temperature=0.3, max_tokens=8192)


def orchestrator_generate(task_desc: str, orch: BrainstormOrchestrator) -> dict:
    """实验组B: Brainstorm Orchestrator 生成方案"""
    task_id = orch.orchestrate(task=task_desc)
    return orch.get_result(task_id)


def format_orchestrator_plan(result: dict) -> str:
    """从编排器结果中提取Top1方案的完整设计内容

    v3.1: 优先使用 Owner 整合后的 integrated_content
    v3.0: 从 steps dict 逐环节提取
    """
    schemes = result.get("schemes", {})

    # 找 Top1 方案
    top1_id = None
    if result.get("top3"):
        top1_id = result["top3"][0].get("plan_id", "")

    if top1_id and len(top1_id) > 1:
        import re as _re
        m = _re.search(r'[A-C]', top1_id)
        if m:
            top1_id = m.group(0)

    if not top1_id or top1_id not in schemes:
        if schemes:
            top1_id = list(schemes.keys())[0]
        else:
            return "(方案内容为空)"

    scheme = schemes[top1_id]

    # v3.1: 优先使用 Owner 整合后的完整文档
    integrated = scheme.get("integrated_content", "")
    if integrated and len(integrated) > 100:
        return integrated

    # v3.0 fallback: 逐环节拼凑
    lines = []
    steps = result.get("step_list", [])
    lines.append(f"方案来源视角: {scheme.get('agent_role', '未知')}")
    lines.append("")

    for step in steps:
        idx = step.get("index", "?")
        name = step.get("name", "?")
        step_id = step.get("id", "")
        lines.append(f"## {idx}. {name}")
        lines.append(f"需求描述: {step.get('description', '')}")
        design = scheme.get("steps", {}).get(step_id, "")
        if design:
            lines.append(f"设计方案: {design}")
        else:
            lines.append("(该环节暂无详细设计)")
        lines.append("")

    history = scheme.get("modification_history", [])
    if history:
        lines.append("## 交叉审查修改记录")
        for mod in history:
            lines.append(f"- [{mod.get('agent_role', '')}] {mod.get('reason', '')}: {mod.get('content', '')[:150]}")

    return "\n".join(lines)


def pairwise_compare(task_desc: str, plan_a: str, plan_b: str,
                     adapter: DeepSeekAdapter) -> dict:
    """成对比较：随机打乱AB顺序（盲评），评委逐维度判断谁更优"""
    import random

    # 随机决定方案1/方案2的映射（消除位置偏差）
    if random.random() < 0.5:
        plan_1, plan_2 = plan_a, plan_b
        label_1, label_2 = "A", "B"
    else:
        plan_1, plan_2 = plan_b, plan_a
        label_1, label_2 = "B", "A"

    user_msg = f"""任务：{task_desc}

方案1：
{plan_1}

方案2：
{plan_2}"""

    messages = [
        {"role": "system", "content": PAIRWISE_JUDGE},
        {"role": "user", "content": user_msg},
    ]
    try:
        data = adapter.chat_with_json(messages, temperature=0.1, max_tokens=2048)
    except Exception as e:
        print(f"  [警告] 评委评分失败: {e}")
        return {"error": str(e), "label_1": label_1, "label_2": label_2}

    # 解析结果，还原到A/B
    result = {"A_wins": 0, "B_wins": 0, "ties": 0, "details": {}, "comment": ""}
    for dim in ["正确性", "完整性", "可行性", "创新性", "业务对齐"]:
        raw = data.get(dim, "=")
        # 根据映射还原
        if raw == "1":
            winner = label_1
        elif raw == "2":
            winner = label_2
        else:
            winner = "="

        result["details"][dim] = winner
        if winner == "A":
            result["A_wins"] += 1
        elif winner == "B":
            result["B_wins"] += 1
        else:
            result["ties"] += 1

    result["comment"] = data.get("总体评价", "")
    return result


def run_benchmark():
    print("=" * 60)
    print("Brainstorm Orchestrator v3.0 - 基准线对比测试 (成对比较)")
    print("=" * 60)
    print(f"测试任务数: {len(TASKS)}")
    print("对照组A: 单次 LLM 调用")
    print("实验组B: Brainstorm Orchestrator (3 Agent x 6阶段)")
    print("评分方式: 盲评成对比较 (5维度逐项判断)")
    print()

    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    llm_cfg = config["llm"]["strong"]
    api_key = llm_cfg.get("api_key", "")
    adapter = DeepSeekAdapter(api_key=api_key, model=llm_cfg.get("model", "deepseek-chat"))
    orch = BrainstormOrchestrator()

    results = []
    total_a_wins = 0
    total_b_wins = 0
    total_ties = 0

    for i, task_info in enumerate(TASKS):
        task_desc = task_info["task"]
        domain = task_info["domain"]
        print(f"[{i + 1}/{len(TASKS)}] {domain}: {task_desc[:60]}...")

        # A组: 单次LLM
        print("  A组 (单次LLM)...", end=" ", flush=True)
        t0 = time.time()
        plan_a = baseline_generate(task_desc, adapter)
        time_a = time.time() - t0
        print(f"done ({time_a:.0f}s, {len(plan_a)}字)")

        # B组: 编排器
        print("  B组 (编排器)...", end=" ", flush=True)
        t0 = time.time()
        result_b = orchestrator_generate(task_desc, orch)
        time_b = time.time() - t0
        plan_b = format_orchestrator_plan(result_b)
        print(f"done ({time_b:.0f}s, {len(plan_b)}字, Y{result_b.get('total_cost_rmb', 0):.4f})")

        # 成对比较
        print("  评委盲评...", end=" ", flush=True)
        comparison = pairwise_compare(task_desc, plan_a, plan_b, adapter)
        if "error" in comparison:
            print(f"失败: {comparison['error']}")
            continue

        a_w = comparison["A_wins"]
        b_w = comparison["B_wins"]
        t = comparison["ties"]
        total_a_wins += a_w
        total_b_wins += b_w
        total_ties += t
        print(f"A赢{a_w}维 B赢{b_w}维 平{t}维 | {comparison['comment'][:40]}")

        results.append({
            "task_id": task_info["id"],
            "domain": domain,
            "task": task_desc,
            "A_chars": len(plan_a),
            "A_time": round(time_a, 1),
            "B_chars": len(plan_b),
            "B_time": round(time_b, 1),
            "B_cost": result_b.get("total_cost_rmb", 0),
            "comparison": comparison,
        })
        print()

    # ============================================================
    # 汇总报告
    # ============================================================
    print()
    print("=" * 60)
    print("成对比较结果汇总")
    print("=" * 60)

    total_dims = total_a_wins + total_b_wins + total_ties  # 5 dims × 5 tasks = 25
    print(f"\n总维度数: {total_dims} (5任务 × 5维度)")
    print(f"A组 (单次LLM) 获胜维度: {total_a_wins} ({total_a_wins / total_dims * 100:.0f}%)")
    print(f"B组 (编排器)   获胜维度: {total_b_wins} ({total_b_wins / total_dims * 100:.0f}%)")
    print(f"平局: {total_ties} ({total_ties / total_dims * 100:.0f}%)")

    print(f"\n{'任务':<25} {'领域':<10} {'A赢':>5} {'B赢':>5} {'平':>5} {'判决':>10}")
    print("-" * 65)
    for r in results:
        comp = r["comparison"]
        aw = comp["A_wins"]
        bw = comp["B_wins"]
        t = comp["ties"]
        if bw > aw:
            verdict = "B胜"
        elif aw > bw:
            verdict = "A胜"
        else:
            verdict = "平局"
        task_short = r["task"][:23]
        print(f"{task_short:<25} {r['domain']:<10} {aw:>5} {bw:>5} {t:>5} {verdict:>10}")

    print("-" * 65)
    print(f"{'合计':<25} {'':10} {total_a_wins:>5} {total_b_wins:>5} {total_ties:>5}")

    # 按维度统计
    print(f"\n{'维度':<12} {'A胜':>5} {'B胜':>5} {'平':>5}")
    print("-" * 30)
    dim_stats = {"正确性": [0, 0, 0], "完整性": [0, 0, 0], "可行性": [0, 0, 0],
                 "创新性": [0, 0, 0], "业务对齐": [0, 0, 0]}
    for r in results:
        for dim, winner in r["comparison"]["details"].items():
            if winner == "A":
                dim_stats[dim][0] += 1
            elif winner == "B":
                dim_stats[dim][1] += 1
            else:
                dim_stats[dim][2] += 1
    for dim, (a, b, t) in dim_stats.items():
        print(f"{dim:<12} {a:>5} {b:>5} {t:>5}")

    total_cost = sum(r["B_cost"] for r in results)
    total_time = sum(r["B_time"] for r in results)
    print(f"\nB组总成本: Y{total_cost:.4f}")
    print(f"B组总耗时: {total_time:.0f}s")

    print()
    if total_b_wins > total_a_wins:
        pct = total_b_wins / total_dims * 100
        print(f">>> 结论: Brainstorm Orchestrator 在 {pct:.0f}% 的维度上优于单次LLM，验证有效")
    elif total_a_wins > total_b_wins:
        pct = total_a_wins / total_dims * 100
        print(f">>> 结论: 单次LLM在 {pct:.0f}% 维度上更优，编排器需要优化")
    else:
        print(">>> 结论: 两者持平，需要更多测试任务验证")

    # 保存
    output = {
        "benchmark_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "num_tasks": len(TASKS),
        "method": "pairwise_comparison",
        "summary": {
            "A_wins": total_a_wins,
            "B_wins": total_b_wins,
            "ties": total_ties,
            "total_dims": total_dims,
            "B_total_cost": round(total_cost, 4),
            "B_total_time": round(total_time, 0),
        },
        "dim_stats": {k: {"A": v[0], "B": v[1], "=": v[2]} for k, v in dim_stats.items()},
        "details": results,
    }
    output_path = Path("benchmark_result.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存: {output_path}")

    return results


if __name__ == "__main__":
    run_benchmark()
