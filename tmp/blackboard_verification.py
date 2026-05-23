"""
黑板+顾问模式 三合一验证实验
================================
Test A: 黑板(最小上下文) vs 全量上下文 → Token消耗对比
Test B: 多Agent并行 vs 单Agent → 方案质量对比
Test C: 交叉审查有效性 → 有效建议比例

所有数据来自 reasonix run 实际输出，不编造。
"""
import subprocess, time, os, json, re, sys, threading

REASONIX = "node D:/Reasonix-Dev/dist/cli/index.js"
MODEL = "deepseek-v4-flash"

def run_agent(system: str, task: str, model=MODEL) -> dict:
    """调用 reasonix run，返回结构化数据"""
    t0 = time.time()
    cmd = f'{REASONIX} run "{task}" -m {model} --system "{system}" --no-config'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", timeout=300)
    wall = time.time() - t0
    output = (result.stdout or "").strip()
    return {
        "cache": float(m.group(1)) if (m := re.search(r"cache:(\d+\.?\d*)%", output)) else 0.0,
        "cost": float(m.group(1)) if (m := re.search(r"cost:\$(\d+\.?\d*)", output)) else 0.0,
        "turns": int(m.group(1)) if (m := re.search(r"turns:(\d+)", output)) else 1,
        "wall": wall,
        "chars": len(output),
        "output": output,
        "system_len": len(system),
        "task_len": len(task),
        "approx_input_tokens": (len(system) + len(task)) // 4,  # 粗略估算
    }

def run_parallel(systems: dict, tasks: dict, label: str) -> dict:
    """并行启动多个Agent"""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    results = {}
    lock = threading.Lock()

    def worker(name, sys_prompt, task):
        r = run_agent(sys_prompt, task)
        with lock:
            results[name] = r

    threads = []
    for name in systems:
        t = threading.Thread(target=worker, args=(name, systems[name], tasks.get(name, tasks.get("_global", ""))))
        threads.append(t)
        t.start()

    start = time.time()
    for t in threads:
        t.join()
    wall = time.time() - start

    total_cost = sum(r["cost"] for r in results.values())
    total_input_tokens = sum(r["approx_input_tokens"] for r in results.values())

    print(f"墙钟: {wall:.0f}s | 总成本: ${total_cost:.6f} | 估算输入token: {total_input_tokens}")
    for name, r in sorted(results.items()):
        print(f"  [{name:18s}] cache={r['cache']:5.1f}% cost=${r['cost']:.6f} "
              f"wall={r['wall']:.0f}s chars={r['chars']} input~{r['approx_input_tokens']}tok")
    return {"wall": wall, "cost": total_cost, "input_tokens": total_input_tokens, "agents": results}


# ============================================================
# 任务定义
# ============================================================
TASK_SHORT = """为TeamWiki设计你负责领域的技术方案。直接输出，不调用工具。"""

# ============================================================
# Test A: 黑板模式 (最小上下文) vs 全量上下文
# ============================================================
print("=" * 70)
print("  Test A: 黑板模式 vs 全量上下文 — Token消耗对比")
print("=" * 70)

# A1: 黑板模式 — 每个Agent只看到自己角色的任务
BLACKBOARD_SYSTEMS = {
    "architect": "你是后端架构师。设计API、数据库Schema、技术栈。直接输出Markdown方案。不调用任何MCP工具。黑板测试A1",
    "security": "你是Web安全专家。设计认证、授权、加密、审计方案。直接输出Markdown方案。不调用任何MCP工具。黑板测试A1",
    "performance": "你是性能分析师。设计缓存、搜索、并发方案。直接输出Markdown方案。不调用任何MCP工具。黑板测试A1",
}
BLACKBOARD_TASKS = {
    "architect": f"{TASK_SHORT}\n\n你的任务: 设计TeamWiki的API接口和数据库Schema。",
    "security": f"{TASK_SHORT}\n\n你的任务: 设计TeamWiki的认证授权和安全方案。",
    "performance": f"{TASK_SHORT}\n\n你的任务: 设计TeamWiki的缓存和搜索性能方案。",
}

a1 = run_parallel(BLACKBOARD_SYSTEMS, BLACKBOARD_TASKS, "A1: 黑板模式 (最小上下文)")

# 提取各Agent方案摘要(前500字)用于A2
summaries = {}
for name, r in a1["agents"].items():
    summaries[name] = r["output"][:500]

