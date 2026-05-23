"""v0.5.0 知识库系统专项测试 — 因果记忆图 Prism"""
import json
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import patch
from src.orchestrator import BrainstormOrchestrator
from tests.conftest import MockLLMAdapter, DECOMPOSE_RESPONSE, PROPOSAL_RESPONSE

# 模拟因果记忆提取的 LLM 响应
CAUSAL_EXTRACTION_RESPONSE = {
    "decisions": [
        {"label": "选择Go语言", "description": "使用Go 1.21+实现短链接服务",
         "tags": ["Go", "后端开发"], "outcome_score": 0.8},
        {"label": "使用Redis缓存", "description": "Redis作为热数据缓存层",
         "tags": ["Redis", "缓存"], "outcome_score": 0.7},
        {"label": "Docker单命令部署", "description": "docker-compose up -d 一键启动",
         "tags": ["Docker", "部署"], "outcome_score": 0.9},
    ],
    "constraints": [
        {"label": "Docker单命令部署", "description": "必须支持一键部署",
         "tags": ["Docker", "部署"]},
    ],
    "outcomes": [
        {"label": "镜像体积小", "description": "Go编译为静态二进制，镜像<20MB",
         "tags": ["性能", "部署"], "score": 0.9},
        {"label": "运维复杂度低", "description": "单容器运行，无外部依赖",
         "tags": ["运维"], "score": 0.8},
    ],
    "risks": [
        {"label": "单机瓶颈", "description": "单实例在高并发下扩展受限",
         "tags": ["扩展性"], "severity": "中"},
    ],
}


