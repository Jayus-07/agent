"""
LangChain LCEL 主 Chain
= 历史感知检索 + 多查询 + 自适应检索 + 上下文压缩 + 会话记忆

架构:
  ask() 手动管理 SQLChatMessageHistory ← 持久化会话记忆
    └── chain                        ← ChunkLevel → MultiQuery → Adaptive → Rerank → HistoryAware
"""
from langchain_classic.chains import create_history_aware_retriever, create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_community.chat_message_histories import SQLChatMessageHistory

from llm.llm_factory import llm
from retrieval.retrievers import ChunkLevelRetriever, AdaptiveRetriever
from retrieval.reranker import RerankCompressor
from config import (
    CHAT_HISTORY_DB,
    ENABLE_HISTORY_AWARE_RETRIEVAL,
    ENABLE_LLM_COMPRESSION,
)
from utils.logger import logger


# =====================================================
# Prompt: 历史感知查询重写
# =====================================================

# 用于将带有上下文依赖的问题（如包含代词或省略）重写为独立的检索查询
CONTEXTUALIZE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是企业知识库的查询重写助手。

根据对话历史，将用户问题重新表述为独立的检索查询。

规则:
1. 如果用户使用代词（他、她、这个、那个、它），请替换为对话历史中的具体实体
2. 如果问题已经独立完整，直接返回原问题
3. 不要回答问题，只输出改写后的查询
4. 保留所有专有名词、技术术语、业务词汇
5. 不要添加解释或 markdown 格式"""),
    MessagesPlaceholder("chat_history"),  # 占位符，运行时注入对话历史
    ("human", "{input}"),
])

# =====================================================
# Prompt: QA 回答
# =====================================================

# 用于最终生成答案，结合检索到的文档和对话历史
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
# 会话历史持久化
# =====================================================

def _get_session_history(session_id: str):
    """
    根据 session_id 获取 SQLite 持久化的对话历史对象。
    使用 SQLChatMessageHistory 自动管理消息的存储和加载。
    """
    return SQLChatMessageHistory(
        session_id=session_id,
        connection=CHAT_HISTORY_DB,
    )


# =====================================================
# Chain 构建器
# =====================================================

class RAGChain:
    """LangChain 高层封装的 RAG 管道，整合两阶段自适应检索、多查询、上下文压缩、历史感知、会话记忆。"""

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
        self.chunk_retriever_base = ChunkLevelRetriever(
            doc_db=self.doc_db,
            vectordb=self.vectordb,
            chunk_retriever=self.chunk_retriever,
            bm25=self.bm25,
            person_index=self.person_index,
        )

    # =================================================
    # Step B: 构建单链
    # =================================================

    def _build_chains(self):
        """
        构建完整的检索-生成链:

          ChunkLevelRetriever → MultiQuery → AdaptiveRetriever → Rerank → HistoryAware → RetrievalChain

        AdaptiveRetriever 在 MultiQuery 合并结果后分析 chunk 的文档分布:
          - 集中在 1-2 个文档 → 补全文档全文
          - 分散在多个文档 → 只给 chunks
        """
        stuff_chain = create_stuff_documents_chain(llm, QA_PROMPT)

        retriever = self.chunk_retriever_base

        # 可选 LLM 压缩
        # if ENABLE_LLM_COMPRESSION:
        #     from langchain_classic.retrievers.document_compressors import LLMChainExtractor
        #     retriever = ContextualCompressionRetriever(
        #         base_compressor=LLMChainExtractor.from_llm(llm),
        #         base_retriever=retriever,
        #     )

        # MultiQuery: 用 LLM 生成多个角度查询，提升召回率
        retriever = MultiQueryRetriever.from_llm(retriever=retriever, llm=llm)
        logger.info("retriever: " + str(retriever))

        # Adaptive: 检查 MultiQuery 合并后的 chunk 文档分布，按需补全文档全文
        retriever = AdaptiveRetriever(
            base_retriever=retriever,
            doc_db=self.doc_db,
        )

        # Rerank: CrossEncoder 全局重排序
        retriever = ContextualCompressionRetriever(
            base_compressor=RerankCompressor(),
            base_retriever=retriever,
        )

        # HistoryAware: 历史感知查询重写
        if ENABLE_HISTORY_AWARE_RETRIEVAL:
            retriever = create_history_aware_retriever(
                llm, retriever, CONTEXTUALIZE_PROMPT
            )

        self.chain = create_retrieval_chain(retriever, stuff_chain)

    # =================================================
    # Step C: 公共入口
    # =================================================

    def ask(self, question: str, session_id: str = "default") -> str:
        logger.info(f"[RAGChain] 收到问题: {question[:60]}... (session={session_id})")

        history = _get_session_history(session_id)
        chat_history = history.messages
        logger.info(f"[RAGChain] 历史消息: {(chat_history)}")

        result = self.chain.invoke({
            "input": question,
            "chat_history": chat_history,
        })

        history.add_message(HumanMessage(content=question))
        history.add_message(AIMessage(content=result["answer"]))

        return result["answer"]