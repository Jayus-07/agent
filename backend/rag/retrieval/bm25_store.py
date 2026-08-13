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
from backend.shared.logger import logger


def source_files_out_of_sync(indexed_docs: list, current_docs: list) -> bool:
    """判断 BM25 索引文档集合与当前文档集合是否一致。

    is_stale 只检查 doc_count==0，检测不到「文档已删除但索引残留」。
    这里比较 source_file 集合，索引有残留（已删文档）或缺失（新文档）都判为需重建。
    """
    indexed = {d.metadata.get("source_file", "") for d in indexed_docs}
    current = {d.metadata.get("source_file", "") for d in current_docs}
    return indexed != current


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

        # 持久化 CountVectorizer（已拟合）+ SHA256 校验
        corpus_data = pickle.dumps(retriever.vectorizer)
        with open(self._corpus_path, "wb") as f:
            f.write(corpus_data)
        self._write_checksum(self._corpus_path, corpus_data)

        # 持久化原始 Document 列表 + SHA256 校验
        docs_data = pickle.dumps(docs)
        with open(self._docs_path, "wb") as f:
            f.write(docs_data)
        self._write_checksum(self._docs_path, docs_data)

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
            vectorizer = self._safe_load_pickle(self._corpus_path)
            docs = self._safe_load_pickle(self._docs_path)
            if vectorizer is None or docs is None:
                return None

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
                loaded = self._safe_load_pickle(self._docs_path)
                all_docs = loaded if loaded is not None else []
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
            loaded = self._safe_load_pickle(self._docs_path)
            all_docs = loaded if loaded is not None else []
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

    def _checksum_path(self, data_path: Path) -> Path:
        """返回 SHA256 校验文件路径。"""
        return Path(str(data_path) + ".sha256")

    def _write_checksum(self, data_path: Path, data: bytes) -> None:
        """写入 SHA256 校验文件。"""
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        chk_path = self._checksum_path(data_path)
        with open(chk_path, "w", encoding="utf-8") as f:
            f.write(digest)

    def _safe_load_pickle(self, data_path: Path) -> Any | None:
        """安全反序列化 pickle 文件：先校验 SHA256 签名再 unpickle。

        防止缓存目录被篡改时的任意代码执行。
        校验失败返回 None，调用方需处理重建逻辑。
        """
        import hashlib
        chk_path = self._checksum_path(data_path)

        # 读数据
        with open(data_path, "rb") as f:
            data = f.read()

        # 校验 SHA256（校验文件不存在时容忍，兼容旧索引）
        if chk_path.exists():
            with open(chk_path, "r", encoding="utf-8") as f:
                expected = f.read().strip()
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                logger.warning(
                    "[BM25Store] SHA256 校验失败: %s (expected %s, got %s)",
                    data_path.name, expected[:16], actual[:16]
                )
                return None

        return pickle.loads(data)