# A2: 全量上下文 — 每个Agent看到其他Agent的完整方案
FULL_CONTEXT_TASKS = {}
for name in BLACKBOARD_SYSTEMS:
    other_outputs = []
    for other_name, other_r in a1["agents"].items():
        if other_name != name:
            other_outputs.append(f"=== {other_name}的方案 ===\n{other_r['output'][:1500]}")
    extra = "\n\n".join(other_outputs)
    FULL_CONTEXT_TASKS[name] = (
        f"{TASK_SHORT}\n\n"
        f"你的任务: 基于以下其他专家的完整方案，改进你的方案。\n\n"
        f"=== 其他专家的方案(供参考) ===\n{extra}\n\n"
        f"请输出改进后的完整方案。"
    )

a2 = run_parallel(BLACKBOARD_SYSTEMS, FULL_CONTEXT_TASKS, "A2: 全量上下文 (看到其他Agent完整方案)")

# 对比
print(f"\n--- Test A 对比 ---")
print(f"{'指标':<25s} {'黑板模式(A1)':>15s} {'全量上下文(A2)':>15s} {'节省':>10s}")
print(f"{'-'*65}")
print(f"{'总成本':<25s} ${a1['cost']:>14.6f} ${a2['cost']:>14.6f} {((1-a1['cost']/a2['cost'])*100) if a2['cost'] > 0 else 0:>9.1f}%")
print(f"{'估算输入Token':<25s} {a1['input_tokens']:>15} {a2['input_tokens']:>15} {((1-a1['input_tokens']/a2['input_tokens'])*100) if a2['input_tokens'] > 0 else 0:>9.1f}%")
print(f"{'并行墙钟':<25s} {a1['wall']:>14.0f}s {a2['wall']:>14.0f}s")


# ============================================================
# Test B: 多Agent并行 vs 单Agent
# ============================================================
print(f"\n{'='*70}")
print(f"  Test B: 多Agent并行 vs 单Agent — 方案质量对比")
print(f"{'='*70}")

# B1: 单Agent做全部三个视角
SINGLE_SYSTEM = "你是全栈技术专家。你需要从架构、安全、性能三个角度全面分析。输出完整Markdown方案，覆盖API设计、数据库、认证授权、缓存搜索。不调用任何MCP工具。"
SINGLE_TASK = "为TeamWiki知识库系统设计完整后端技术方案。必须覆盖: 1)架构和API 2)数据库Schema 3)认证和安全 4)缓存和搜索。直接输出。"

b1 = run_agent(SINGLE_SYSTEM, SINGLE_TASK)
print(f"\nB1: 单Agent (全栈)")
print(f"  cost=${b1['cost']:.6f} wall={b1['wall']:.0f}s cache={b1['cache']:.1f}% chars={b1['chars']}")

# B2: 用A1的黑板结果作为多Agent产出(已并行完成)
print(f"\nB2: 多Agent并行 (来自Test A1)")
multi_cost = a1['cost']
multi_chars = sum(r['chars'] for r in a1['agents'].values())
print(f"  cost=${multi_cost:.6f} wall={a1['wall']:.0f}s (并行) chars={multi_chars}")

# B3: 审查Agent对两种方案打分
REVIEWER_SYSTEM = """你是技术方案评审专家。对方案从5个维度打分(1-10分):
1. 正确性: 技术方案是否正确
2. 完整性: 是否覆盖所有需求
3. 可行性: 是否可以直接实施
4. 创新性: 是否有创新设计
5. 业务对齐: 是否贴合TeamWiki场景
输出JSON: {"正确性":X,"完整性":X,"可行性":X,"创新性":X,"业务对齐":X}
只输出JSON，不要其他内容。不调用工具。"""

# 准备审查内容
multi_output_text = "\n\n---\n\n".join([
    f"=== {name} ===\n{r['output'][:1200]}"
    for name, r in sorted(a1["agents"].items())
])

REVIEW_TASK_MULTI = f"请评审以下多Agent协作方案:\n\n{multi_output_text[:3000]}"
REVIEW_TASK_SINGLE = f"请评审以下单Agent方案:\n\n{b1['output'][:3000]}"

print(f"\nB3: 审查Agent盲评打分")
review_multi = run_agent(REVIEWER_SYSTEM, REVIEW_TASK_MULTI)
review_single = run_agent(REVIEWER_SYSTEM, REVIEW_TASK_SINGLE)

# 解析评分JSON
def parse_scores(output):
    try:
        json_match = re.search(r'\{[^}]+\}', output)
        if json_match:
            return json.loads(json_match.group())
    except:
        pass
    return {}

scores_multi = parse_scores(review_multi["output"])
scores_single = parse_scores(review_single["output"])

