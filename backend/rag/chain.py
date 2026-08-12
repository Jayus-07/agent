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
from backend.rag.citation import CitationFormatter
from backend.rag.evidence_gate import EvidenceGateController
from backend.rag.evidence_gate.self_correction import SelfCorrectionStrategy
from backend.config import ENABLE_HISTORY_AWARE_RETRIEVAL
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
# P1 改造：Markdown 正文 + 末尾 <!--META--> 注释（与 Citation 兼容，避 JSON 与 Faithfulness 冲突）
# 详见 docs/architecture/rag-evidence-gate.md §0.4 表
QA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是电商企业知识库助手。你只能依据「资料」中明确提供的信息回答问题。

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
{context}"""),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])

# 单文档格式化：含元数据标签（非空字段才显示，不浪费 token）
DOCUMENT_PROMPT = PromptTemplate.from_template(
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
        # ── PR-1.4: 3 个策略对象（替代原来的 mutable 字段）──
        self.gate = EvidenceGateController()
        self.corrector = SelfCorrectionStrategy()
        self.formatter = CitationFormatter()
        # ── RAGChain 自有状态（PR-1.4 保留）──
        self._last_query: str = ""
        self._last_meta: dict = {}  # P1: META 注释解析结果

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
            llm, QA_PROMPT,
            document_prompt=DOCUMENT_PROMPT,
            document_separator="\n\n---\n\n",
        )
        def _timed_stuff(inp):
            from backend.observability.tracer import trace_collector, SpanKind
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
        """RAGChain 入口：线性 3 段 — prepare → execute → respond。"""
        trace, t_total = self._start(question, session_id)
        try:
            chat_history = self._prepare(question, session_id)
            result = self._execute(question, chat_history)
            return self._respond(result, trace, question, session_id, t_total)
        except Exception:
            self._finish_error(trace, t_total)
            raise

    def _start(self, question: str, session_id: str):
        """开启 Trace + root span。"""
        from backend.observability.tracer import trace_collector
        import time as _time
        trace = trace_collector.start(question, session_id)
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
        except Exception:
            pass  # 埋点失败不影响主流程

    def _handle_llm_reject(self, meta, trace, question, session_id, t_total) -> str:
        """LLM 自报拒答 → self-correction 或直接拒答。"""
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
        """统一拒答：构造 RejectInfo + 写 trace + finish trace + 返回 msg。"""
        from backend.rag.evidence_gate import build_rejection_response
        msg, info = build_rejection_response(decision, layer,
                                             self_correction_attempted=self_correction_attempted)
        try:
            trace.metadata["rejection"] = info.to_dict()
        except Exception:
            logger.debug("trace metadata rejection 写入失败", exc_info=True)
        metrics = {"rejected": True, "reason": info.reason, "gate_layer": layer}
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
        from backend.observability.tracer import trace_collector
        from backend.config.llm import LLM_MODEL
        from backend.infra.llm.factory import get_llm_factory
        import time as _time
        total_ms = int((_time.time() - t_total) * 1000)
        self._end_root_span(trace,
            output={"answer_preview": answer[:200], "answer_len": len(answer)},
            metrics={"span_count": sum(1 for s in trace.spans if s.parent_id is not None)})
        provider = ""
        try:
            provider = get_llm_factory()._get_provider(LLM_MODEL)
        except Exception:
            logger.debug("LLM provider 检测失败", exc_info=True)
        trace_collector.finish(trace, answer, total_ms, LLM_MODEL, provider)

    def _finish_error(self, trace, t_total: float):
        """异常路径收尾。"""
        from backend.observability.tracer import trace_collector
        import time as _time
        try:
            self._end_root_span(trace, status="error",
                                metrics={"error": "pipeline_failed"})
            trace_collector.finish(trace, "[ERROR]",
                                   int((_time.time() - t_total) * 1000), "", "")
        except Exception:
            pass

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

        Evidence Gate 在 _execute 末尾跑：
          - 读取 hybrid.py 已注入的 retrieval Gate decision
          - 跑 Rerank Gate (基于 context 上的 rerank_score)
        拒答时把 GateDecision 写到 result["__evidence_gate_decision__"]，
        上层 ask() 据此短路 verify/evaluate。

        Returns:
            dict 含 "context" / "answer" / "__evidence_gate_decision__" / 可选 "__rejected"
        """
        from backend.observability.tracer import trace_collector, SpanKind
        import time as _time

        # ── 记录 query 上下文（§D4 修复） ──
        self._last_query = question
        try:
            from backend.rag.retrieval.query_analyzer import QueryAnalyzer
            self.gate.set_query_analysis(QueryAnalyzer().analyze(question))
        except Exception:
            self.gate.set_query_analysis(None)

        # ── retrieval span（包裹整个检索过程，挂 debug event）──
        ret_span = trace_collector.start_span(
            "retrieval", parent_id="root",
            name="混合检索", type="retrieval",
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
        mq_span = trace_collector.start_span("mq_check", name="多查询扩展")
        mq = getattr(self, '_mq_retriever', None)
        triggered = mq._last_triggered if mq else False
        from backend.rag.retrieval.multi_query import get_mq_mode
        trace_collector.end_span(mq_span,
            metrics={"triggered": triggered, "mode": get_mq_mode()},
            status="skipped" if not triggered else "success")

        # ── Evidence Gate 决策链 ────────────────────────────────
        result["__evidence_gate_decision__"] = self._run_evidence_gates(
            question, context_docs
        )
        return result

    def _run_evidence_gates(self, question: str, context_docs: list):
        """两层 Gate（Retrieval + Rerank）的合并判定。

        行为契约：
          - 任一 Gate passed=False → 返回该 GateDecision
          - 都通过 → 返回最后一个 passed=True 的 GateDecision
          - 总开关关闭 / 任何异常 → 返回 passed=True (透传)
        """
        from backend.rag.evidence_gate import (
            evidence_gate_retrieval, evidence_gate_rerank,
            is_evidence_gate_enabled, gate_retrieval_passthrough,
        )
        from backend.observability.tracer import trace_collector, SpanKind
        from backend.rag.guardrails import check_faithfulness  # noqa: F401

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
            except Exception:
                ret_decision = gate_retrieval_passthrough()
        else:
            # 没注入（空召回或 fallback 路径）→ 自己跑一次
            try:
                from backend.config import VEC_MIN_SCORE, DOC_TYPE_COVERAGE_REQUIRED
                ret_decision = evidence_gate_retrieval(
                    context_docs,
                    query_analysis=self.gate.query_analysis,
                    vec_min_score=VEC_MIN_SCORE,
                    require_doc_type_coverage=DOC_TYPE_COVERAGE_REQUIRED,
                )
            except Exception:
                ret_decision = gate_retrieval_passthrough()

        trace_collector.end_span(gate_span, metrics=ret_decision.to_metrics(),
                                 status="success" if ret_decision.passed else "rejected")

        if not ret_decision.passed:
            return ret_decision

        # ── Gate 2: Rerank（基于 context 上的 rerank_score）──
        from backend.rag.evidence_gate import risk_level_from_intent_and_doctype
        try:
            self.gate.set_risk_level(risk_level_from_intent_and_doctype(
                self.gate.intent,
                getattr(self.gate.query_analysis, "doc_types", []) or [],
            ))
        except Exception:
            self.gate.set_risk_level("low")

        rerank_span = trace_collector.start_span(
            "evidence_gate_rerank", name="Evidence Gate - Rerank",
            kind=SpanKind.RERANK_GATE.value,
        )
        try:
            from backend.config import (
                RERANK_MIN_TOP1, RERANK_MIN_AVG, RERANK_MIN_GAP,
                RERANK_HIGH_RISK_MIN_TOP1,
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
        except Exception:
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
            from backend.rag.retrieval.query_analyzer import QueryAnalyzer
            qa = QueryAnalyzer()
            pq = qa.analyze(ret_span.input.get("question", ""))
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
        from backend.rag.evidence_gate import parse_meta_comment, RejectReason

        raw_answer = self.formatter.strip_think(result["answer"])
        context_docs = result.get("context", [])

        # ── P1: 解析 META 注释 ──
        meta_span = trace_collector.start_span("meta_parse", name="META 注释解析")
        cleaned_answer, meta = parse_meta_comment(raw_answer)
        self._last_meta = meta
        trace_collector.end_span(meta_span,
                                 metrics={"can_answer": meta.get("can_answer"),
                                          "citations_count": len(meta.get("citations", [])) if meta else 0,
                                          "confidence": meta.get("confidence")})

        # ── Citation 校验（基于已剥离 META 的 cleaned_answer）──
        citation_span = trace_collector.start_span(
            "citation", name="引文校验")
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
        from backend.observability.tracer import trace_collector
        from backend.rag.evidence_gate.claim_verifier import verify_answer

        claim_span = trace_collector.start_span(
            "claim_verify", name="Claim 原文校验")
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
        from backend.observability.tracer import trace_collector
        self._last_faithfulness = None

        try:
            from backend.rag.guardrails import check_faithfulness

            # 剥离 reference section（避免元数据行被误提取为 claim）
            ref_match = re.search(r'\n---\n\s*\n###\s*参考文献\s*\n', answer)
            answer_body = answer[:ref_match.start()] if ref_match else answer
            ref_section = answer[ref_match.start():] if ref_match else ""

            faith_span = trace_collector.start_span(
                "faithfulness", name="忠实度验证")
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

    def _trace(self, trace, answer: str, t_total: float):
        """收尾阶段：结束 root span + 完成 Trace。"""
        from backend.observability.tracer import trace_collector
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
            logger.debug("LLM provider 检测失败", exc_info=True)
        trace_collector.finish(trace, answer, total_ms, LLM_MODEL, provider)

    @staticmethod
    def _end_root_span(trace, output: dict = None, metrics: dict = None,
                       status: str = "success"):
        """查找并结束 root span（parent_id=None 的那条）。"""
        from backend.observability.tracer import trace_collector
        for sp in trace.spans:
            if sp.parent_id is None:
                trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
                return