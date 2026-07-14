"""
LangChain LCEL 主 Chain
= 历史感知检索 + 多查询 + 自适应检索 + 上下文压缩 + 三层记忆

架构:
  L1 短期: 当前调用的消息缓冲区
  L2 会话: PostgreSQL 持久化 (via SessionRepository)
  L3 长期: PostgreSQL + pgvector (via MemoryRepository)
  MemoryManager 统一管理三层，chain 只持有引用。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from langchain_classic.chains import create_history_aware_retriever, create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from llm.llm_factory import llm
from retrieval.retrievers import ChunkLevelRetriever, AdaptiveRetriever
from retrieval.reranker import RerankCompressor
from config import ENABLE_HISTORY_AWARE_RETRIEVAL, CITATION_SUPPORT_THRESHOLD, RERANK_TIMEOUT
from utils.logger import logger
from utils.timeout import safe_call_with_timeout


# =====================================================
# 并行 MultiQueryRetriever（替换 LangChain 默认串行版本）
# =====================================================

class ParallelMultiQueryRetriever(MultiQueryRetriever):
    """
    与 MultiQueryRetriever 相同，但多个查询变体的检索并发执行。
    继承父类的 generate_queries，只覆盖 retrieve_documents 为并发版本。
    """
    max_workers: int = 3

    def retrieve_documents(
        self, queries: List[str], run_manager=None
    ) -> List[Document]:
        """并发检索所有查询变体，合并去重"""
        all_docs: List[Document] = []

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(queries))) as executor:
            futures = {
                executor.submit(self._retrieve_single, q): q
                for q in queries
            }
            for future in as_completed(futures):
                try:
                    docs = future.result()
                    all_docs.extend(docs)
                except Exception as e:
                    logger.warning(f"[ParallelMultiQuery] 检索失败 '{futures[future][:50]}': {e}")

        logger.info(f"[ParallelMultiQuery] {len(queries)} 查询 → {len(all_docs)} 文档 (并发={self.max_workers})")
        # 使用父类的去重方法
        return self.unique_union(all_docs)

    def _retrieve_single(self, query: str) -> List[Document]:
        """单个查询的检索（线程安全）"""
        # LangChain BaseRetriever 的标准调用方式: invoke() → _get_relevant_documents()
        return self.retriever.invoke(query)


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

        AdaptiveRetriever 在 MultiQuery 合并结果后分析 chunk 的文档分布:
          - 集中在 1-2 个文档 → 补全文档全文
          - 分散在多个文档 → 只给 chunks
        """
        # Citation Filter: 注入文档序号 + 自定义文档格式，使 LLM 可内联引用 [1][2]
        def _index_docs(input_dict):
            docs = input_dict.get("context", [])
            for i, doc in enumerate(docs, 1):
                doc.metadata["index"] = i
            return input_dict

        stuff_chain = (
            RunnableLambda(_index_docs)
            | create_stuff_documents_chain(
                llm, QA_PROMPT,
                document_prompt=DOCUMENT_PROMPT,
                document_separator="\n\n---\n\n",
            )
        )

        retriever = self.chunk_retriever_base

        # MultiQuery: 用 LLM 生成多个角度查询 → **并发检索**，提升召回率
        retriever = ParallelMultiQueryRetriever.from_llm(
            retriever=retriever, llm=llm
        )
        retriever.max_workers = 3  # 在 from_llm 之后设置自定义属性
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

        # L1/L2/L3: 加载会话上下文 (由 MemoryManager 统一管理)
        l1 = self._memory.start_session(session_id, question) if self._memory else None
        chat_history = list(l1.messages) if l1 else []

        result = self.chain.invoke({
            "input": question,
            "chat_history": chat_history,
        })

        answer = result["answer"]
        # 剥离 <think>...</think> 推理块（MiniMax M3 / DeepSeek R1 等会输出）
        answer = _strip_think_blocks(answer)
        context_docs = result.get("context", [])

        # Citation Filter: 用 CrossEncoder 验证每个 chunk 是否真正支撑答案
        if context_docs:
            answer, verified_docs = _verify_support(answer, context_docs, question)
        else:
            verified_docs = []

        # 附加参考文献（仅显示文中引用 + 验证通过 的来源）
        references = _format_references(verified_docs, answer)
        if references:
            answer = answer + references

        # 提取结构化来源（供前端 SourceCard 展示）
        self._last_sources = _extract_sources(verified_docs, answer)

        # L2/L3: 持久化本轮（MemoryManager 委托 MemoryService → PostgreSQL）
        if self._memory:
            self._memory.end_turn(session_id, question, answer)

        return answer