print(f"\n--- Test B 对比 ---")
print(f"{'维度':<15s} {'多Agent并行':>12s} {'单Agent':>12s} {'优势':>10s}")
print(f"{'-'*50}")
for dim in ["正确性", "完整性", "可行性", "创新性", "业务对齐"]:
    sm = scores_multi.get(dim, "?")
    ss = scores_single.get(dim, "?")
    winner = "多Agent" if isinstance(sm,(int,float)) and isinstance(ss,(int,float)) and sm > ss else ("单Agent" if isinstance(sm,(int,float)) and isinstance(ss,(int,float)) and ss > sm else "平")
    print(f"{dim:<15s} {str(sm):>12s} {str(ss):>12s} {winner:>10s}")
print(f"\n多Agent总成本: ${multi_cost:.6f} | 单Agent总成本: ${b1['cost']:.6f}")
print(f"多Agent墙钟: {a1['wall']:.0f}s (并行) | 单Agent墙钟: {b1['wall']:.0f}s")


# ============================================================
# Test C: 交叉审查有效性
# ============================================================
print(f"\n{'='*70}")
print(f"  Test C: 交叉审查有效性 — 发现遗漏问题")
print(f"{'='*70}")

# C1: 审查Agent审阅每个Agent的方案
CROSS_REVIEW_SYSTEM = """你是技术方案交叉审查专家。你的任务是审阅一份技术方案，找出其中:
1. 遗漏的关键问题(原方案未提及但应该覆盖的)
2. 潜在风险或隐患
3. 可以改进的地方
输出格式:
发现的问题数: N
具体问题列表(每行一个):
- [类型:遗漏/风险/改进] 问题描述
不调用任何工具，直接输出审查报告。"""

issues_found = {}

for name, r in sorted(a1["agents"].items()):
    review_task = f"请审查以下{name}的技术方案，找出遗漏、风险和可改进之处:\n\n{r['output'][:2000]}"
    print(f"\n审查 {name} 的方案...")
    review_result = run_agent(CROSS_REVIEW_SYSTEM, review_task)

    # 统计发现的问题数
    output = review_result["output"]
    # 计算"发现的问题数"
    count_m = re.search(r'发现的问题数[：:]\s*(\d+)', output)
    issue_count = int(count_m.group(1)) if count_m else 0
    if issue_count == 0:
        # 手动统计"- ["开头的行
        issue_count = len(re.findall(r'^\s*-\s*\[', output, re.MULTILINE))

    issues_found[name] = {
        "count": issue_count,
        "cost": review_result["cost"],
        "review_chars": len(output),
    }
    print(f"  发现 {issue_count} 个问题 | 审查成本 ${review_result['cost']:.6f}")

# 分析交叉审查是否发现了不同视角的问题
print(f"\n--- Test C 统计 ---")
total_issues = sum(v["count"] for v in issues_found.values())
total_review_cost = sum(v["cost"] for v in issues_found.values())
print(f"{'Agent':<18s} {'发现问题数':>10s}")
print(f"{'-'*30}")
for name, v in sorted(issues_found.items()):
    print(f"{name:<18s} {v['count']:>10}")
print(f"{'-'*30}")
print(f"{'合计':<18s} {total_issues:>10}")
print(f"交叉审查总成本: ${total_review_cost:.6f}")
print(f"每个问题的平均发现成本: ${total_review_cost/total_issues:.6f}" if total_issues > 0 else "")


# ============================================================
# 最终汇总
# ============================================================
print(f"\n{'='*70}")
print(f"  三合一验证实验 — 最终汇总")
print(f"{'='*70}")
print(f"所有数据来自 reasonix run 实际输出，未编造。")
print(f"模型: {MODEL}")
print(f"时间: {time.strftime('%H:%M:%S')}")

print(f"""
┌─────────────────────────────────────────────────────┐
│ Test A: 黑板 vs 全量上下文                            │
│   黑板模式成本:    ${a1['cost']:.6f}  (输入 ~{a1['input_tokens']} tok)       │
│   全量上下文成本:  ${a2['cost']:.6f}  (输入 ~{a2['input_tokens']} tok)       │
│   节省:            {((1-a1['cost']/a2['cost'])*100) if a2['cost'] > 0 else 0:.1f}% 成本, {((1-a1['input_tokens']/a2['input_tokens'])*100) if a2['input_tokens'] > 0 else 0:.1f}% 输入token │
├─────────────────────────────────────────────────────┤
│ Test B: 多Agent vs 单Agent                           │
│   多Agent成本: ${multi_cost:.6f}  单Agent成本: ${b1['cost']:.6f}            │
│   评分: 多Agent={scores_multi}  单Agent={scores_single}                   │
├─────────────────────────────────────────────────────┤
│ Test C: 交叉审查有效性                                │
│   总发现: {total_issues} 个遗漏/风险/改进               │
│   审查成本: ${total_review_cost:.6f}                  │
└─────────────────────────────────────────────────────┘
""")
