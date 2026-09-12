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
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain_core.runnables import RunnableLambda

from backend.config import (
    ENABLE_HISTORY_AWARE_RETRIEVAL,
    ENABLE_TOKEN_STREAMING,
    EVIDENCE_TOKEN_BUDGET,
)
from backend.infra.llm import llm
from backend.infra.llm.proxy import emit_stream_delta, extract_chunk_text
from backend.rag.citation import CitationFormatter
from backend.rag.context import get_context, set_context
from backend.rag.evidence_gate import EvidenceGateController
from backend.rag.evidence_gate.self_correction import SelfCorrectionStrategy
from backend.rag.reranker import RerankCompressor
from backend.rag.retrieval.retrievers import AdaptiveRetriever, ChunkLevelRetriever
from backend.memory.token_budget import trim_texts_to_budget
from backend.shared.logger import logger

# =====================================================
# Prompt: 历史感知查询重写（DEFAULT fallback — 优先从 prompt_service 获取）
# =====================================================

DEFAULT_CONTEXTUALIZE_SYSTEM = """你是跨境电商知识库的查询重写助手。

根据对话历史，将用户问题重新表述为独立的检索查询。

规则:
1. 如果用户使用代词（他、她、这个、那个、它），请替换为对话历史中的具体实体
2. 如果问题已经独立完整，直接返回原问题
3. 不要回答问题，只输出改写后的查询
4. 保留所有专有名词、技术术语、业务词汇
5. 不要添加解释或 markdown 格式"""

# =====================================================
# Prompt: QA 回答（DEFAULT fallback — 优先从 prompt_service 获取）
# =====================================================

DEFAULT_QA_SYSTEM = """你是电商企业知识库助手。你只能依据「资料」中明确提供的信息回答问题。

## 核心规则

1. **单证据原则**：每个事实、数字、日期、时效、条件，必须能由一个 Evidence 独立支持。禁止拼接多个 Evidence 推导原文不存在的新事实。

2. **证据边界**：每条 Evidence 标注了 [Query]、[文档]、[章节]。不同 Query、不同章节的信息属于不同上下文，**禁止跨边界拼接**。

3. **数字/时效零容忍**：所有数字、日期、百分比、SLA 必须与原文逐字一致。禁止修改、换算、推断。禁止将一条 Evidence 中的数字套用到另一条 Evidence。

4. **信息不足时**：明确写「资料未提及」。禁止猜测、常识补充、相似流程推断。

## 回答格式

正文用 Markdown。每个事实必须带 Evidence 引用 [En]（如 [E1]、[E2]）。

资料充分时示例：
```
客服需要审核退货原因和凭证真实性。[E1]
差评处理要求48小时内给出具体解决方案。[E2]
```

信息不足时：
```
资料未提及。
```

正文末尾必须输出：
- 可回答 → `<!--META{{"can_answer":true,"citations":["E1","E2"],"confidence":0.85}}-->`
- 不可回答 → `<!--META{{"can_answer":false,"reason":"no_evidence","confidence":0.1}}-->`

reason 取值：no_evidence / low_relevance / insufficient / out_of_scope

资料:
{context}"""

DEFAULT_DOCUMENT_TEMPLATE = (
    "[Evidence E{index}]\n"
    "{query_label}"
    "{doc_label}"
    "{section_label}"
    "{chunk_label}"
    "{type_label}"
    "{domain_label}"
    "{page_content}"
)


# =====================================================
# Prompt 构建器：优先从 prompt_service 获取，降级到 DEFAULT 常量
# =====================================================

def _build_contextualize_prompt() -> ChatPromptTemplate:
    """从 prompt_service 构建查询重写 ChatPromptTemplate。"""
    try:
        from backend.prompts.service import prompt_service
        prompt_service.get_template_sync("rag.contextualize")
    except Exception:
        pass
    return ChatPromptTemplate.from_messages([
        ("system", DEFAULT_CONTEXTUALIZE_SYSTEM),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])


def _build_qa_prompt() -> ChatPromptTemplate:
    """从 prompt_service 构建 QA ChatPromptTemplate。"""
    system_text = DEFAULT_QA_SYSTEM
    try:
        from backend.prompts.service import prompt_service
        full = prompt_service.get_template_sync("rag.qa")
        parts = full.split("---", 1)
        if len(parts) == 2:
            system_text = parts[0].strip()
    except Exception:
        pass
    return ChatPromptTemplate.from_messages([
        ("system", system_text),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])


