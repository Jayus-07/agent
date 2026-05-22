"""
LangChain LCEL 主 Chain
= 历史感知检索 + 多查询 + 上下文压缩 + 会话记忆

架构:
  RunnableWithMessageHistory          ← 持久化会话记忆
    └── RunnableBranch                ← 路由: doc-level / chunk-level
          ├── doc_chain               ← 文档级 QA
          └── chunk_chain             ← 片段级 QA (含 MultiQuery)
"""
from langchain_classic.chains import create_history_aware_retriever, create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.runnables import RunnableBranch
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.chat_message_histories import SQLChatMessageHistory

from llm.llm_factory import llm
from rag.langchain_retrievers import DocLevelRetriever, ChunkLevelRetriever
from config import (
    DOC_LEVEL_KEYWORDS,
    RERANK_TOP_K,
    CHAT_HISTORY_DB,
    ENABLE_HISTORY_AWARE_RETRIEVAL,
    ENABLE_LLM_COMPRESSION,
)
from utils.logger import logger


# =====================================================
# Prompt: 历史感知查询重写
# =====================================================

CONTEXTUALIZE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是企业知识库的查询重写助手。

根据对话历史，将用户问题重新表述为独立的检索查询。

规则:
1. 如果用户使用代词（他、她、这个、那个、它），请替换为对话历史中的具体实体
2. 如果问题已经独立完整，直接返回原问题
3. 不要回答问题，只输出改写后的查询
4. 保留所有专有名词、技术术语、业务词汇
5. 不要添加解释或 markdown 格式"""),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])

# =====================================================
# Prompt: QA 回答
# =====================================================

QA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是企业知识库助手。

请基于提供的资料回答问题。

要求：
1. 不编造不存在的信息
2. 如果资料不足，基于已有信息归纳，不要凭空补充
3. 输出详细，使用分点或分段说明
4. 资料中提到的背景、细节、职责应充分展开

资料：
{context}"""),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])


# =====================================================
# 路由判断
# =====================================================

def _is_doc_level(input_dict: dict) -> bool:
    q = input_dict.get("input", "")
    return any(k in q for k in DOC_LEVEL_KEYWORDS)


# =====================================================
# 会话历史持久化
# =====================================================

def _get_session_history(session_id: str):
    return SQLChatMessageHistory(
        session_id=session_id,
        connection=CHAT_HISTORY_DB,
    )


# =====================================================
# Chain 构建器
# =====================================================

class RAGChain:
    """LangChain 高层封装的 RAG 管道"""

    def __init__(
        self,
        doc_db,
        vectordb,
        chunk_retriever,
        bm25,
        person_index: dict = None,
    ):
        self.doc_db = doc_db
        self.vectordb = vectordb
        self.chunk_retriever = chunk_retriever
        self.bm25 = bm25
        self.person_index = person_index or {}

        self._build_retrievers()
        self._build_chains()
        logger.info("LangChain RAG Chain 初始化完成")

    # =================================================
    # Step A: 构建 BaseRetriever 实例
    # =================================================

    def _build_retrievers(self):
        self.doc_retriever = DocLevelRetriever(
            doc_db=self.doc_db,
            bm25=self.bm25,
            person_index=self.person_index,
            k=5,
            rerank_top_k=RERANK_TOP_K,
        )

        self.chunk_retriever_base = ChunkLevelRetriever(
            doc_db=self.doc_db,
            vectordb=self.vectordb,
            chunk_retriever=self.chunk_retriever,
            bm25=self.bm25,
            person_index=self.person_index,
        )

    # =================================================
    # Step B: 构建两条 Chain
    # =================================================

    def _build_chains(self):
        # — 通用 QA chain —
        stuff_chain = create_stuff_documents_chain(llm, QA_PROMPT)

        # 注意包裹顺序: BaseRetriever → Compressor → MultiQuery → HistoryAware → RetrievalChain
        # create_history_aware_retriever 返回 Runnable, 不能放进 ContextualCompressionRetriever

        doc_retriever = self.doc_retriever
        chunk_retriever = self.chunk_retriever_base

        # 可选: LLM 提取式压缩 (包裹在 BaseRetriever 上)
        if ENABLE_LLM_COMPRESSION:
            from langchain_classic.retrievers.document_compressors import LLMChainExtractor

            llm_compressor = LLMChainExtractor.from_llm(llm)
            doc_retriever = ContextualCompressionRetriever(
                base_compressor=llm_compressor,
                base_retriever=doc_retriever,
            )
            chunk_retriever = ContextualCompressionRetriever(
                base_compressor=llm_compressor,
                base_retriever=chunk_retriever,
            )

        # Chunk 路径: MultiQueryRetriever (多角度查询)
        chunk_retriever = MultiQueryRetriever.from_llm(
            retriever=chunk_retriever,
            llm=llm,
        )

        if ENABLE_HISTORY_AWARE_RETRIEVAL:
            doc_retriever = create_history_aware_retriever(
                llm, doc_retriever, CONTEXTUALIZE_PROMPT
            )
            chunk_retriever = create_history_aware_retriever(
                llm, chunk_retriever, CONTEXTUALIZE_PROMPT
            )
        # 注意：history_aware_retriever 返回 Runnable, 之后不能再接 ContextualCompressionRetriever

        # 构建完整的 retrieval chain (Rerank 已在 BaseRetriever 内部完成)
        self.doc_chain = create_retrieval_chain(doc_retriever, stuff_chain)
        self.chunk_chain = create_retrieval_chain(chunk_retriever, stuff_chain)

        # — Branch 路由 —
        self.branch = RunnableBranch(
            (_is_doc_level, self.doc_chain),
            self.chunk_chain,  # default
        )

        # — 最外层: 会话记忆 —
        self.chain_with_memory = RunnableWithMessageHistory(
            self.branch,
            _get_session_history,
            input_messages_key="input",
            history_messages_key="chat_history",
            output_messages_key="answer",
        )

    # =================================================
    # Step C: 公共入口
    # =================================================


    def ask(self, question: str, session_id: str = "default") -> str:
        logger.info(f"[RAGChain] 收到问题: {question[:60]}... (session={session_id})")

        result = self.chain_with_memory.invoke(
            {"input": question},
            config={"configurable": {"session_id": session_id}},
        )
        return result["answer"]
