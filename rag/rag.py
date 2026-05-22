import asyncio
import os
import hashlib
import shutil
import time
from functools import lru_cache
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from llm.llm_factory import llm
from tools.Entity_recognition import is_person_query
from tools.metadata import extract_person_names, build_all_metadata_async
from tools.Loader import load_documents_from_directory
from tools.keyword import extract_chunk_keywords
from rag.hybrid_search import hybrid_retrieve, rrf_fusion_docs
from rag.multi_query import generate_multi_queries
from rag.rewrite import rewrite_query
from rag.retriever import CustomRetriever
from rag.bm25_retriever import build_bm25_retriever
from rag.reranker import rerank
from config import (
    EMBEDDING_MODEL_PATH,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    BM25_SEARCH_K,
    HYBRID_SEARCH_K,
    RERANK_TOP_K,
    MULTI_QUERY_NUM,
    CHROMA_PATH,
    DOC_DB_PATH,
    DOCS_DIRECTORY,
    DOC_LEVEL_KEYWORDS,
    OVERALL_REQUEST_TIMEOUT,
    ENABLE_RESOURCE_MONITOR,
    global_keywords,
    need_multi_query_keywords,
)
from utils.logger import logger
from utils.resource_monitor import resource_monitor

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'


class RAGPipeline:
    """RAG 知识库问答管道"""

    def __init__(self):
        self.vectordb = None
        self.doc_db = None
        self.chunk_retriever = None
        self.bm25 = None
        self._person_to_doc_cache = {}
        self._init()

    # =====================================================
    # 初始化
    # =====================================================

    def _init(self):
        self._load_and_chunk()
        self._build_doc_index()
        self._build_metadata()
        self._init_embedding()
        self._init_vector_dbs()
        self._init_retrievers()
        logger.info("RAG 管道初始化完成")

    def _load_and_chunk(self):
        logger.info("加载文档...")
        documents = load_documents_from_directory(DOCS_DIRECTORY)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )
        self.docs = splitter.split_documents(documents)
        logger.info(f"文档切分完成: {len(documents)} 个原始文档 → {len(self.docs)} 个 chunk")

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
        doc_level_texts, doc_level_meta = asyncio.run(
            build_all_metadata_async(self.docs, self.doc_map)
        )
        self.doc_level_texts = doc_level_texts
        self.doc_level_meta = doc_level_meta
        logger.info(f"元数据构建完成: {len(doc_level_texts)} 个文档级, {len(self.docs)} 个 chunk 级")

    def _init_embedding(self):
        self.embedding = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_PATH)

    def _init_vector_dbs(self):
        self.vectordb = self._load_or_create_db(
            CHROMA_PATH,
            create_fn=lambda: Chroma.from_documents(
                self.docs, embedding=self.embedding, persist_directory=CHROMA_PATH,
            ),
            db_type="chunk 级",
        )
        self.doc_db = self._load_or_create_db(
            DOC_DB_PATH,
            create_fn=lambda: Chroma.from_texts(
                texts=self.doc_level_texts,
                embedding=self.embedding,
                metadatas=self.doc_level_meta,
                persist_directory=DOC_DB_PATH,
            ),
            db_type="文档级",
        )

    def _load_or_create_db(self, db_path, create_fn, db_type):
        if not self._check_db_version(db_path):
            db = Chroma(persist_directory=db_path, embedding_function=self.embedding)
            logger.info(f"加载现有{db_type}向量库: {db_path}")
            return db
        db = create_fn()
        logger.info(f"创建新{db_type}向量库: {db_path}")
        self._save_db_version(db_path)
        return db

    def _init_retrievers(self):
        self.chunk_retriever = CustomRetriever(self.vectordb)
        self.bm25 = build_bm25_retriever(self.docs, k=BM25_SEARCH_K)
        self.person_index = self._build_person_index()

        # 构建 LangChain 高层 Chain（历史记忆 + 上下文压缩）
        from rag.langchain_chain import RAGChain
        self.lc_chain = RAGChain(
            doc_db=self.doc_db,
            vectordb=self.vectordb,
            chunk_retriever=self.chunk_retriever,
            bm25=self.bm25,
            person_index=self.person_index,
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
    # 路由判断
    # =====================================================

    @staticmethod
    def is_doc_level_question(q: str) -> bool:
        return any(k in q for k in DOC_LEVEL_KEYWORDS)

    @staticmethod
    def need_global_search(question: str) -> bool:
        question_lower = question.lower()
        if any(k in question_lower for k in global_keywords):
            logger.info(f"检测到全局检索需求: {question[:30]}...")
            return True
        return False

    @staticmethod
    def need_multi_query(q: str) -> bool:
        q_lower = q.lower()
        if len(q_lower) > 8:
            return True
        if any(k in q_lower for k in need_multi_query_keywords):
            return True
        return False

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

    def get_doc_ids_by_person(self, person_name: str) -> list:
        index = self._build_person_index()
        return index.get(person_name, [])

    # =====================================================
    # 关键词过滤
    # =====================================================

    @staticmethod
    def _score_by_keyword_overlap(question: str, doc_results: list, fallback_k: int = 3) -> list:
        query_kw = set(extract_chunk_keywords(question))
        if not query_kw:
            return doc_results

        scored = []
        for doc in doc_results:
            doc_kw = set(doc.metadata.get("keywords", []))
            overlap = len(query_kw & doc_kw)
            scored.append((doc, overlap))

        scored.sort(key=lambda x: x[1], reverse=True)
        filtered = [doc for doc, score in scored if score > 0]

        if not filtered:
            logger.info(f"关键词过滤无命中(query_kw={query_kw})，回退前{fallback_k}个")
            filtered = [doc for doc, _ in scored[:fallback_k]]

        logger.info(f"关键词过滤: query_kw={query_kw}, 命中 {len(filtered)}/{len(doc_results)}")
        return filtered

    @classmethod
    def _filter_docs_by_keywords(cls, question: str, doc_results: list, fallback_k: int = 3) -> list:
        filtered = cls._score_by_keyword_overlap(question, doc_results, fallback_k)
        return list(set([
            doc.metadata.get("doc_id")
            for doc in filtered
            if doc.metadata.get("doc_id")
        ]))

    # =====================================================
    # 公共入口
    # =====================================================

    def ask(self, question: str, session_id: str = "default") -> str:
        print("\n==============================")
        print("问题:", question)
        print("==============================")

        logger.info(f"收到问题: {question} (session={session_id})")

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

    # =====================================================
    # Doc-level QA
    # =====================================================

    async def _doc_level_answer(self, question: str) -> str:
        try:
            rewritten_list = rewrite_query(question)
            if not rewritten_list:
                rewritten_list = [question]
            logger.info(f"问题重写完成: {rewritten_list}")

            person_names_list = extract_person_names(question)
            logger.info(f"提取到人名: {person_names_list if person_names_list else '无'}")

            docs = []
            docs_rrf = None
            if person_names_list:
                person_name = (
                    person_names_list[0]
                    if isinstance(person_names_list, list)
                    else person_names_list
                )
                logger.info(f"使用人名过滤检索: {person_name}")
                matched_doc_ids = self.get_doc_ids_by_person(person_name)

                if matched_doc_ids:
                    logger.info(f"倒排索引命中 {len(matched_doc_ids)} 个文档")
                    try:
                        results = self.doc_db.get(
                            where={"doc_id": {"$in": list(matched_doc_ids)}}
                        )
                        docs = []
                        for i, content in enumerate(results["documents"]):
                            doc_obj = type("Doc", (), {
                                "page_content": content,
                                "metadata": results["metadatas"][i],
                            })()
                            docs.append(doc_obj)
                        logger.info(f"人名过滤后找到 {len(docs)} 个文档")
                    except Exception as e:
                        logger.error(f"批量获取文档失败: {e}", exc_info=True)
                else:
                    logger.warning(f"未找到包含 '{person_name}' 的文档")

            if not docs:
                if is_person_query(question):
                    logger.warning("人物查询但未命中文档，拒绝 fallback")
                    return "未找到该人物相关信息。"

                logger.info(f"进入 Hybrid Retrieval (Multi-query: {len(rewritten_list)} 个查询)")

                all_vector_docs = []
                all_bm25_docs = []

                async def search_single_query(q):
                    v_docs = await asyncio.to_thread(self.doc_db.similarity_search, q, 3)
                    b_docs = await asyncio.to_thread(self.bm25.invoke, q)
                    return v_docs, b_docs

                tasks = [search_single_query(q) for q in rewritten_list]
                results = await asyncio.gather(*tasks)

                for v_docs, b_docs in results:
                    all_vector_docs.extend(v_docs)
                    all_bm25_docs.extend(b_docs)

                all_vector_docs = self._score_by_keyword_overlap(question, all_vector_docs)
                all_bm25_docs = self._score_by_keyword_overlap(question, all_bm25_docs)

                docs_rrf = rrf_fusion_docs(all_vector_docs, all_bm25_docs, k=5)
                logger.info(
                    f"Hybrid 检索完成: {len(all_vector_docs)} vector + "
                    f"{len(all_bm25_docs)} BM25 → {len(docs_rrf)} 融合结果"
                )

            docs_to_rerank = docs_rrf if docs_rrf is not None else docs

            if docs_to_rerank:
                reranked_results = await asyncio.to_thread(
                    lambda: rerank(question, docs_to_rerank, top_k=RERANK_TOP_K)
                )
                docs = [doc for doc, _ in reranked_results]
            else:
                docs = []

            docs = docs[:5]
            logger.info(f"rerank 后结果: {len(docs)} 个文档")

            if not docs:
                logger.warning(f"文档级检索未找到相关内容: {question}")
                return "抱歉，未找到相关文档内容。"

            context_parts = []
            for idx, d in enumerate(docs, 1):
                source = d.metadata.get("source_file", "unknown")
                content = d.page_content[:1500]
                context_parts.append(
                    f"\n[文档 {idx}]\n来源: {source}\n内容长度: {len(content)} 字\n\n详细内容:\n{content}\n"
                )

            context = "\n\n".join(context_parts)
            if is_person_query(question):
                context += "\n请尽可能详细展开，不少于8-12句。"

            logger.info(f"构建上下文完成: {len(docs)} 个文档 | {len(context)} 字符")

            prompt = f"""
    你是企业知识库助手。

请基于提供的资料回答问题。

要求：
1. 不要编造不存在的信息
2. 如果资料不足，基于已有信息进行归纳，不要凭空补充
3. 输出必须详细，不要只用一句话回答
4. 使用分点或分段说明
5. 资料中提到的背景、细节、职责应充分展开

资料如下：
{context}

问题：
{question}

请输出结构化答案：
"""
            llm_response = await asyncio.to_thread(llm.invoke, prompt)
            response = llm_response.content
            logger.info(f"文档级问答完成: {question[:50]}...")
            return response

        except Exception as e:
            logger.error(f"文档级问答失败: {e}", exc_info=True)
            return f"处理问题时出现错误: {str(e)}"

    # =====================================================
    # Chunk-level QA
    # =====================================================

    async def _chunk_level_answer(self, question: str) -> str:
        try:
            rewritten = rewrite_query(question)
            if not rewritten:
                rewritten = [question]
            logger.info(f"问题重写完成: {rewritten}")

            person_names_list = extract_person_names(question)
            person_name = None
            if person_names_list:
                logger.info(f"检测到人名实体: {person_names_list}")
                person_name = person_names_list[0] if isinstance(person_names_list, list) else person_names_list

            # —— Stage 1: 文档检索 ——
            if self.need_global_search(question):
                logger.info("全局检索模式（无 Doc Filter）")
                doc_ids = None
            else:
                logger.info("两阶段检索模式（Doc → Chunk）")
                if person_name:
                    matched_doc_ids = self.get_doc_ids_by_person(person_name)
                    if matched_doc_ids:
                        doc_ids = matched_doc_ids
                        logger.info(f"通过人名匹配到 {len(doc_ids)} 个文档")
                    else:
                        logger.warning(f"未找到人名文档，降级相似度检索")
                        doc_results = await asyncio.to_thread(
                            self.doc_db.similarity_search, rewritten[0], 5
                        )
                        doc_ids = self._filter_docs_by_keywords(question, doc_results)
                else:
                    doc_results = await asyncio.to_thread(
                        self.doc_db.similarity_search, rewritten[0], 5
                    )
                    if not doc_results:
                        logger.warning(f"Doc-level 检索未找到相关文档: {rewritten}")
                        return "抱歉，未找到相关的文档内容。"
                    doc_ids = self._filter_docs_by_keywords(question, doc_results)

                logger.info(f"Stage 1 - 召回 {len(doc_ids)} 个相关文档: {doc_ids}")

            # —— Stage 2: Chunk 级检索 ——
            if self.need_multi_query(rewritten[0]):
                logger.info("启用 Multi Query")
                queries = await asyncio.to_thread(
                    lambda: generate_multi_queries(rewritten[0], MULTI_QUERY_NUM)
                )
            else:
                logger.info("跳过 Multi Query")
                queries = rewritten

            async def search_single_query(q):
                return await asyncio.to_thread(
                    lambda: hybrid_retrieve(
                        q, self.chunk_retriever, self.bm25,
                        k=HYBRID_SEARCH_K, doc_ids=doc_ids,
                    )
                )

            tasks = [search_single_query(q) for q in queries]
            results = await asyncio.gather(*tasks)

            all_docs = []
            seen = set()
            for res_list in results:
                for d in res_list:
                    cid = d.metadata["chunk_id"]
                    if cid not in seen:
                        seen.add(cid)
                        all_docs.append(d)

            if not all_docs:
                logger.warning(f"片段级检索未找到相关内容: {rewritten}")
                return "抱歉，未找到相关的详细内容。"

            logger.info(f"Stage 2 - 召回 {len(all_docs)} 个 chunks")

            reranked = await asyncio.to_thread(
                lambda: rerank(question, all_docs, top_k=RERANK_TOP_K)
            )
            if not reranked:
                logger.warning(f"重排序后无有效结果: {question}")
                return "抱歉，找到的内容相关性较低，无法提供准确答案。"

            context = "\n\n".join(d.page_content for d, _ in reranked)

            prompt = f"""
你是企业知识库助手。

请严格基于提供的上下文回答问题。

要求：
1. 不要编造不存在的信息
2. 如果上下文没有相关内容，直接回答"不知道"
3. 回答尽量简洁准确

{context}

问题：{question}
"""
            llm_response = await asyncio.to_thread(llm.invoke, prompt)
            response = llm_response.content
            logger.info(f"片段级问答完成，问题: {question[:50]}...")
            return response

        except Exception as e:
            logger.error(f"片段级问答失败: {e}", exc_info=True)
            return f"处理问题时出现错误: {str(e)}"



