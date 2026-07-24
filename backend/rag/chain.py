"""
LangChain LCEL 主 Chain

流水线（外层先执行）:
  ① HistoryAware   — Query Understanding: 对话历史改写指代/省略
  ② MultiQuery     — Query Expansion:   关键词检测复杂度 → LLM 改写
  ③ ChunkLevel     — Hybrid Retrieval:  向量 + BM25 混合检索
  ④ Adaptive       — Document Expansion: 同文档相邻 Chunk 扩展
  ⑤ Rerank         — CrossEncoder:      全局重排序 + 阈值过滤
  ⑥ LLM Generate   — 带引用标注 [1][2]

三层记忆:
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

from backend.infra.llm import llm
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

# 单文档格式化：含元数据标签（非空字段才显示，不浪费 token）
DOCUMENT_PROMPT = PromptTemplate.from_template(
    "[文档{index}] 来源: {source_file}"
    "{doc_type_label}"
    "{business_domain_label}"
    "{summary_label}"
    "\n{page_content}"
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
        """构建完整的检索-生成链。

        执行顺序（外层先执行）:

          ① HistoryAware   — Query Understanding: 利用对话历史重写指代/省略
          ② MultiQuery     — Query Expansion:   关键词检测复杂度 → LLM 多角度改写
          ③ ChunkLevel     — Hybrid Retrieval:  向量 + BM25 混合检索
          ④ Adaptive       — Document Expansion: 同文档相邻 Chunk 扩展
          ⑤ Rerank         — CrossEncoder:      全局重排序 + 阈值过滤
          ⑥ LLM Generate   — 带引用标注 [1][2] 的最终回答
        """
        # Citation Filter: 注入文档序号 + 自定义文档格式，使 LLM 可内联引用 [1][2]
        def _index_docs(input_dict):
            docs = input_dict.get("context", [])
            for i, doc in enumerate(docs, 1):
                doc.metadata["index"] = i
                # 注入元数据标签（非空才显示，不浪费 token）
                dt = doc.metadata.get("doc_type", "")
                doc.metadata["doc_type_label"] = f"\n类型: {dt}" if dt and dt != "general" else ""
                bd = doc.metadata.get("business_domain", "")
                doc.metadata["business_domain_label"] = f"\n领域: {bd}" if bd else ""
                summary = doc.metadata.get("summary", "")
                doc.metadata["summary_label"] = f"\n摘要: {summary[:120]}" if summary else ""
            return input_dict

        _stuff = create_stuff_documents_chain(
            llm, QA_PROMPT,
            document_prompt=DOCUMENT_PROMPT,
            document_separator="\n\n---\n\n",
        )
        def _timed_stuff(inp):
            from backend.rag.tracer import trace_collector, SpanKind
            llm_span = trace_collector.start_span(
                "llm_generate", name="LLM生成",
                kind=SpanKind.LLM.value,
                input={"question": inp.get("input", "")[:1000]},
            )
            try:
                r = _stuff.invoke(inp)
                # 注入 token + finish_reason + cost_usd（从 proxy 模块级缓存读）
                from backend.infra.llm.proxy import _last_call_meta
                metrics = dict(_last_call_meta)  # {prompt_tokens, completion_tokens, total_tokens, finish_reason, cost_usd}
                # 截断文本字段，避免大输出撑爆 trace
                completion_text = ""
                if hasattr(r, "content") and isinstance(r.content, str):
                    completion_text = r.content[:1000]
                elif hasattr(r, "content"):
                    completion_text = str(r.content)[:1000]
                if completion_text:
                    metrics["completion_text"] = completion_text
                trace_collector.end_span(llm_span, metrics=metrics)
                return r
            except Exception:
                trace_collector.end_span(llm_span, status="error")
                raise
        stuff_chain = RunnableLambda(_index_docs) | RunnableLambda(_timed_stuff)

        # ── ③ Hybrid Retrieval（最内层：实际搜索）─────────
        retriever = self.chunk_retriever_base

        # ── ④ Adaptive: 同文档 Chunk 扩展 ──────────────
        retriever = AdaptiveRetriever(
            base_retriever=retriever,
            doc_db=self.doc_db,
        )

        # ── ⑤ Rerank: CrossEncoder 全局重排序 ──────────
        retriever = ContextualCompressionRetriever(
            base_compressor=RerankCompressor(),
            base_retriever=retriever,
        )

        # ── ② MultiQuery: 复杂度检测 → LLM 改写 ─────────
        from backend.rag.retrieval.multi_query import MultiQueryRetriever
        retriever = MultiQueryRetriever(base_retriever=retriever)
        self._mq_retriever = retriever  # 供 tracer 读取 MultiQuery 状态

        # ── ① HistoryAware: 对话历史改写（最外层，最先执行）─
        if ENABLE_HISTORY_AWARE_RETRIEVAL:
            retriever = create_history_aware_retriever(
                llm, retriever, CONTEXTUALIZE_PROMPT
            )

        self.chain = create_retrieval_chain(retriever, stuff_chain)

    # =================================================
    # Step C: 公共入口
    # =================================================

    def ask(self, question: str, session_id: str = "default") -> str:
        """RAGChain 入口：5 段式 — 准备 → 执行 → 验证 → [评估] → 收尾。"""
        from backend.rag.tracer import trace_collector
        import time as _time

        t_total = _time.time()
        trace = trace_collector.start(question, session_id)
        trace_collector.start_span("root", parent_id=None,
                                   name="RAG Agent", type="agent",
                                   input={"question": question})
        logger.info(f"[RAGChain] 收到问题: {question[:60]}... (session={session_id})")

        try:
            chat_history = self._prepare(question, session_id)
            result = self._execute(question, chat_history)
            answer = self._verify(result, question, session_id)
            answer = self._evaluate(answer, result.get("context", []))  # 评估 + 剔除幻觉
            self._trace(trace, answer, t_total)
            return answer
        except Exception:
            try:
                self._end_root_span(trace, status="error",
                                    metrics={"error": "pipeline_failed"})
                trace_collector.finish(trace, "[ERROR]", int((_time.time()-t_total)*1000), "", "")
            except Exception:
                pass
            raise

    def _prepare(self, question: str, session_id: str) -> list:
        """准备阶段：Memory 启动会话，返回 chat_history。"""
        l1 = self._memory.start_session(session_id, question) if self._memory else None
        return list(l1.messages) if l1 else []

    def _execute(self, question: str, chat_history: list) -> dict:
        """执行阶段：chain.invoke + MultiQuery 决策 trace + Retrieval Debug。"""
        from backend.rag.tracer import trace_collector
        import time as _time

        # ── retrieval span（包裹整个检索过程，挂 debug event）──
        ret_span = trace_collector.start_span(
            "retrieval", parent_id="root",
            name="Hybrid Retrieval", type="retrieval",
            kind="retrieval",
            input={"question": question[:500]},
        )
        t_ret_start = _time.time()

        result = self.chain.invoke({"input": question, "chat_history": chat_history})

        # ── 采集检索中间结果 ──
        context_docs = result.get("context", [])
        self._record_retrieval_events(ret_span, context_docs)
        trace_collector.end_span(ret_span,
            metrics={"duration_ms": int((_time.time() - t_ret_start) * 1000),
                     "total_docs": len(context_docs)})

        # mq_check
        mq_span = trace_collector.start_span("mq_check", name="MultiQuery")
        mq = getattr(self, '_mq_retriever', None)
        triggered = mq._last_triggered if mq else False
        from backend.rag.retrieval.multi_query import get_mq_mode
        trace_collector.end_span(mq_span,
            metrics={"triggered": triggered, "mode": get_mq_mode()},
            status="skipped" if not triggered else "success")
        return result

    def _record_retrieval_events(self, ret_span, context_docs: list) -> None:
        """采集检索各阶段的中间结果，写入 ret_span.events。"""
        from backend.rag.tracer import trace_collector

        # ── Event 1: Query Analyzer ──
        try:
            from backend.rag.retrieval.query_analyzer import QueryAnalyzer
            qa = QueryAnalyzer()
            pq = qa.analyze(ret_span.input.get("question", ""))
            trace_collector.add_event(ret_span, "query_analyzer", "info",
                f"intent={pq.intent}, doc_types={pq.doc_types}",
                data={"intent": pq.intent, "doc_types": pq.doc_types,
                      "metadata_filter": pq.to_metadata_filter()})
        except Exception:
            pass

        # ── Event 2: Rerank 结果 ──
        rerank_scores = []
        for doc in context_docs[:10]:
            score = doc.metadata.get("rerank_score")
            if score is not None:
                rerank_scores.append({
                    "chunk_id": doc.metadata.get("chunk_id", ""),
                    "score": round(score, 4),
                    "snippet": doc.page_content[:120],
                    "source": doc.metadata.get("source_file", ""),
                    "doc_type": doc.metadata.get("doc_type", ""),
                })
        if rerank_scores:
            trace_collector.add_event(ret_span, "rerank", "info",
                f"top {len(rerank_scores)} scored chunks",
                data={"scored": rerank_scores})

        # ── Event 3: Final Context ──
        trace_collector.add_event(ret_span, "final_context", "info",
            f"{len(context_docs)} chunks → LLM",
            data={"chunks": [{
                "chunk_id": d.metadata.get("chunk_id", ""),
                "source": d.metadata.get("source_file", ""),
                "doc_type": d.metadata.get("doc_type", ""),
                "keywords": d.metadata.get("chunk_keywords", ""),
                "snippet": d.page_content[:100],
            } for d in context_docs[:8]]})

    def _verify(self, result: dict, question: str, session_id: str = "default") -> str:
        """验证阶段：剥离 think 块 + Citation 校验 + 格式化引用。"""
        from backend.rag.tracer import trace_collector

        answer = _strip_think_blocks(result["answer"])
        context_docs = result.get("context", [])

        # Citation
        citation_span = trace_collector.start_span(
            "citation", name="Citation")
        if context_docs:
            answer, verified_docs = _verify_support(answer, context_docs, question)
        else:
            verified_docs = []
        trace_collector.end_span(citation_span,
                             metrics={"verified_citations": len(verified_docs),
                                      "total_citations": len(context_docs)})
        references = _format_references(verified_docs, answer)
        if references:
            answer = answer + references
        self._last_sources = _extract_sources(verified_docs, answer)

        if self._memory:
            self._memory.end_turn(session_id, question, answer)

        return answer

    def _evaluate(self, answer: str, context_docs: list) -> str:
        """评估阶段：Faithfulness 忠实性检测 + 自动剔除不可信句子。

        默认关闭（ENABLE_FAITHFULNESS=false）。
        开启后：检测 → 三级漏斗 → 返回安全答案。
        结果同时存入 self._last_faithfulness 供外部读取。

        LangGraph 迁移点：
          当 rewrite 触发频率 > 10% 或需要并行多源验证时，
          将 check_faithfulness + rewrite_claim 拆为独立 LangGraph 节点，
          _evaluate() 改为返回 FaithfulnessResult 而非直接改写 answer。
        """
        from backend.rag.tracer import trace_collector
        self._last_faithfulness = None

        try:
            from backend.rag.guardrails import check_faithfulness
            faith_span = trace_collector.start_span(
                "faithfulness", name="Faithfulness")
            self._last_faithfulness = check_faithfulness(answer, context_docs)
            trace_collector.end_span(faith_span,
                                 metrics={
                                     "score": self._last_faithfulness.score,
                                     "claims": self._last_faithfulness.total_claims,
                                     "supported": self._last_faithfulness.supported_claims,
                                     "unsupported": self._last_faithfulness.unsupported_claims,
                                 })

            # 如果有不可信 claim，用清洗后的答案
            if self._last_faithfulness.cleaned_answer and \
               self._last_faithfulness.cleaned_answer != answer:
                logger.warning(
                    f"[RAGChain] 自动剔除 {self._last_faithfulness.unsupported_claims} 条不可信内容"
                )
                return self._last_faithfulness.cleaned_answer
            return answer
        except Exception as e:
            logger.warning(f"[RAGChain] Faithfulness 检测跳过: {e}")
            trace_collector.end_span(faith_span, status="skipped",
                                 metrics={"error": str(e)[:100]})
            return answer

    def _trace(self, trace, answer: str, t_total: float):
        """收尾阶段：结束 root span + 完成 Trace。"""
        from backend.rag.tracer import trace_collector
        from backend.config import LLM_MODEL
        from backend.infra.llm.factory import get_llm_factory
        import time as _time

        total_ms = int((_time.time()-t_total)*1000)
        self._end_root_span(trace,
            output={"answer_preview": answer[:200], "answer_len": len(answer)},
            metrics={"span_count": sum(1 for s in trace.spans if s.parent_id is not None)})

        provider = ""
        try:
            provider = get_llm_factory()._get_provider(LLM_MODEL)
        except Exception:
            pass
        trace_collector.finish(trace, answer, total_ms, LLM_MODEL, provider)

    @staticmethod
    def _end_root_span(trace, output: dict = None, metrics: dict = None,
                       status: str = "success"):
        """查找并结束 root span（parent_id=None 的那条）。"""
        from backend.rag.tracer import trace_collector
        for sp in trace.spans:
            if sp.parent_id is None:
                trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
                return

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