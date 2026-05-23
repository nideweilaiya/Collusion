"""
Collusion完整流水线 vs 单Agent 盲评对比
==========================================
流水线: 并行提案 → 交叉审查 → 审查后修订 → Owner整合 → 最终方案
对比: 单Agent直接生成 → 最终方案
盲评: 审查Agent不知道方案来源，5维打分
"""
import subprocess, time, json, re, threading, os, tempfile

REASONIX = "node D:/Reasonix-Dev/dist/cli/index.js"
MODEL = "deepseek-v4-flash"

def run_rx(system, task):
    t0 = time.time()
    tf = tempfile.NamedTemporaryFile(mode='w', suffix='_t.txt', delete=False, encoding='utf-8')
    tf.write(task); tf.close()
    sf = tempfile.NamedTemporaryFile(mode='w', suffix='_s.txt', delete=False, encoding='utf-8')
    sf.write(system); sf.close()
    tfp = tf.name.replace(chr(92), '/')
    sfp = sf.name.replace(chr(92), '/')
    cmd = f'{REASONIX} run "$(cat {tfp})" -m {MODEL} --system "$(cat {sfp})" --no-config'
    r = subprocess.run(['bash', '-c', cmd], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=300)
    os.unlink(tf.name); os.unlink(sf.name)
    out = (r.stdout or "").strip()
    return {"cost": float(m.group(1)) if (m := re.search(r"cost:\$(\d+\.?\d*)", out)) else 0,
            "cache": float(m.group(1)) if (m := re.search(r"cache:(\d+\.?\d*)%", out)) else 0,
            "wall": time.time()-t0, "output": out, "chars": len(out)}

def run_parallel(systems, tasks):
    results = {}
    def w(name, sys, tsk):
        results[name] = run_rx(sys, tsk)
    threads = [threading.Thread(target=w, args=(n, systems[n], tasks[n])) for n in systems]
    t0 = time.time()
    for t in threads: t.start()
    for t in threads: t.join()
    return {"wall": time.time()-t0, "cost": sum(r["cost"] for r in results.values()),
            "agents": results}

TASK_TOPIC = "TeamWiki知识库系统后端方案"
TASK_SCOPE = "100人团队协作Wiki，Markdown编辑，版本历史，全文搜索，三级权限(组织/空间/页面)，REST API"

# =================================================================
# Phase 1: 并行提案 (3 Agent, 黑板模式)
# =================================================================
print("=" * 60)
print("Phase 1: 并行提案")
print("=" * 60)

PROPOSAL_SYSTEMS = {
    "architect": "你是后端架构师。设计API、数据库Schema、技术栈选型。直接输出完整Markdown方案。不调用工具。",
    "security": "你是安全专家。设计认证授权、数据加密、审计日志方案。直接输出完整Markdown方案。不调用工具。",
    "performance": "你是性能分析师。设计缓存策略、搜索方案、容量规划。直接输出完整Markdown方案。不调用工具。",
}
PROPOSAL_TASKS = {
    "architect": f"为{TASK_TOPIC}设计架构方案。范围: {TASK_SCOPE}。输出: 1)技术栈选型 2)API设计 3)数据库Schema 4)架构图(ASCII)。全中文。",
    "security": f"为{TASK_TOPIC}设计安全方案。范围: {TASK_SCOPE}。输出: 1)JWT认证 2)三级权限模型 3)XSS/CSRF/SQL注入防护 4)审计日志。全中文。",
    "performance": f"为{TASK_TOPIC}设计性能方案。范围: {TASK_SCOPE}。输出: 1)三层缓存架构 2)Meilisearch全文搜索 3)容量规划 4)并发策略。全中文。",
}
p1 = run_parallel(PROPOSAL_SYSTEMS, PROPOSAL_TASKS)
print(f"提案完成: wall={p1['wall']:.0f}s cost=${p1['cost']:.6f}")
for n, r in sorted(p1['agents'].items()):
    print(f"  [{n:12s}] {r['chars']}chars cost=${r['cost']:.6f} cache={r['cache']:.1f}%")

pipeline_cost = p1['cost']

# =================================================================
# Phase 2: 交叉审查 (每个Agent审查另外两个的方案)
# =================================================================
print(f"\n{'='*60}")
print("Phase 2: 交叉审查")
print("=" * 60)

CROSS_REVIEW_SYS = "你是技术审查专家。审查以下方案，找出遗漏、风险和可改进之处。每个发现用- [类型]格式。先写总数: 发现问题数:N。不调用工具。"

