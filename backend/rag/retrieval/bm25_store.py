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
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, List, Optional

from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from backend.config import BM25_CANDIDATE_K

from backend.config import BM25_INDEX_DIR
from backend.shared.logger import logger

# 索引格式版本：分词器等影响倒排统计的变更需递增，load 时版本不符自动重建。
# v3（2026-10-01 C 阶段）：条目身份收口为 doc_id+vector_id，废除 basename
# 兜底匹配（跨 KB 同名文件互删的根源）——版本不符触发一次 canonical 重建。
BM25_META_VERSION = 3

# 同一进程内的读写互斥。跨进程读取使用单文件 bundle，避免看到
# corpus.pkl 与 docs.pkl 来自不同一代索引的中间状态。
_BUILD_LOCK = threading.RLock()


def _tokenize_chinese(text: str) -> List[str]:
    """BM25 中文分词（jieba）。

    langchain BM25Retriever 默认按空格/小写切分，对中文语料会退化成整句匹配；
    构建索引与查询两侧必须使用同一分词函数（load 时也需显式设置，
    preprocess_func 不会被 pickle 持久化）。
    """
    try:
        import jieba
    except ImportError:
        return [t for t in text.split() if t]
    return [t for t in jieba.lcut(text) if t.strip()]


def _doc_matches(
    doc: Document,
    doc_id_set: set[str],
    file_basenames: set[str],
) -> bool:
    """判断 Document 是否属于给定文档集合（C 阶段收口）。

    身份 = doc_id（vector_id 为补充精确键）。**basename 匹配已废除**：
    source_file 只有 basename，不同 KB 的同名文件在 BM25 里完全同形，
    按 basename 删旧条目会跨库误伤（A 库删 a.pdf 连带 B 库同名 a.pdf 的
    chunks 从索引消失）。file_basenames 参数保留兼容旧签名，仅记录日志。
    """
    meta = doc.metadata or {}
    if meta.get("doc_id") in doc_id_set:
        return True
    if file_basenames:
        logger.warning(
            "[BM25Store] basename 匹配已废除（跨 KB 同名误伤），本次调用忽略: %s",
            sorted(file_basenames)[:3])
    return False


def doc_id_counts(docs: list) -> dict[str, int]:
    """文档列表 → {doc_id: chunk_count}（一致性对账的单一口径）。

    取代旧 {basename: count}：跨 KB 同名文件的计数在 basename 口径下
    被合并，既可能假报漂移也可能掩盖漂移。
    """
    counts: dict[str, int] = {}
    for d in docs:
        doc_id = str((d.metadata or {}).get("doc_id") or "")
        counts[doc_id] = counts.get(doc_id, 0) + 1
    return counts


def source_files_out_of_sync(indexed_docs: list, current_docs: list) -> bool:
    """判断 BM25 索引文档集合与当前文档集合是否一致（doc_id 计数口径）。

    C 阶段收口：旧实现按 {basename: chunk_count} 对账——跨 KB 同名文件
    计数被合并，假报/掩盖漂移皆有。现按 {doc_id: chunk_count}（见
    doc_id_counts），身份与 BM25 条目本体一致。
    """
    return doc_id_counts(indexed_docs) != doc_id_counts(current_docs)


def compute_content_hash(docs: list) -> str:
    """计算文档列表的内容摘要 hash（用于检测内容漂移）。"""
    import hashlib
    h = hashlib.sha256()
    for d in docs:
        h.update(d.page_content.encode("utf-8", errors="replace"))
        h.update(d.metadata.get("source_file", "").encode("utf-8"))
    return h.hexdigest()[:16]


def compute_content_hash_unordered(docs: list) -> str:
    """按内容集合计算摘要，忽略向量库与 BM25 的返回顺序差异。

    向量数据库通常按内部 id 返回 chunk，而 BM25 按写入顺序持久化；
    两者内容一致时不应因顺序不同被只读启动校验误判为漂移。
    """
    import hashlib

    records = sorted(
        (
            str(d.metadata.get("source_file") or ""),
            str(d.page_content or ""),
        )
        for d in docs
    )
    h = hashlib.sha256()
    for source_file, page_content in records:
        h.update(page_content.encode("utf-8", errors="replace"))
        h.update(source_file.encode("utf-8"))
    return h.hexdigest()[:16]


