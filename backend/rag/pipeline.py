"""RAG 管道 — 主入口"""
import os
import json
import time
from collections import OrderedDict
from collections.abc import Iterable

# R-P0-1（Windows 原生库加载顺序加固）：langchain_text_splitters 顶层会拉起
# sentence_transformers→torch；若该导入发生在 chroma/doc_db 等原生库已加载
# 之后（如 indexer.py:471 惰性导入 parse_and_chunk 触发），进程确定性段错误
# （exit 139，见 docs/RAG质量专项-01-审计报告.md §2.2 与探针 logs/r2_probe*.log）。
# 在任何原生库加载前预导入，使其进入 sys.modules，后续惰性导入变为无操作。
# 实测：预导入后完整启动（含恢复重索引）正常；失败时软降级不影响启动。
try:
    import langchain_text_splitters  # noqa: F401
except Exception:  # pragma: no cover - 环境缺失时保持旧行为
    pass

from backend.rag.embedding_singleton import get_embedding
from backend.rag.vectorstore.pgvector_store import PgVectorKnowledgeStore

from backend.rag.preprocessing.metadata import build_all_metadata_async
from backend.rag.preprocessing.loader import load_documents_from_directory
from backend.rag.indexing.doc_id import derive_doc_id_from_path
from backend.rag.base import CustomRetriever
from backend.rag.retrieval.bm25_store import BM25Store, source_files_out_of_sync, compute_content_hash
from backend.rag.chain import RAGChain
from backend.config import (
    EMBEDDING_MODEL_PATH,
    BM25_CANDIDATE_K,
    CHROMA_PATH,
    DOC_DB_PATH,
    DOCS_DIRECTORY,
    DOC_REGISTRY_PATH,
    ENABLE_INCREMENTAL_INDEX,
    ENABLE_MEMORY,
    OVERALL_REQUEST_TIMEOUT,
    RAG_ANSWER_CACHE_ENABLED,
    ENABLE_RESOURCE_MONITOR,
)
from backend.shared.logger import logger
from backend.observability.resource import resource_monitor
from backend.infra.async_utils import run_async as _run_async

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'


