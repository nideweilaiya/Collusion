"""Collusion v1.2 — 结构图谱双关联路由引擎

核心算法:
  给定起点文件(start_file)和终点描述(goal_description)，
  通过结构图谱计算中间需要经过的最小文件集合。

三层实现:
  1. AST 静态分析: 解析 Python import 调用图
  2. 标签收敛: 利用 collusion_check 做反向语义匹配
  3. 双关联重叠: 正向集合 ∩ 反向集合 = 最小文件集

参考:
  - ARISE: 多粒度程序图 API (arXiv:2605.03117, 2026)
  - CodexGraph: Neo4j 图数据库集成 (2025)
  - Trailmark: Python 调用图生成器
"""
import ast
import os
import json
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional


class CodeGraphAdapter:
    """CodeGraph SQLite 适配器 — 读取 tree-sitter AST 生成的代码图谱

    参考: https://github.com/colbymchenry/codegraph
    特点: 281+ 文件, 13K+ 节点, 26K+ 调用边, full-text search
    """

    def __init__(self, db_path: str):
        import sqlite3
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def get_files(self) -> List[str]:
        return [r[0] for r in self.conn.execute("SELECT path FROM files")]

    def expand_from_file(self, start_file: str, n_steps: int = 2) -> Set[str]:
        """从文件出发，沿调用边向下游扩展 N 步"""
        visited = set()
        # 标准化路径: 去掉 ./ \
        start = start_file.replace("\\", "/").lstrip("./")
        current = {start}
        for _ in range(n_steps):
            next_level = set()
            for f in current:
                if f in visited:
                    continue
                visited.add(f)
                # 找该文件所有节点 (路径包含匹配)
                nodes = [r[0] for r in self.conn.execute(
                    "SELECT id FROM nodes WHERE file_path LIKE ?", (f"%{f}%",)
                )]
                for nid in nodes:
                    # 找所有目标
                    targets = [r[0] for r in self.conn.execute(
                        "SELECT target FROM edges WHERE source = ?", (nid,)
                    )]
                    for tid in targets:
                        target_files = [r[0] for r in self.conn.execute(
                            "SELECT file_path FROM nodes WHERE id = ?", (tid,)
                        )]
                        next_level.update(tf.replace("\\\\", "/") for tf in target_files)
            current = next_level - visited
        visited |= current
        return visited

    def search_symbols(self, query: str, limit: int = 50) -> List[dict]:
        """全文搜索符号名"""
        rows = self.conn.execute(
            "SELECT name, kind, file_path, start_line FROM nodes "
            "WHERE name LIKE ? OR qualified_name LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def search_files_by_name(self, query: str) -> Set[str]:
        """按文件名或路径搜索"""
        found = set()
        for r in self.conn.execute(
            "SELECT path FROM files WHERE path LIKE ?", (f"%{query}%",)
        ):
            found.add(r[0])
        return found

    def get_stats(self) -> dict:
        n_files = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        n_nodes = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        n_edges = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        return {"n_files": n_files, "n_nodes": n_nodes, "n_edges": n_edges}

    def close(self):
        self.conn.close()


class StructureGraph:
    """项目结构图谱 — AST 静态分析 + 标签关联"""

    def __init__(self, project_root: str):
        self.root = Path(project_root)
        self._graph: Dict[str, Set[str]] = {}  # file → {imported files}
        self._symbols: Dict[str, List[str]] = {}  # file → [defined symbols]
        self._built = False

    def build(self) -> int:
        """构建项目调用图"""
        py_files = list(self.root.rglob("*.py"))
        for f in py_files:
            if '__pycache__' in str(f) or '.git' in str(f):
                continue
            rel = str(f.relative_to(self.root)).replace("\\", "/")
            self._graph.setdefault(rel, set())
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                # 提取 import 关系
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imported = alias.name.replace(".", "/") + ".py"
                            self._graph[rel].add(imported)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imported = node.module.replace(".", "/") + ".py"
                            self._graph[rel].add(imported)
                    # 提取定义的符号
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        self._symbols.setdefault(rel, []).append(node.name)
            except Exception:
                continue
        self._built = True
        return len(self._graph)

    def expand_forward(self, start_file: str, n_steps: int = 2) -> Set[str]:
        """正向扩展: 从起点沿调用链下游走 N 步"""
        visited = set()
        current = {start_file}
        for _ in range(n_steps):
            next_level = set()
            for f in current:
                if f not in visited:
                    visited.add(f)
                    deps = self._graph.get(f, set())
                    for d in deps:
                        # 找真实存在的文件
                        if d in self._graph:
                            next_level.add(d)
                        else:
                            # 尝试模糊匹配
                            for g in self._graph:
                                if d.split("/")[-1] in g:
                                    next_level.add(g)
                                    break
            current = next_level - visited
        visited |= current
        return visited

    def find_by_tag(self, tag: str) -> Set[str]:
        """按标签查找文件"""
        found = set()
        tag_lower = tag.lower()
        # 在符号名中搜索
        for f, syms in self._symbols.items():
            for s in syms:
                if tag_lower in s.lower():
                    found.add(f)
        # 在文件名中搜索
        for f in self._graph:
            if tag_lower in f.lower():
                found.add(f)
        return found

    def shortest_path(self, start: str, end: str) -> int:
        """计算两个文件之间的最短调用距离 (BFS)"""
        if start == end:
            return 0
        if start not in self._graph:
            return -1

        visited = {start}
        queue = [(start, 0)]
        while queue:
            current, dist = queue.pop(0)
            for neighbor in self._graph.get(current, set()):
                if neighbor == end:
                    return dist + 1
                if neighbor not in visited:
                    # 模糊匹配
                    visited.add(neighbor)
                    if neighbor in self._graph:
                        queue.append((neighbor, dist + 1))
        return -1

    def get_stats(self) -> dict:
        return {
            "n_files": len(self._graph),
            "n_symbols": sum(len(s) for s in self._symbols.values()),
            "n_edges": sum(len(deps) for deps in self._graph.values()),
        }


def _find_codegraph_db(project_root: str) -> Optional[str]:
    """自动发现 CodeGraph 数据库"""
    import os, glob as _glob
    candidates = [
        os.path.join(project_root, ".codegraph", "codegraph.db"),
        os.path.join(project_root, ".codegraph.db"),
        os.path.join(os.path.dirname(project_root), ".codegraph", "codegraph.db"),
    ]
    # 也搜索环境变量和用户目录
    candidates.append("D:/Reasonix/.codegraph/codegraph.db")
    for p in candidates:
        if os.path.isfile(p):
            return p
    # 全局搜索
    for p in _glob.glob("D:/**/.codegraph/codegraph.db", recursive=True):
        return p
    return None


def route(start_file: str, goal_description: str,
          project_root: str, orchestrator=None) -> dict:
    """双关联路由 — collusion_route 核心函数

    Args:
        start_file: 起点文件路径 (相对路径, 如 src/orchestrator.py)
        goal_description: 终点需求描述
        project_root: 项目根目录
        orchestrator: BrainstormOrchestrator 实例 (用于标签/语义匹配)

    Returns:
        {"files": [...], "layer": 1-5, "explanation": "..."}
    """
    # Step 0: 检测 CodeGraph 数据库
    cg_db = _find_codegraph_db(project_root)
    cg = None
    if cg_db:
        try:
            cg = CodeGraphAdapter(cg_db)
        except Exception:
            pass

    # Step 1: 正向扩展 — 三层 fallback
    forward = set()
    sg = None  # 延迟初始化，供 Layer 2/3 共用
    try:
        if cg:
            forward = cg.expand_from_file(start_file, n_steps=2)
        else:
            sg = StructureGraph(project_root)
            sg.build()
            forward = sg.expand_forward(start_file, n_steps=2)
    except Exception:
        # 第三层 fallback：内置 AST 解析（已初始化但未 build，补 build）
        sg = StructureGraph(project_root)
        sg.build()
        forward = set()  # AST 解析也失败时降级为空集，后续靠反向收敛保底

    # Step 2: 反向收敛
    backward = set()
    if cg:
        # 用 CodeGraph 全文搜索
        for word in goal_description.lower().split():
            if len(word) >= 3:
                backward |= cg.search_files_by_name(word)
                for sym in cg.search_symbols(word, limit=10):
                    backward.add(sym["file_path"])
    else:
        sg = StructureGraph(project_root)
        sg.build()
        backward = set()
        for word in goal_description.lower().split():
            backward |= sg.find_by_tag(word)

    # 同时检查 Collusion 资产库
    tags = []
    if orchestrator:
        try:
            precheck = orchestrator.pre_check_knowledge(goal_description)
            for asset in precheck.get("relevant_assets", [])[:3]:
                for t in asset.get("tags", []):
                    if isinstance(t, dict):
                        tags.append(t.get("value", ""))
                    elif isinstance(t, str):
                        tags.append(t)
        except Exception:
            pass
    for tag in tags[:10]:
        if cg:
            backward |= cg.search_files_by_name(tag)
            for sym in cg.search_symbols(tag, limit=5):
                backward.add(sym["file_path"])
        else:
            backward |= sg.find_by_tag(tag)

    # Step 3: Layer 1 — 双关联重叠
    overlap = forward & backward

    if cg:
        cg_stats = cg.get_stats()
        graph_stats = cg_stats
        engine_info = f"CodeGraph({cg_stats['n_nodes']} nodes, {cg_stats['n_edges']} edges)"
    else:
        graph_stats = sg.get_stats() if sg else {}
        engine_info = "AST"
    result = {
        "project_root": project_root,
        "start_file": start_file,
        "goal": goal_description[:100],
        "engine": engine_info,
        "graph_stats": graph_stats,
        "forward_files": sorted(forward)[:20],
        "backward_files": sorted(backward)[:20],
    }

    if len(overlap) >= 2:
        result["files"] = sorted(overlap)[:10]
        result["layer"] = 1
        result["explanation"] = f"双关联重叠: {len(overlap)} 个文件 (正向∩反向)"
        return result

    # Layer 2: 最短路径
    best_pair, best_dist = None, 999
    for f in forward:
        for b in backward:
            d = sg.shortest_path(f, b)
            if 0 <= d < best_dist:
                best_dist = d
                best_pair = (f, b)
    if best_pair and best_dist <= 5:
        result["files"] = [best_pair[0], best_pair[1]]
        result["layer"] = 2
        result["explanation"] = f"最短路径: {best_pair[0]} → {best_pair[1]} (距离={best_dist})"
        return result

    # Layer 3: 标签关联 (只用反向扩展)
    if backward:
        result["files"] = sorted(backward)[:10]
        result["layer"] = 3
        result["explanation"] = f"标签关联: {len(backward)} 个匹配文件 (无直接调用关系)"
        return result

    # Layer 4: 语义检索
    if orchestrator:
        try:
            assets = orchestrator.search_assets(goal_description, top_k=5)
            sem_files = set()
            for a in assets:
                keywords = a.get("keywords", [])
                for kw in keywords[:3]:
                    sem_files |= sg.find_by_tag(kw)
            if sem_files:
                result["files"] = sorted(sem_files)[:10]
                result["layer"] = 4
                result["explanation"] = f"语义检索: {len(sem_files)} 个关联文件"
                return result
        except Exception:
            pass

    # Layer 5: Agent 自主判断 (返回所有候选取并集)
    all_candidates = forward | backward
    result["files"] = sorted(all_candidates)[:15]
    result["layer"] = 5
    result["explanation"] = f"Agent自主判断: 返回 {len(all_candidates)} 个候选文件 (正向∪反向)"
    return result
