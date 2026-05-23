"""Collusion v1.0.0 真实对比测试 — 按测试方案执行

执行 3 个任务序列 × 2 个任务 = 6 次真实编排
收集耗时/质量/成本/检索命中/预警/Agent路由 全指标

用法:
  python benchmark_real_comparison.py          # 在当前版本运行
  python benchmark_real_comparison.py --group A  # 强制标记为 A 组
"""
import json
import sys
import os
import time
from pathlib import Path

# Windows console UTF-8 support
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(str(Path(__file__).parent))

# ============================================================
# 检测当前版本支持的功能
# ============================================================
HAS_KNOWLEDGE = False
HAS_CAUSAL = False
HAS_AGENT_GRAPH = False
try:
    from src.orchestrator import BrainstormOrchestrator
    o = BrainstormOrchestrator("config.json")
    HAS_KNOWLEDGE = hasattr(o, 'pre_check_knowledge')
    HAS_CAUSAL = hasattr(o, 'query_causal_memory')
    HAS_AGENT_GRAPH = hasattr(o, 'agent_graph')
except Exception:
    pass

GROUP = "B" if HAS_KNOWLEDGE else "A"
if "--group" in sys.argv:
    idx = sys.argv.index("--group")
    GROUP = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else GROUP

print(f"{'='*60}")
print(f"  Collusion 真实对比测试 — {GROUP} 组")
print(f"  Knowledge: {HAS_KNOWLEDGE} | Causal: {HAS_CAUSAL} | AgentGraph: {HAS_AGENT_GRAPH}")
print(f"{'='*60}")

# ============================================================
# 任务定义
# ============================================================
SEQUENCES = [
    {
        "id": "S1",
        "name": "相似方案复用",
        "tasks": [
            {
                "id": "S1T1",
                "desc": "设计一个文件分享服务（支持上传、下载、分享链接、过期时间）",
                "tags": ["文件分享", "上传", "过期时间"],
            },
            {
                "id": "S1T2",
                "desc": "设计一个短链接服务（支持长链转短链、自定义别名、过期时间、访问统计）",
                "tags": ["短链接", "过期时间", "访问统计"],
            },
        ],
    },
    {
        "id": "S2",
        "name": "废案预警与因果规避",
        "tasks": [
            {
                "id": "S2T1",
                "desc": "设计一个高并发实时协作编辑器（要求多人同时编辑、冲突解决、版本历史）",
                "tags": ["实时协作", "冲突解决", "高并发"],
            },
            {
                "id": "S2T2",
                "desc": "设计一个简易的在线文档查看器（支持Markdown渲染、评论、但不要求实时协作）",
                "tags": ["文档查看", "Markdown", "评论"],
            },
        ],
    },
    {
        "id": "S3",
        "name": "跨领域借鉴",
        "tasks": [
            {
                "id": "S3T1",
                "desc": "设计一个Minecraft Mod的自动钓鱼系统（含状态机、水域检测、背包管理）",
                "tags": ["Minecraft", "状态机", "背包管理"],
            },
            {
                "id": "S3T2",
                "desc": "设计一个Discord Bot的自动任务分配系统（含任务队列、状态追踪、异常回退）",
                "tags": ["Discord", "任务队列", "状态追踪"],
            },
        ],
    },
]

RESULTS = {
    "group": GROUP,
    "has_knowledge": HAS_KNOWLEDGE,
    "has_causal": HAS_CAUSAL,
    "has_agent_graph": HAS_AGENT_GRAPH,
    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    "tasks": [],
    "summary": {},
}

# ============================================================
# 执行测试
# ============================================================
from src.orchestrator import BrainstormOrchestrator
orch = BrainstormOrchestrator("config.json")

