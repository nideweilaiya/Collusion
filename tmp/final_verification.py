"""
黑板+顾问模式 三合一验证 (最终版)
所有数据来自 reasonix run 实际输出
"""
import subprocess, time, json, re, threading, os, tempfile

REASONIX = "node D:/Reasonix-Dev/dist/cli/index.js"
MODEL = "deepseek-v4-flash"

def run_rx(system, task):
    """调用 reasonix run (task和system都通过临时文件传递)"""
    t0 = time.time()
    import tempfile
    tf = tempfile.NamedTemporaryFile(mode='w', suffix='_task.txt', delete=False, encoding='utf-8')
    tf.write(task)
    tf.close()
    sf = tempfile.NamedTemporaryFile(mode='w', suffix='_sys.txt', delete=False, encoding='utf-8')
    sf.write(system)
    sf.close()

    cmd = f'{REASONIX} run "$(cat {tf.name.replace(chr(92), "/")})" -m {MODEL} --system "$(cat {sf.name.replace(chr(92), "/")})" --no-config'
    r = subprocess.run(['bash', '-c', cmd], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=300)
    os.unlink(tf.name)
    os.unlink(sf.name)

    out = (r.stdout or "").strip()
    return {
        "cost": float(m.group(1)) if (m := re.search(r"cost:\$(\d+\.?\d*)", out)) else 0,
        "cache": float(m.group(1)) if (m := re.search(r"cache:(\d+\.?\d*)%", out)) else 0,
        "wall": time.time() - t0,
        "output": out,
        "chars": len(out),
    }

def run_parallel(systems, tasks, label):
    """并行启动多个Agent"""
    print(f"\n--- {label} ---")
    results = {}
    lock = threading.Lock()
    def w(name, sys, tsk):
        r = run_rx(sys, tsk)
        with lock: results[name] = r

    threads = [threading.Thread(target=w, args=(n, systems[n], tasks[n])) for n in systems]
    t0 = time.time()
    for t in threads: t.start()
    for t in threads: t.join()
    wall = time.time() - t0

    total_cost = sum(r["cost"] for r in results.values())
    for name, r in sorted(results.items()):
        print(f"  [{name:12s}] cache={r['cache']:5.1f}% cost=${r['cost']:.6f} "
              f"wall={r['wall']:.0f}s chars={r['chars']}")
    print(f"  => 并行墙钟={wall:.0f}s 总成本=${total_cost:.6f}")
    return {"wall": wall, "cost": total_cost, "agents": results}

# ============================================================
# Test A: 黑板 vs 全量上下文
# ============================================================
print("=" * 60)
print("Test A: 黑板模式 vs 全量上下文")
print("=" * 60)

SYSTEMS = {
    "db": "PostgreSQL架构师。设计数据库Schema和API。直接输出Markdown。不调用任何工具。",
    "sec": "Web安全专家。设计认证授权方案。直接输出Markdown。不调用任何工具。",
    "perf": "性能分析师。设计缓存和搜索方案。直接输出Markdown。不调用任何工具。",
}

# A1: 黑板 - 最小上下文
TASKS_MINIMAL = {
    "db": "为TeamWiki设计数据库Schema和API接口。输出SQL DDL和API端点列表。",
    "sec": "为TeamWiki设计JWT认证和三级权限(组织/空间/页面)方案。输出具体实现。",
    "perf": "为TeamWiki设计Redis缓存策略和Meilisearch搜索方案。输出配置和策略。",
}
a1 = run_parallel(SYSTEMS, TASKS_MINIMAL, "A1: 黑板(最小上下文)")

# A2: 全量上下文
TASKS_FULL = {}
for name in SYSTEMS:
    others = "\n\n".join([
        f"[{n}的方案]\n{a1['agents'][n]['output'][:1200]}"
        for n in SYSTEMS if n != name
    ])
    TASKS_FULL[name] = (
        f"参考其他专家的方案，改进你的TeamWiki方案。\n\n{others}\n\n"
        f"输出改进后的完整方案。"
    )
a2 = run_parallel(SYSTEMS, TASKS_FULL, "A2: 全量上下文")

print(f"\nTest A 结果:")
print(f"  黑板: cost=${a1['cost']:.6f} wall={a1['wall']:.0f}s")
print(f"  全量: cost=${a2['cost']:.6f} wall={a2['wall']:.0f}s")
print(f"  节省: {((1-a1['cost']/a2['cost'])*100):.1f}% 成本" if a2['cost'] > 0 else "")

# ============================================================
# Test B: 多Agent vs 单Agent
# ============================================================
print(f"\n{'='*60}")
print("Test B: 多Agent并行 vs 单Agent")
print("=" * 60)

