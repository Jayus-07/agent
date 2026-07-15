"""BM25 持久化存储 -- 磁盘索引避免每次启动重建

存储格式:
  data/bm25/
  ├── corpus.pkl       # CountVectorizer 实例（pickle）
  ├── docs.pkl         # Document 对象列表（pickle）
  └── meta.json        # 元数据（doc_count, build_time_s, built_at, version）
"""

from __future__ import annotations

import json
import os
import pickle
import time
from pathlib import Path
from typing import List, Optional

from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from backend.config import BM25_SEARCH_K

from backend.config import BM25_INDEX_DIR
from backend.utils.logger import logger


class BM25Store:
    """磁盘持久化 BM25 索引。

    使用示例:
        store = BM25Store()
        retriever = store.load()
        if retriever is None:
            retriever = store.build(docs)
    """

    def __init__(self, index_dir: Optional[str] = None):
        self.index_dir = Path(index_dir or BM25_INDEX_DIR)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.index_dir / "meta.json"
        self._corpus_path = self.index_dir / "corpus.pkl"
        self._docs_path = self.index_dir / "docs.pkl"

    # ── 公共方法 ──────────────────────────────────────

    def build(
        self, docs: List[Document], k: int = None
    ) -> Optional[BM25Retriever]:
        """构建并持久化 BM25 索引。

        Args:
            docs: Document 对象列表
            k: 检索返回数量，默认取 config.BM25_SEARCH_K

        Returns:
            可直接使用的 BM25Retriever 实例；文档为空时返回 None
        """
        if k is None:
            k = BM25_SEARCH_K
        logger.info(f"[BM25Store] 构建索引，{len(docs)} 个文档...")
        t0 = time.time()

        # 空文档列表：BM25Retriever.from_documents([]) 会抛异常
        if not docs:
            elapsed = time.time() - t0
            self._write_meta(0, elapsed)
            # 清理旧的持久化文件
            for p in (self._corpus_path, self._docs_path):
                if p.exists():
                    p.unlink()
            logger.info("[BM25Store] 空文档列表，跳过索引构建")
            return None

        retriever = BM25Retriever.from_documents(docs, k=k)

        # 持久化 CountVectorizer（已拟合）
        with open(self._corpus_path, "wb") as f:
            pickle.dump(retriever.vectorizer, f)
        # 持久化原始 Document 列表
        with open(self._docs_path, "wb") as f:
            pickle.dump(docs, f)

        elapsed = time.time() - t0
        self._write_meta(len(docs), elapsed)
        logger.info(
            f"[BM25Store] 索引构建完成: {len(docs)} 文档, {elapsed:.1f}s"
        )
        return retriever

    def load(self, k: int = None) -> Optional[BM25Retriever]:
        """从磁盘加载 BM25 索引。

        Args:
            k: 检索返回数量，默认取 config.BM25_SEARCH_K

        Returns:
            BM25Retriever 实例，索引不存在或损坏时返回 None
        """
        if k is None:
            k = BM25_SEARCH_K
        if not self._corpus_path.exists() or not self._docs_path.exists():
            logger.info("[BM25Store] 索引文件不存在，需要重建")
            return None

        try:
            with open(self._corpus_path, "rb") as f:
                vectorizer = pickle.load(f)
            with open(self._docs_path, "rb") as f:
                docs = pickle.load(f)

            # 直接构造 BM25Retriever，跳过 from_documents 的拟合步骤
            retriever = BM25Retriever(
                vectorizer=vectorizer,
                docs=docs,
                k=k,
            )

            meta = self._read_meta()
            logger.info(
                f"[BM25Store] 索引加载成功: {meta.get('doc_count', '?')} 文档, "
                f"构建于 {meta.get('built_at', '?')}"
            )
            return retriever
        except Exception as e:
            logger.warning(f"[BM25Store] 索引加载失败: {e}，将重建")
            return None

    def add_documents(
        self, docs: List[Document], k: int = 20
    ) -> BM25Retriever:
        """增量添加文档后全量重建索引。

        BM25 的 IDF 依赖全量文档统计，增量添加必须全量重建以保持 IDF 准确。

        Args:
            docs: 要添加的 Document 列表
            k: 检索返回数量

        Returns:
            重建后的 BM25Retriever 实例
        """
        all_docs: List[Document] = []
        if self._docs_path.exists():
            try:
                with open(self._docs_path, "rb") as f:
                    all_docs = pickle.load(f)
            except Exception:
                logger.warning("[BM25Store] 读取已有文档失败，将全量重建")
                all_docs = []

        all_docs.extend(docs)
        logger.info(
            f"[BM25Store] 增量添加 {len(docs)} 文档，"
            f"总计 {len(all_docs)}，全量重建..."
        )
        return self.build(all_docs, k=k)

    def remove_documents(
        self, doc_ids: List[str], k: int = 20
    ) -> Optional[BM25Retriever]:
        """按 doc_id 删除文档后全量重建索引。

        Args:
            doc_ids: 要删除的 doc_id 列表
            k: 检索返回数量

        Returns:
            重建后的 BM25Retriever 实例；无索引时返回 None
        """
        if not self._docs_path.exists():
            logger.info("[BM25Store] 索引不存在，跳过删除")
            return None

        try:
            with open(self._docs_path, "rb") as f:
                all_docs: List[Document] = pickle.load(f)
        except Exception:
            logger.warning("[BM25Store] 读取已有文档失败，跳过删除")
            return None

        remaining = [
            d for d in all_docs
            if d.metadata.get("doc_id") not in doc_ids
        ]

        removed = len(all_docs) - len(remaining)
        if removed > 0:
            logger.info(
                f"[BM25Store] 删除 {removed} 个文档，"
                f"剩余 {len(remaining)}，全量重建..."
            )
            return self.build(remaining, k=k)
        else:
            logger.info("[BM25Store] 未匹配到需要删除的文档")
            return self.load(k=k)

    @property
    def is_stale(self) -> bool:
        """检查索引是否过期（文档数为 0 视为过期）。"""
        if not self._meta_path.exists():
            return True
        meta = self._read_meta()
        return meta.get("doc_count", 0) == 0

    def doc_count(self) -> int:
        """返回已持久化的文档数量。"""
        meta = self._read_meta()
        return meta.get("doc_count", 0)

    # ── 内部方法 ──────────────────────────────────────

    def _write_meta(self, doc_count: int, build_time_s: float) -> None:
        """写入元数据 JSON 文件。"""
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "doc_count": doc_count,
                "build_time_s": round(build_time_s, 1),
                "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "version": 1,
            }, f, ensure_ascii=False, indent=2)

    def _read_meta(self) -> dict:
        """读取元数据 JSON 文件。"""
        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
