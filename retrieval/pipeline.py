"""RAG 管道 — 主入口"""
import asyncio
import concurrent.futures
import os
import hashlib
import shutil
import time
from pathlib import Path

from langchain_huggingface import HuggingFaceEmbeddings
from retrieval.knowledge_store import ChromaKnowledgeStore

from preprocessing.metadata import build_all_metadata_async
from preprocessing.loader import load_documents_from_directory
from retrieval.base import CustomRetriever
from retrieval.bm25 import build_bm25_retriever
from config import (
    EMBEDDING_MODEL_PATH,
    BM25_SEARCH_K,
    CHROMA_PATH,
    DOC_DB_PATH,
    DOCS_DIRECTORY,
    DOC_REGISTRY_PATH,
    ENABLE_INCREMENTAL_INDEXING,
    OVERALL_REQUEST_TIMEOUT,
    ENABLE_RESOURCE_MONITOR,
)
from utils.logger import logger
from utils.resource_monitor import resource_monitor

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'


def _run_async(coro):
    """安全地运行异步协程 — 兼容有/无事件循环两种场景"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


class RAGPipeline:
    def __init__(self):
        self.vectordb = None
        self.doc_db = None
        self.chunk_retriever = None
        self.bm25 = None
        self._person_to_doc_cache = {}
        self._init()

    def _init(self):
        self._load_and_chunk()
        self._build_doc_index()
        self._init_embedding()

        # 决定走增量还是全量重建
        if ENABLE_INCREMENTAL_INDEXING:
            used_incremental = self._init_vector_dbs_incremental()
        else:
            used_incremental = False

        if not used_incremental:
            # 全量重建路径: metadata → from_documents/from_texts
            self._build_metadata()
            self._init_vector_dbs_full()
            # 全量重建后同步 registry，下次启动即可走增量
            self._sync_registry_after_full_rebuild()

        self._init_retrievers()
        logger.info("RAG 管道初始化完成")

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
        doc_level_texts, doc_level_meta = _run_async(
            build_all_metadata_async(self.docs, self.doc_map)
        )
        self.doc_level_texts = doc_level_texts
        self.doc_level_meta = doc_level_meta
        logger.info(f"元数据构建完成: {len(doc_level_texts)} 个文档级, {len(self.docs)} 个 chunk 级")

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
        from retrieval.doc_registry import DocumentRegistry
        from retrieval.indexer import IncrementalIndexer

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
            logger.warning(f"增量索引失败: {e}，回退全量重建")
            # 销毁 registry 以便全量重建时重建
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
        if not self._check_db_version(db_path):
            return self._load_existing_db(db_path, db_type)
        db = create_fn()
        logger.info(f"创建新{db_type}向量库: {db_path}")
        self._save_db_version(db_path)
        return db

    def _sync_registry_after_full_rebuild(self):
        """全量重建后将所有文档信息写入 registry，下次启动走增量。"""
        from retrieval.doc_registry import DocumentRegistry
        from retrieval.indexer import IncrementalIndexer

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
        self.bm25 = build_bm25_retriever(self.docs, k=BM25_SEARCH_K)
        self.person_index = self._build_person_index()

        from retrieval.chain import RAGChain
        from memory import memory_manager
        self.lc_chain = RAGChain(
            doc_db=self.doc_db,
            vectordb=self.vectordb,
            chunk_retriever=self.chunk_retriever,
            bm25=self.bm25,
            person_index=self.person_index,
            memory_manager=memory_manager,
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
        return h.hexdigest()

    @staticmethod
    def _check_db_version(db_path: str) -> bool:
        version_file = os.path.join(db_path, ".version")
        current = RAGPipeline._compute_db_version()
        if not os.path.exists(db_path):
            return True
        if os.path.exists(version_file):
            stored = open(version_file, encoding="utf-8").read().strip()
            if stored == current:
                logger.info(f"向量库版本匹配: {db_path}")
                return False
        logger.warning(f"向量库版本不匹配或缺失，将重建: {db_path}")
        shutil.rmtree(db_path, ignore_errors=True)
        return True

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
        logger.info(f"问题: {question[:80]}  (kb={kb_id})")

        logger.info(f"收到问题: {question[:80]} (session={session_id}, kb={kb_id})")

        # KB 隔离：注入 kb_id 到 contextvars filter
        # "default" / "*" = 不隔离，查询所有知识库
        if kb_id and kb_id != "*" and kb_id != "default":
            from retrieval.context import RequestContext, set_context
            ctx = RequestContext(
                metadata_filter={"kb_id": kb_id},
                intent_label="",
                query=question,
            )
            set_context(ctx)
            logger.info(f"[RAG.ask] kb_id={kb_id} → metadata_filter 已注入")

        if ENABLE_RESOURCE_MONITOR:
            resource_monitor.increment_request()
            if not resource_monitor.check_resources():
                logger.warning("系统资源紧张，请求被拒绝")
                return "系统资源紧张，请稍后重试"
            resource_monitor.log_status()

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
