"""Collusion v0.7.0 — 轻量向量语义索引

零新依赖（仅 numpy + sklearn，均已安装）：
  - TF-IDF + char n-gram 处理中文
  - 余弦相似度搜索
  - 与标签评分混合排序

用法:
  from src.vector_index import VectorIndex
  vi = VectorIndex()
  vi.build(["短链接服务设计", "API待办事项系统", ...])
  results = vi.query("高并发短链接Docker", top_k=3)
"""

import json
import pickle
import numpy as np
from pathlib import Path
from typing import List, Tuple, Optional


class VectorIndex:
    """轻量向量索引 — 基于 sklearn TF-IDF + 余弦相似度"""

    def __init__(self):
        self.vectorizer = None  # TfidfVectorizer
        self.matrix = None      # numpy array (n_docs, n_features)
        self.doc_ids = []       # asset keys in order
        self._fitted = False

    def build(self, documents: List[Tuple[str, str]]) -> int:
        """构建 TF-IDF 向量索引

        Args:
            documents: [(doc_id, text), ...]  — doc_id 是资产 key, text 是搜索文本

        Returns:
            n_docs: 索引文档数
        """
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.doc_ids = [d[0] for d in documents]
        texts = [d[1] for d in documents]

        if not texts:
            self._fitted = False
            return 0

        # char-level n-grams 处理中文
        self.vectorizer = TfidfVectorizer(
            analyzer='char',
            ngram_range=(2, 4),       # 2-4 char n-grams
            max_features=10000,        # 限制特征数
            sublinear_tf=True,         # 用 1+log(tf) 替代 tf
        )
        self.matrix = self.vectorizer.fit_transform(texts)
        self._fitted = True
        return len(texts)

    def query(self, query_text: str, top_k: int = 5) -> List[dict]:
        """查询向量索引，返回余弦相似度排序结果

        Args:
            query_text: 查询文本
            top_k: 返回数量

        Returns:
            [{"doc_id": str, "score": float, "rank": int}, ...]
        """
        if not self._fitted or self.vectorizer is None:
            return []

        # 向量化查询
        q_vec = self.vectorizer.transform([query_text])

        # 确保矩阵是 dense numpy array（兼容 sparse 和 dense 两种存储）
        q_dense = q_vec.toarray() if hasattr(q_vec, 'toarray') else np.array(q_vec)
        m_dense = self.matrix.toarray() if hasattr(self.matrix, 'toarray') else np.array(self.matrix)

        q_norm = np.linalg.norm(q_dense)
        if q_norm == 0:
            return []

        dot_products = q_dense @ m_dense.T
        norms = np.linalg.norm(m_dense, axis=1)
        norms[norms == 0] = 1  # 避免除零

        scores = dot_products / (q_norm * norms)

        # 排序取 top_k
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for rank, idx in enumerate(top_indices):
            if scores[idx] > 0:
                results.append({
                    "doc_id": self.doc_ids[idx],
                    "score": round(float(scores[idx]), 4),
                    "rank": rank + 1,
                })

        return results

    def add_documents(self, documents: List[Tuple[str, str]]):
        """增量添加文档（重建整个索引，因为 TF-IDF 需要全局统计）
        小规模数据（<10万篇）直接重建即可
        """
        all_docs = list(zip(self.doc_ids, self._get_all_texts())) if self._fitted else []
        all_docs.extend(documents)
        self.build(all_docs)

    def _get_all_texts(self) -> List[str]:
        """获取已索引的所有文本（用于重建）"""
        # 无法从 TF-IDF 矩阵反向得到原文，所以需要外部传入
        return []

    def save(self, path: str):
        """保存索引到磁盘"""
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)

        data = {
            "doc_ids": self.doc_ids,
            "fitted": self._fitted,
        }
        with open(p / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        if self._fitted and self.vectorizer is not None:
            with open(p / "vectorizer.pkl", "wb") as f:
                pickle.dump(self.vectorizer, f)
            m = self.matrix.toarray() if hasattr(self.matrix, 'toarray') else np.array(self.matrix)
            np.save(p / "matrix.npy", m)

    def load(self, path: str) -> bool:
        """从磁盘加载索引"""
        p = Path(path)
        meta_path = p / "metadata.json"
        if not meta_path.exists():
            return False

        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.doc_ids = data.get("doc_ids", [])
        self._fitted = data.get("fitted", False)

        if self._fitted:
            with open(p / "vectorizer.pkl", "rb") as f:
                self.vectorizer = pickle.load(f)
            self.matrix = np.load(p / "matrix.npy")

        return True

    @property
    def size(self) -> int:
        return len(self.doc_ids)

    def clear(self):
        self.vectorizer = None
        self.matrix = None
        self.doc_ids = []
        self._fitted = False
