"""
真实多Agent并行测试
- 用 parallel-scheduler.py 的架构
- 每个Agent独立 reasonix run
- 记录每轮每个Agent的真实缓存数据
- 不做任何模拟或编造
"""
import subprocess, time, os, json, re, sys

REASONIX_CMD = "node D:/Reasonix-Dev/dist/cli/index.js"

def run_agent(system: str, task: str, model="deepseek-v4-flash") -> dict:
    """调用 reasonix run，返回完整输出和缓存数据"""
    t0 = time.time()
    # Windows: 用 shell=True 确保 node 能找到
    cmd = f'{REASONIX_CMD} run "{task}" -m {model} --system "{system}" --no-config'
    result = subprocess.run(
        cmd, shell=True,
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    wall = time.time() - t0
    output = (result.stdout or "").strip()
    # 解析缓存和成本
    cache_m = re.search(r"cache:(\d+\.?\d*)%", output)
    cost_m = re.search(r"cost:\$(\d+\.?\d*)", output)
    turns_m = re.search(r"turns:(\d+)", output)
    return {
        "cache": float(cache_m.group(1)) if cache_m else 0.0,
        "cost": float(cost_m.group(1)) if cost_m else 0.0,
        "turns": int(turns_m.group(1)) if turns_m else 1,
        "wall": wall,
        "chars": len(output),
        "output": output,
    }

def run_parallel_batch(system_prompts: dict, task_template: str, model="deepseek-v4-flash") -> dict:
    """并行启动多个Agent，返回所有结果"""
    import threading
    results = {}
    lock = threading.Lock()

    def worker(name, system, task):
        r = run_agent(system, task, model)
        with lock:
            results[name] = r

    threads = []
    for name, system in system_prompts.items():
        task = task_template + f"\n\n从{name}角度分析和设计。直接输出方案，不调用工具。"
        t = threading.Thread(target=worker, args=(name, system, task))
        threads.append(t)
        t.start()

    start = time.time()
    for t in threads:
        t.join()
    wall = time.time() - start

    return {"wall": wall, "agents": results}

def print_batch_results(label: str, data: dict):
    """输出批次结果"""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"总墙钟: {data['wall']:.0f}s")
    total_cost = 0
    for name, r in sorted(data["agents"].items()):
        total_cost += r["cost"]
        print(f"  [{name:15s}] cache={r['cache']:5.1f}%  cost=${r['cost']:.6f}  "
              f"wall={r['wall']:.0f}s  turns={r['turns']}  chars={r['chars']}")
    print(f"  总成本: ${total_cost:.6f}")

# ============================================================
# 三个固定角色 System Prompts
# ============================================================
SYSTEMS = {
    "db_architect": "你是PostgreSQL数据库架构师。精通Schema设计、索引优化、范式理论。直接输出SQL DDL。不调用任何工具。",
    "security_expert": "你是Web安全专家。精通OWASP Top 10、JWT认证、权限模型、审计日志。直接输出方案。不调用任何工具。",
    "perf_analyst": "你是后端性能分析师。精通缓存策略、搜索选型、并发优化、容量规划。直接输出方案。不调用任何工具。",
}

TASK = """为TeamWiki知识库系统设计方案。
TeamWiki: 100人团队协作Wiki，支持Markdown编辑、版本历史、全文搜索、三级权限(组织→空间→页面)、REST API。
请输出你负责领域的完整方案，具体可落地。"""

print("=" * 60)
print("  真实多Agent并行缓存测试")
print("=" * 60)
print(f"Reasonix: {REASONIX_CMD}")
print(f"模型: deepseek-v4-flash")
print(f"Agent数: {len(SYSTEMS)} (并行启动)")
print(f"开始时间: {time.strftime('%H:%M:%S')}")

# ===== 测试1: 冷启动 =====
print("\n>>> 测试1: 冷启动 (首次调用, system prompt从未见过)")
batch1 = run_parallel_batch(SYSTEMS, TASK)
print_batch_results("测试1: 冷启动", batch1)

# ===== 测试2: 立即预热 =====
print("\n>>> 测试2: 预热 (同system, 立即重跑, 应命中缓存)")
batch2 = run_parallel_batch(SYSTEMS, TASK)
print_batch_results("测试2: 预热", batch2)

# ===== 测试3: 短间隔 =====
print("\n>>> 测试3: 短间隔 (等待30秒, 验证缓存是否保留)")
print("等待30秒...")
time.sleep(30)
batch3 = run_parallel_batch(SYSTEMS, TASK)
print_batch_results("测试3: 30秒间隔", batch3)

# ===== 测试4: 超60秒 =====
print("\n>>> 测试4: 超60秒间隔 (等待70秒, 验证缓存是否丢失)")
print("等待70秒...")
time.sleep(70)
batch4 = run_parallel_batch(SYSTEMS, TASK)
print_batch_results("测试4: 70秒间隔", batch4)

# ===== 测试5: 恢复 =====
print("\n>>> 测试5: 立即恢复 (紧接测试4, 验证缓存能否恢复)")
batch5 = run_parallel_batch(SYSTEMS, TASK)
print_batch_results("测试5: 恢复", batch5)

# ===== 汇总 =====
print(f"\n{'='*60}")
print(f"  完整汇总")
print(f"{'='*60}")
print(f"{'测试':<20s} {'db_architect':>12s} {'security_expert':>12s} {'perf_analyst':>12s} {'墙钟':>8s}")
print("-" * 70)
for label, batch in [
    ("1.冷启动", batch1), ("2.预热", batch2), ("3.30s间隔", batch3),
    ("4.70s间隔", batch4), ("5.恢复", batch5)
]:
    caches = [f"{batch['agents'][n]['cache']:.1f}%" for n in ["db_architect","security_expert","perf_analyst"]]
    print(f"{label:<20s} {caches[0]:>12s} {caches[1]:>12s} {caches[2]:>12s} {batch['wall']:.0f}s")

print(f"\n结束时间: {time.strftime('%H:%M:%S')}")
print("所有数据来自 reasonix run 实际输出，未编造。")