def _build_document_prompt() -> PromptTemplate:
    """从 prompt_service 构建证据格式化 PromptTemplate。"""
    template_str = DEFAULT_DOCUMENT_TEMPLATE
    try:
        from backend.prompts.service import prompt_service
        template_str = prompt_service.get_template_sync("rag.document")
    except Exception:
        pass
    return PromptTemplate.from_template(template_str)


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
        # ── PR-1.4: 策略对象（formatter 保留为实例字段；gate/corrector 迁移到 RequestContext）──
        self.formatter = CitationFormatter()
        # ── RAGChain 自有状态 ──
        # 决策中间态（_last_meta/_last_faithfulness/_last_query）已迁移到
        # RequestContext（P1 并发隔离），见类底部 property；
        # _last_sources 是跨线程输出通道（rag_search 在 to_thread 返回后
        # 于主线程 getattr 读取），必须保留为实例字段。
        self._last_sources: list = []
        self._chains_dirty = False

        self._build_retrievers()
        self._build_chains()
        self._register_prompt_reload_hooks()
        logger.info("LangChain RAG Chain 初始化完成")

    # ── 决策中间态：随 RequestContext 隔离（contextvars），防单例并发串扰 ──
    # 保留 _last_* 属性名仅为兼容既有测试/外部读取，实际存储于请求上下文。
    @property
    def _last_meta(self) -> dict:
        return get_context().meta

    @_last_meta.setter
    def _last_meta(self, value: dict) -> None:
        get_context().meta = value

    @property
    def _last_faithfulness(self):
        return get_context().faithfulness

    @_last_faithfulness.setter
    def _last_faithfulness(self, value) -> None:
        get_context().faithfulness = value

    @property
    def _last_query(self) -> str:
        return get_context().query

    @_last_query.setter
    def _last_query(self, value: str) -> None:
        get_context().query = value

    @property
    def gate(self):
        ctx = get_context()
        if ctx.gate is None:
            ctx.gate = EvidenceGateController()
        return ctx.gate

    @gate.setter
    def gate(self, value) -> None:
        get_context().gate = value

    @property
    def corrector(self):
        ctx = get_context()
        if ctx.corrector is None:
            ctx.corrector = SelfCorrectionStrategy()
        return ctx.corrector

    @corrector.setter
    def corrector(self, value) -> None:
        get_context().corrector = value

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
          ② Rerank         — CrossEncoder:      全局重排序 + 阈值过滤（只在合并结果上执行一次）
          ③ MultiQuery     — Query Expansion:   关键词检测复杂度 → LLM 多角度改写
          ④ Adaptive       — Document Expansion: 同文档相邻 Chunk 扩展
          ⑤ ChunkLevel     — Hybrid Retrieval:  向量 + BM25 混合检索
          ⑥ LLM Generate   — 带引用标注 [1][2] 的最终回答
        """
        # Citation Filter: 注入文档序号 + 自定义文档格式，使 LLM 可内联引用 [1][2]
        def _index_docs(input_dict):
            docs = input_dict.get("context", [])
            # ── 证据 token 预算（P3）：rerank 后输入顺序即相关性顺序，从头保留，
            # 超出预算的尾部文档整体丢弃（长文档场景仅靠 top_k 条数会挤爆上下文）。
            # 首条文档即使超预算也保留（保证至少有证据可引用）。
            if EVIDENCE_TOKEN_BUDGET > 0 and docs:
                page_texts = [d.page_content for d in docs]
                kept_texts, dropped = trim_texts_to_budget(
                    page_texts, EVIDENCE_TOKEN_BUDGET)
                if dropped:
                    docs = docs[:len(kept_texts)]
                    input_dict["context"] = docs
                    logger.info(
                        f"[RAGChain] 证据 token 预算裁剪: 丢弃 {dropped} 个尾部文档 "
                        f"(budget={EVIDENCE_TOKEN_BUDGET})")
            for i, doc in enumerate(docs, 1):
                doc.metadata["index"] = i
                # ── Evidence 边界字段（非空才显示，不浪费 token）──
                sq = doc.metadata.get("source_query", "")
                doc.metadata["query_label"] = f"[Query: {sq}]\n" if sq else ""
                doc.metadata["doc_label"] = f"[文档: {doc.metadata.get('source_file', '')}]\n"
                section = doc.metadata.get("section_title", "")
                doc.metadata["section_label"] = f"[章节: {section}]\n" if section else ""
                cid = doc.metadata.get("chunk_id", "")
                doc.metadata["chunk_label"] = f"[Chunk: {cid}]\n" if cid else ""
                dt = doc.metadata.get("doc_type", "")
                doc.metadata["type_label"] = f"[类型: {dt}]\n" if dt and dt != "general" else ""
                bd = doc.metadata.get("business_domain", "")
                doc.metadata["domain_label"] = f"[业务域: {bd}]\n" if bd and bd != "general" else ""
            return input_dict

        _stuff = create_stuff_documents_chain(
            llm, _build_qa_prompt(),
            document_prompt=_build_document_prompt(),
            document_separator="\n\n---\n\n",
        )
        def _timed_stuff(inp):
            from backend.observability.tracer import SpanKind, trace_collector
            # ── 空检索短路：0 docs 时跳过 LLM（~4.8s），Gate 会拒答 ──
            context_docs = inp.get("context", [])
            # P0-1: 检索边界收口 — 调到本层时 context 已就绪（retriever 执行
            # 完毕、LLM 生成尚未开始），此刻收口 "retrieval" span 才是真实
            # 检索耗时。旧实现在外层 invoke 返回后才收口，把 LLM 生成的
            # 十几秒全部计入了"检索"。
            try:
                trace_collector.end_open_span(
                    "retrieval",
                    metrics={"retrieved_chunks": len(context_docs),
                             "total_docs": len(context_docs)})
            except Exception:
                logger.debug("[RAGChain] retrieval span 提前收口失败", exc_info=True)
            if not context_docs:
                logger.info("[RAGChain] 空检索短路，跳过 LLM Generate")
                return AIMessage(content="知识库暂无相关资料。")
            # Gate 前置：检索层 Gate 已拒（实体覆盖/证据不足）→ 跳过 LLM 生成
            gate_injected = (context_docs[0].metadata or {}).get("__evidence_gate_decision__") or {}
            if gate_injected.get("gate_passed") is False:
                logger.info(
                    f"[RAGChain] Gate 前置拒答，跳过 LLM Generate: "
                    f"reason={gate_injected.get('gate_reason')}")
                return AIMessage(content="知识库暂无相关资料。")
            llm_span = trace_collector.start_span(
                "llm_generate", name="LLM生成",
                kind=SpanKind.LLM.value,
                input={"question": inp.get("input", "")[:1000]},
            )
            try:
                # ── P1 真 token 级流式：流式消费生成 chunk，边生成边经 sink
                # 推给 SSE（TTFT 从"生成完"提前到"首 chunk 到达"）。
                # 聚合后返回类型与 invoke 保持一致（当前版本
                # create_stuff_documents_chain 返回 str，下游 strip_think
                # 等仅接受 str）；开关关闭时走 invoke。
                if ENABLE_TOKEN_STREAMING:
                    parts: list[str] = []
                    is_message = False
                    for chunk in _stuff.stream(inp):
                        text = extract_chunk_text(chunk)
                        if not text:
                            continue
                        parts.append(text)
                        emit_stream_delta(text)
                        if not isinstance(chunk, str) and hasattr(chunk, "content"):
                            is_message = True
                    joined = "".join(parts)
                    r = AIMessage(content=joined) if is_message else joined
                else:
                    r = _stuff.invoke(inp)
                # 注入 token + finish_reason + cost_usd（从 proxy ContextVar 读，
                # 与 _record_tokens 同上下文，保证并发下各请求读到自己的 token）
                from backend.infra.llm.proxy import _last_call_meta_var
                metrics = dict(_last_call_meta_var.get())
                # P0-3: ContextVar 为空（跨线程丢失 / provider 未返回）时，
                # 兜底从 response_metadata 提取；仍缺失则显式留痕，
                # 让 token 采集失败可观测而非静默归零。
                if not (metrics.get("total_tokens") or metrics.get("prompt_tokens")
                        or metrics.get("completion_tokens")):
                    tu = trace_collector.parse_tokens(r)
                    if tu:
                        metrics.update(tu)
                        metrics["token_source"] = "response_metadata"
                    else:
                        metrics["token_source"] = "unavailable"
                        try:
                            from backend.observability.metrics import llm_usage_missing_total
                            llm_usage_missing_total.inc()
                        except Exception:
                            pass
                # 截断文本字段，避免大输出撑爆 trace。
                # 注意：create_stuff_documents_chain 的返回值随 langchain 版本不同
                # 可能是 AIMessage（.content）或 str（无 .content 属性）——
                # 旧版返回 str 曾导致 completion_text 永远为空（RESPONSE 面板空白）。
                if isinstance(r, str):
                    completion_text = r[:1000]
                elif hasattr(r, "content") and isinstance(r.content, str):
                    completion_text = r.content[:1000]
                elif hasattr(r, "content"):
                    completion_text = str(r.content)[:1000]
                if not completion_text:
                    # 兜底：推理模型 thinking 开启时 content 为空（输出在
                    # reasoning_content），记录思考链供审计而非静默留空
                    reasoning = (getattr(r, "additional_kwargs", {}) or {}).get("reasoning_content", "")
                    if reasoning:
                        completion_text = f"[reasoning] {reasoning[:900]}"
                if completion_text:
                    metrics["completion_text"] = completion_text
                logger.info(
                    f"[RAGChain][diag] llm metrics keys={sorted(metrics.keys())} "
                    f"ct_len={len(completion_text)} "
                    f"r_type={type(r).__name__} "
                    f"content_repr={repr(getattr(r, 'content', r))[:80]}")
                trace_collector.end_span(llm_span, metrics=metrics)
                return r
            except Exception:
                trace_collector.end_span(llm_span, status="error")
                raise
        stuff_chain = RunnableLambda(_index_docs) | RunnableLambda(_timed_stuff)

        # ── ⑤ Hybrid Retrieval（最内层：实际搜索）─────────
        retriever = self.chunk_retriever_base

        # ── ④ Adaptive: 同文档 Chunk 扩展 ──────────────
        retriever = AdaptiveRetriever(
            base_retriever=retriever,
            doc_db=self.doc_db,
        )

        # ── ③ MultiQuery: 复杂度检测 → LLM 改写 ─────────
        from backend.rag.retrieval.multi_query import MultiQueryRetriever
        retriever = MultiQueryRetriever(base_retriever=retriever)
        self._mq_retriever = retriever  # 供 tracer 读取 MultiQuery 状态

        # ── ② Rerank: CrossEncoder 全局重排序（包在 MultiQuery 外层）──
        # 变体先各自检索合并去重，重排只在合并结果上执行一次；
        # 避免每个改写变体各自触发一次重排（2026-09-03 事故性能问题）
        retriever = ContextualCompressionRetriever(
            base_compressor=RerankCompressor(),
            base_retriever=retriever,
        )
        self._rerank_wrapper = retriever  # Rerank 包装层（供测试/诊断断言装配顺序）

        # ── Gate 前置：检索后、LLM 生成前执行 Evidence Gate（Gate 1+1.5+2）──
        # Why: 原 Gate 排在 LLM 之后（_execute 里 invoke 后评估），拒答场景
        # 白烧一次 LLM 调用（2~6s + token）。前置后拒答在 _timed_stuff 短路。
        # decision 经 doc.metadata 注入传递（链内同步安全，不依赖 ContextVar）。
        gate_retriever = RunnableLambda(self._gate_wrap_retrieve)

        # ── ① HistoryAware: 对话历史改写（最外层，最先执行）─
        # 双链策略：standalone 链跳过 HistoryAware LLM 调用，首轮对话省 ~1-2s
        self.chain_standalone = create_retrieval_chain(gate_retriever, stuff_chain)
        if ENABLE_HISTORY_AWARE_RETRIEVAL:
            retriever = create_history_aware_retriever(
                llm, gate_retriever, _build_contextualize_prompt()
            )

        self.chain = create_retrieval_chain(retriever, stuff_chain)

    def _register_prompt_reload_hooks(self):
        """注册 prompt 热加载钩子：版本发布时标记 chain 需要重建。"""
        try:
            from backend.prompts.service import prompt_service
            for key in ("rag.qa", "rag.contextualize", "rag.document"):
                prompt_service.register_reload_hook(key, self._mark_chains_dirty)
        except Exception:
            logger.debug("[RAGChain] prompt_service 不可用，跳过热加载注册",
                         exc_info=True)

    def _mark_chains_dirty(self):
        self._chains_dirty = True

    # =================================================
    # Step C: 公共入口
    # =================================================

    def ask(self, question: str, session_id: str = "default") -> str:
        """RAGChain 入口：线性 3 段 — prepare → execute → respond。

        每次调用先初始化请求级中间态（P1 并发隔离）：
          - 决策状态（_last_meta/_last_faithfulness/mq_triggered）随 RequestContext
            隔离：RAGChain 是进程级单例，实例字段会被并发请求互相覆盖；
          - gate/corrector 每请求新建实例：复用实例会让 intent/risk_level/
            retry_count 在并发请求间串扰（retry_count 还会跨请求累积）。
        """
        if self._chains_dirty:
            self._build_chains()
            self._chains_dirty = False
        trace, t_total = self._start(question, session_id)
        try:
            ctx = get_context()
            ctx.meta = {}
            ctx.faithfulness = None
            ctx.mq_triggered = False
            ctx.gate = EvidenceGateController()
            ctx.corrector = SelfCorrectionStrategy()
            set_context(ctx)
            chat_history = self._prepare(question, session_id)
            result = self._execute(question, chat_history)
            return self._respond(result, trace, question, session_id, t_total)
        except Exception:
            self._finish_error(trace, t_total)
            raise

    def _start(self, question: str, session_id: str):
        """开启 Trace + root span。

        嵌入模式：若已在父 trace（如 agent）内调用，不创建独立子 trace，
        而是在父 trace 中创建 "rag_skill" span 作为作用域根，所有 RAG span
        自动嵌套其下。前端无需跳转子 trace 即可看到完整 RAG 链路。
        """
        import time as _time

        from backend.observability.tracer import _scope_root_var, trace_collector
        prev = trace_collector.current()
        if prev is not None:
            trace = prev
            trace._rag_embedded = True
            trace_collector.start_span(
                "rag_skill", name="RAG 智能问答", type="agent",
                kind="skill", input={"question": question})
            _scope_root_var.set("rag_skill")
            logger.info(f"[RAGChain] 嵌入模式（父 trace={prev.id[:8]}）: {question[:60]}...")
        else:
            trace = trace_collector.start(question, session_id)
            trace._rag_embedded = False
            trace.sla_threshold_ms = 30000
            trace_collector.start_span("root", parent_id=None,
                                       name="RAG 智能问答", type="agent",
                                       input={"question": question})
            logger.info(f"[RAGChain] 收到问题: {question[:60]}... (session={session_id})")
        return trace, _time.time()

    def _respond(self, result, trace, question, session_id, t_total) -> str:
        """统一决策：Gate 1+2(注入) → verify+evaluate → Gate 3 LLM 自报 → Self-Correction。

        返回 answer 或 rejection msg。
        """
        # Gate 1+2: retrieval/rerank 已在 _execute 注入 decision
        decision = result.get("__evidence_gate_decision__")
        if decision is not None and not decision.passed:
            answer = self._reject(decision, decision.layer or "retrieval",
                                trace, t_total)
            self._record_rag_metric("rejected")
            return answer

        # Citation + ClaimVerifier + Faithfulness
        answer = self._verify(result, question, session_id)

        # ── 程序化 Claim Verifier（确定性事实校验，LLM Judge 不能覆盖）──
        answer = self._verify_claims(answer, result.get("context", []))
        if answer is None:
            # 数字/时效编造 → 零容忍，直接拒答
            from backend.rag.evidence_gate import GateDecision, RejectReason
            decision = GateDecision(
                passed=False, reason=RejectReason.HALLUCINATION,
                layer="claim_verify", score=0.0,
                diagnostics={"reason": "numeric_fact_not_supported"},
            )
            answer = self._reject(decision, "claim_verify", trace, t_total)
            self._record_rag_metric("rejected")
            return answer

        answer = self._evaluate(answer, result.get("context", []))

        # ── Evaluation Gate: Faithfulness 低分拒答（程序化门槛，LLM 自报不能覆盖）──
        # 修复：is_groundedness_acceptable 此前只定义未被调用，
        # FAITHFULNESS_REJECT_SCORE / HIGH_RISK_REJECT_SCORE 一直未生效。
        # 阈值从 config 传入（保持配置单一来源，函数签名的默认值仅作兜底）。
        if self._last_faithfulness is not None:
            from backend.config import (
                FAITHFULNESS_REJECT_SCORE,
                HIGH_RISK_REJECT_SCORE,
            )
            from backend.rag.evidence_gate import (
                GateDecision,
                RejectReason,
                is_groundedness_acceptable,
            )
            acceptable, _ = is_groundedness_acceptable(
                self._last_faithfulness.score,
                risk_level=self.gate.risk_level,
                low_threshold=FAITHFULNESS_REJECT_SCORE,
                high_threshold=HIGH_RISK_REJECT_SCORE,
            )
            if not acceptable:
                decision = GateDecision(
                    passed=False, reason=RejectReason.HALLUCINATION,
                    layer="evaluation", score=self._last_faithfulness.score,
                    diagnostics={"reason": "faithfulness_below_threshold"},
                )
                answer = self._reject(decision, "evaluation", trace, t_total)
                self._record_rag_metric("rejected")
                return answer

        # Gate 3: LLM 自报拒答 (META can_answer=False)
        meta = self._last_meta or {}
        if not meta.get("can_answer", True):
            answer = self._handle_llm_reject(meta, trace, question, session_id, t_total)
            self._record_rag_metric("rejected")
            return answer

        # 2026-08-11：NLI 推理超时 fallback 视为"有输出"（避免与拒答混淆）
        if self._last_faithfulness and self._last_faithfulness.score >= 0.99:
            self._record_rag_metric("fallback")
        else:
            self._record_rag_metric("hit")

        self._finish(trace, answer, t_total)
        return answer

    def _record_rag_metric(self, status: str) -> None:
        """埋点 RAG 查询结果到运营指标（2026-08-11）。"""
        try:
            from backend.observability.metrics import record_rag_status
            record_rag_status(status)
        except Exception as e:
            # 埋点失败不影响主流程（可观测降级），但必须留痕，不能静默吞掉
            logger.debug(f"[RAGChain] 指标埋点失败: {e}", exc_info=True)

    def _handle_llm_reject(self, meta, trace, question, session_id, t_total) -> str:
        """Gate 3（LLM 自报拒答）的统一处理。

        先尝试 self-correction（改写 query 重试）——目的是把『资料确实没有』
        和『检索没找对』区分开；重试仍失败或已用尽重试次数时，走统一拒答，
        保证不生成无依据答案。self-correction 不能无限循环，
        循环次数受 SelfCorrectionStrategy.retry_count 上限约束。
        """
        decision = self.gate.build_decision_from_meta(meta)

        if self.corrector.can_retry():
            retried = self._try_self_correct(decision, trace, question, session_id, t_total)
            if retried is not None:
                self._finish(trace, retried, t_total)
                return retried

        # self-correction 关闭 / 失败 / 重试用尽 → 拒答
        attempted = self.corrector.retry_count > 0
        return self._reject(decision, "generation", trace, t_total,
                            self_correction_attempted=attempted)

    def _reject(self, decision, layer: str, trace, t_total: float,
                self_correction_attempted: bool = False) -> str:
        """统一拒答路径。

        所有 Gate 拒答（retrieval/rerank/claim_verify/generation）都走这里：
        构造 RejectInfo → 写 trace（rejection metadata + root span 状态）→
        完成 trace → 返回拒答消息。集中在一处是为了让拒答行为（消息格式、
        trace 埋点、指标）保持一致，而不是散落在各 Gate 分支。
        """
        from backend.rag.evidence_gate import build_rejection_response
        msg, info = build_rejection_response(decision, layer,
                                             self_correction_attempted=self_correction_attempted)
        try:
            trace.metadata["rejection"] = info.to_dict()
        except Exception:
            logger.debug("trace metadata rejection 写入失败", exc_info=True)
        # P1-8: 拒答详情单一事实源 = metadata.rejection（上方已写），
        # root span 只留布尔索引位，不再三处冗余 reason/gate_layer。
        metrics = {"rejected": True}
        if self_correction_attempted:
            metrics["self_correction"] = "attempted"
        self._end_root_span(trace,
            output={"answer_preview": msg[:200], "answer_len": len(msg)},
            metrics=metrics)
        self._finish(trace, msg, t_total)
        logger.info(f"[RAGChain] 拒答 layer={layer} reason={info.reason}")
        return msg

    def _finish(self, trace, answer: str, t_total: float):
        """统一 trace 收尾。"""
        import time as _time

        from backend.config.llm import LLM_MODEL
        from backend.infra.llm.factory import get_llm_factory
        from backend.observability.tracer import _scope_root_var, trace_collector
        total_ms = int((_time.time() - t_total) * 1000)
        self._end_root_span(trace,
            output={"answer_preview": answer[:200], "answer_len": len(answer)},
            metrics={"span_count": sum(1 for s in trace.spans if s.parent_id is not None)})
        # 嵌入模式：清理 scope root，不 finish trace（属于父 trace）
        if getattr(trace, '_rag_embedded', False):
            _scope_root_var.set(None)
            return
        provider = ""
        try:
            provider = get_llm_factory()._get_provider(LLM_MODEL)
        except Exception:
            logger.debug("LLM provider 检测失败", exc_info=True)
        trace_collector.finish(trace, answer, total_ms, LLM_MODEL, provider)

    def _finish_error(self, trace, t_total: float):
        """异常路径收尾。

        主流程异常时把 root span 标为 error 并完成 trace，
        保证 Trace 不因异常而丢失（P0）。此函数自身在异常处理路径中，
        收尾再失败时不覆盖原始异常，仅记录日志。
        """
        import time as _time

        from backend.observability.tracer import _scope_root_var, trace_collector
        try:
            self._end_root_span(trace, status="error",
                                metrics={"error": "pipeline_failed"})
            if getattr(trace, '_rag_embedded', False):
                _scope_root_var.set(None)
                return
            trace_collector.finish(trace, "[ERROR]",
                                   int((_time.time() - t_total) * 1000), "", "")
        except Exception as e:
            logger.error("[RAGChain] error cleanup failed: %s", e, exc_info=True)

    def _try_self_correct(self, original_decision, trace, question, session_id, t_total):
        """Self-Correction：改写 query 重试。

        Returns:
            None    → 改写失败 / 仍拒答 (让 _handle_llm_reject 走兜底)
            str     → 新答案 (成功) 或 重试后的拒答 msg
        """
        self.corrector.record_attempt(success=False)
        reason_str = (original_decision.reason.value
                      if original_decision.reason else "no_evidence")
        new_query = self.corrector.try_rewrite(question, reason_str)
        if new_query is None:
            return None

        try:
            history = self._prepare(new_query, session_id)
            result = self._execute(new_query, history)

            # 仍拒答 (Gate 1+2) → 走 _handle_llm_reject 兜底
            decision = result.get("__evidence_gate_decision__")
            if decision is not None and not decision.passed:
                return None

            answer = self._verify(result, new_query, session_id)
            # 二次生成同样必须过程序化 Claim 校验（数字/时效零容忍），
            # 否则 self-correction 会成为绕过 ClaimVerifier 的旁路。
            answer = self._verify_claims(answer, result.get("context", []))
            if answer is None:
                # 二次生成仍编造确定性事实 → 放弃，走 _handle_llm_reject 兜底拒答
                return None
            answer = self._evaluate(answer, result.get("context", []))
            meta = self._last_meta or {}
            if not meta.get("can_answer", True):
                # LLM 二次拒答 → 走 _handle_llm_reject 兜底
                return None
            logger.info(f"[RAGChain] Self-Correction 救活: question={question[:60]}")
            return answer
        except Exception as e:
            logger.warning(f"[Self-Correction] 重试失败: {e}")
            return None

    def _prepare(self, question: str, session_id: str) -> list:
        """准备阶段：Memory 启动会话，返回 chat_history。"""
        l1 = self._memory.start_session(session_id, question) if self._memory else None
        return list(l1.messages) if l1 else []

    def _execute(self, question: str, chat_history: list) -> dict:
        """执行阶段：chain.invoke + MultiQuery 决策 trace + Retrieval Debug。

        Evidence Gate 在 _execute 末尾统一编排（唯一执行点）：
          - Gate 1: 优先读取 hybrid.py 已注入的 retrieval decision（快路径，
            避免 Retriever 层与 Chain 层重复计算同一 Gate）；仅当空召回或
            未注入（fallback 路径）时在此补跑一次，处理 NO_EVIDENCE。
          - Gate 2: 基于 context 上的 rerank_score 跑 Rerank Gate。
        拒答时把 GateDecision 写到 result["__evidence_gate_decision__"]，
        上层 ask() 据此短路 verify/evaluate。

        Returns:
            dict 含 "context" / "answer" / "__evidence_gate_decision__" / 可选 "__rejected"
        """
        import time as _time

        from backend.observability.tracer import trace_collector

        # ── 记录 query 上下文（§D4 修复） ──
        self._last_query = question
        try:
            from backend.rag.context import get_context
            from backend.rag.retrieval.query_analyzer import QueryAnalyzer
            qa_result = QueryAnalyzer().analyze(question)
            get_context().query_analysis = qa_result
            self.gate.set_query_analysis(qa_result)
        except Exception as e:
            # 查询分析失败不阻塞主链路（软降级），但需留痕以便定位
            logger.debug(f"[RAGChain] QueryAnalyzer 分析失败: {e}", exc_info=True)
            self.gate.set_query_analysis(None)

        # ── retrieval span（挂在真实检索边界：_timed_stuff 收到 context 时
        # 提前收口；此处的 end_span 仅在异常/短路等未收口路径兜底）──
        from backend.observability.tracer import SpanName as _SpanName
        ret_span = trace_collector.start_span(
            "retrieval",
            name=_SpanName.RETRIEVAL, type="retrieval",
            kind="retrieval",
            input={"question": question[:500]},
        )

        # ── 双链选择：无历史时跳过 HistoryAware LLM 调用 ──
        active_chain = self.chain_standalone if not chat_history else self.chain
        result = active_chain.invoke({"input": question, "chat_history": chat_history})

        # ── 采集检索中间结果（正常路径 span 已提前收口，end_span 幂等跳过）──
        context_docs = result.get("context", [])
        self._record_retrieval_events(ret_span, context_docs)
        trace_collector.end_span(ret_span, metrics={"total_docs": len(context_docs)})

        # mq_check
        mq_span = trace_collector.start_span("mq_check", name=_SpanName.MULTI_QUERY)
        mq = getattr(self, '_mq_retriever', None)
        triggered = mq._last_triggered if mq else False
        from backend.rag.retrieval.multi_query import get_mq_mode
        trace_collector.end_span(mq_span,
            metrics={"triggered": triggered, "mode": get_mq_mode()},
            status="skipped" if not triggered else "success")

        # ── Evidence Gate 决策链 ────────────────────────────────
        # Gate 已前置到检索后/LLM 前（_gate_wrap_retrieve，链内执行），
        # 此处从 doc.metadata 反序列化决策结果，供 _respond 统一决策。
        # 无注入（空召回等）→ 回退原 Gate 评估路径，保持空召回拒答行为。
        injected = (context_docs[0].metadata.get("__evidence_gate_decision__")
                    if context_docs else None)
        if injected is not None:
            result["__evidence_gate_decision__"] = self._decision_from_injected(injected)
        else:
            result["__evidence_gate_decision__"] = self._run_evidence_gates(
                question, context_docs)
        return result

    def _gate_wrap_retrieve(self, payload):
        """Gate 前置检索包装：调底层检索 → 执行 Evidence Gate → 注入 decision。

        create_retrieval_chain / history_aware_retriever 的 retriever 槽位。
        payload 兼容 dict（{"input": ...}）与 str 两种输入形态。
        实体覆盖校验使用 self._last_query（原始用户问题）——history-aware
        改写后的 query 不反映用户原始实体。
        """
        query = payload.get("input") if isinstance(payload, dict) else payload
        docs = list(self._rerank_wrapper.invoke(query))
        try:
            from backend.rag.evidence_gate import is_evidence_gate_enabled
            if is_evidence_gate_enabled() and docs:
                question = self._last_query or (query if isinstance(query, str) else str(query))
                decision = self._run_evidence_gates(question, docs)
                if decision is not None:
                    stamp = {
                        "gate_passed": bool(decision.passed),
                        "gate_layer": decision.layer or "retrieval",
                        "gate_score": float(decision.score or 0.0),
                        "gate_reason": (decision.reason.value if decision.reason else ""),
                        **(decision.diagnostics or {}),
                    }
                    for d in docs:
                        d.metadata["__evidence_gate_decision__"] = stamp
        except Exception as e:  # noqa: BLE001
            # Gate 前置失败 → 不拦截 docs，交由原兜底路径处理（软降级）
            logger.warning(f"[RAGChain] Gate 前置评估失败，透传 docs: {e}", exc_info=True)
        return docs

    def _decision_from_injected(self, injected):
        """doc.metadata 注入的 decision dict → GateDecision 对象。

        无注入（空 docs / Gate 关闭）→ 透传放行，与原 _run_evidence_gates
        的兜底行为一致。
        """
        from backend.rag.evidence_gate import (
            gate_retrieval_passthrough,
            is_evidence_gate_enabled,
        )
        if injected is None or not is_evidence_gate_enabled():
            return gate_retrieval_passthrough()
        from backend.rag.evidence_gate import GateDecision, RejectReason
        try:
            return GateDecision(
                passed=bool(injected.get("gate_passed")),
                reason=(RejectReason(injected["gate_reason"])
                        if injected.get("gate_reason") else None),
                layer="retrieval",
                score=float(injected.get("gate_score", 0.0)),
                diagnostics={k: v for k, v in injected.items()
                             if k not in ("gate_passed", "gate_layer",
                                          "gate_score", "gate_reason")},
            )
        except Exception as e:
            logger.warning(f"[RAGChain] Gate decision 反序列化失败，透传放行: {e}")
            return gate_retrieval_passthrough()

    def _run_evidence_gates(self, question: str, context_docs: list):
        """两层 Gate（Retrieval + Rerank）的合并判定。

        行为契约：
          - 任一 Gate passed=False → 返回该 GateDecision
          - 都通过 → 返回最后一个 passed=True 的 GateDecision
          - 总开关关闭 / 任何异常 → 返回 passed=True (透传)
        """
        from backend.observability.tracer import SpanKind, trace_collector
        from backend.rag.evidence_gate import (
            evidence_gate_rerank,
            evidence_gate_retrieval,
            gate_retrieval_passthrough,
            is_evidence_gate_enabled,
        )

        if not is_evidence_gate_enabled():
            return gate_retrieval_passthrough()

        # ── Gate 1: Retrieval（hybrid.py 注入的 decision 作为快路径）──
        gate_span = trace_collector.start_span(
            "evidence_gate_retrieval", name="Evidence Gate - Retrieval",
            kind=SpanKind.RETRIEVAL_GATE.value,
        )

        # 优先复用 hybrid.py 注入的 decision
        injected = (context_docs[0].metadata.get("__evidence_gate_decision__")
                    if context_docs else None)
        if injected is not None:
            # 序列化 → 反序列化为 GateDecision-like
            from backend.rag.evidence_gate import GateDecision, RejectReason
            try:
                ret_decision = GateDecision(
                    passed=bool(injected.get("gate_passed")),
                    reason=(RejectReason(injected["gate_reason"])
                            if injected.get("gate_reason") else None),
                    layer="retrieval",
                    score=float(injected.get("gate_score", 0.0)),
                    diagnostics={k: v for k, v in injected.items()
                                 if k not in ("gate_passed", "gate_layer",
                                              "gate_score", "gate_reason")},
                )
            except Exception as e:
                # 注入的 decision 反序列化失败 → 透传放行（软降级），留痕以便 trace 定位
                logger.warning(f"[RAGChain] Gate 1 decision 反序列化失败，透传放行: {e}")
                ret_decision = gate_retrieval_passthrough()
        else:
            # 没注入（空召回或 fallback 路径）→ 自己跑一次
            try:
                from backend.config import DOC_TYPE_COVERAGE_REQUIRED, VEC_MIN_SCORE
                ret_decision = evidence_gate_retrieval(
                    context_docs,
                    query_analysis=self.gate.query_analysis,
                    vec_min_score=VEC_MIN_SCORE,
                    require_doc_type_coverage=DOC_TYPE_COVERAGE_REQUIRED,
                )
            except Exception as e:
                # Gate 评估异常 → 透传放行（软降级），留痕；不放行拒答会误伤正常检索
                logger.warning(f"[RAGChain] Gate 1 评估异常，透传放行: {e}", exc_info=True)
                ret_decision = gate_retrieval_passthrough()

        trace_collector.end_span(gate_span, metrics=ret_decision.to_metrics(),
                                 status="success" if ret_decision.passed else "rejected")

        if not ret_decision.passed:
            return ret_decision

        # ── Gate 1.5: 查询实体覆盖校验（P2，2026-08-21）──
        # 主题相近但无答案：问题核心实体（含同义词闭包）不在 rerank 后 top-3
        # 召回文本中 → 改判拒答。在 chain 层用原始 question 判定（hybrid 层
        # 拿到的是同义词变体 query，不适用）；异常不干预原判（软降级）。
        if context_docs:
            try:
                from backend.config import GATE_ENTITY_CHECK_ENABLED
                if GATE_ENTITY_CHECK_ENABLED:
                    from backend.rag.evidence_gate import (
                        GateDecision,
                        RejectReason,
                        find_missing_entities,
                    )
                    missing = find_missing_entities(question, context_docs)
                    if missing:
                        ret_decision = GateDecision(
                            passed=False, reason=RejectReason.NO_EVIDENCE,
                            layer="retrieval", score=ret_decision.score,
                            diagnostics={**(ret_decision.diagnostics or {}),
                                         "entity_check": "fail",
                                         "missing_entities": missing[:5]},
                        )
                        logger.info(
                            f"[RAGChain] 实体覆盖校验拒答: missing={missing[:5]}"
                        )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[RAGChain] 实体覆盖校验异常，忽略: {e}")

        if not ret_decision.passed:
            return ret_decision

        # ── Gate 2: Rerank（基于 context 上的 rerank_score）──
        from backend.rag.evidence_gate import risk_level_from_intent_and_doctype
        try:
            self.gate.set_risk_level(risk_level_from_intent_and_doctype(
                self.gate.intent,
                getattr(self.gate.query_analysis, "doc_types", []) or [],
            ))
        except Exception as e:
            # 风险等级推导失败 → 保守按低风险处理（软降级），留痕
            logger.debug(f"[RAGChain] 风险等级推导失败，按 low 处理: {e}", exc_info=True)
            self.gate.set_risk_level("low")

        rerank_span = trace_collector.start_span(
            "evidence_gate_rerank", name="Evidence Gate - Rerank",
            kind=SpanKind.RERANK_GATE.value,
        )
        try:
            from backend.config import (
                RERANK_HIGH_RISK_MIN_TOP1,
                RERANK_MIN_AVG,
                RERANK_MIN_GAP,
                RERANK_MIN_TOP1,
            )
            rerank_decision = evidence_gate_rerank(
                context_docs,
                intent=self.gate.intent,
                risk_level=self.gate.risk_level,
                min_top1=RERANK_MIN_TOP1,
                min_avg=RERANK_MIN_AVG,
                min_gap=RERANK_MIN_GAP,
                high_risk_min_top1=RERANK_HIGH_RISK_MIN_TOP1,
            )
        except Exception as e:
            # Gate 2 评估异常 → 透传放行（软降级），留痕；不放行会误伤正常检索
            logger.warning(f"[RAGChain] Gate 2 评估异常，透传放行: {e}", exc_info=True)
            rerank_decision = gate_retrieval_passthrough()

        trace_collector.end_span(rerank_span,
                                 metrics=rerank_decision.to_metrics(),
                                 status="success" if rerank_decision.passed else "rejected")

        return rerank_decision

    def _record_retrieval_events(self, ret_span, context_docs: list) -> None:
        """采集检索各阶段的中间结果，写入 ret_span.events。"""
        from backend.observability.tracer import trace_collector

        # ── Event 1: Query Analyzer ──
        try:
            from backend.rag.context import get_context
            pq = get_context().query_analysis
            if pq is not None:
                trace_collector.add_event(ret_span, "query_analyzer", "info",
                    f"intent={pq.intent}, doc_types={pq.doc_types}",
                    data={"intent": pq.intent, "doc_types": pq.doc_types,
                          "metadata_filter": pq.to_metadata_filter()})
        except Exception:
            logger.debug("query_analysis span 记录失败", exc_info=True)

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
        """验证阶段：剥离 think 块 + META 注释解析 + Citation 校验 + 格式化引用。

        P1 改造：
          - 解析 LLM 末尾 <!--META--> 注释，剥离出纯 Markdown
          - META 信息存到 self._last_meta，让 ask() 后续判定拒答/放行
        """
        from backend.observability.tracer import trace_collector
        from backend.rag.evidence_gate import parse_meta_comment

        raw_answer = self.formatter.strip_think(result["answer"])
        context_docs = result.get("context", [])

        # ── P1: 解析 META 注释 ──
        from backend.observability.tracer import SpanName as _SpanName
        meta_span = trace_collector.start_span("meta_parse", name=_SpanName.META_PARSE)
        cleaned_answer, meta = parse_meta_comment(raw_answer)
        self._last_meta = meta
        trace_collector.end_span(meta_span,
                                 metrics={"can_answer": meta.get("can_answer"),
                                          "citations_count": len(meta.get("citations", [])) if meta else 0,
                                          "confidence": meta.get("confidence")})

        # ── Citation 校验（基于已剥离 META 的 cleaned_answer）──
        citation_span = trace_collector.start_span(
            "citation", name=_SpanName.CITATION)
        if context_docs:
            answer, verified_docs = self.formatter.verify_support(cleaned_answer, context_docs, question)
        else:
            answer = cleaned_answer
            verified_docs = []
        trace_collector.end_span(citation_span,
                             metrics={"verified_citations": len(verified_docs),
                                      "total_citations": len(context_docs)})
        references = self.formatter.format_references(verified_docs, answer)
        if references:
            answer = answer + references
        self._last_sources = self.formatter.extract_sources(verified_docs, answer)

        if self._memory:
            self._memory.end_turn(session_id, question, answer)

        return answer

    def _verify_claims(self, answer: str, context_docs: list) -> str | None:
        """程序化 Claim 校验（非 LLM）。

        确定性事实（数字+单位/日期/金额/时效）与引用 chunk 原文比对。
        任一 claim 校验失败 → 返回 None（调用方拒答），LLM Judge 不能覆盖。

        Returns:
            answer（通过时原样返回）或 None（编造事实被拦截）。
        """
        from backend.observability.tracer import SpanName as _SpanName
        from backend.observability.tracer import trace_collector
        from backend.rag.evidence_gate.claim_verifier import verify_answer

        claim_span = trace_collector.start_span(
            "claim_verify", name=_SpanName.CLAIM_VERIFY)
        try:
            verifier = verify_answer(answer, context_docs)
            trace_collector.end_span(claim_span,
                metrics={"passed": verifier.passed,
                         "failed_claims": len(verifier.failed_claims),
                         "reason": verifier.reason})
            if not verifier.passed:
                logger.warning(
                    f"[RAGChain] ClaimVerifier 拦截编造事实: {verifier.detail[:200]}"
                )
                return None
            return answer
        except Exception as e:
            # 校验器异常不阻塞主流程（软失败），交给 LLM Judge 兜底
            logger.warning(f"[RAGChain] ClaimVerifier 异常跳过: {e}")
            trace_collector.end_span(claim_span, status="skipped",
                                     metrics={"error": str(e)[:100]})
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
        import re

        from backend.observability.tracer import SpanName as _SpanName
        from backend.observability.tracer import trace_collector
        self._last_faithfulness = None

        try:
            from backend.rag.guardrails import check_faithfulness

            # 剥离 reference section（避免元数据行被误提取为 claim）
            ref_match = re.search(r'\n---\n\s*\n###\s*参考文献\s*\n', answer)
            answer_body = answer[:ref_match.start()] if ref_match else answer
            ref_section = answer[ref_match.start():] if ref_match else ""

            faith_span = trace_collector.start_span(
                "faithfulness", name=_SpanName.EVALUATE)
            self._last_faithfulness = check_faithfulness(answer_body, context_docs)
            trace_collector.end_span(faith_span,
                                 metrics={
                                     "score": self._last_faithfulness.score,
                                     "claims": self._last_faithfulness.total_claims,
                                     "supported": self._last_faithfulness.supported_claims,
                                     "unsupported": self._last_faithfulness.unsupported_claims,
                                 })

            # 如果有不可信 claim，用清洗后的答案（保留原 reference section）
            if self._last_faithfulness.cleaned_answer and \
               self._last_faithfulness.cleaned_answer != answer_body:
                logger.warning(
                    f"[RAGChain] 自动剔除 {self._last_faithfulness.unsupported_claims} 条不可信内容"
                )
                return self._last_faithfulness.cleaned_answer + ref_section
            return answer
        except Exception as e:
            logger.warning(f"[RAGChain] Faithfulness 检测跳过: {e}")
            trace_collector.end_span(faith_span, status="skipped",
                                 metrics={"error": str(e)[:100]})
            return answer

    @staticmethod
    def _end_root_span(trace, output: dict = None, metrics: dict = None,
                       status: str = "success"):
        """查找并结束 root span（parent_id=None 的那条）。

        嵌入模式：结束 "rag_skill" span（而非父 trace 的 root）。
        """
        from backend.observability.tracer import trace_collector
        if getattr(trace, '_rag_embedded', False):
            for sp in trace.spans:
                if sp.span_id == "rag_skill":
                    trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
                    return
            return
        for sp in trace.spans:
            if sp.parent_id is None:
                trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
                return
