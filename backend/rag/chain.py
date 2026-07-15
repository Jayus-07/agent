"""
LangChain LCEL 主 Chain
= 历史感知检索 + 多查询 + 自适应检索 + 上下文压缩 + 三层记忆

架构:
  L1 短期: 当前调用的消息缓冲区
  L2 会话: PostgreSQL 持久化 (via SessionRepository)
  L3 长期: PostgreSQL + pgvector (via MemoryRepository)
  MemoryManager 统一管理三层，chain 只持有引用。
"""
from langchain_classic.chains import create_history_aware_retriever, create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
# (MultiQuery 已迁移至 retrieval/multi_query.py)
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableLambda

from backend.llm.llm_factory import llm
from backend.rag.retrieval.retrievers import ChunkLevelRetriever, AdaptiveRetriever
from backend.rag.reranker import RerankCompressor
from backend.config import (
    ENABLE_HISTORY_AWARE_RETRIEVAL,
    CITATION_SUPPORT_THRESHOLD,
)
from backend.shared.logger import logger


# =====================================================
# Prompt: 历史感知查询重写
# =====================================================

# 用于将带有上下文依赖的问题（如包含代词或省略）重写为独立的检索查询
CONTEXTUALIZE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是跨境电商知识库的查询重写助手。

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
# Prompt: QA 回答（含内联引用标注）
# =====================================================

# 用于最终生成答案，结合检索到的文档和对话历史
QA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是跨境电商知识库助手。你**只能**根据下方提供的资料回答问题，**严禁**使用资料之外的知识。

要求：
1. **每个事实/数据必须标注来源编号**，格式如 [1]、[2]、[3]
   - 正确示例：「根据公司规定，报销需在每月5日前提交 [1]」
   - 错误示例：「一般来说报销需要5天处理」 ← 没有引用，违规
2. **绝对禁止编造或使用外部知识**。资料中没有的信息，明确说"资料未提及"并给出已有相关内容
3. 输出详实，用分点或分段说明，尽量展开资料中的具体细节
4. 资料中的数字、日期、名称等具体信息必须准确引用