class TestCausalMemoryPhase:
    """Phase 6.6 因果记忆图记录 + 查询 + 风险预警 全链路测试"""

    def test_causal_memory_recorded_directly(self, tmp_path, monkeypatch):
        """验证 _record_causal_memory() 直接调用能正确记录因果图"""
        monkeypatch.chdir(tmp_path)
        # 只需要因果提取的响应，创建器不消耗
        mock_llm = MockLLMAdapter([CAUSAL_EXTRACTION_RESPONSE])

        with patch.object(BrainstormOrchestrator, '_create_adapter') as m:
            m.return_value = mock_llm
            orch = BrainstormOrchestrator()
        orch.fast_llm = mock_llm
        orch.knowledge_config["enable_causal_memory"] = True

        from src.models import OrchestratorState
        state = OrchestratorState(
            task_id="test_prism_001",
            original_task="设计一个短链接服务，支持Docker部署",
        )
        state.schemes = {
            "plan_A": {"integrated_content": (
                "选择Go语言实现短链接服务，使用Redis做缓存，PostgreSQL持久化。"
                "采用Docker单命令部署，Go编译为静态二进制，镜像<20MB。"
                "风险：单机部署可能成为瓶颈。需要支持每秒10万+短链生成、自定义短码、"
                "过期清理、访问统计等核心功能。采用前后端分离架构，RESTful API设计。"
                "Go的goroutine模型天然适合IO密集型场景，每个请求仅消耗约2KB内存。"
                "Redis使用Cluster模式部署，支持自动分片和故障转移。"
                "PostgreSQL使用主从复制，写主读从，可扩展。"
            ), "object_name": "技术架构对象", "agent_role": "性能架构师"},
        }
        state.vote_results = [
            {"plan_id": "plan_A", "rank": 1, "total_score": 8.5},
        ]

        # 直接调用因果记录（不调 LLM，只创建待分析文件）
        orch._record_causal_memory(state)

        # 验证待分析文件已创建
        pending_file = orch.data_dir / "causal_memory" / f"pending_{state.task_id}.json"
        assert pending_file.exists(), f"待分析文件未创建: {pending_file}"

        # 模拟 Reasonix 写入因果数据
        result = orch.save_causal_data(
            task_id=state.task_id,
            decisions=[{'label':'选择Go','description':'Go 1.21+','tags':['Go'],'outcome_score':0.8}],
            constraints=[{'label':'Docker部署','description':'必须支持','tags':['Docker']}],
            outcomes=[{'label':'镜像小','description':'<20MB','tags':['性能'],'score':0.9}],
            risks=[{'label':'单机瓶颈','description':'高并发受限','tags':['扩展性'],'severity':'中'}],
        )
        assert result['saved'] >= 4, f"应保存≥4个节点，实际: {result}"

        # 验证因果图文件已创建
        graph_path = orch.data_dir / "causal_memory" / "graph.json"
        assert graph_path.exists(), f"因果图文件未创建: {graph_path}"

        with open(graph_path, "r", encoding="utf-8") as f:
            graph = json.load(f)

        assert len(graph["nodes"]) >= 4, f"因果图应≥4个节点，实际: {len(graph['nodes'])}"
        node_types = [n["node_type"] for n in graph["nodes"].values()]
        assert "decision" in node_types, f"缺少决策节点，只有: {node_types}"
        assert "outcome" in node_types, f"缺少结果节点"
        assert "risk" in node_types, f"缺少风险节点"
        print(f"  ✅ 因果记录: {len(graph['nodes'])} 节点, {len(graph['edges'])} 边")
        for nid, n in graph["nodes"].items():
            print(f"     {n['node_type']}: {n['label']}")

    def test_query_causal_memory(self, tmp_path, monkeypatch):
        """验证因果记忆查询功能"""
        monkeypatch.chdir(tmp_path)
        orch = BrainstormOrchestrator()

        # 手动写入因果图
        graph = {
            "nodes": {
                "dec_1": {"id": "dec_1", "node_type": "decision",
                          "label": "选择Go语言", "tags": ["Go", "后端"],
                          "description": "使用Go 1.21+",
                          "task_id": "task_1", "outcome_score": 0.8},
                "dec_2": {"id": "dec_2", "node_type": "decision",
                          "label": "使用Redis缓存", "tags": ["Redis", "缓存"],
                          "description": "Redis作为缓存层",
                          "task_id": "task_1", "outcome_score": 0.7},
                "out_1": {"id": "out_1", "node_type": "outcome",
                          "label": "镜像体积小", "tags": ["性能"],
                          "description": "镜像<20MB",
                          "task_id": "task_1", "outcome_score": 0.9},
                "risk_1": {"id": "risk_1", "node_type": "risk",
                           "label": "单机瓶颈", "tags": ["扩展性"],
                           "description": "高并发下受限",
                           "task_id": "task_1", "severity": "中"},
            },
            "edges": [
                {"source_id": "dec_1", "target_id": "out_1",
                 "relation": "leads_to", "task_id": "task_1"},
            ],
            "version": "0.5.0",
        }
        graph_path = orch.data_dir / "causal_memory"
        graph_path.mkdir(parents=True, exist_ok=True)
        with open(graph_path / "graph.json", "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)

        # 测试查询 — 应匹配 Go/Redis 决策
        results = orch.query_causal_memory(["Go", "短链接", "后端"], top_k=5)
        assert len(results) >= 1, f"查询应返回结果，实际: {len(results)}"
        labels = [r["label"] for r in results]
        assert "选择Go语言" in labels, f"应找到Go决策，实际: {labels}"
        print(f"  ✅ 因果查询: 找到 {len(results)} 个匹配节点")
        for r in results[:3]:
            print(f"     [{r['relevance']:.3f}] {r['node_type']}: {r['label']}")

        # 测试风险预警
        warnings = orch.causal_risk_warning("设计一个高并发短链接服务")
        assert len(warnings) >= 1, f"应返回风险预警，实际: {len(warnings)}"
        risk_labels = [w["risk"] for w in warnings]
        assert any("单机瓶颈" in l for l in risk_labels), f"应包含单机瓶颈风险: {risk_labels}"
        print(f"  ✅ 风险预警: {len(warnings)} 条")
        for w in warnings[:2]:
            print(f"     ⚠️ {w['risk']} (severity={w['severity']}, rel={w['relevance']:.3f})")

    def test_causal_memory_empty_graph_graceful(self):
        """空因果图应优雅降级"""
        orch = BrainstormOrchestrator()
        results = orch.query_causal_memory(["Python", "API"], top_k=3)
        assert results == []
        warnings = orch.causal_risk_warning("设计一个API")
        assert warnings == []
        print("  ✅ 空因果图: 优雅降级 (返回空列表)")