review_results = {}
for reviewer_name in PROPOSAL_SYSTEMS:
    # 审查另一个Agent的方案 (循环分配)
    target_name = {"architect": "security", "security": "performance", "performance": "architect"}[reviewer_name]
    target_output = p1['agents'][target_name]['output']
    task = f"审查这个TeamWiki {target_name} 方案:\n\n{target_output[:2000]}\n\n找出遗漏、风险和可改进之处。"
    key = f"{reviewer_name}_reviews_{target_name}"
    review_results[key] = run_rx(CROSS_REVIEW_SYS, task)

rev_cost = sum(r['cost'] for r in review_results.values())
rev_issues = sum(len(re.findall(r'^\s*-\s*\[', r['output'], re.MULTILINE)) for r in review_results.values())
pipeline_cost += rev_cost
print(f"交叉审查完成: cost=${rev_cost:.6f} 发现{rev_issues}个问题")
for k, r in sorted(review_results.items()):
    issues = len(re.findall(r'^\s*-\s*\[', r['output'], re.MULTILINE))
    print(f"  [{k:30s}] {issues}个问题 cost=${r['cost']:.6f}")

# =================================================================
# Phase 3: 审查后修订 (每个Agent根据审查意见改进方案)
# =================================================================
print(f"\n{'='*60}")
print("Phase 3: 审查后修订")
print("=" * 60)

REVISION_SYSTEMS = {
    "architect": "你是后端架构师。根据审查意见改进你的TeamWiki方案。整合反馈，输出修订后的完整方案。不调用工具。",
    "security": "你是安全专家。根据审查意见改进你的TeamWiki方案。整合反馈，输出修订后的完整方案。不调用工具。",
    "performance": "你是性能分析师。根据审查意见改进你的TeamWiki方案。整合反馈，输出修订后的完整方案。不调用工具。",
}
REVISION_TASKS = {}
for name in PROPOSAL_SYSTEMS:
    # 找到审查该Agent的意见
    reviews_for_me = []
    for k, r in review_results.items():
        if k.endswith(f"_{name}"):
            reviews_for_me.append(r['output'])
    feedback = "\n\n".join(reviews_for_me) if reviews_for_me else "无审查意见"
    REVISION_TASKS[name] = (
        f"根据以下审查意见，改进你的TeamWiki方案。\n\n"
        f"=== 你的原始方案 ===\n{p1['agents'][name]['output'][:1500]}\n\n"
        f"=== 审查意见 ===\n{feedback[:1500]}\n\n"
        f"输出修订后的完整方案。整合所有合理建议。全中文。"
    )

p3 = run_parallel(REVISION_SYSTEMS, REVISION_TASKS)
pipeline_cost += p3['cost']
print(f"修订完成: wall={p3['wall']:.0f}s cost=${p3['cost']:.6f}")
for n, r in sorted(p3['agents'].items()):
    print(f"  [{n:12s}] {r['chars']}chars cost=${r['cost']:.6f}")

# =================================================================
# Phase 4: Owner整合
# =================================================================
print(f"\n{'='*60}")
print("Phase 4: Owner整合")
print("=" * 60)

INTEGRATOR_SYS = "你是技术方案整合专家。将多个专家的修订方案整合为一份统一、一致、可落地的完整技术方案。消除冲突，保持逻辑一致。输出最终方案。不调用工具。"
integration_text = "\n\n---\n\n".join([
    f"==={name}的修订方案===\n{p3['agents'][name]['output'][:2000]}"
    for name in sorted(PROPOSAL_SYSTEMS)
])
INTEGRATOR_TASK = f"整合以下{TASK_TOPIC}的多个专家方案为一份最终方案:\n\n{integration_text[:6000]}\n\n输出完整的最终技术方案。全中文。"

p4 = run_rx(INTEGRATOR_SYS, INTEGRATOR_TASK)
pipeline_cost += p4['cost']
collusion_final = p4['output']
print(f"整合完成: cost=${p4['cost']:.6f} chars={p4['chars']}")

# =================================================================
# 单Agent对照组
# =================================================================
print(f"\n{'='*60}")
print("单Agent对照组")
print("=" * 60)