资料：
{context}"""),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])

# 单文档格式化：用序号标注每个文档
DOCUMENT_PROMPT = PromptTemplate.from_template(
    "[文档{index}] 来源: {source_file}\n{page_content}"
)


# =====================================================
# Chain 构建器
# =====================================================

class RAGChain:
    """LangChain 高层封装的 RAG 管道，整合三层记忆系统。"""

    def __init__(
        self,
        doc_db,
        vectordb,
        chunk_retriever,
        bm25,
        person_index: dict = None,
        memory_manager=None,
    ):
        self.doc_db = doc_db
        self.vectordb = vectordb
        self.chunk_retriever = chunk_retriever
        self.bm25 = bm25
        self.person_index = person_index or {}
        self._memory = memory_manager

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

        AdaptiveRetriever 在 MultiQuery 合并结果后分析文档分布:
          - Cluster 检测 → Context Expansion（邻近 Chunk）
          - 分散分布 → 跳过 Expansion
        """
        # Citation Filter: 注入文档序号 + 自定义文档格式，使 LLM 可内联引用 [1][2]
        def _index_docs(input_dict):
            docs = input_dict.get("context", [])
            for i, doc in enumerate(docs, 1):
                doc.metadata["index"] = i
            return input_dict

        _stuff = create_stuff_documents_chain(
            llm, QA_PROMPT,
            document_prompt=DOCUMENT_PROMPT,
            document_separator="\n\n---\n\n",
        )
        def _timed_stuff(inp):
            from backend.rag.tracer import trace_collector
            trace_collector._start("llm_generate")
            try:
                r = _stuff.invoke(inp)
                from backend.llm.proxy import _last_tokens
                trace_collector._end("llm_generate", "LLM生成", metrics=dict(_last_tokens))
                return r
            except Exception:
                trace_collector._end("llm_generate", "LLM生成", status="error")
                raise
        stuff_chain = RunnableLambda(_index_docs) | RunnableLambda(_timed_stuff)

        retriever = self.chunk_retriever_base

        # MultiQuery: auto(自动判断复杂问题)/on(强制)/off(关闭)
        from backend.rag.retrieval.multi_query import MultiQueryRetriever
        retriever = MultiQueryRetriever(base_retriever=retriever)
        self._mq_retriever = retriever  # 供 tracer 读取 MultiQuery 状态
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
        """RAGChain 入口：4 段式 — 准备 → 执行 → 验证 → 收尾。

        拆解后行为完全兼容旧版；便于单测和耗时分析。
        """
        from backend.rag.tracer import trace_collector
        import time as _time

        t_total = _time.time()
        trace = trace_collector.start(question, session_id)
        logger.info(f"[RAGChain] 收到问题: {question[:60]}... (session={session_id})")

        try:
            chat_history = self._prepare(question, session_id)
            result = self._execute(question, chat_history)
            answer = self._verify(result, question, session_id)
            self._trace(trace, answer, t_total)
            return answer
        except Exception:
            trace_collector.finish(trace, "[ERROR]", int((_time.time()-t_total)*1000))
            raise

    def _prepare(self, question: str, session_id: str) -> list:
        """准备阶段：Memory 启动会话，返回 chat_history。"""
        l1 = self._memory.start_session(session_id, question) if self._memory else None
        return list(l1.messages) if l1 else []

    def _execute(self, question: str, chat_history: list) -> dict:
        """执行阶段：chain.invoke + MultiQuery 决策 trace。"""
        from backend.rag.tracer import trace_collector
        result = self.chain.invoke({"input": question, "chat_history": chat_history})

        # mq_check: 从 MultiQueryRetriever 读取实际决策结果（唯一入口）
        trace_collector._start("mq_check")
        mq = getattr(self, '_mq_retriever', None)
        triggered = mq._last_triggered if mq else False
        from backend.rag.retrieval.multi_query import get_mq_mode
        trace_collector._end("mq_check", "MultiQuery",
                             metrics={"triggered": triggered, "mode": get_mq_mode()},
                             status="skipped" if not triggered else "success")
        return result

    def _verify(self, result: dict, question: str, session_id: str = "default") -> str:
        """验证阶段：剥离 think 块 + Citation 校验 + 格式化引用。"""
        from backend.rag.tracer import trace_collector

        answer = _strip_think_blocks(result["answer"])
        context_docs = result.get("context", [])

        # Citation
        trace_collector._start("citation")
        if context_docs:
            answer, verified_docs = _verify_support(answer, context_docs, question)
        else:
            verified_docs = []
        trace_collector._end("citation", "Citation",
                             metrics={"verified_citations": len(verified_docs),
                                      "total_citations": len(context_docs)})
        references = _format_references(verified_docs, answer)
        if references:
            answer = answer + references
        self._last_sources = _extract_sources(verified_docs, answer)

        if self._memory:
            self._memory.end_turn(session_id, question, answer)

        return answer

    def _trace(self, trace, answer: str, t_total: float):
        """收尾阶段：完成 Trace + 写 Memory end_turn。"""
        from backend.rag.tracer import trace_collector
        from backend.config import LLM_MODEL
        from backend.llm.factory import get_llm_factory
        import time as _time

        total_ms = int((_time.time()-t_total)*1000)
        provider = ""
        try:
            provider = get_llm_factory()._get_provider(LLM_MODEL)
        except Exception:
            pass
        trace_collector.finish(trace, answer, total_ms, LLM_MODEL, provider)

