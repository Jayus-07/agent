"""RAG 管道 — 主入口"""
import os
import hashlib
import shutil
import time
from pathlib import Path

from langchain_huggingface import HuggingFaceEmbeddings
from backend.rag.vectorstore.knowledge_store import ChromaKnowledgeStore

from backend.rag.preprocessing.metadata import build_all_metadata_async
from backend.rag.preprocessing.loader import load_documents_from_directory
from backend.rag.base import CustomRetriever
from backend.rag.retrieval.bm25_store import BM25Store
from backend.rag.chain import RAGChain
from backend.config import (
    EMBEDDING_MODEL_PATH,
    BM25_SEARCH_K,
    CHROMA_PATH,
    DOC_DB_PATH,
    DOCS_DIRECTORY,
    DOC_REGISTRY_PATH,
    ENABLE_INCREMENTAL_INDEXING,
    ENABLE_MEMORY,
    OVERALL_REQUEST_TIMEOUT,
    ENABLE_RESOURCE_MONITOR,
)
from backend.shared.logger import logger
from backend.observability.resource import resource_monitor
from backend.shared.async_utils import run_async as _run_async

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'


class RAGPipeline:
    def __init__(self):
        self.vectordb = None
        self.doc_db = None
        self.chunk_retriever = None
        self.bm25 = None
        self._person_to_doc_cache = {}
        self._init()

    def _init(self):
        """初始化入口：4 个准备阶段 + 收尾。

        拆解后每个阶段职责单一，便于测试与定位。
        """
        self._prepare_documents()
        self._prepare_vector_store()
        self._prepare_retrievers()
        logger.info("RAG 管道初始化完成")

    def _prepare_documents(self):
        """阶段 1：加载并构建文档索引。"""
        self._load_and_chunk()
        self._build_doc_index()
        self._init_embedding()

    def _prepare_vector_store(self):
        """阶段 2：构建向量库（增量优先，回退全量重建）。"""
        used_incremental = (
            self._init_vector_dbs_incremental()
            if ENABLE_INCREMENTAL_INDEXING
            else False
        )
        if used_incremental:
            return

        # 全量重建路径
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
        for d in self.docs:
            fname = d.metadata["file_path"]
            name = os.path.basename(fname)
            if name not in self.doc_map:
                self.doc_map[name] = []
            self.doc_map[name].append(d.page_content)
        logger.info(f"文档级索引: {len(self.doc_map)} 个文档")

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
        self.embedding = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_PATH)

    def _init_vector_dbs_full(self):
        """全量重建向量库（兜底/首次运行）。"""
        self.vectordb = self._load_or_create_db(
            CHROMA_PATH,
            create_fn=lambda: ChromaKnowledgeStore.from_documents(
                self.docs, embedding=self.embedding, persist_directory=CHROMA_PATH,
            ),
            db_type="chunk 级",
        )
        self.doc_db = self._load_or_create_db(
            DOC_DB_PATH,
            create_fn=lambda: ChromaKnowledgeStore.from_texts(
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

        # 执行增量同步
        try:
            indexer = IncrementalIndexer(
                docs_dir=DOCS_DIRECTORY,
                vectordb=self.vectordb,
                doc_db=self.doc_db,
                embedding=self.embedding,
                registry=registry,
            )
            result = indexer.sync()
            logger.info(f"增量索引: {result}")
            return True
        except Exception as e:
            logger.warning(f"增量索引失败（{type(e).__name__}: {e}），将回退全量重建；如为 NameError 请检查 indexer 变量作用域")
            if 'registry' in locals():
                try:
                    registry.clear()
                except Exception:
                    pass
            return False

    def _load_existing_db(self, db_path: str, db_type: str):
        """加载已有向量库（不做版本检查，不创建）。"""
        db = ChromaKnowledgeStore(
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
        self._save_db_version(db_path)
        return db

    def _sync_registry_after_full_rebuild(self):
        """全量重建后将所有文档信息写入 registry，下次启动走增量。"""
        from backend.rag.indexing.doc_registry import DocumentRegistry
        from backend.rag.indexing.indexer import IncrementalIndexer

        try:
            registry = DocumentRegistry(DOC_REGISTRY_PATH)
            registry.clear()
        except Exception as e:
            logger.warning(f"无法初始化 registry: {e}")
            return

        # 扫描所有文档
        indexer = IncrementalIndexer(
            docs_dir=DOCS_DIRECTORY,
            vectordb=self.vectordb,
            doc_db=self.doc_db,
            embedding=self.embedding,
            registry=registry,
        )
        disk_files = indexer._scan_disk()

        for file_path, (file_hash, _, _) in disk_files.items():
            kb_id = indexer._derive_kb_id(file_path)
            doc_id = hashlib.md5(
                os.path.basename(file_path).encode()
            ).hexdigest()[:10]

            # 从 chunk 级向量库查找该文件的所有 chunk ID
            try:
                chunk_data = self.vectordb.get(
                    where={"file_path": file_path}
                )
                chunk_ids = chunk_data.get("ids", [])
            except Exception:
                chunk_ids = []

            # 从 doc 级向量库查找 doc_db_id
            doc_db_id = ""
            try:
                doc_data = self.doc_db.get(where={"doc_id": doc_id})
                doc_ids = doc_data.get("ids", [])
                doc_db_id = doc_ids[0] if doc_ids else ""
            except Exception:
                pass

            registry.register(
                file_path=file_path,
                doc_id=doc_id,
                file_hash=file_hash,
                kb_id=kb_id,
                chunk_ids=chunk_ids,
                doc_db_id=doc_db_id,
            )

        logger.info(
            f"Registry 同步完成: {registry.count()} 条记录"
        )

    def _init_retrievers(self):
        self.chunk_retriever = CustomRetriever(self.vectordb)

        # BM25: 优先从磁盘加载持久化索引，避免每次启动重建
        bm25_store = BM25Store()
        self.bm25 = bm25_store.load(k=BM25_SEARCH_K)
        if self.bm25 is None:
            logger.info("[RAG] BM25 索引不存在，全量重建...")
            self.bm25 = bm25_store.build(self.docs, k=BM25_SEARCH_K)
        elif bm25_store.is_stale:
            logger.info("[RAG] BM25 索引已过期（文档数为 0），重建...")
            self.bm25 = bm25_store.build(self.docs, k=BM25_SEARCH_K)
        else:
            logger.info(
                f"[RAG] BM25 索引从磁盘加载成功 "
                f"({bm25_store.doc_count()} 文档)，跳过重建"
            )

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

    # =====================================================
    # 版本校验
    # =====================================================

    @staticmethod
    def _compute_db_version() -> str:
        h = hashlib.md5()
        docs_path = Path(DOCS_DIRECTORY)
        for fpath in sorted(docs_path.rglob("*")):
            if fpath.is_file():
                h.update(str(fpath).encode())
                h.update(str(fpath.stat().st_size).encode())
                h.update(str(fpath.stat().st_mtime).encode())  # mtime 防同大小替换
        return h.hexdigest()

    @staticmethod
    def _need_rebuild(db_path: str) -> bool:
        """纯查询：版本不匹配或缺失则需要重建（不删文件）。"""
        version_file = os.path.join(db_path, ".version")
        if not os.path.exists(db_path):
            return True
        if os.path.exists(version_file):
            stored = open(version_file, encoding="utf-8").read().strip()
            current = RAGPipeline._compute_db_version()
            if stored == current:
                logger.info(f"向量库版本匹配: {db_path}")
                return False
        logger.warning(f"向量库版本不匹配或缺失，需要重建: {db_path}")
        return True

    @staticmethod
    def _rebuild_db(db_path: str) -> None:
        """副作用：删除旧库，由 _need_rebuild + create_fn 配套调用。"""
        shutil.rmtree(db_path, ignore_errors=True)

    @staticmethod
    def _save_db_version(db_path: str):
        version_file = os.path.join(db_path, ".version")
        with open(version_file, "w", encoding="utf-8") as f:
            f.write(RAGPipeline._compute_db_version())

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
                    person_names = [person_names]
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

    def ask(self, question: str, session_id: str = "default", kb_id: str = "default") -> str:
        """提问入口：3 段式 — 准备 → 执行 → 清理。

        拆解后便于单测和异常定位；行为完全兼容旧版。
        """
        logger.info(f"收到问题: {question[:80]} (session={session_id}, kb={kb_id})")
        self._prepare_context(kb_id, question)
        try:
            if not self._check_resources():
                return "系统资源紧张，请稍后重试"
            return self._execute_chain(question, session_id)
        finally:
            self._cleanup()

    def _prepare_context(self, kb_id: str, question: str):
        """注入 kb_id + QueryAnalyzer metadata → contextvars metadata_filter。"""
        from backend.rag.context import RequestContext, set_context
        from backend.rag.retrieval.query_analyzer import QueryAnalyzer
        from backend.rag.routing.kb_router import KBRouter
        from backend.rag.retrieval.kb_filter import build_kb_filter

        mf: dict = {}

        # KB Router → 候选 KB 列表 → $or filter
        try:
            router = KBRouter()
            kb_result = router.route(question)
            candidate_ids = [c["kb_id"] for c in kb_result.get("candidates", [])]
            kb_filter = build_kb_filter(candidate_ids)
            if kb_filter:
                mf.update(kb_filter)
        except Exception:
            pass

        # 兼容旧 kb_id 参数（显式指定时覆盖 Router）
        if kb_id and kb_id not in ("*", "default"):
            mf["kb_id"] = kb_id

        # QueryAnalyzer → doc_type / business_domain 过滤
        try:
            qa = QueryAnalyzer()
            pq = qa.analyze(question)
            qf = pq.to_metadata_filter()
            mf.update({k: v for k, v in qf.items() if v})
        except Exception:
            pass

        if not mf:
            return

        ctx = RequestContext(
            metadata_filter=mf,
            intent_label=pq.intent if 'pq' in dir() else "",
            query=question,
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

    def _cleanup(self):
        """清理 contextvars（无论成功失败都执行）。"""
        from backend.rag.context import clear_context
        clear_context()


# 线程安全单例（供 FastAPI deps + MCP server 共用）
import threading as _threading
_pipeline_lock = _threading.Lock()
_pipeline_singleton: RAGPipeline | None = None
_pipeline_init_error: str | None = None


def get_rag_pipeline() -> RAGPipeline:
    """惰性初始化 RAGPipeline 单例（线程安全）。"""
    global _pipeline_singleton, _pipeline_init_error
    if _pipeline_singleton is None:
        with _pipeline_lock:
            if _pipeline_singleton is None:
                try:
                    _pipeline_singleton = RAGPipeline()
                    _pipeline_init_error = None
                    logger.info("[pipeline] RAGPipeline 单例初始化成功")
                except Exception as e:
                    _pipeline_init_error = str(e)
                    logger.error(f"[pipeline] RAGPipeline 初始化失败: {e}")
    if _pipeline_init_error is not None and _pipeline_singleton is None:
        raise RuntimeError(f"RAG 服务不可用（重试中）: {_pipeline_init_error}")
    return _pipeline_singleton