SINGLE_SYS = "你是全栈技术专家。为TeamWiki设计完整后端方案。必须从架构、安全、性能三个角度全面覆盖。输出完整、一致、可落地的方案。不调用工具。"
SINGLE_TASK = (
    f"为{TASK_TOPIC}设计完整后端技术方案。\n"
    f"范围: {TASK_SCOPE}\n"
    f"必须覆盖: 1)技术架构和API设计 2)数据库Schema 3)JWT认证和三级权限 4)缓存和全文搜索 "
    f"5)安全防护(XSS/CSRF/SQL注入) 6)部署和容量规划。\n"
    f"输出一份完整、统一、可直接实施的方案。全中文。"
)

single_result = run_rx(SINGLE_SYS, SINGLE_TASK)
single_cost = single_result['cost']
single_output = single_result['output']
print(f"单Agent: cost=${single_cost:.6f} wall={single_result['wall']:.0f}s chars={single_result['chars']}")

# =================================================================
# Phase 5: 盲评
# =================================================================
print(f"\n{'='*60}")
print("Phase 5: 盲评 (审查Agent不知道方案来源)")
print("=" * 60)

BLIND_SYS = "你是技术方案盲评专家。你会收到两份方案(方案X和方案Y)，你不知道哪份是谁写的。从5个维度打分(1-10)。只输出JSON。不调用工具。"

# 随机分配X/Y (用固定规则避免偏差: 流水线=方案X, 单Agent=方案Y)
blind_task = (
    f"盲评以下两份{TASK_TOPIC}方案:\n\n"
    f"=== 方案X ===\n{collusion_final[:3000]}\n\n"
    f"=== 方案Y ===\n{single_output[:3000]}\n\n"
    f"对方案X和方案Y分别打分。只输出:\n"
    f'{{"方案X":{{"正确性":X,"完整性":X,"可行性":X,"创新性":X,"业务对齐":X}},'
    f'"方案Y":{{"正确性":Y,"完整性":Y,"可行性":Y,"创新性":Y,"业务对齐":Y}}}}'
)
blind = run_rx(BLIND_SYS, blind_task)
pipeline_cost += blind['cost']

def parse_blind(text):
    m = re.search(r'\{.+"方案X".+"方案Y".+\}', text, re.DOTALL)
    return json.loads(m.group()) if m else {}

scores = parse_blind(blind['output'])
sx = scores.get("方案X", {})  # Collusion流水线
sy = scores.get("方案Y", {})  # 单Agent

# =================================================================
# 最终汇总
# =================================================================
print(f"\n{'='*60}")
print("  完整流水线 vs 单Agent — 最终结论")
print("=" * 60)
print("所有数据来自 reasonix run 实际输出。")
print()

print("【流水线各阶段成本】")
print(f"  Phase 1 并行提案:     ${p1['cost']:.6f}")
print(f"  Phase 2 交叉审查:     ${rev_cost:.6f} (发现{rev_issues}个问题)")
print(f"  Phase 3 审查后修订:   ${p3['cost']:.6f}")
print(f"  Phase 4 Owner整合:    ${p4['cost']:.6f}")
print(f"  Phase 5 盲评:         ${blind['cost']:.6f}")
print(f"  ─────────────────────────")
print(f"  流水线总成本:          ${pipeline_cost:.6f}")
print(f"  单Agent成本:           ${single_cost:.6f}")
print()

print("【盲评结果】(方案X=Collusion流水线, 方案Y=单Agent)")
print(f"  {'维度':<12s} {'流水线(方案X)':>12s} {'单Agent(方案Y)':>12s} {'胜出':>8s}")
print(f"  {'-'*46}")
wins = {"流水线": 0, "单Agent": 0, "平": 0}
for dim in ["正确性","完整性","可行性","创新性","业务对齐"]:
    vx = sx.get(dim, "?")
    vy = sy.get(dim, "?")
    if isinstance(vx,(int,float)) and isinstance(vy,(int,float)):
        if vx > vy: wins["流水线"] += 1; w = "流水线"
        elif vy > vx: wins["单Agent"] += 1; w = "单Agent"
        else: wins["平"] += 1; w = "平"
    else:
        w = "?"
    print(f"  {dim:<12s} {str(vx):>12s} {str(vy):>12s} {w:>8s}")

print(f"\n  流水线胜: {wins['流水线']}/5  单Agent胜: {wins['单Agent']}/5  平: {wins['平']}/5")
print(f"\n  流水线总成本: ${pipeline_cost:.6f}")
print(f"  单Agent成本: ${single_cost:.6f}")
print(f"  成本比: {pipeline_cost/single_cost:.1f}x" if single_cost > 0 else "")
print(f"\n  交叉审查有效性: 发现{rev_issues}个问题, 成本${rev_cost:.6f}")