# B1: 单Agent
B1_SYS = "你是全栈技术专家。为TeamWiki设计完整后端方案，覆盖架构/API/数据库/安全/性能/搜索。直接输出方案。不调用工具。"
B1_TASK = "为TeamWiki知识库系统设计完整后端技术方案。必须覆盖: 1)整体架构 2)API设计 3)数据库Schema 4)认证安全 5)缓存策略 6)全文搜索 7)部署方案。全中文输出。"
b1 = run_rx(B1_SYS, B1_TASK)
print(f"B1 单Agent: cost=${b1['cost']:.6f} wall={b1['wall']:.0f}s cache={b1['cache']:.1f}% chars={b1['chars']}")

# B2 = A1的结果(多Agent已并行完成)
print(f"B2 多Agent: cost=${a1['cost']:.6f} wall={a1['wall']:.0f}s (3Agent并行)")

# B3: 审查打分
REVIEW_SYS = '你是技术方案评审专家。从5个维度评分(1-10)。只输出JSON: {"正确性":X,"完整性":X,"可行性":X,"创新性":X,"业务对齐":X}'

# 准备简化的审查文本
multi_summary = "\n".join([
    f"[{n}] {a1['agents'][n]['output'][:800]}"
    for n in sorted(SYSTEMS)
])
single_summary = b1['output'][:2500]

r_m = run_rx(REVIEW_SYS, f"评审这个TeamWiki方案，输出JSON分数:\n{multi_summary[:2500]}")
r_s = run_rx(REVIEW_SYS, f"评审这个TeamWiki方案，输出JSON分数:\n{single_summary}")

def parse_scores(text):
    m = re.search(r'\{[^}]+\}', text)
    return json.loads(m.group()) if m else {}

sm = parse_scores(r_m["output"])
ss = parse_scores(r_s["output"])

print(f"\nB3 审查评分:")
print(f"  {'维度':<12s} {'多Agent':>6s} {'单Agent':>6s}")
for dim in ["正确性","完整性","可行性","创新性","业务对齐"]:
    vm = sm.get(dim, "?")
    vs = ss.get(dim, "?")
    print(f"  {dim:<12s} {str(vm):>6s} {str(vs):>6s}")

# ============================================================
# Test C: 交叉审查
# ============================================================
print(f"\n{'='*60}")
print("Test C: 交叉审查有效性")
print("=" * 60)

CROSS_SYS = "你是技术审查专家。审查以下方案，找出遗漏的问题、潜在风险、可改进之处。每个发现用 - [类型] 格式。先写: 发现问题总数:N"
total_issues = 0
total_review_cost = 0

for name in sorted(SYSTEMS):
    task = f"审查这个TeamWiki {name} 方案:\n\n{a1['agents'][name]['output'][:1200]}\n\n找出遗漏、风险和可改进之处。"
    cr = run_rx(CROSS_SYS, task)
    count = len(re.findall(r'^\s*-\s*\[', cr["output"], re.MULTILINE))
    total_issues += count
    total_review_cost += cr["cost"]
    print(f"  [{name}] 发现 {count} 个问题 | cost=${cr['cost']:.6f}")

print(f"\n  总发现问题: {total_issues}")
print(f"  审查总成本: ${total_review_cost:.6f}")
print(f"  每问题成本: ${total_review_cost/total_issues:.6f}" if total_issues > 0 else "")

# ============================================================
# 汇总
# ============================================================
print(f"\n{'='*60}")
print("三合一验证 最终结果")
print("=" * 60)
print("数据来源: reasonix run 实际输出 (model: deepseek-v4-flash)")
print()

print("Test A | 黑板 vs 全量上下文")
print(f"  黑板模式: ${a1['cost']:.6f} ({a1['wall']:.0f}s)")
print(f"  全量上下文: ${a2['cost']:.6f} ({a2['wall']:.0f}s)")
print(f"  成本节省: {((1-a1['cost']/a2['cost'])*100):.1f}%" if a2['cost']>0 else "  成本节省: N/A")
print()

print("Test B | 多Agent vs 单Agent")
print(f"  多Agent成本: ${a1['cost']:.6f} ({a1['wall']:.0f}s 并行)")
print(f"  单Agent成本: ${b1['cost']:.6f} ({b1['wall']:.0f}s)")
print(f"  评分-多Agent: {sm}")
print(f"  评分-单Agent: {ss}")
print()

print("Test C | 交叉审查有效性")
print(f"  发现问题: {total_issues} 个")
print(f"  审查成本: ${total_review_cost:.6f}")
print()

print("所有数据真实，来自 reasonix CLI 实际执行。")