for seq in SEQUENCES:
    print(f"\n{'─'*60}")
    print(f"  序列 {seq['id']}: {seq['name']}")
    print(f"{'─'*60}")

    for task in seq["tasks"]:
        print(f"\n  ▶ 任务 {task['id']}: {task['desc'][:60]}...")

        t_start = time.time()

        # 知识预检（仅 B 组）
        precheck = None
        if HAS_KNOWLEDGE:
            try:
                precheck = orch.pre_check_knowledge(task["desc"])
                if precheck["relevant_assets"]:
                    print(f"    📚 检索到 {len(precheck['relevant_assets'])} 个相关历史资产")
                if precheck["discarded_warnings"]:
                    print(f"    ⚠️  发现 {len(precheck['discarded_warnings'])} 个废案警告")
            except Exception as e:
                print(f"    [预检跳过] {e}")

        # 执行编排
        print(f"    ⏳ 编排中...")
        t_orch_start = time.time()
        try:
            task_id = orch.orchestrate(task["desc"])
            # 等待完成
            for _ in range(180):  # max 3min
                time.sleep(2)
                state = orch.get_state(task_id)
                if state and state.get("phase") == "done":
                    break
            orch_time = time.time() - t_orch_start
            print(f"    ✅ 完成 ({orch_time:.1f}s)")
        except Exception as e:
            print(f"    ❌ 编排失败: {e}")
            RESULTS["tasks"].append({
                "task_id": task["id"],
                "desc": task["desc"],
                "status": "failed",
                "error": str(e),
            })
            continue

        t_total = time.time() - t_start

        # 收集结果
        state = orch.get_state(task_id)
        result_data = {
            "task_id": task["id"],
            "task_seq": seq["id"],
            "desc": task["desc"][:80],
            "tags": task["tags"],
            "status": "done",
            "orch_task_id": task_id,
            "time_s": round(t_total, 1),
            "orch_time_s": round(orch_time, 1),
        }

        if state:
            result_data["phase"] = state.get("phase")
            result_data["tokens"] = state.get("total_tokens", 0)
            result_data["cost_rmb"] = round(state.get("total_cost_rmb", 0), 6)

            # 方案质量
            top3 = state.get("top3_plans", [])
            if top3:
                result_data["top1_score"] = top3[0].get("total_score", 0)
                result_data["top1_comment"] = top3[0].get("comment", "")
                result_data["n_schemes"] = len(state.get("schemes", {}))

        # 检索命中（B组）
        if HAS_KNOWLEDGE and precheck:
            result_data["precheck_assets"] = len(precheck.get("relevant_assets", []))
            result_data["precheck_warnings"] = len(precheck.get("discarded_warnings", []))
            if precheck["relevant_assets"]:
                result_data["top_relevance"] = precheck["relevant_assets"][0].get("relevance_score", 0)

        # 因果记忆查询（B组）
        if HAS_CAUSAL:
            try:
                cq = orch.query_causal_memory(task["tags"], top_k=3)
                result_data["causal_matches"] = len(cq)
                rw = orch.causal_risk_warning(task["desc"])
                result_data["risk_warnings"] = len(rw)
            except Exception:
                result_data["causal_matches"] = 0
                result_data["risk_warnings"] = 0

        # Agent 路由（B组）
        if HAS_AGENT_GRAPH and orch.agent_graph is not None:
            try:
                ag = orch.agent_graph.select_agents(task["tags"],
                    ["业务价值对象", "技术架构对象", "安全与合规对象"], top_k=3)
                result_data["agent_selection"] = ag
                # 记录到图
                orch.agent_graph.record_task(
                    task_id, task["desc"], ag, success=True, tags=task["tags"]
                )
                ag_stats = orch.agent_graph.get_stats()
                result_data["agent_graph_agents"] = ag_stats["n_agents"]
            except Exception:
                pass

        RESULTS["tasks"].append(result_data)

        # 打印摘要
        print(f"    耗时: {t_total:.1f}s | Token: {result_data.get('tokens', 'N/A')} | "
              f"成本: ¥{result_data.get('cost_rmb', 'N/A')}")
        if result_data.get("top1_score"):
            print(f"    Top1 评分: {result_data['top1_score']}")
        if result_data.get("precheck_assets"):
            print(f"    知识库命中: {result_data['precheck_assets']} 资产")
        if result_data.get("risk_warnings"):
            print(f"    风险预警: {result_data['risk_warnings']} 条")
        if result_data.get("agent_selection"):
            print(f"    Agent 路由: {result_data['agent_selection']}")

        # B 组：记录到因果图 + Agent 图
        if HAS_CAUSAL:
            try:
                orch.save_causal_data(
                    task_id=task_id,
                    decisions=[{"label": f"方案决策-{task['id']}",
                                "description": task["desc"][:100],
                                "tags": task["tags"], "outcome_score": 0.5}],
                )
            except Exception:
                pass

# ============================================================
# 汇总
# ============================================================
total_time = sum(t.get("time_s", 0) for t in RESULTS["tasks"])
total_tokens = sum(t.get("tokens", 0) for t in RESULTS["tasks"])
total_cost = sum(t.get("cost_rmb", 0) for t in RESULTS["tasks"])
avg_score = sum(t.get("top1_score", 0) for t in RESULTS["tasks"]) / max(len(RESULTS["tasks"]), 1)
total_precheck = sum(t.get("precheck_assets", 0) for t in RESULTS["tasks"])
total_warnings = sum(t.get("risk_warnings", 0) for t in RESULTS["tasks"])

RESULTS["summary"] = {
    "n_tasks": len(RESULTS["tasks"]),
    "total_time_s": round(total_time, 1),
    "avg_time_s": round(total_time / max(len(RESULTS["tasks"]), 1), 1),
    "total_tokens": total_tokens,
    "total_cost_rmb": round(total_cost, 6),
    "avg_score": round(avg_score, 2),
    "total_precheck_hits": total_precheck,
    "total_risk_warnings": total_warnings,
}

print(f"\n{'='*60}")
print(f"  {GROUP} 组汇总")
print(f"{'='*60}")
print(f"  任务数:       {RESULTS['summary']['n_tasks']}")
print(f"  总耗时:       {RESULTS['summary']['total_time_s']:.1f}s")
print(f"  平均耗时:     {RESULTS['summary']['avg_time_s']:.1f}s")
print(f"  总 Token:     {RESULTS['summary']['total_tokens']}")
print(f"  总成本:       ¥{RESULTS['summary']['total_cost_rmb']}")
print(f"  平均评分:     {RESULTS['summary']['avg_score']}")
if HAS_KNOWLEDGE:
    print(f"  知识库命中:   {RESULTS['summary']['total_precheck_hits']} 次")
    print(f"  风险预警:     {RESULTS['summary']['total_risk_warnings']} 次")

# 保存
out = f"benchmark_real_{GROUP}.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2)
print(f"\n  结果已保存: {out}")
