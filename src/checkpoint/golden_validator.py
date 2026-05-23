"""压缩器黄金验证集评估工具

用途:
  1. CI/CD 中检测压缩器质量退化
  2. 切换底层模型后验证压缩质量
  3. 开发阶段快速验证压缩器输出

使用方法:
  from src.checkpoint.golden_validator import GoldenValidator
  validator = GoldenValidator(golden_path="data/golden/compression_scenarios.json")
  result = validator.evaluate(compressor)
  print(result["summary"])  # 召回率、精确度
"""

import json
from pathlib import Path
from typing import Dict, List, Optional


class GoldenValidator:
    """黄金验证集评估器"""

    def __init__(self, golden_path: str = None):
        if golden_path is None:
            golden_path = Path(__file__).parent.parent.parent / "data/golden/compression_scenarios.json"
        with open(golden_path, "r", encoding="utf-8") as f:
            self.scenarios = json.load(f)

    def evaluate(self, compressor, orchestrator=None) -> dict:
        """对每个场景执行压缩并计算召回率

        Args:
            compressor: SituationCompressor 实例
            orchestrator: BrainstormOrchestrator 实例（用于检索，可选）

        Returns:
            {"summary": str, "scenarios": [...], "overall_recall": float}
        """
        from src.models import RetrievedContext

        results = []
        total_recall = 0.0
        total_scenarios = 0

        for scenario in self.scenarios:
            # 构建 RetrievedContext
            retrieved = RetrievedContext(
                task_id=f"golden_{scenario['id']}",
                relevant_assets=scenario.get("retrieved_assets", []),
                discard_warnings=scenario.get("discard_warnings", []),
            )

            # 压缩
            snapshot = compressor.compress(
                task=scenario["task"],
                retrieved=retrieved,
            )

            # 校验
            checks = self._validate_snapshot(snapshot, scenario)
            recall = checks.get("recall", 0.0)

            results.append({
                "id": scenario["id"],
                "task": scenario["task"][:60],
                "snapshot_length": len(snapshot.to_prompt_fragment()),
                "budget_ok": len(snapshot.to_prompt_fragment()) <= 1250,
                "checks": checks,
            })

            total_recall += recall
            total_scenarios += 1

        overall_recall = total_recall / max(total_scenarios, 1)

        return {
            "summary": (
                f"评估 {total_scenarios} 场景 | "
                f"平均召回率: {overall_recall:.0%} | "
                f"预算合规: {sum(1 for r in results if r['budget_ok'])}/{total_scenarios}"
            ),
            "overall_recall": round(overall_recall, 4),
            "pass": overall_recall >= 0.7,
            "scenarios": results,
        }

    @staticmethod
    def _validate_snapshot(snapshot, scenario: dict) -> dict:
        """验证单场景的压缩质量"""
        expected_constraints = scenario.get("expected_constraints", [])
        expected_pitfalls = scenario.get("expected_pitfalls", [])
        min_recall = scenario.get("min_relevance_recall", 0.7)

        # 约束召回
        frag = snapshot.to_prompt_fragment()
        constraints_hit = sum(
            1 for c in expected_constraints
            if any(kw in frag for kw in c.split())
        )
        constraint_recall = (
            constraints_hit / len(expected_constraints)
            if expected_constraints else 1.0
        )

        # 坑点召回
        pitfalls_hit = sum(
            1 for p in expected_pitfalls
            if any(kw in frag for kw in p.split()[:3])
        )
        pitfall_recall = (
            pitfalls_hit / len(expected_pitfalls)
            if expected_pitfalls else 1.0
        )

        overall_recall = (constraint_recall * 0.6 + pitfall_recall * 0.4)
        budget_ok = len(frag) <= 1250

        return {
            "constraint_recall": round(constraint_recall, 2),
            "pitfall_recall": round(pitfall_recall, 2),
            "recall": round(overall_recall, 2),
            "budget_ok": budget_ok,
            "pass": overall_recall >= min_recall and budget_ok,
        }
