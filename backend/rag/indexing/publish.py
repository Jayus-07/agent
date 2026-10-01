"""候选版本发布协议（B 阶段：上传并发/幂等/失败回滚收口）。

契约（必须保持）：候选不可见、旧版不动、成功后发布、失败自动恢复。

候选期（indexer 以候选 store 运行）：
  - chunk/doc 向量写进 generation 专属 collection（``{main}::cand:{gen}``），
    检索只看主 collection → 候选天然不可见；id 内嵌 generation，同文档
    多代次行互不覆盖；
  - chunk_store 走内存缓冲（正式表在发布时一次性翻新）；
  - BM25 构建进 ``{BM25_INDEX_DIR}/staging-{gen}`` 目录，语料 = 主 collection
    ∪ 候选 collection − 旧 chunk（先写后删窗口的旧版排除）。

发布协议（publish_candidate，步骤全部幂等、可断点续跑）：
  a. 运行记录置 publishing（崩溃恢复的续跑锚点）；
  b. registry 条件 upsert（register_published CAS）——**提交点**。输掉 CAS
     （已有更新的发布者）抛 SupersededCandidate，调用方清理候选并报
     superseded；此后任何步骤失败都属于「已发布待收尾」，重试续跑而非回滚；
  c. 候选向量整体晋级主 collection（UPDATE collection，单语句原子）；
  d. BM25 快照文件原子切换 + PUBLISHED 指针（读侧版本检查挂点，C 阶段）；
  e. 暂存源文件 os.replace 到正式路径（旧文件自此才被覆盖）；
  f. chunk_store 按 doc 翻新（delete+insert 发布缓冲行）；
  g. 财务版本快照：旧 chunk 批量 is_latest=False（历史版保留可检索）；
  h. 旧版清理：非财务按旧 chunk_ids 精确删；旧 doc 级向量按 id 删；
  i. doc_version +1（覆盖场景）、运行记录置 published。

失败语义：候选阶段（b 之前）任何失败 → cleanup_candidate + 运行记录
failed，旧版文件/登记行/向量/BM25 全部原样；发布阶段（b 之后）失败 →
保持 publishing 状态等待重试续跑（步骤幂等），绝不回滚已提交的注册。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

from backend.shared.logger import logger

# BM25 快照的产物集合（发布切换 = 逐个原子替换 + 指针收尾）
_BM25_ARTIFACTS = ("index.pkl", "corpus.pkl", "docs.pkl", "meta.json")


class SupersededCandidate(RuntimeError):
    """发布 CAS 失败：同一逻辑文档已被更新的候选发布（本次候选被取代）。"""


class CandidatePublishError(RuntimeError):
    """发布阶段失败（提交点之后）——必须保持 publishing 状态等待续跑。"""


# ── 候选期存储替身 ──────────────────────────────────────────────


class CompositeVectorStore:
    """主 ∪ 候选 两个 collection 的只读拼接视图（仅 BM25 canonical 语料消费）。

    rebuild_from_vectorstore 只调用 ``.get()`` 与 collection 命名属性；
    其余接口显式拒绝，防止误用为写入面。
    """

    def __init__(self, stores: list, collection_label: str = ""):
        self._stores = stores
        self._collection = collection_label or str(
            getattr(stores[0], "_collection", "unknown") if stores else "unknown"
        )

    @property
    def _collection_name(self) -> str:  # 兼容 getattr 探测
        return self._collection

    def get(self, where: dict | None = None) -> dict:
        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[dict] = []
        for store in self._stores:
            payload = store.get(where=where) if where else store.get()
            rows = list(payload.get("ids") or [])
            ids.extend(str(x) for x in rows)
            documents.extend(payload.get("documents") or [])
            metadatas.extend(payload.get("metadatas") or [])
        return {"ids": ids, "documents": documents, "metadatas": metadatas}

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(
            f"CompositeVectorStore 只支持 BM25 语料读取，拒绝调用 {name}")


class BufferingChunkStore:
    """chunk_store 候选期代理：写入进内存缓冲，发布时一次性翻新正式表。

    候选期正式 chunk_store 保持旧版本内容（旧版不动契约）；崩溃丢失缓冲
    无碍——发布的翻新数据可从候选向量 collection 的 content/metadata 重建
    （recover_chunk_rows_from_vectors）。
    """

    def __init__(self) -> None:
        self._rows_by_doc: dict[str, list[dict]] = {}
        self._cleared_docs: set[str] = set()

    def insert_batch(self, doc_id: str, chunks: list[dict]) -> int:
        self._rows_by_doc[doc_id] = list(chunks)
        self._cleared_docs.add(doc_id)
        return len(chunks)

    def delete_by_doc_id(self, doc_id: str) -> int:
        self._rows_by_doc.pop(doc_id, None)
        self._cleared_docs.add(doc_id)
        return 0

    def flush(self, real_store) -> int:
        """发布翻新：先清旧版行再插入本次缓冲（与 indexer 直写顺序一致）。"""
        total = 0
        for doc_id in self._cleared_docs:
            try:
                real_store.delete_by_doc_id(doc_id)
            except Exception:  # noqa: BLE001 — 首次上传时无旧行，删除失败可忽略
                pass
        for doc_id, rows in self._rows_by_doc.items():
            if rows:
                total += real_store.insert_batch(doc_id, rows)
        return total

    def discard(self) -> None:
        self._rows_by_doc.clear()
        self._cleared_docs.clear()


@dataclass
class CandidateStores:
    """一次候选运行的全部隔离存储（generation 绑定）。"""

    generation: str
    vectordb: Any                 # chunk 向量候选 collection
    doc_db: Any                   # doc 级向量候选 collection
    bm25_store: Any               # BM25 staging 目录 store
    bm25_dir: Path
    chunk_store: BufferingChunkStore
    main_vectordb: Any
    main_doc_db: Any
    # BM25 canonical 语料源（主 ∪ 候选拼接）
    bm25_source: Any = None
    _owned: bool = field(default=True)


def candidate_bm25_dir(index_dir: str | Path, generation: str) -> Path:
    return Path(index_dir) / f"staging-{generation}"


def _collection_of(store) -> str:
    """store 的 collection 名探测（pgvector 用 _collection，fake/旧桩兼容
    _collection_name）；两者皆缺返回空串（调用方按空跳过）。"""
    return str(getattr(store, "_collection", None)
               or getattr(store, "_collection_name", "") or "")


def make_candidate_stores(pipeline, generation: str,
                          bm25_index_dir: str | None = None) -> CandidateStores:
    """按 pipeline 的主存储构造一次候选运行的全部隔离实例。

    - chunk/doc 候选实例经 ``clone_for_candidate(generation)`` 协议构造
      （pgvector = 同表 generation 专属 collection；测试 fake 同协议实现），
      检索/人名索引/BM25 canonical 拉取均只看主 collection → 候选不可见；
    - BM25 staging 目录：主 BM25 store index_dir 的同级 ``staging-{gen}``
      （与正式目录同文件系统，发布切换是原子 os.replace）；
    - embedding_function 复用 pipeline 实例（候选写入需要同一套向量）。
    """
    from backend.config.database import BM25_INDEX_DIR as _DEFAULT_BM25_DIR
    from backend.rag.retrieval.bm25_store import BM25Store

    cand_vectordb = pipeline.vectordb.clone_for_candidate(generation)
    cand_doc_db = pipeline.doc_db.clone_for_candidate(generation)
    main_bm25 = getattr(pipeline, "bm25_store", None)
    base_dir = (
        bm25_index_dir
        or getattr(main_bm25, "index_dir", None)
        or _DEFAULT_BM25_DIR
    )
    # staging 与主目录同级（同文件系统保证 os.replace 原子）
    main_dir = Path(base_dir)
    bm25_dir = main_dir.parent / f"staging-{generation}" \
        if main_dir.name not in ("", "/") else Path(base_dir) / f"staging-{generation}"
    cand_bm25 = BM25Store(index_dir=str(bm25_dir))
    composite = CompositeVectorStore(
        [pipeline.vectordb, cand_vectordb],
        collection_label=str(getattr(pipeline.vectordb, "_collection", "unknown")),
    )
    return CandidateStores(
        generation=generation,
        vectordb=cand_vectordb,
        doc_db=cand_doc_db,
        bm25_store=cand_bm25,
        bm25_dir=bm25_dir,
        chunk_store=BufferingChunkStore(),
        main_vectordb=pipeline.vectordb,
        main_doc_db=pipeline.doc_db,
        bm25_source=composite,
    )


def cleanup_candidate(stores: CandidateStores) -> None:
    """候选失败清理：丢缓冲、删候选 collection 行、删 BM25 staging 目录。

    所有动作幂等且绝不触碰主 collection / 正式 BM25 / 正式文件。
    drop 必须在主 store 上对「候选 collection 名」执行（drop_collection
    拒绝删除自身 collection，防误删线上数据）。
    """
    try:
        stores.chunk_store.discard()
    except Exception:  # noqa: BLE001
        pass
    for main_store, cand_name in (
        (stores.main_vectordb, _collection_of(stores.vectordb)),
        (stores.main_doc_db, _collection_of(stores.doc_db)),
    ):
        try:
            if cand_name:
                main_store.drop_collection(cand_name)
        except Exception as e:  # noqa: BLE001 — 残留候选行交由 Sweeper 对账
            logger.warning(
                f"[Publish] 候选 collection 清理失败 (gen={stores.generation}): {e}")
    try:
        if stores.bm25_dir.exists():
            shutil.rmtree(stores.bm25_dir, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Publish] BM25 staging 目录清理失败: {e}")


def cleanup_candidate_by_generation(
    generation: str, *, chroma_path: str, doc_db_path: str, bm25_index_dir: str,
) -> None:
    """无候选 store 实例时的代次清理（终态收口/恢复任务用，全幂等）。"""
    from backend.rag.vectorstore.pgvector_store import (
        PgVectorKnowledgeStore,
        candidate_collection_name,
    )

    for base in (chroma_path, doc_db_path):
        try:
            main = PgVectorKnowledgeStore(persist_directory=base, embedding_function=None)
            main.drop_collection(candidate_collection_name(base, generation))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[Publish] 候选 collection 清理失败 (gen={generation[:12]}): {e}")
    try:
        shutil.rmtree(candidate_bm25_dir(bm25_index_dir, generation), ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Publish] BM25 staging 目录清理失败: {e}")


# ── BM25 快照切换 ──────────────────────────────────────────────


def _write_published_pointer(index_dir: Path, meta: dict, generation: str) -> None:
    """写发布指针（C 阶段读侧每请求版本检查的廉价入口）。

    指针在全部产物文件替换完成后最后写入——读到新指针就能读到完整新快照。
    """
    import json
    import tempfile

    pointer = {
        "generation": generation,
        "built_at": meta.get("built_at", ""),
        "doc_count": meta.get("doc_count", 0),
        "content_hash": meta.get("content_hash", ""),
        "vector_set_hash": meta.get("vector_set_hash", ""),
    }
    path = index_dir / "PUBLISHED.json"
    fd, tmp = tempfile.mkstemp(prefix=".PUBLISHED.", suffix=".tmp", dir=str(index_dir))
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(json.dumps(pointer, ensure_ascii=False, indent=2).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def switch_bm25_snapshot(cand_store, main_store, generation: str) -> dict:
    """把候选目录构建好的 BM25 快照原子切换为线上快照。

    逐文件 os.replace（同文件系统原子）；读取方要么读到完整旧代、要么完整
    新代（bundle 自带 meta，加载不混代）。全部产物落定后写 PUBLISHED 指针。
    非 文件型主 store（测试桩/旧实现）走 adopt_snapshot 协议钩子。
    """
    cand_dir = Path(cand_store.index_dir)
    meta = cand_store.get_metadata()
    adopt = getattr(main_store, "adopt_snapshot", None)
    if not callable(adopt) and not hasattr(main_store, "index_dir"):
        logger.warning(
            "[Publish] 主 BM25 store 不支持快照切换（无 index_dir 也无 "
            "adopt_snapshot），跳过——仅允许测试桩，生产实现必须支持")
        return meta
    if callable(adopt) and not hasattr(main_store, "index_dir"):
        adopt(cand_store, generation)
        return meta
    main_dir = Path(main_store.index_dir)
    main_dir.mkdir(parents=True, exist_ok=True)
    for name in _BM25_ARTIFACTS:
        src = cand_dir / name
        if not src.exists():
            raise CandidatePublishError(f"BM25 staging 缺少产物: {name}")
        os.replace(src, main_dir / name)
        chk = Path(str(src) + ".sha256")
        if chk.exists():
            os.replace(chk, Path(str(main_dir / name) + ".sha256"))
    # 指针统一由 store 维护（重建/切换同一入口，最后写）
    main_store.write_published_pointer(generation, meta)
    logger.info(
        f"[Publish] BM25 快照已切换 generation={generation} "
        f"doc_count={meta.get('doc_count')}")
    return meta


# ── 发布协议 ──────────────────────────────────────────────────


def publish_candidate(
    *,
    run_store,
    upload_id: str,
    registry,
    stores: CandidateStores,
    final_path: str,
    doc_id: str,
    kb_id: str,
    file_hash: str,
    base_generation: str,
    index_result: dict,
    old_row: dict | None,
    old_chunk_ids: list[str],
    old_doc_db_id: str,
    main_bm25_store,
) -> str:
    """候选 → 正式的发布事务（步骤见模块 docstring；全程幂等可续跑）。

    返回 "published"。输掉单发布者 CAS 抛 SupersededCandidate（候选未提交，
    调用方清理即可）；提交点之后失败抛 CandidatePublishError（保持
    publishing 状态，重试从publishing 续跑）。
    """
    generation = stores.generation
    chunk_ids = list(index_result.get("chunk_ids") or [])
    doc_db_id = index_result.get("doc_db_id", "")
    registry_metadata = dict(index_result.get("registry_metadata") or {})
    if not chunk_ids:
        raise CandidatePublishError("候选结果为空（0 chunk），拒绝发布")
    # version_id 沿用声明值；未声明时以 generation 兜底（版本可追溯）
    registry_metadata.setdefault("version_id", generation)

    run_store.mark_status(upload_id, "publishing", stage="publish")

    # ── b. registry CAS（提交点）──
    # 断点续跑：提交点之后重入时 registry.active_generation 已是本代次，
    # 守卫基准改为本代次自身（幂等重放），而非最初观测的 base。
    existing_row = registry.get_by_path(final_path)
    if isinstance(existing_row, dict) and existing_row.get("active_generation") == generation:
        expected_base = generation
    else:
        expected_base = base_generation
    rowcount = registry.register_published(
        file_path=final_path,
        doc_id=doc_id,
        file_hash=file_hash,
        kb_id=kb_id,
        chunk_ids=chunk_ids,
        doc_db_id=doc_db_id,
        metadata=registry_metadata,
        active_generation=generation,
        expected_base_generation=expected_base,
    )
    if rowcount == 0:
        raise SupersededCandidate(
            f"文档已被更新的版本发布（base={expected_base[:12] or '∅'}），"
            f"本次候选被取代: {os.path.basename(final_path)}")

    # ── c. 候选向量晋级主 collection（在主 store 上执行：source=候选名）──
    promoted_chunks = stores.main_vectordb.promote_collection(
        _collection_of(stores.vectordb))
    promoted_docs = stores.main_doc_db.promote_collection(
        _collection_of(stores.doc_db))
    logger.info(
        f"[Publish] 候选向量晋级: chunks={promoted_chunks} docs={promoted_docs} "
        f"(gen={generation[:12]})")

    # ── d. BM25 快照切换 ──
    switch_bm25_snapshot(stores.bm25_store, main_bm25_store, generation)

    # ── e. 源文件替换（旧文件自此才被覆盖）──
    staging_path = index_result.get("staging_path") or ""
    if staging_path and os.path.isfile(staging_path):
        os.replace(staging_path, final_path)
    elif not os.path.isfile(final_path):
        raise CandidatePublishError(
            f"暂存文件与正式文件均缺失，无法完成发布: {final_path}")

    # ── f. chunk_store 翻新 ──
    try:
        from backend.rag.indexing.chunk_store import get_chunk_store
        stores.chunk_store.flush(get_chunk_store())
    except Exception as e:
        raise CandidatePublishError(f"chunk_store 翻新失败（待续跑）: {e}") from e

    # ── g. 财务版本快照：旧 chunk 批量翻 is_latest=False ──
    old_doc_id = (old_row or {}).get("doc_id", "")
    use_snapshot = False
    if old_doc_id:
        from backend.rag.indexing.indexer import IncrementalIndexer
        use_snapshot = IncrementalIndexer._should_use_version_snapshot(
            (old_row or {}).get("doc_type", ""), final_path)
        if use_snapshot:
            try:
                flipped = stores.main_vectordb.update_metadata_where(
                    where={"doc_id": old_doc_id},
                    metadata_update={"is_latest": False},
                )
                logger.info(
                    f"[Publish] 财务版本快照: 旧版 {flipped} chunks is_latest=False")
            except Exception as e:
                raise CandidatePublishError(
                    f"旧版 is_latest 翻转失败（待续跑）: {e}") from e

    # ── h. 旧版清理（精确按 id，绝不 doc_id 条件删）──
    if not use_snapshot and old_chunk_ids:
        try:
            stores.main_vectordb.delete(ids=[str(x) for x in old_chunk_ids])
            logger.info(
                f"[Publish] 已清理旧 chunk 向量 {len(old_chunk_ids)} 条")
        except Exception as e:
            logger.warning(f"[Publish] 旧 chunk 向量清理失败（Sweeper 兜底）: {e}")
    if old_doc_id:
        new_doc_db_id = doc_db_id
        old_db_id = old_doc_db_id or (old_row or {}).get("doc_db_id", "")
        if old_db_id and old_db_id != new_doc_db_id:
            try:
                stores.main_doc_db.delete(ids=[old_db_id])
            except Exception as e:
                logger.warning(f"[Publish] 旧 doc 级向量清理失败: {e}")

    # ── i. 计数与收口 ──
    if old_doc_id and not (index_result.get("skipped")):
        try:
            registry.bump_doc_version(doc_id, delta=1)
        except Exception as e:  # noqa: BLE001 — 计数失败不回滚发布
            logger.warning(f"[Publish] doc_version 自增失败: {e}")
    run_store.mark_status(upload_id, "published", stage="publish")
    logger.info(
        f"[Publish] 发布完成: {os.path.basename(final_path)} "
        f"gen={generation[:12]} chunks={len(chunk_ids)}")
    return "published"