def _strip_think_blocks(text: str) -> str:
    """剥离 <think>...</think> 推理块（MiniMax M3 / DeepSeek R1 等会输出）"""
    import re
    # 去除 <think>...</think> 完整块（含换行）
    cleaned = re.sub(r'<think>[\s\S]*?</think>\s*', '', text)
    # 去除只有开头没有结尾的 <think>（异常截断）
    cleaned = re.sub(r'<think>[\s\S]*', '', cleaned)
    return cleaned.strip()


def _verify_support(answer: str, docs: list, question: str = "") -> tuple:
    """
    Citation Filter 核心：用 CrossEncoder 反向验证每个 chunk 是否真正支撑答案。

    阶段 1: 以问题为 query 对每个 chunk 打分，过滤不相关的 chunk
    阶段 2: 以句子为单位验证是否被剩余 chunk 支撑，无支撑则标记 [推断]
    返回: (cleaned_answer, verified_docs)
    """
    from retrieval.reranker import reranker as _ce

    if not docs:
        return answer, []

    # —— 阶段 1: 以问题为 query，对每个 chunk 打分（判断 chunk 是否与问题相关） ——
    pairs = [(question or answer, doc.page_content[:800]) for doc in docs]
    scores = safe_call_with_timeout(
        _ce.predict,
        timeout=RERANK_TIMEOUT,
        default_value=None,
        error_message="Citation 支撑验证超时",
        sentences=pairs,
    )

    if scores is None:
        logger.warning("[CitationFilter] 验证失败，回退到原始结果")
        return answer, docs

    # —— 阶段 2: 过滤 + 写入 support_score ——
    verified = []
    for doc, score in zip(docs, scores):
        if float(score) > CITATION_SUPPORT_THRESHOLD:
            doc.metadata["support_score"] = round(float(score), 4)
            verified.append(doc)

    logger.info(
        f"[CitationFilter] 支撑验证: {len(docs)} → {len(verified)} 个 chunk "
        f"(threshold={CITATION_SUPPORT_THRESHOLD})"
    )

    if not verified:
        logger.warning("[CitationFilter] 所有 chunk 未通过验证，回退")
        return answer, docs

    # —— 阶段 3: 句子级验证 ——
    cleaned = _mark_unsupported_sentences(answer, verified)

    return cleaned, verified


def _mark_unsupported_sentences(answer: str, verified_docs: list) -> str:
    """检查每个句子是否有 chunk 支撑，无支撑则标记 [推断]"""
    import re
    from collections import defaultdict
    from retrieval.reranker import reranker as _ce

    # 剥离已有参考文献部分（避免把来源列表当正文检查）
    ref_marker = "\n\n---\n\n### 参考文献"
    ref_start = answer.find(ref_marker)
    body = answer[:ref_start] if ref_start != -1 else answer

    # 分句
    raw_sentences = re.split(r'(?<=[。！？])\s*', body)

    # 过滤空句和纯格式行
    sentences = []
    for s in raw_sentences:
        stripped = s.strip()
        if not stripped or re.match(r'^[\s\[\]\d,，、\-—#*>\-|]+$', stripped):
            sentences.append((s, True))  # skip verification
        else:
            sentences.append((s, False))

    # 批量建所有 sentence×chunk 对，一次 CrossEncoder 调用
    all_pairs = []
    pair_index = []  # (sent_idx, doc_idx)
    for i, (_, skip) in enumerate(sentences):
        if skip:
            continue
        for j, doc in enumerate(verified_docs):
            all_pairs.append((sentences[i][0].strip(), doc.page_content[:500]))
            pair_index.append((i, j))

    if not all_pairs:
        return answer

    all_scores = safe_call_with_timeout(
        _ce.predict,
        timeout=RERANK_TIMEOUT,
        default_value=None,
        error_message="句子验证超时",
        sentences=all_pairs,
    )

    # 按句子聚合最高分
    sent_max = defaultdict(float)
    if all_scores is not None:
        for (sent_idx, _), score in zip(pair_index, all_scores):
            sent_max[sent_idx] = max(sent_max[sent_idx], float(score))

    # 组装输出
    result_parts = []
    for i, (original, skip) in enumerate(sentences):
        if skip:
            result_parts.append(original)
        elif sent_max.get(i, 0) < CITATION_SUPPORT_THRESHOLD:
            result_parts.append(f"[推断] {original}")
        else:
            result_parts.append(original)

    return "".join(result_parts)


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
                "filename": fname,
                "doc_type": doc_type,
                "type_label": type_label_map.get(doc_type, doc_type),
                "score": round(float(score), 2) if score is not None else None,
            }

    return sorted(seen.values(), key=lambda s: s["filename"])