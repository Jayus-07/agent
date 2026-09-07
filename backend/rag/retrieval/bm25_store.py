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
from typing import Any, List, Optional

from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from backend.config import BM25_SEARCH_K

from backend.config import BM25_INDEX_DIR
from backend.shared.logger import logger


def _doc_matches(
    doc: Document,
    doc_id_set: set[str],
    file_basenames: set[str],
) -> bool:
    """判断 Document 是否匹配给定的 doc_id 集合或文件名集合。

    双键匹配（doc_id + source_file/file_path basename），用于 remove/replace 操作。
    """
    meta = doc.metadata or {}
    if meta.get("doc_id") in doc_id_set:
        return True
    if file_basenames:
        src = meta.get("source_file", "")
        fp = meta.get("file_path", "")
        if os.path.basename(src) in file_basenames or os.path.basename(fp) in file_basenames:
            return True
    return False


def source_files_out_of_sync(indexed_docs: list, current_docs: list) -> bool:
    """判断 BM25 索引文档集合与当前文档集合是否一致。

    比较 {source_file: chunk_count} 字典：文件集合不一致或同一文件的 chunk 数量
    不一致都判为需重建（后者检测文档修改后旧 chunk 残留导致的计数漂移）。
    """
    indexed: dict[str, int] = {}
    for d in indexed_docs:
        sf = d.metadata.get("source_file", "")
        indexed[sf] = indexed.get(sf, 0) + 1
    current: dict[str, int] = {}
    for d in current_docs:
        sf = d.metadata.get("source_file", "")
        current[sf] = current.get(sf, 0) + 1
    return indexed != current


def compute_content_hash(docs: list) -> str:
    """计算文档列表的内容摘要 hash（用于检测内容漂移）。"""
    import hashlib
    h = hashlib.sha256()
    for d in docs:
        h.update(d.page_content.encode("utf-8", errors="replace"))
        h.update(d.metadata.get("source_file", "").encode("utf-8"))
    return h.hexdigest()[:16]


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
        content_hash = compute_content_hash(docs)
        self._write_meta(len(docs), elapsed, content_hash=content_hash)
        logger.info(
            f"[BM25Store] 索引构建完成: {len(docs)} 文档, {elapsed:.1f}s, hash={content_hash}"
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
        self, doc_ids: List[str], k: int = 20, file_paths: Optional[List[str]] = None
    ) -> Optional[BM25Retriever]:
        """按 doc_id 或 file_path（含 source_file 文件名）删除文档后全量重建索引。

        doc_id 与 file_path 双键过滤（P0-2）：历史上 loader 与 indexer 的 doc_id 协议
        不一致（loader=sha256[:16] vs registry=md5[:10]），仅按 doc_id 过滤会导致
        删除后 BM25 残留。BM25 的 chunk metadata 始终含 file_path/source_file，
        以文件名为第二键可绕开协议分裂，保证删除真正命中。

        Args:
            doc_ids: 要删除的 doc_id 列表
            file_paths: 要删除的完整文件路径列表（取其 basename 与 metadata.source_file 匹配）
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

        doc_id_set = set(doc_ids or [])
        file_basenames = {os.path.basename(fp) for fp in (file_paths or [])}

        remaining = [d for d in all_docs if not _doc_matches(d, doc_id_set, file_basenames)]

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

    def replace_documents(
        self,
        docs: List[Document],
        k: int = 20,
        *,
        doc_id: str = "",
        file_path: str = "",
    ) -> Optional[BM25Retriever]:
        """替换指定文档的 chunks 后全量重建索引。

        先移除该 doc_id / file_path 对应的旧 entries，再追加新 entries，
        单次 build() 完成（避免 remove + add 两次重建）。

        解决 P0-1/P0-3：旧 add_documents 只追加不清理，导致文档修改后
        BM25 残留旧 chunk，计数漂移（339 chunks vs 343 BM25）。

        Args:
            docs: 新的 Document 列表（替换后的完整 chunks）
            k: 检索返回数量
            doc_id: 要替换的 doc_id
            file_path: 要替换的文件路径（basename 匹配）

        Returns:
            重建后的 BM25Retriever 实例；无索引且无新文档时返回 None
        """
        doc_id_set = {doc_id} if doc_id else set()
        file_basenames = {os.path.basename(file_path)} if file_path else set()

        existing: List[Document] = []
        if self._docs_path.exists():
            try:
                loaded = self._safe_load_pickle(self._docs_path)
                existing = loaded if loaded is not None else []
            except Exception:
                logger.warning("[BM25Store] 读取已有文档失败，将全量重建")
                existing = []

        remaining = [
            d for d in existing
            if not _doc_matches(d, doc_id_set, file_basenames)
        ]
        removed = len(existing) - len(remaining)
        remaining.extend(docs)

        logger.info(
            f"[BM25Store] 替换文档 doc_id={doc_id!r}: "
            f"移除 {removed} 旧 chunks，新增 {len(docs)}，"
            f"总计 {len(remaining)}，全量重建..."
        )
        return self.build(remaining, k=k)

    def load_docs(self) -> List[Document]:
        """从磁盘加载持久化的 Document 列表（供一致性检查等外部消费者使用）。

        Returns:
            Document 列表；索引不存在或损坏时返回空列表
        """
        if not self._docs_path.exists():
            return []
        try:
            loaded = self._safe_load_pickle(self._docs_path)
            return loaded if loaded is not None else []
        except Exception:
            logger.warning("[BM25Store] load_docs 加载失败")
            return []

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

    def get_content_hash(self) -> str:
        """返回已持久化的内容 hash（空字符串表示无记录）。"""
        return self._read_meta().get("content_hash", "")

    # ── 内部方法 ──────────────────────────────────────

    def _write_meta(self, doc_count: int, build_time_s: float, content_hash: str = "") -> None:
        """写入元数据 JSON 文件。"""
        meta = {
            "doc_count": doc_count,
            "build_time_s": round(build_time_s, 1),
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "version": 1,
        }
        if content_hash:
            meta["content_hash"] = content_hash
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

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
