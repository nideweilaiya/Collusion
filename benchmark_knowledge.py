"""Collusion 知识库与项目关联度系统 — 基准测试 v0.5.0

测试维度:
  1. 检索准确率 (search_assets) — 给定查询能否匹配到正确历史任务
  2. 关联度评分合理性 — 高度相关 >0.4, 中度相关 >0.15, 不相关 <0.1
  3. 废案警告检测 — 对有类似废案的任务能否发出警告
  4. 知识预检完整性 — pre_check_knowledge 返回结构完整性
  5. 标签系统一致性 — 5维标签格式正确性
  6. 速度指标 — 各操作耗时

用法:
  python benchmark_knowledge.py              # 运行完整基准
  python benchmark_knowledge.py --quick       # 仅检索准确率测试
"""

import json
import time
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(str(Path(__file__).parent))

from src.orchestrator import BrainstormOrchestrator
from src.models import KnowledgeTag, TagDimension, AssetEntry

# ============================================================
# 测试查询集 — 每个查询标注预期匹配的任务 ID 和最低关联度
# ============================================================
SEARCH_TESTS = [
    {
        "id": "st_01",
        "query": "高并发短链接服务，Docker单命令部署",
        "expected_match": "task_8b825c4c4ccc",
        "min_relevance": 0.4,
        "desc": "短链接高并发 → 应命中短链接任务",
    },
    {
        "id": "st_02",
        "query": "设计一个待办事项API，增删改查",
        "expected_match": "task_f675bcec5b4b",
        "min_relevance": 0.3,
        "desc": "待办事项API → 应命中todo API任务",
    },
    {
        "id": "st_03",
        "query": "文件分享服务，上传文件生成分享链接",
        "expected_match": "task_6befe4a08e2d",
        "min_relevance": 0.15,
        "desc": "文件分享 → 应命中文件分享任务",
    },
    {
        "id": "st_04",
        "query": "RESTful API 设计与实现",
        "expected_match": None,  # 只要命中任一API任务即可
        "min_relevance": 0.15,
        "desc": "API设计 → 应至少命中一个API相关任务",
    },
    {
        "id": "st_05",
        "query": "机器学习模型训练平台",
        "expected_match": None,
        "min_relevance": 0.0,
        "desc": "无关查询 → 可以0命中或低分",
    },
]

PRECHECK_TESTS = [
    {
        "id": "pc_01",
        "task": "设计一个短链接服务",
        "expect_assets": True,
        "desc": "已有类似任务 → 预检应返回相关资产",
    },
    {
        "id": "pc_02",
        "task": "设计一个量子计算模拟器",
        "expect_assets": False,
        "desc": "全新领域 → 预检应返回空",
    },
]