def compute_vector_manifest_hash(docs: list) -> str:
    """按向量 chunk 身份和内容计算稳定 manifest hash。"""
    import hashlib

    records = sorted(
        (
            str((doc.metadata or {}).get("vector_id") or ""),
            str((doc.metadata or {}).get("doc_id") or ""),
            str((doc.metadata or {}).get("chunk_id") or ""),
            str(doc.page_content or ""),
        )
        for doc in docs
    )
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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
        self._bundle_path = self.index_dir / "index.pkl"
        self._meta_path = self.index_dir / "meta.json"
        self._corpus_path = self.index_dir / "corpus.pkl"
        self._docs_path = self.index_dir / "docs.pkl"

    # ── 公共方法 ──────────────────────────────────────

    def build(
        self, docs: List[Document], k: int = None,
        metadata: dict[str, Any] | None = None,
    ) -> Optional[BM25Retriever]:
        """构建并持久化 BM25 索引。

        Args:
            docs: Document 对象列表
            k: 检索候选数量，默认取 config.BM25_CANDIDATE_K；最终答案数量由
               hybrid_retrieve 的 k 控制

        Returns:
            可直接使用的 BM25Retriever 实例；文档为空时返回 None
        """
        if k is None:
            k = BM25_CANDIDATE_K
        logger.info(f"[BM25Store] 构建索引，{len(docs)} 个文档...")
        t0 = time.time()

        retriever = None
        if docs:
            retriever = BM25Retriever.from_documents(
                docs, k=k, preprocess_func=_tokenize_chinese
            )

        elapsed = time.time() - t0
        content_hash = compute_content_hash(docs) if docs else ""
        meta = self._make_meta(
            len(docs), elapsed, content_hash=content_hash, metadata=metadata
        )

        # index.pkl 是跨进程读取的单文件快照；旧的三个文件仍保留，
        # 供旧运维脚本和历史索引平滑升级。
        bundle = {
            "version": BM25_META_VERSION,
            "vectorizer": retriever.vectorizer if retriever else None,
            "docs": list(docs),
            "meta": meta,
        }
        bundle_data = pickle.dumps(bundle, protocol=pickle.HIGHEST_PROTOCOL)
        corpus_data = pickle.dumps(
            retriever.vectorizer if retriever else None,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
        docs_data = pickle.dumps(list(docs), protocol=pickle.HIGHEST_PROTOCOL)
        with _BUILD_LOCK:
            self._write_atomic(self._bundle_path, bundle_data)
            self._write_atomic_checksum(self._bundle_path, bundle_data)
            self._write_atomic(self._corpus_path, corpus_data)
            self._write_atomic_checksum(self._corpus_path, corpus_data)
            self._write_atomic(self._docs_path, docs_data)
            self._write_atomic_checksum(self._docs_path, docs_data)
            self._write_meta_dict(meta)
        # 指针最后写：读到新指针就能读到完整新快照（重建也是发布）
        self.write_published_pointer(str((metadata or {}).get("generation") or ""), meta)

        if not docs:
            logger.info("[BM25Store] 空文档列表，已发布空索引")
            return None
        logger.info(
            f"[BM25Store] 索引构建完成: {len(docs)} 文档, {elapsed:.1f}s, hash={content_hash}"
        )
        return retriever

    def load(self, k: int = None) -> Optional[BM25Retriever]:
        """从磁盘加载 BM25 索引。

        Args:
            k: 检索候选数量，默认取 config.BM25_CANDIDATE_K

        Returns:
            BM25Retriever 实例，索引不存在或损坏时返回 None
        """
        try:
            if k is None:
                k = BM25_CANDIDATE_K
            bundle = (
                self._safe_load_pickle(self._bundle_path)
                if self._bundle_path.exists()
                else None
            )
            if isinstance(bundle, dict) and "docs" in bundle:
                vectorizer = bundle.get("vectorizer")
                docs = bundle.get("docs")
                meta = bundle.get("meta") or {}
            else:
                if not self._corpus_path.exists() or not self._docs_path.exists():
                    logger.info("[BM25Store] 索引文件不存在，需要重建")
                    return None
                vectorizer = self._safe_load_pickle(self._corpus_path)
                docs = self._safe_load_pickle(self._docs_path)
                meta = self._read_meta()
            if vectorizer is None or docs is None:
                return None

            # 直接构造 BM25Retriever，跳过 from_documents 的拟合步骤；
            # 查询侧必须与构建侧使用同一分词函数（见 _tokenize_chinese 注释）
            retriever = BM25Retriever(
                vectorizer=vectorizer,
                docs=docs,
                k=k,
                preprocess_func=_tokenize_chinese,
            )

            # 版本不匹配（如分词器变更）→ 旧索引的倒排统计与新查询分词不一致，需重建
            if meta.get("version", 0) < BM25_META_VERSION:
                logger.info(
                    f"[BM25Store] 索引版本过期 "
                    f"(meta={meta.get('version', 0)} < {BM25_META_VERSION})，将重建"
                )
                return None
            logger.info(
                f"[BM25Store] 索引加载成功: {meta.get('doc_count', '?')} 文档, "
                f"构建于 {meta.get('built_at', '?')}"
            )
            return retriever
        except Exception as e:
            logger.warning(f"[BM25Store] 索引加载失败: {e}，将重建")
            return None

    def add_documents(
        self, docs: List[Document], k: int | None = None
    ) -> BM25Retriever:
        """增量添加文档后全量重建索引。

        BM25 的 IDF 依赖全量文档统计，增量添加必须全量重建以保持 IDF 准确。

        Args:
            docs: 要添加的 Document 列表
            k: 检索候选数量；为空时使用 config.BM25_CANDIDATE_K

        Returns:
            重建后的 BM25Retriever 实例
        """
        if k is None:
            k = BM25_CANDIDATE_K
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
        self, doc_ids: List[str], k: int | None = None,
        file_paths: Optional[List[str]] = None
    ) -> Optional[BM25Retriever]:
        """按 doc_id 或 file_path（含 source_file 文件名）删除文档后全量重建索引。

        doc_id 与 file_path 双键过滤（P0-2）：历史上 loader 与 indexer 的 doc_id 协议
        不一致（loader=sha256[:16] vs registry=md5[:10]），仅按 doc_id 过滤会导致
        删除后 BM25 残留。BM25 的 chunk metadata 始终含 file_path/source_file，
        以文件名为第二键可绕开协议分裂，保证删除真正命中。

        Args:
            doc_ids: 要删除的 doc_id 列表
            file_paths: 要删除的完整文件路径列表（取其 basename 与 metadata.source_file 匹配）
            k: 检索候选数量；为空时使用 config.BM25_CANDIDATE_K

        Returns:
            重建后的 BM25Retriever 实例；无索引时返回 None
        """
        if k is None:
            k = BM25_CANDIDATE_K
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
        k: int | None = None,
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
            k: 检索候选数量；为空时使用 config.BM25_CANDIDATE_K
            doc_id: 要替换的 doc_id
            file_path: 要替换的文件路径（basename 匹配）

        Returns:
            重建后的 BM25Retriever 实例；无索引且无新文档时返回 None
        """
        if k is None:
            k = BM25_CANDIDATE_K
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

    def rebuild_from_vectorstore(
        self,
        vectorstore: Any,
        *,
        exclude_ids: set[str] | None = None,
        k: int | None = None,
    ) -> dict[str, Any]:
        """从当前向量集合重建 BM25，保证两者使用同一批 chunk。

        ``exclude_ids`` 用于重索引的先写后删窗口：旧 chunk 仍在向量库中，
        但已不属于待发布版本，必须在 BM25 快照中排除。构建完成后才会原子
        发布 bundle；构建失败会保留上一代 BM25。
        """
        payload = vectorstore.get()
        ids = list(payload.get("ids") or [])
        texts = list(payload.get("documents") or [])
        metadatas = list(payload.get("metadatas") or [])
        if not (len(ids) == len(texts) == len(metadatas)):
            raise ValueError(
                "向量库返回的 ids/documents/metadatas 数量不一致"
            )

        excluded = {str(item) for item in (exclude_ids or set())}
        docs: list[Document] = []
        for vector_id, text, raw_meta in zip(ids, texts, metadatas):
            vector_id = str(vector_id)
            if vector_id in excluded:
                continue
            if not str(text or "").strip():
                raise ValueError(f"向量 chunk 内容为空，拒绝发布 BM25: {vector_id}")
            meta = dict(raw_meta or {})
            meta.setdefault("vector_id", vector_id)
            meta.setdefault("chunk_id", vector_id)
            docs.append(Document(page_content=str(text), metadata=meta))

        collection = str(
            getattr(vectorstore, "_collection", None)
            or getattr(vectorstore, "_collection_name", None)
            or "unknown"
        )
        vector_hash = compute_vector_manifest_hash(docs)
        self.build(
            docs,
            k=k,
            metadata={
                "source": "vectorstore",
                "collection": collection,
                "vector_count": len(docs),
                "vector_set_hash": vector_hash,
                "excluded_vector_count": len(excluded),
            },
        )
        loaded_ids = {
            str((doc.metadata or {}).get("vector_id"))
            for doc in self.load_docs()
        }
        expected_ids = {
            str((doc.metadata or {}).get("vector_id")) for doc in docs
        }
        if loaded_ids != expected_ids:
            raise RuntimeError(
                "BM25 发布后集合校验失败: "
                f"expected={len(expected_ids)} actual={len(loaded_ids)}"
            )
        return {
            "collection": collection,
            "doc_count": len(docs),
            "vector_set_hash": vector_hash,
            "excluded_vector_count": len(excluded),
        }

    def load_docs_strict(self) -> List[Document]:
        """审计用严格快照：缺失、损坏或校验失败必须显式报错。"""
        with _BUILD_LOCK:
            try:
                if self._bundle_path.exists():
                    bundle = self._safe_load_pickle(self._bundle_path)
                    if not isinstance(bundle, dict) or "docs" not in bundle:
                        raise RuntimeError("BM25 bundle 缺失有效文档列表")
                    docs = bundle["docs"]
                elif self._docs_path.exists():
                    docs = self._safe_load_pickle(self._docs_path)
                else:
                    raise RuntimeError("BM25 文档快照不存在")
            except (OSError, pickle.UnpicklingError, EOFError, ImportError) as exc:
                raise RuntimeError("BM25 文档快照读取失败") from exc
            if not isinstance(docs, list) or not all(
                isinstance(doc, Document) for doc in docs
            ):
                raise RuntimeError("BM25 文档快照结构无效或校验失败")
            return docs

    def load_docs(self) -> List[Document]:
        """从磁盘加载持久化的 Document 列表（供一致性检查等外部消费者使用）。

        Returns:
            Document 列表；索引不存在或损坏时返回空列表
        """
        try:
            bundle = (
                self._safe_load_pickle(self._bundle_path)
                if self._bundle_path.exists()
                else None
            )
            if isinstance(bundle, dict) and "docs" in bundle:
                loaded = bundle.get("docs")
            else:
                if not self._docs_path.exists():
                    return []
                loaded = self._safe_load_pickle(self._docs_path)
            return loaded if loaded is not None else []
        except Exception:
            logger.warning("[BM25Store] load_docs 加载失败")
            return []

    @property
    def is_stale(self) -> bool:
        """检查索引是否过期（文档数为 0 视为过期）。"""
        if not self._bundle_path.exists() and not self._meta_path.exists():
            return True
        meta = self.get_metadata()
        return meta.get("doc_count", 0) == 0

    def doc_count(self) -> int:
        """返回已持久化的文档数量。"""
        meta = self.get_metadata()
        return meta.get("doc_count", 0)

    def get_content_hash(self) -> str:
        """返回已持久化的内容 hash（空字符串表示无记录）。"""
        return self.get_metadata().get("content_hash", "")

    def get_metadata(self) -> dict[str, Any]:
        """返回 BM25 快照元数据，供启动门禁和管理端审计使用。"""
        if self._bundle_path.exists():
            bundle = self._safe_load_pickle(self._bundle_path)
            if isinstance(bundle, dict) and isinstance(bundle.get("meta"), dict):
                return dict(bundle["meta"])
        return self._read_meta()

    def write_published_pointer(self, generation: str, meta: dict | None = None) -> None:
        """写发布指针（PUBLISHED.json，全部产物落定后最后写）。

        读侧每请求热刷新检查的廉价入口（见 published_generation）。
        build（重建发布）与发布切换（switch_bm25_snapshot）统一走本方法，
        保证任何产物落定路径都同步推进代次指针。
        """
        import json as _json
        import tempfile as _tempfile

        meta = meta if meta is not None else self.get_metadata()
        pointer = {
            "generation": generation,
            "built_at": meta.get("built_at", ""),
            "doc_count": meta.get("doc_count", 0),
            "content_hash": meta.get("content_hash", ""),
            "vector_set_hash": meta.get("vector_set_hash", ""),
        }
        self.index_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = _tempfile.mkstemp(prefix=".PUBLISHED.", suffix=".tmp",
                                    dir=str(self.index_dir))
        os.close(fd)
        tmp_path = Path(tmp)
        try:
            with open(tmp_path, "wb") as f:
                f.write(_json.dumps(pointer, ensure_ascii=False, indent=2).encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.index_dir / "PUBLISHED.json")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def published_generation(self) -> str:
        """已发布代次（读 PUBLISHED 指针，C 阶段每请求热刷新检查入口）。

        廉价路径 = 读指针 JSON（几十字节）；无指针回退 meta.json 的
        generation 字段；都没有返回 ""。**绝不在此加载 pickle**——
        本方法在每次检索前调用，必须保持 O(1)。
        """
        try:
            pointer = self.index_dir / "PUBLISHED.json"
            if pointer.exists():
                import json as _json
                with open(pointer, "r", encoding="utf-8") as f:
                    return str(_json.load(f).get("generation") or "")
            meta = self._read_meta()
            return str(meta.get("generation") or "")
        except Exception:  # noqa: BLE001 — 指针读失败按「未知代次」处理
            return ""

    # ── 内部方法 ──────────────────────────────────────

    def _make_meta(
        self,
        doc_count: int,
        build_time_s: float,
        content_hash: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成索引元数据。"""
        meta = {
            "doc_count": doc_count,
            "build_time_s": round(build_time_s, 1),
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "version": BM25_META_VERSION,
        }
        if content_hash:
            meta["content_hash"] = content_hash
        if metadata:
            meta.update(metadata)
        return meta

    def _write_meta(
        self,
        doc_count: int,
        build_time_s: float,
        content_hash: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """写入元数据 JSON 文件。"""
        self._write_meta_dict(
            self._make_meta(doc_count, build_time_s, content_hash, metadata)
        )

    def _write_meta_dict(self, meta: dict[str, Any]) -> None:
        """原子写入元数据 JSON 文件。"""
        data = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
        self._write_atomic(self._meta_path, data)

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        """同目录临时文件 + replace，避免半写文件被读取。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        os.close(temp_fd)
        temp_path = Path(temp_name)
        try:
            with open(temp_path, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _write_atomic_checksum(self, data_path: Path, data: bytes) -> None:
        """原子写入数据文件的 SHA256 校验文件。"""
        import hashlib

        self._write_atomic(
            self._checksum_path(data_path),
            hashlib.sha256(data).hexdigest().encode("ascii"),
        )

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