class RAGPipeline:
    # 进程内已见会话标记上限（有界 LRU）：原 set 永不清理，长期运行内存无界增长
    _SEEN_SESSIONS_MAX = 10000

    def __init__(self):
        self.vectordb = None
        self.doc_db = None
        self.chunk_retriever = None
        self._seen_sessions: OrderedDict[str, None] = OrderedDict()
        self.bm25 = None
        self.bm25_store = None  # BM25Store 引用，供运行时删除/重建索引
        self._person_to_doc_cache = {}
        self._init()

    def _mark_session_seen(self, session_id: str) -> None:
        """标记会话已见（LRU 有界，防止内存无界增长）。"""
        self._seen_sessions[session_id] = None
        self._seen_sessions.move_to_end(session_id)
        while len(self._seen_sessions) > self._SEEN_SESSIONS_MAX:
            self._seen_sessions.popitem(last=False)

    def _session_has_history(self, session_id: str) -> bool:
        """检查会话在记忆库中是否已有历史轮次。

        防止服务重启后进程内 _seen_sessions 丢失，把带 PG 历史的多轮对话
        误判为首轮，用不含上下文的首轮缓存答案回填请求。
        """
        memory = getattr(self.lc_chain, "_memory", None)
        if memory is None or not session_id:
            return False
        try:
            buf = memory.start_session(session_id, "")
            return bool(buf is not None and len(buf) > 0)
        except Exception:
            return False

    def _init(self):
        """初始化入口：4 个准备阶段 + 收尾。

        拆解后每个阶段职责单一，便于测试与定位。
        """
        self._prepare_documents()
        self._prepare_vector_store()
        self._prepare_retrievers()
        logger.info("RAG 管道初始化完成")

    def _prepare_documents(self):
        """阶段 1：初始化 embedding 单例。

        全量 docs 的解析分块（_load_and_chunk）只在全量重建路径需要；
        增量索引模式下该结果不会被使用，却要把整个知识库解析进内存，
        是冷启动耗时与内存占用的大头 → 延迟到 _prepare_vector_store 按需执行。
        """
        self._init_embedding()

    def _ensure_docs_loaded(self):
        """懒加载兜底：增量模式下 self.docs 未加载时按需加载（幂等）。"""
        if getattr(self, "docs", None) is None:
            self._load_and_chunk()
            self._build_doc_index()

    def _prepare_vector_store(self):
        """阶段 2：构建向量库（增量优先，回退全量重建）。"""
        used_incremental = (
            self._init_vector_dbs_incremental()
            if ENABLE_INCREMENTAL_INDEX
            else False
        )
        if used_incremental:
            return

        # 全量重建路径（需要全量 docs 解析分块）
        self._ensure_docs_loaded()
        self._build_metadata()
        self._init_vector_dbs_full()
        # 同步 registry，失败不影响当前查询
        try:
            self._sync_registry_after_full_rebuild()
        except Exception as e:
            logger.error(f"同步 registry 失败（不影响当前查询）: {e}", exc_info=True)

    def _prepare_retrievers(self):
        """阶段 3：构建检索器（BM25 + 自定义 + chain）。"""
        self._init_retrievers()

    # =====================================================
    # 初始化步骤
    # =====================================================

    def _load_and_chunk(self):
        logger.info("加载文档...")
        self.docs = load_documents_from_directory(DOCS_DIRECTORY)
        logger.info(f"文档加载完成: {len(self.docs)} 个 chunk")

    def _build_doc_index(self):
        self.doc_map = {}
        all_file_paths = set()
        for d in self.docs:
            fname = d.metadata["file_path"]
            all_file_paths.add(fname)
            name = os.path.basename(fname)
            if name not in self.doc_map:
                self.doc_map[name] = []
            self.doc_map[name].append(d.page_content)
        logger.info(
            f"文档级索引: {len(self.doc_map)} 个唯一文档名, "
            f"{len(all_file_paths)} 个源文件"
        )

    def _build_metadata(self):
        logger.info("开始异步批量构建元数据...")
        try:
            doc_level_texts, doc_level_meta = _run_async(
                build_all_metadata_async(self.docs, self.doc_map)
            )
            self.doc_level_texts = doc_level_texts
            self.doc_level_meta = doc_level_meta
            logger.info(f"元数据构建完成: {len(doc_level_texts)} 个文档级, {len(self.docs)} 个 chunk 级")
        except Exception as e:
            logger.error(f"元数据构建失败: {e}（可能为 KeywordResult 类型兼容问题，提取关键词时降级处理）")
            # 降级：不阻塞启动
            self.doc_level_texts = []
            self.doc_level_meta = []

    def _init_embedding(self):
        self.embedding = get_embedding()  # 全局单例，避免重复加载 400MB 模型

    def _init_vector_dbs_full(self):
        """全量重建向量库（兜底/首次运行）。"""
        self.vectordb = self._load_or_create_db(
            CHROMA_PATH,
            create_fn=lambda: PgVectorKnowledgeStore.from_documents(
                self.docs, embedding=self.embedding, persist_directory=CHROMA_PATH,
            ),
            db_type="chunk 级",
        )
        self.doc_db = self._load_or_create_db(
            DOC_DB_PATH,
            create_fn=lambda: PgVectorKnowledgeStore.from_texts(
                texts=self.doc_level_texts,
                embedding=self.embedding,
                metadatas=self.doc_level_meta,
                persist_directory=DOC_DB_PATH,
            ),
            db_type="文档级",
        )

    def _init_vector_dbs_incremental(self) -> bool:
        """增量索引向量库。成功返回 True，回退全量重建返回 False。"""
        from backend.rag.indexing.doc_registry import DocumentRegistry
        from backend.rag.indexing.indexer import IncrementalIndexer

        logger.info("启用增量索引模式")

        # 加载已有向量库
        try:
            self.vectordb = self._load_existing_db(CHROMA_PATH, "chunk 级")
            self.doc_db = self._load_existing_db(DOC_DB_PATH, "文档级")
        except Exception as e:
            logger.warning(f"加载向量库失败: {e}，回退全量重建")
            return False

        # 初始化注册表
        try:
            registry = DocumentRegistry(DOC_REGISTRY_PATH)
        except Exception as e:
            logger.warning(f"注册表初始化失败: {e}，回退全量重建")
            return False

        # 全量重建快照：在任何破坏性操作（sync 异常 clear / 全量重建 clear）之前
        # 抓取 registry 现状，供 _sync_registry_after_full_rebuild 回填 doc_id/
        # status/minhash_sig——防止全量重建把语义 slug 与近重复基线抹掉
        #（2026-09-17 事故：registry.clear() + md5 重派导致主语料 doc_id 全变、
        #  minhash_sig 清空、4 份 pending_review 近重复副本被翻成 active）。
        try:
            self._registry_snapshot = {
                p: dict(r) for p, r in registry.list_all().items()
            }
        except Exception:
            self._registry_snapshot = {}
            logger.warning("registry 快照失败（若触发全量重建将无法回填历史字段）", exc_info=True)

        # 执行增量同步
        try:
            indexer = IncrementalIndexer(
                docs_dir=DOCS_DIRECTORY,
                vectordb=self.vectordb,
                doc_db=self.doc_db,
                embedding=self.embedding,
                registry=registry,
                kb_id="default",  # 触发 _derive_kb_id 按第一级子目录派生，实现 kb 隔离
            )
            result = indexer.sync()
            logger.info(f"增量索引: {result}")
            return True
        except Exception as e:
            logger.warning(
                f"增量索引失败（{type(e).__name__}: {e}），将回退全量重建；"
                f"如为 NameError 请检查 indexer 变量作用域",
                exc_info=True,
            )
            if 'registry' in locals():
                try:
                    registry.clear()
                except Exception:
                    logger.warning("registry 清理失败", exc_info=True)
            return False

    def _load_existing_db(self, db_path: str, db_type: str):
        """加载已有向量库（不做版本检查，不创建）。"""
        db = PgVectorKnowledgeStore(
            persist_directory=db_path, embedding_function=self.embedding,
        )
        logger.info(f"加载已有{db_type}向量库: {db_path}")
        return db

    def _load_or_create_db(self, db_path, create_fn, db_type):
        if not self._need_rebuild(db_path):
            return self._load_existing_db(db_path, db_type)
        self._rebuild_db(db_path)
        db = create_fn()
        logger.info(f"创建新{db_type}向量库: {db_path}")
        return db

    def _sync_registry_after_full_rebuild(self):
        """全量重建后将所有文档信息写入 registry，下次启动走增量。"""
        from backend.rag.indexing.doc_registry import DocumentRegistry
        from backend.rag.indexing.indexer import IncrementalIndexer

        try:
            registry = DocumentRegistry(DOC_REGISTRY_PATH)
        except Exception as e:
            logger.warning(f"无法初始化 registry: {e}")
            return
        # 优先用增量阶段抓取的快照（sync 崩溃路径可能已 clear，届时本地现查为空）
        snapshot = getattr(self, "_registry_snapshot", None)
        if not snapshot:
            try:
                snapshot = {p: dict(r) for p, r in registry.list_all().items()}
            except Exception:
                snapshot = {}
        registry.clear()

        # 扫描所有文档
        indexer = IncrementalIndexer(
            docs_dir=DOCS_DIRECTORY,
            vectordb=self.vectordb,
            doc_db=self.doc_db,
            embedding=self.embedding,
            registry=registry,
            kb_id="default",  # 触发 _derive_kb_id 按第一级子目录派生，实现 kb 隔离
        )
        disk_files = indexer._scan_disk()

        for file_path, (file_hash, _, _) in disk_files.items():
            kb_id = indexer._derive_kb_id(file_path)
            snap = snapshot.get(file_path) or {}
            # 快照中已有 doc_id（语义 slug 或历史 hash）→ 原样沿用；
            # 否则按 md5 协议新派生（首次入库）。
            doc_id = snap.get("doc_id") or derive_doc_id_from_path(file_path, DOCS_DIRECTORY)

            # 从 chunk 级向量库查找该文件的所有 chunk ID
            try:
                chunk_data = self.vectordb.get(
                    where={"file_path": file_path}
                )
                chunk_ids = chunk_data.get("ids", [])
            except Exception:
                logger.warning("ChromaDB chunk 数据读取失败", exc_info=True)
                chunk_ids = []

            # P1-10: 全量重建路径补写 chunk_store（消除"registry 有记录但 chunk_store 0 条"
            # 的不一致——此前全量重建只注册 chunk_ids，不写 chunk 文本，导致
            # GET /documents/{id}/chunks 返回空）
            try:
                from backend.rag.indexing.chunk_store import get_chunk_store
                chunk_docs = chunk_data.get("documents") or []
                chunk_metas = chunk_data.get("metadatas") or [{}] * len(chunk_docs)
                cs = get_chunk_store()
                cs.delete_by_doc_id(doc_id)  # 幂等：先清旧数据
                cs.insert_batch(doc_id, [
                    {
                        "chunk_index": i,
                        "content": (chunk_docs[i] or "") if i < len(chunk_docs) else "",
                        "keywords": (chunk_metas[i].get("chunk_keywords", "") if i < len(chunk_metas) else ""),
                        "llm_keywords": "",
                        "llm_model": "",
                        "section_title": (chunk_metas[i].get("section_title", "") if i < len(chunk_metas) else ""),
                        "doc_type": (chunk_metas[i].get("doc_type", "general") if i < len(chunk_metas) else "general"),
                        "kb_id": kb_id,
                        "department": "general",
                        "simulated_questions": (chunk_metas[i].get("simulated_questions", []) if i < len(chunk_metas) else []),
                    }
                    for i in range(len(chunk_ids))
                ])
            except Exception as e:
                logger.warning(f"[Pipeline] 全量重建写 chunk_store 失败 (doc_id={doc_id}): {e}")

            # 从 doc 级向量库查找 doc_db_id
            doc_db_id = ""
            try:
                doc_data = self.doc_db.get(where={"doc_id": doc_id})
                doc_ids = doc_data.get("ids", [])
                doc_db_id = doc_ids[0] if doc_ids else ""
            except Exception:
                logger.warning("doc_db 元数据读取失败", exc_info=True)

            registry.register(
                file_path=file_path,
                doc_id=doc_id,
                file_hash=file_hash,
                kb_id=kb_id,
                chunk_ids=chunk_ids,
                doc_db_id=doc_db_id,
                metadata={
                    "doc_type": snap.get("doc_type", "general"),
                    "minhash_sig": snap.get("minhash_sig", ""),
                    "near_dup_id": snap.get("near_dup_id", ""),
                    "summary": snap.get("summary", ""),
                    "keywords": snap.get("keywords", ""),
                    "business_domain": snap.get("business_domain", ""),
                },
            )
            # 回填非重建产物字段（近重复基线：非 active 状态 + minhash_sig 兜底）
            self._restore_snapshot_fields(registry, file_path, snap)

        # 不在磁盘上的存量行（如近重复隔离副本 pending_review）原样恢复，
        # 防止全量重建把它们从 registry 抹掉（检索层按状态软过滤依赖这些行）。
        registered = set(disk_files.keys())
        for p, row in snapshot.items():
            if p in registered or row.get("status") in ("deleted", "", None):
                continue
            try:
                registry.register(
                    file_path=p,
                    doc_id=row.get("doc_id", ""),
                    file_hash=row.get("file_hash", ""),
                    kb_id=row.get("kb_id", ""),
                    chunk_ids=row.get("chunk_ids") or [],
                    doc_db_id=row.get("doc_db_id", ""),
                    metadata={
                        "doc_type": row.get("doc_type", "general"),
                        "minhash_sig": row.get("minhash_sig", ""),
                        "near_dup_id": row.get("near_dup_id", ""),
                        "summary": row.get("summary", ""),
                        "keywords": row.get("keywords", ""),
                        "business_domain": row.get("business_domain", ""),
                    },
                )
                self._restore_snapshot_fields(registry, p, row)
                logger.info(
                    f"[Pipeline] 快照恢复离盘存量行: {os.path.basename(p)} "
                    f"status={row.get('status')}"
                )
            except Exception as e:
                logger.warning(f"[Pipeline] 快照行恢复失败 ({p}): {e}")

        logger.info(
            f"Registry 同步完成: {registry.count()} 条记录"
        )

    @staticmethod
    def _restore_snapshot_fields(registry, file_path: str, snap: dict) -> None:
        """回填快照中的非重建产物字段：非 active 状态（pending_review 等审核态）
        与 minhash_sig（近重复检测依据）。active 不用回写（register 默认即 active）。"""
        if not snap:
            return
        try:
            status = snap.get("status")
            if status and status != "active":
                registry.update_status(file_path, status)
            sig = snap.get("minhash_sig")
            if sig:
                registry.update_fields(file_path, {"minhash_sig": sig})
        except Exception as e:
            logger.warning(f"[Pipeline] registry 快照回填失败 ({file_path}): {e}")

    def _init_retrievers(self):
        self.chunk_retriever = CustomRetriever(self.vectordb)

        # BM25: 优先从磁盘加载持久化索引，避免每次启动重建
        bm25_store = BM25Store()
        self.bm25_store = bm25_store  # 保留 store 引用，供删除/重索引时更新
        self.bm25 = bm25_store.load(k=BM25_CANDIDATE_K)

        # BM25 重建语料源：优先向量库（indexer 实际写入的 chunks），
        # 回退 self.docs（loader chunks）。两者切分策略不同，
        # 向量库语料保证 BM25 与向量检索的 chunk 集合一致。
        # 增量模式下 self.docs 平时不加载，仅向量库语料不可用时懒加载兜底
        vectorstore_docs = self._build_bm25_corpus_from_vectorstore()
        if vectorstore_docs:
            bm25_source = vectorstore_docs
        else:
            self._ensure_docs_loaded()
            bm25_source = self.docs

        if self.bm25 is None:
            logger.info("[RAG] BM25 索引不存在，全量重建...")
            self.bm25 = bm25_store.build(bm25_source, k=BM25_CANDIDATE_K)
        elif bm25_store.is_stale:
            logger.info("[RAG] BM25 索引已过期（文档数为 0），重建...")
            self.bm25 = bm25_store.build(bm25_source, k=BM25_CANDIDATE_K)
        elif source_files_out_of_sync(self.bm25.docs, bm25_source):
            logger.info("[RAG] BM25 索引与文档目录不一致（残留/缺失），重建...")
            self.bm25 = bm25_store.build(bm25_source, k=BM25_CANDIDATE_K)
        elif bm25_store.get_content_hash() and bm25_store.get_content_hash() != compute_content_hash(bm25_source):
            logger.info("[RAG] BM25 索引内容 hash 不匹配（文档已修改），重建...")
            self.bm25 = bm25_store.build(bm25_source, k=BM25_CANDIDATE_K)
        else:
            logger.info(
                f"[RAG] BM25 索引从磁盘加载成功 "
                f"({bm25_store.doc_count()} 文档, hash={bm25_store.get_content_hash()})，跳过重建"
            )

        # 人名索引只存 doc_id，不存正文；启动时构建一次可避免首次查询才付出
        # doc_db 全量读取成本。命中后仍会经过 Stage 2 授权、过滤和 Evidence Gate。
        self.person_index = self._build_person_index()

        if ENABLE_MEMORY:
            from backend.memory import memory_manager
            _mem = memory_manager
        else:
            _mem = None
        self.lc_chain = RAGChain(
            doc_db=self.doc_db,
            vectordb=self.vectordb,
            chunk_retriever=self.chunk_retriever,
            bm25=self.bm25,
            person_index=self.person_index,
            memory_manager=_mem,
        )

    def remove_documents_from_bm25(self, doc_ids: list[str], file_paths: list[str] | None = None) -> None:
        """运行时删除文档后，从 BM25 索引移除对应 chunk 并全量重建。

        BM25 的 IDF 依赖全量文档统计，删除必须全量重建以保持准确。
        只更新内存中的 self.bm25 与磁盘持久化索引，不动 Chroma 向量。
        file_paths 作为第二过滤键（P0-2：doc_id 协议分裂时按文件名兜底命中）。
        """
        if not doc_ids or self.bm25_store is None:
            return
        try:
            new_retriever = self.bm25_store.remove_documents(
                doc_ids, k=BM25_CANDIDATE_K, file_paths=file_paths,
            )
            if new_retriever is not None:
                self.bm25 = new_retriever
            logger.info(f"[RAG] BM25 已移除文档 {doc_ids} (file_paths={file_paths})")
        except Exception as e:
            logger.warning(f"[RAG] BM25 移除文档失败 ({doc_ids}): {e}")

    def _build_bm25_corpus_from_vectorstore(self) -> list:
        """从 chunk 向量库读取全部文档，作为 BM25 重建语料。

        向量库是 indexer 实际写入的权威数据源；用它构建 BM25 可保证
        BM25 chunk 集合与向量检索完全一致，消除 loader/indexer 切分差异。
        返回空列表表示向量库不可用，调用方应回退 self.docs。
        """
        try:
            result = self.vectordb.get()
            ids = result.get("ids") or []
            documents = result.get("documents") or []
            metadatas = result.get("metadatas") or []
            if not ids:
                return []
            from langchain_core.documents import Document
            docs = []
            for i, cid in enumerate(ids):
                text = documents[i] if i < len(documents) else ""
                meta = metadatas[i] if i < len(metadatas) else {}
                if text:
                    docs.append(Document(page_content=text, metadata={**meta, "vector_id": cid}))
            skipped = len(ids) - len(docs)
            if skipped:
                logger.warning(f"[RAG] BM25 语料跳过 {skipped} 个空文本 chunk (向量库 {len(ids)} → BM25 {len(docs)})")
            logger.info(f"[RAG] 从向量库构建 BM25 语料: {len(docs)} chunks")
            return docs
        except Exception as e:
            logger.warning(f"[RAG] BM25 语料读取失败，回退 loader docs: {e}")
            return []

    def refresh_bm25_from_store(self) -> None:
        """刷新 BM25 与人名索引（indexer 上传/重索引后调用）。

        上传会新增/替换 doc_db 记录；如果只刷新 BM25 而不刷新人名索引，
        新文档直到进程重启前都无法走 person_name 快速路径。
        """
        if self.bm25_store is None:
            return
        reloaded = self.bm25_store.load(k=BM25_CANDIDATE_K)
        if reloaded is not None:
            self.bm25 = reloaded
            logger.info(f"[RAG] BM25 已从磁盘刷新 ({self.bm25_store.doc_count()} 文档)")
        else:
            logger.warning("[RAG] BM25 磁盘刷新失败，保持当前内存索引")
        self.refresh_person_index()

    def refresh_person_index(self) -> None:
        """清空并重建人名倒排索引，同时更新已创建的 RAGChain 引用。"""
        self._person_to_doc_cache = {}
        self.person_index = self._build_person_index()
        if getattr(self, "lc_chain", None) is not None:
            self.lc_chain.person_index = self.person_index

    def check_consistency(self):
        """审计 5 个存储之间的索引一致性。"""
        from backend.rag.indexing.consistency import IndexConsistencyChecker
        return IndexConsistencyChecker(self).check()

    # =====================================================
    # 全量重建判定（pgvector 语义）
    # =====================================================

    @staticmethod
    def _need_rebuild(db_path: str) -> bool:
        """纯查询：collection 内无向量行 = 需要重建（空库或已被清空）。

        原 Chroma 实现比对磁盘 .version 指纹（docs 目录 mtime 哈希）判定
        语料漂移；pgvector 轨下向量库在 rag_vectors 表、目录恒不存在，
        指纹读写两端均已失效（2026-09-18 收口确认），且增量由 registry
        驱动、指纹冗余，改为按 collection 行数判定。此处构造 store 仅做
        行计数（embedding_function 惰性不被调用）；PG 不可达直接抛错
        fail-fast，由上层决定走全量重建或启动失败。
        """
        store = PgVectorKnowledgeStore(persist_directory=db_path, embedding_function=None)
        if store.count() > 0:
            logger.info(f"向量库已有数据（collection 非空）: {db_path}")
            return False
        logger.warning(f"向量库为空，需要重建: {db_path}")
        return True

    @staticmethod
    def _rebuild_db(db_path: str) -> None:
        """副作用：清空该 collection 全部向量行，由 _need_rebuild + create_fn 配套调用。

        原 Chroma 实现 rmtree 磁盘目录（Windows 句柄占用时显式抛错，避免
        维度错配的隐蔽故障）；pgvector 轨数据在 PG 表，按 collection 精确
        清空（对齐 Chroma reset 语义）。
        """
        store = PgVectorKnowledgeStore(persist_directory=db_path, embedding_function=None)
        cleared = store.clear()
        if cleared:
            logger.info(f"已清空旧向量库 collection（{cleared} 行）: {db_path}")

    # =====================================================
    # 人名倒排索引
    # =====================================================

    def _build_person_index(self):
        if self._person_to_doc_cache:
            return self._person_to_doc_cache

        logger.info("构建人名索引...")
        start_time = time.time()
        try:
            all_docs = self.doc_db.get()
            person_index = {}
            for metadata in all_docs['metadatas']:
                doc_id = metadata.get('doc_id')
                person_names = metadata.get('person_names', [])
                if isinstance(person_names, str):
                    # 落库经 _sanitize_metadata，list 型 person_names 读回是
                    # JSON 数组串；兼容历史逗号串
                    person_names = json.loads(person_names) if person_names.startswith("[") else [person_names]
                for person in person_names:
                    if person not in person_index:
                        person_index[person] = set()
                    person_index[person].add(doc_id)
            self._person_to_doc_cache = {
                person: list(doc_ids)
                for person, doc_ids in person_index.items()
            }
            elapsed = time.time() - start_time
            logger.info(f"人名索引构建完成: {len(self._person_to_doc_cache)} 个, 耗时 {elapsed:.2f}s")
        except Exception as e:
            logger.error(f"人名索引构建失败: {e}")
            self._person_to_doc_cache = {}
        return self._person_to_doc_cache

    # =====================================================
    # 公共入口
    # =====================================================

    def ask(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        kb_ids: list[str] | None = None,
        subject_type: str = "",
        department: str = "",
        permissions: Iterable[str] | None = None,
    ) -> str:
        """提问入口：3 段式 — 准备 → 执行 → 清理。

        拆解后便于单测和异常定位；行为完全兼容旧版。
        Phase 4: 首轮问答命中缓存时跳过 LLM 生成（~4.8s），多轮对话不走缓存。
        kb_ids: 多知识库指定（客服系统用），优先级高于 kb_id。
        subject_type/department: 主体属性（customer/employee+部门），检索侧
        授权用；空 = 未声明主体，保持旧行为（见 knowledge_base.authorized_kbs）。
        """
        self.last_answer_meta: dict = {}
        logger.info(f"收到问题: {question[:80]} (session={session_id}, kb={kb_id})")
        self._prepare_context(kb_id, question, kb_ids=kb_ids,
                              subject_type=subject_type, department=department,
                              permissions=permissions)
        try:
            if not self._check_resources():
                return "系统资源紧张，请稍后重试"

            is_first_turn = session_id not in self._seen_sessions
            if is_first_turn and self._session_has_history(session_id):
                # 进程重启后本地标记丢失，但会话实际有多轮历史 → 不走首轮缓存
                is_first_turn = False

            if is_first_turn:
                cached = self._check_answer_cache(question, kb_id)
                if cached is not None:
                    self._mark_session_seen(session_id)
                    return cached

            answer = self._execute_chain(question, session_id)
            self._mark_session_seen(session_id)

            self._snapshot_answer_meta()

            if is_first_turn and answer and not self._is_rejection(answer):
                self._write_answer_cache(question, kb_id, answer)

            return answer
        finally:
            self._cleanup()

    def _prepare_context(self, kb_id: str, question: str, kb_ids: list[str] | None = None,
                         subject_type: str = "", department: str = "",
                         permissions: Iterable[str] | None = None):
        """注入 kb_id + QueryAnalyzer metadata → contextvars metadata_filter。

        主体属性以本次调用声明为准回填到运行态借读的权威身份实例
        （组合非复制）：图路径该实例已由 RequestContext.bind() 注入；
        CS/eval/直连路径无图上下文，用默认实例。mf 为空时提前返回、
        不触碰上下文——与旧实现"未 set 即默认空身份"语义一致。
        """
        from backend.rag.context import RagRequestState, get_context, set_context
        from backend.rag.retrieval.query_analyzer import QueryAnalyzer
        from backend.rag.routing.kb_router import KBRouter
        from backend.rag.retrieval.kb_filter import build_kb_filter

        current_identity = get_context().identity
        effective_subject_type = (
            subject_type or getattr(current_identity, "subject_type", "")
        )
        effective_department = (
            department or getattr(current_identity, "department", "")
        )
        effective_permissions = (
            permissions
            if permissions is not None
            else getattr(current_identity, "permissions", None)
        )
        mf: dict = {}

        # kb_ids（多知识库）优先级最高 → 显式 kb_id → KB Router 推断
        if kb_ids and len(kb_ids) > 1:
            mf["$or"] = [{"kb_id": kid} for kid in kb_ids]
        elif kb_ids and len(kb_ids) == 1:
            mf["kb_id"] = kb_ids[0]
        else:
            explicit_kb = bool(kb_id and kb_id not in ("*", "default"))
            if explicit_kb:
                mf["kb_id"] = kb_id
            else:
                try:
                    router = KBRouter()
                    kb_result = router.route(question)
                    candidate_ids = [c["kb_id"] for c in kb_result.get("candidates", [])]
                    kb_filter = build_kb_filter(candidate_ids)
                    if kb_filter:
                        mf.update(kb_filter)
                except Exception:
                    logger.debug("kb_filter 合并失败", exc_info=True)

        # QueryAnalyzer → doc_type / business_domain 过滤
        try:
            qa = QueryAnalyzer()
            pq = qa.analyze(question)
            qf = pq.to_metadata_filter()
            mf.update({k: v for k, v in qf.items() if v})
        except Exception:
            logger.debug("query_filter 合并失败", exc_info=True)

        # 保留旧契约：没有过滤条件且调用方没有声明任何授权属性时，
        # 不创建新的 RAG 状态，避免覆盖图路径已绑定的身份实例。
        if (
            not mf
            and not effective_subject_type
            and not effective_department
            and effective_permissions is None
        ):
            return

        ctx = RagRequestState(
            metadata_filter=mf,
            intent_label=pq.intent if 'pq' in dir() else "",
            query=question,
            identity=get_context().identity,
        )
        ctx.identity.subject_type = effective_subject_type
        ctx.identity.department = effective_department
        ctx.identity.permissions = (
            None
            if effective_permissions is None
            else tuple(sorted(set(effective_permissions)))
        )
        set_context(ctx)
        logger.info(f"[RAG.ask] metadata_filter={mf}")

    def _check_resources(self) -> bool:
        """资源监控。返回 True=可继续，False=拒绝。"""
        if not ENABLE_RESOURCE_MONITOR:
            return True
        resource_monitor.increment_request()
        if not resource_monitor.check_resources():
            logger.warning("系统资源紧张，请求被拒绝")
            return False
        resource_monitor.log_status()
        return True

    def _execute_chain(self, question: str, session_id: str) -> str:
        """执行 chain 调用并记录耗时。"""
        start_time = time.time()
        try:
            result = self.lc_chain.ask(question, session_id=session_id)
            elapsed = time.time() - start_time
            logger.info(f"请求完成，耗时: {elapsed:.2f}s")
            if elapsed > OVERALL_REQUEST_TIMEOUT * 0.8:
                logger.warning(f"请求耗时较长: {elapsed:.2f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"请求失败 (耗时: {elapsed:.2f}s): {e}", exc_info=True)
            raise

    def _snapshot_answer_meta(self):
        """从 chain 快照置信度/证据/来源信息，供下游（客服知识服务、
        rag-server /ask、远端代理）读取。

        sources 为结构化引用列表（index/filename/doc_type/score），
        供前端 SourceCard 与客服置信度判定使用；截断防 meta 膨胀。
        """
        try:
            chain = self.lc_chain
            meta = {}
            if hasattr(chain, "_last_meta") and isinstance(chain._last_meta, dict):
                meta.update(chain._last_meta)
            if hasattr(chain, "_last_sources"):
                sources = chain._last_sources or []
                meta["source_count"] = len(sources)
                meta["sources"] = sources[:8]
            self.last_answer_meta = meta
        except Exception:
            self.last_answer_meta = {}

    def _check_answer_cache(self, question: str, kb_id: str) -> str | None:
        """首轮问答缓存查询。命中返回缓存答案，未命中返回 None。

        RAG_ANSWER_CACHE_ENABLED=false 时整体旁路（调试看真实生成）。
        """
        if not RAG_ANSWER_CACHE_ENABLED:
            return None
        try:
            from backend.rag.answer_cache import get_answer_cache
            from backend.rag.context import get_context
            from backend.config.llm import LLM_MODEL
            ctx = get_context()
            ident = ctx.identity
            scope = self._authorization_scope(ident)
            cached = get_answer_cache().get(
                question, kb_id, ctx.metadata_filter, LLM_MODEL, scope=scope,
            )
            if cached is not None:
                logger.info(f"[RAG.ask] 缓存命中: {question[:60]}")
            return cached
        except Exception as e:
            logger.debug(f"[RAG.ask] 缓存查询失败（非致命）: {e}")
            return None

    def _write_answer_cache(self, question: str, kb_id: str, answer: str) -> None:
        """首轮问答成功后写入缓存。失败不影响主流程；开关关闭时跳过。"""
        if not RAG_ANSWER_CACHE_ENABLED:
            return
        try:
            from backend.rag.answer_cache import get_answer_cache
            from backend.rag.context import get_context
            from backend.config.llm import LLM_MODEL
            ctx = get_context()
            ident = ctx.identity
            scope = self._authorization_scope(ident)
            get_answer_cache().put(
                question, kb_id, ctx.metadata_filter, LLM_MODEL, answer,
                scope=scope,
            )
        except Exception as e:
            logger.debug(f"[RAG.ask] 缓存写入失败（非致命）: {e}")

    @staticmethod
    def _is_rejection(answer: str) -> bool:
        """判断答案是否为拒答（拒答不缓存 — 文档更新后可能可以回答）。

        2026-09-15 补漏：EvidenceGate 的标准 NO_EVIDENCE 话术
        "知识库暂无相关资料。" 此前不在标记表中 → 拒答被当正常答案缓存
        1 小时（实测污染整轮验证：授权过滤导致的拒答被复用给其他主体）。
        """
        rejection_markers = (
            "知识库暂无相关资料",
            "知识库中未找到",
            "无法找到",
            "没有足够的信息",
            "无法回答",
            "资料不足",
            "未能获取任何有效数据",
        )
        return any(marker in answer for marker in rejection_markers)

    @staticmethod
    def _authorization_scope(identity) -> str:
        """生成包含主体、部门和文档权限的缓存隔离键。"""
        permissions = getattr(identity, "permissions", None)
        permission_scope = (
            "-" if permissions is None else ",".join(sorted(set(permissions))) or "-"
        )
        return (
            f"{getattr(identity, 'subject_type', '') or '-'}:"
            f"{getattr(identity, 'department', '') or '-'}:"
            f"{permission_scope}"
        )

    def retrieve_knowledge(
        self,
        question: str,
        kb_id: str = "default",
        top_k: int = 3,
        subject_type: str = "",
        department: str = "",
        permissions: Iterable[str] | None = None,
    ) -> str:
        """轻量检索：只检索不生成回答，供 BusinessAnalyzer 等下游使用。

        与 ask() 的区别:
          - ask(): 完整链路 BM25→向量→rerank→LLM→evidence gate（~30-120s）
          - retrieve_knowledge(): 只 BM25→向量→rerank，返回原始文本（~3-5s）

        用于需要用 RAG 内容做后续分析的场景（非直接回答用户）。
        """
        import time as _time
        t0 = _time.monotonic()

        self._prepare_context(
            kb_id,
            question,
            subject_type=subject_type,
            department=department,
            permissions=permissions,
        )
        try:
            # 读取 _prepare_context 注入的 metadata_filter（KB 路由 + QueryAnalyzer）
            try:
                from backend.rag.context import get_context
                mf = get_context().metadata_filter
                user_permissions = get_context().identity.permissions
            except Exception:
                mf = None
                user_permissions = None

            chunks = []
            # BM25 检索 —— LangChain BM25Retriever 的公开接口是 .invoke(query)
            # （旧代码误用 .search，BM25 腿 100% 断，被软降级吞掉）；它不支持
            # metadata 过滤，结果按 Chroma where 语义手工后过滤。
            try:
                from backend.rag.permissions import filter_documents_by_permission
                bm25_results = self.bm25.invoke(question)
                for doc in bm25_results:
                    if not self._doc_matches_filter(getattr(doc, "metadata", {}), mf):
                        continue
                    if not filter_documents_by_permission([doc], user_permissions):
                        continue
                    chunks.append(doc.page_content if hasattr(doc, 'page_content') else str(doc))
            except Exception as e:
                # BM25 失败 → 降级只用向量检索（软降级），留痕；全部失败时 chunks 为空返回 ""
                logger.warning(f"[RAG.retrieve] BM25 检索失败，跳过: {e}", exc_info=True)

            # 向量检索 —— CustomRetriever 的接口是 .retrieve（旧代码误用
            # LangChain 的 get_relevant_documents，向量腿同样 100% 断）。
            # 原生支持 metadata_filter，KB/域过滤在此生效。
            try:
                vec_results = self.chunk_retriever.retrieve(
                    question, k=top_k, metadata_filter=mf,
                )
                for doc in vec_results[:top_k]:
                    content = doc.page_content if hasattr(doc, 'page_content') else str(doc)
                    if content not in chunks:
                        chunks.append(content)
            except Exception as e:
                # 向量检索失败 → 保留 BM25 结果（软降级），留痕
                logger.warning(f"[RAG.retrieve] 向量检索失败，跳过: {e}", exc_info=True)

            elapsed = _time.monotonic() - t0
            logger.info(
                f"[RAG.retrieve] {len(chunks)} chunks, "
                f"{elapsed:.1f}s (skip LLM generation)"
            )

            if not chunks:
                return ""
            return "\n\n---\n\n".join(chunks[:top_k])
        finally:
            self._cleanup()

    @staticmethod
    def _doc_matches_filter(meta: dict, mf: dict | None) -> bool:
        """按 Chroma where 表达式语义对单个文档 metadata 做匹配。

        BM25Retriever 无 metadata 过滤参数（向量腿的过滤在 CustomRetriever
        内部完成），BM25 命中结果在此统一后过滤，防止跨知识库泄漏。
        """
        if not mf:
            return True

        def match(cond, val) -> bool:
            if isinstance(cond, dict):
                if "$in" in cond:
                    return val in cond["$in"]
                if "$eq" in cond:
                    return val == cond["$eq"]
                if "$ne" in cond:
                    return val != cond["$ne"]
                return True
            return val == cond

        def where(expr: dict) -> bool:
            for k, v in expr.items():
                if k == "$and":
                    if not all(where(sub) for sub in v):
                        return False
                elif k == "$or":
                    if not any(where(sub) for sub in v):
                        return False
                elif not match(v, (meta or {}).get(k)):
                    return False
            return True

        return where(mf)

    def _cleanup(self):
        """清理 contextvars（无论成功失败都执行）。"""
        from backend.rag.context import clear_context
        clear_context()


# 线程安全单例（供 FastAPI deps + MCP server 共用）
import threading as _threading
_pipeline_lock = _threading.Lock()
_pipeline_singleton: RAGPipeline | None = None
_pipeline_init_error: str | None = None
_pipeline_initializing: bool = False


def _get_local_pipeline() -> RAGPipeline:
    """惰性初始化本地 RAGPipeline 单例（线程安全，可能阻塞数十分钟）。

    RAGPipeline() 构造含全量增量同步，期间持 _pipeline_lock。
    【禁止】在事件循环线程直接调用 —— async 端点必须用
    `await asyncio.to_thread(get_rag_pipeline)`，状态检查用非阻塞的
    get_rag_pipeline_state()。
    rag-server（backend/services/rag_server.py）直接调用本函数，
    绕过 RAG_MODE 路由，防止服务端误配 remote 时自我代理。
    """
    global _pipeline_singleton, _pipeline_init_error, _pipeline_initializing
    if _pipeline_singleton is None:
        with _pipeline_lock:
            if _pipeline_singleton is None:
                _pipeline_initializing = True
                try:
                    _pipeline_singleton = RAGPipeline()
                    _pipeline_init_error = None
                    logger.info("[pipeline] RAGPipeline 单例初始化成功")
                except Exception as e:
                    _pipeline_init_error = str(e)
                    logger.error(f"[pipeline] RAGPipeline 初始化失败: {e}")
                finally:
                    _pipeline_initializing = False
    if _pipeline_init_error is not None and _pipeline_singleton is None:
        raise RuntimeError(f"RAG 服务不可用（重试中）: {_pipeline_init_error}")
    return _pipeline_singleton


def get_rag_pipeline():
    """RAG pipeline 统一入口（所有消费方经此获取，勿直接调 _get_local_pipeline）。

    RAG_MODE=local（默认）→ 本地单例（历史行为，含索引同步阻塞）
    RAG_MODE=remote      → 远端代理（backend/rag/client.py，同 ask/retrieve 签名，
                            本进程不加载 embedding/Chroma）
    返回类型：RAGPipeline | RAGServiceProxy（鸭子类型兼容问答面）。
    """
    # 函数内读取（非 import 期固化）：测试与运行时可动态切换
    from backend.config.rag import RAG_MODE
    if RAG_MODE == "remote":
        from backend.rag.client import get_rag_proxy
        return get_rag_proxy()
    return _get_local_pipeline()


def _get_local_pipeline_state() -> dict:
    """本地单例状态 —— 不触碰 _pipeline_lock，可在事件循环线程安全调用。

    返回 {"state": "ready"|"initializing"|"error"|"not_started", ...}
    """
    if _pipeline_singleton is not None:
        return {"state": "ready"}
    if _pipeline_initializing:
        return {"state": "initializing", "message": "RAG 管道初始化中（含索引同步），请稍后重试"}
    if _pipeline_init_error is not None:
        return {"state": "error", "error": _pipeline_init_error}
    return {"state": "not_started"}


def get_rag_pipeline_state() -> dict:
    """非阻塞状态查询（供 deps.get_rag_status 等使用）。

    remote 模式返回 {"state": "remote", "endpoint": ...}，
    就绪与否由真正调用时的 HTTP 结果决定（fail-fast，错误信息含排查指引）。
    """
    from backend.config.rag import RAG_MODE
    if RAG_MODE == "remote":
        from backend.config.rag import RAG_SERVICE_URL
        return {"state": "remote", "endpoint": RAG_SERVICE_URL}
    return _get_local_pipeline_state()