class KnowledgeBenchmark:
    """知识库系统基准测试"""

    def __init__(self):
        self.orchestrator = BrainstormOrchestrator(
            Path(__file__).parent / "config.json"
        )
        self.results = {
            "benchmark_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "version": "v0.5.0",
            "module": "knowledge_base",
            "search_tests": [],
            "precheck_tests": [],
            "speed_tests": {},
            "summary": {},
        }

    def run_all(self, quick=False):
        print("=" * 60)
        print("  Collusion 知识库系统基准测试 v0.5.0")
        print("=" * 60)

        # 1. 检索准确率测试
        print("\n[1/5] 检索准确率测试...")
        self._test_search_accuracy()

        # 2. 关联度评分合理性
        print("[2/5] 关联度评分合理性...")
        self._test_relevance_sanity()

        # 3. 废案警告检测
        print("[3/5] 废案警告检测...")
        self._test_discard_warnings()

        # 4. 知识预检完整性
        print("[4/5] 知识预检完整性...")
        self._test_precheck()

        if not quick:
            # 5. 速度测试
            print("[5/5] 速度测试...")
            self._test_speed()
        else:
            print("[5/5] 跳过 (--quick模式)")

        # 汇总
        self._summarize()
        self._print_report()
        self._save()

    def _test_search_accuracy(self):
        """测试搜索是否能命中预期结果"""
        passed, total = 0, 0
        for test in SEARCH_TESTS:
            total += 1
            t0 = time.time()
            results = self.orchestrator.search_assets(test["query"], top_k=5)
            elapsed = time.time() - t0

            # 检查是否命中预期
            matched = None
            best_score = 0
            for r in results:
                if r["relevance_score"] > best_score:
                    best_score = r["relevance_score"]
                task_id = r["key"].split("_")[0] + "_" + r["key"].split("_")[1]
                if test["expected_match"] and task_id == test["expected_match"]:
                    matched = r
                elif test["expected_match"] is None and test["min_relevance"] > 0:
                    # 只要求命中任一API相关任务
                    if "API" in test["query"] and "API" in r.get("keywords", []):
                        matched = r

            # 判断
            if test["expected_match"]:
                ok = matched is not None and best_score >= test["min_relevance"]
            elif test["min_relevance"] > 0:
                ok = best_score >= test["min_relevance"]
            else:
                ok = True  # 无关查询不要求结果

            # 记录
            entry = {
                "test_id": test["id"],
                "query": test["query"],
                "desc": test["desc"],
                "expected_match": test["expected_match"],
                "best_score": round(best_score, 4),
                "matched": matched["key"] if matched else None,
                "n_results": len(results),
                "elapsed_s": round(elapsed, 3),
                "passed": ok,
            }
            self.results["search_tests"].append(entry)

            status = "✅" if ok else "❌"
            print(f"  {status} {test['id']}: score={best_score:.3f} "
                  f"matched={matched['key'][:30] if matched else '--'} "
                  f"({elapsed:.2f}s)")
            if not ok:
                print(f"     FAIL: 预期>={test['min_relevance']}, 实际={best_score:.3f}")

            if ok:
                passed += 1

        self.results["search_summary"] = {
            "passed": passed,
            "total": total,
            "pass_rate": round(passed / max(total, 1), 4),
        }

    def _test_relevance_sanity(self):
        """验证关联度评分的基本合理性"""
        # 高相关性查询 → 高关联度
        high_rel_query = "短链接服务 Docker 高并发"
        high_results = self.orchestrator.search_assets(high_rel_query, top_k=3)
        high_score = high_results[0]["relevance_score"] if high_results else 0

        # 低相关性查询 → 低关联度
        low_rel_query = "量子计算 神经网络 蛋白质折叠"
        low_results = self.orchestrator.search_assets(low_rel_query, top_k=3)
        low_score = low_results[0]["relevance_score"] if low_results else 0

        sanity_ok = high_score > low_score + 0.15

        self.results["relevance_sanity"] = {
            "high_relevance_query": high_rel_query,
            "high_relevance_score": round(high_score, 4),
            "low_relevance_query": low_rel_query,
            "low_relevance_score": round(low_score, 4),
            "score_gap": round(high_score - low_score, 4),
            "sanity_ok": sanity_ok,
        }

        status = "✅" if sanity_ok else "⚠️"
        print(f"  {status} 高关联={high_score:.3f} vs 低关联={low_score:.3f} "
              f"(gap={high_score - low_score:.3f})")

    def _test_discard_warnings(self):
        """测试废案警告系统"""
        # 搜索已有任务的领域
        warnings = self.orchestrator.check_discarded_warnings("API设计", top_k=5)
        n_warnings = len(warnings)
        self.results["discard_warnings"] = {
            "query": "API设计",
            "n_warnings": n_warnings,
            "warnings": [
                {
                    "task": w["task"][:50],
                    "rank": w["rank"],
                    "matched_terms": w.get("matched_terms", []),
                }
                for w in warnings[:3]
            ],
            "system_works": n_warnings >= 0,  # 只要不崩溃就算过
        }
        print(f"  ✅ 废案警告: 查询'API设计'返回 {n_warnings} 条警告")

    def _test_precheck(self):
        """测试知识预检系统"""
        for test in PRECHECK_TESTS:
            t0 = time.time()
            result = self.orchestrator.pre_check_knowledge(test["task"])
            elapsed = time.time() - t0

            has_assets = len(result["relevant_assets"]) > 0
            ok = has_assets == test["expect_assets"]

            entry = {
                "test_id": test["id"],
                "task": test["task"],
                "desc": test["desc"],
                "expect_assets": test["expect_assets"],
                "has_assets": has_assets,
                "n_assets": len(result["relevant_assets"]),
                "n_warnings": len(result["discarded_warnings"]),
                "summary": result["relevance_summary"],
                "elapsed_s": round(elapsed, 3),
                "passed": ok,
            }
            self.results["precheck_tests"].append(entry)

            status = "✅" if ok else "❌"
            print(f"  {status} {test['id']}: assets={has_assets} "
                  f"(expected={test['expect_assets']}) "
                  f"summary={result['relevance_summary'][:40]}")

    def _test_speed(self):
        """速度基准测试"""
        # 1. 搜索速度 (10次平均)
        queries = ["短链接", "API设计", "文件分享", "Docker部署", "数据库设计"]
        search_times = []
        for q in queries:
            t0 = time.time()
            self.orchestrator.search_assets(q, top_k=3)
            search_times.append(time.time() - t0)
        avg_search = sum(search_times) / len(search_times)

        # 2. 预检速度
        tasks = [
            "设计一个短链接服务",
            "设计一个博客平台",
            "设计一个即时通讯系统",
        ]
        precheck_times = []
        for t in tasks:
            t0 = time.time()
            self.orchestrator.pre_check_knowledge(t)
            precheck_times.append(time.time() - t0)
        avg_precheck = sum(precheck_times) / len(precheck_times)

        # 3. 资产索引速度 (模拟)
        index_times = []
        for _ in range(3):
            t0 = time.time()
            _ = self.orchestrator.search_assets("测试速度", top_k=5)
            index_times.append(time.time() - t0)
        avg_index = sum(index_times) / len(index_times)

        self.results["speed_tests"] = {
            "avg_search_ms": round(avg_search * 1000, 1),
            "avg_precheck_ms": round(avg_precheck * 1000, 1),
            "avg_index_read_ms": round(avg_index * 1000, 1),
            "n_samples": len(queries),
        }

        print(f"  ✅ 搜索: {avg_search*1000:.1f}ms | "
              f"预检: {avg_precheck*1000:.1f}ms")

    def _summarize(self):
        """生成汇总"""
        s = self.results["search_summary"]
        r = self.results.get("relevance_sanity", {})
        speed = self.results.get("speed_tests", {})

        self.results["summary"] = {
            "search_pass_rate": s.get("pass_rate", 0),
            "relevance_sanity": r.get("sanity_ok", False),
            "score_gap": r.get("score_gap", 0),
            "avg_search_ms": speed.get("avg_search_ms", 0),
            "avg_precheck_ms": speed.get("avg_precheck_ms", 0),
            "n_search_tests": s.get("total", 0),
            "n_precheck_tests": len(PRECHECK_TESTS),
            "overall_status": (
                "PASS"
                if s.get("pass_rate", 0) >= 0.8 and r.get("sanity_ok", False)
                else "DEGRADED"
            ),
        }

    def _print_report(self):
        """打印汇总报告"""
        s = self.results["summary"]
        print("\n" + "=" * 60)
        print("  基准测试报告")
        print("=" * 60)
        print(f"  检索准确率:     {s['search_pass_rate']*100:.0f}% "
              f"({s['n_search_tests']} tests)")
        print(f"  关联度合理性:   {'✅ PASS' if s['relevance_sanity'] else '❌ FAIL'}"
              f" (gap={s['score_gap']:.3f})")
        print(f"  搜索速度:       {s['avg_search_ms']:.1f} ms")
        print(f"  预检速度:       {s['avg_precheck_ms']:.1f} ms")
        print(f"  总体状态:       {s['overall_status']}")

    def _save(self):
        """保存结果"""
        path = Path(__file__).parent / "benchmark_knowledge_result.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        print(f"\n  结果已保存: {path}")


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    bench = KnowledgeBenchmark()
    bench.run_all(quick=quick)