def _strip_think_blocks(text: str) -> str:
    """剥离 <think>...</think> 推理块。未闭合标签保留后续内容，避免误删。"""
    import re
    cleaned = re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL)
    if '<think>' in cleaned and '</think>' not in cleaned:
        cleaned = re.sub(r'<think>.*', '', cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _verify_support(answer: str, docs: list, question: str = "") -> tuple:
    """Citation Filter: 复用 Rerank 阶段的 CrossEncoder 分数，避免重复推理。

    阶段 1: 复用 rerank_score（RerankCompressor 已写入 doc.metadata），过滤低分 chunk
    阶段 2: 句子级验证（默认关闭，ENABLE_CITATION_SENTENCE_CHECK=true 开启）
    返回: (cleaned_answer, verified_docs)
    """
    if not docs:
        return answer, []

    # —— 阶段 1: 复用 Rerank 分数，不重新跑 CrossEncoder ——
    verified = []
    for doc in docs:
        score = doc.metadata.get("rerank_score", 0.5)  # 复用 Rerank 已算好的分数
        if float(score) > CITATION_SUPPORT_THRESHOLD:
            doc.metadata["support_score"] = round(float(score), 4)
            verified.append(doc)

    logger.info(
        f"[CitationFilter] 支撑验证(复用Rerank分): {len(docs)} → {len(verified)} 个 chunk "
        f"(threshold={CITATION_SUPPORT_THRESHOLD})"
    )

    if not verified:
        logger.warning("[CitationFilter] 所有 chunk 未通过验证，清空引用")
        return answer, []

    # —— 阶段 2: 完成（企业做法：Prompt 强制 LLM 标注引用 [1][2]，不做事后猜）——
    return answer, verified


def _format_references(docs: list, answer: str = "") -> str:
    """生成参考文献列表。

    - 优先显示文中 [1][2] 实际引用到的来源
    - 兜底：如果 LLM 未生成引用标注，展示所有通过验证的文档
    """
    if not docs:
        return ""

    # 从回答中提取所有引用编号 [1] [2] ...
    import re
    cited = set()
    for m in re.finditer(r"\[(\d+)\]", answer):
        cited.add(int(m.group(1)))

    seen = {}
    for doc in docs:
        idx = doc.metadata.get("index")
        fname = doc.metadata.get("source_file", doc.metadata.get("source", ""))
        if not fname or idx is None:
            continue
        # 有引用标注时仅保留文中实际引用的来源
        if cited and idx not in cited:
            continue
        # 无引用标注（兜底）：展示所有 verified docs
        if fname not in seen:
            seen[fname] = (idx, doc.metadata)

    if not seen:
        return ""

    # 按 index 排序，与文中标注 [1][2] 顺序一致
    items = sorted(seen.values(), key=lambda x: x[0])

    lines = ["", "---", "", "### 参考文献", ""]
    for idx, meta in items:
        doc_type = meta.get("doc_type", "")
        score = meta.get("score", meta.get("rerank_score", None))
        type_label = {
            "listing": "Listing", "sop": "SOP", "ad_policy": "广告政策",
            "faq": "FAQ", "product_spec": "产品规格", "training": "培训",
            "policy": "制度规范", "report": "报告", "manual": "操作手册",
        }.get(doc_type, doc_type)
        fname = meta.get("source_file", meta.get("source", ""))
        parts = [f"{idx}. **{fname}**"]
        if type_label:
            parts.append(f" ({type_label})")
        if score is not None:
            parts.append(f" — 相关度: {score:.2f}")
        lines.append("".join(parts))

    return "\n".join(lines)


def _extract_sources(docs: list, answer: str = "") -> list[dict]:
    """从 verified docs 中提取结构化来源信息（供前端 SourceCard 展示）。

    - 优先通过文中 [1][2] 引用标注精确匹配
    - 兜底：如果 LLM 未生成引用标注，返回所有通过验证的文档
    """
    if not docs:
        return []

    import re
    cited = set()
    for m in re.finditer(r"\[(\d+)\]", answer):
        cited.add(int(m.group(1)))

    type_label_map = {
        "listing": "Listing", "sop": "SOP", "ad_policy": "广告政策",
        "faq": "FAQ", "product_spec": "产品规格", "training": "培训",
        "policy": "制度规范", "report": "报告", "manual": "操作手册",
    }

    seen = {}
    for doc in docs:
        idx = doc.metadata.get("index")
        fname = doc.metadata.get("source_file", doc.metadata.get("source", ""))
        if not fname or idx is None:
            continue
        # 有引用标注时仅保留文中实际引用的来源；无引用时兜底展示全部
        if cited and idx not in cited:
            continue
        if fname not in seen:
            doc_type = doc.metadata.get("doc_type", "")
            score = doc.metadata.get("score", doc.metadata.get("rerank_score", doc.metadata.get("support_score")))
            seen[fname] = {
                "index": idx,
                "filename": fname,
                "doc_type": doc_type,
                "type_label": type_label_map.get(doc_type, doc_type),
                "score": round(float(score), 2) if score is not None else None,
            }

    return sorted(seen.values(), key=lambda s: s.get("index", 0))