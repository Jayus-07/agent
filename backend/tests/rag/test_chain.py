"""RAG Chain 核心链路测试（P1 整改新增）。

覆盖任务要求的 8 类关键行为：
  1. 正常问答：ask() → retrieval → rerank → generate → 返回正常答案
  2. 无检索结果 → Gate 拒答，不输出 LLM 生成内容
  3. Retrieval fallback 可观测 → 见 test_retrievers.py
  4. Gate rejection：拒绝时不输出无依据答案
  5. Self-Correction 成功：第一次 Gate 拒答 → rewrite → 第二次检索成功
  6. Self-Correction 最终失败：重试后仍拒答 → 明确拒答、不产生幻觉；重试次数受上限约束（不无限循环）
  7. Trace：检索/多查询扩展/META 解析/引文校验/事实校验/忠实度评估 标准 span 均产生
  8. Gate 1 只执行一次：注入 decision 时 chain 层不重复计算；空召回时仅补跑一次

测试原则：mock 掉外部依赖（chain.invoke / LLM / 向量库），保留 chain 内部编排逻辑真实执行。
"""
import time as _time
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document


# =====================================================
# Helpers
# =====================================================

def _make_docs(n: int = 2) -> list:
    """构造通过 Gate 的检索文档（rerank_score 满足 top1/avg/gap 判定）。"""
    docs = []
    for i in range(1, n + 1):
        docs.append(Document(
            page_content=f"文档{i}内容。跨境退货政策：退货窗口 30 天。",
            metadata={
                "chunk_id": f"c{i}", "doc_id": f"d{i}",
                "doc_type": "general", "source_file": f"doc{i}.md",
                "index": i, "rerank_score": 0.8 if i == 1 else 0.6,
                "similarity": 0.7,
            },
        ))
    return docs


def _inject_gate_ok(docs: list, passed: bool = True) -> list:
    """模拟 hybrid.py 注入 Gate 1 decision（挂到 docs[0].metadata）。"""
    docs[0].metadata["__evidence_gate_decision__"] = {
        "gate_passed": passed,
        "gate_layer": "retrieval",
        "gate_score": 0.8 if passed else 0.1,
        "gate_reason": "" if passed else "no_evidence",
    }
    return docs


def _stub_chain():
    """构造 RAGChain（绕过 __init__，不起真实 chain/向量库/LLM）。"""
    from backend.rag.chain import RAGChain
    from backend.rag.citation import CitationFormatter
    from backend.rag.evidence_gate import EvidenceGateController
    from backend.rag.evidence_gate.self_correction import SelfCorrectionStrategy
    chain = RAGChain.__new__(RAGChain)
    chain.doc_db = None
    chain.vectordb = None
    chain.chunk_retriever = None
    chain.bm25 = None
    chain.person_index = {}
    chain._memory = None
    chain._last_meta = {}
    chain._last_faithfulness = None
    chain._last_sources = []
    chain._last_query = ""
    chain.gate = EvidenceGateController()
    chain.corrector = SelfCorrectionStrategy()
    chain.formatter = CitationFormatter()
    return chain


def _start_trace(name: str):
    """起一个 trace + root span，返回 (trace, t0)。"""
    from backend.observability.tracer import trace_collector
    trace = trace_collector.start(name, session_id="t1")
    try:
        trace_collector.start_span("root", parent_id=None, name="test", type="agent")
    except RuntimeError:
        pass  # root 已存在
    return trace, _time.time()


def _qa_answer(text: str, can_answer: bool = True, reason: str = "no_evidence") -> str:
    """构造带 META 注释的 LLM 输出。"""
    if can_answer:
        meta = '{"can_answer":true,"citations":[],"confidence":0.9}'
    else:
        meta = f'{{"can_answer":false,"reason":"{reason}","confidence":0.1}}'
    return f"{text}<!--META{meta}-->"


# =====================================================
# 1. 正常问答链路
# =====================================================

class TestNormalQA:
    def test_ask_returns_answer_when_gates_pass(self, monkeypatch):
        chain = _stub_chain()
        docs = _inject_gate_ok(_make_docs())

        def fake_invoke(inp):
            return {"input": inp, "context": docs,
                    "answer": _qa_answer("退货窗口为 30 天。")}
        chain.chain = SimpleNamespace(invoke=fake_invoke)

        # 避免真实 NLI/LLM 评估：仅关闭 Faithfulness 的 LLM 调用
        monkeypatch.setattr(chain, "_evaluate", lambda answer, ctx: answer)

        trace, t0 = _start_trace("normal-qa")
        monkeypatch.setattr(chain, "_start",
                            lambda q, sid: (trace, t0))

        answer = chain.ask("退货窗口是多久？", session_id="t1")
        assert "退货窗口为 30 天" in answer
        assert "<!--META" not in answer  # META 注释已被剥离


# =====================================================
# 2. 无检索结果 → Gate 拒答
# =====================================================

class TestEmptyRecallReject:
    def test_empty_recall_rejects_without_llm_answer(self, monkeypatch):
        chain = _stub_chain()
        # 空召回：chain.invoke 返回空 context（LLM 输出不应被采用）
        chain.chain = SimpleNamespace(invoke=lambda inp: {
            "input": inp, "context": [], "answer": "不应被输出的内容",
        })

        def boom(*a, **kw):
            raise AssertionError("Gate 拒答后不应进入 verify 生成路径")
        monkeypatch.setattr(chain, "_verify", boom)

        trace, t0 = _start_trace("empty-recall")
        monkeypatch.setattr(chain, "_start", lambda q, sid: (trace, t0))

        msg = chain.ask("知识库里没有的问题", session_id="t1")
        assert "暂无" in msg or "未提及" in msg
        assert "不应被输出的内容" not in msg


# =====================================================
# 3. Gate rejection：拒绝时不输出无依据答案
# =====================================================

class TestGateRejection:
    def test_gate_rejection_does_not_output_answer(self, monkeypatch):
        chain = _stub_chain()
        docs = _inject_gate_ok(_make_docs(), passed=False)
        chain.chain = SimpleNamespace(invoke=lambda inp: {
            "input": inp, "context": docs, "answer": "低相关度的幻觉答案",
        })

        def boom(*a, **kw):
            raise AssertionError("Gate 拒答后不应进入 verify 生成路径")
        monkeypatch.setattr(chain, "_verify", boom)

        trace, t0 = _start_trace("gate-reject")
        monkeypatch.setattr(chain, "_start", lambda q, sid: (trace, t0))

        msg = chain.ask("某问题", session_id="t1")
        assert "暂无" in msg or "未提及" in msg
        assert "幻觉答案" not in msg


# =====================================================
# 4. Self-Correction
# =====================================================

def _make_sc_chain(monkeypatch, rewrite_result="改写后的问题",
                   second_answer=None, second_gate_passed=True):
    """构造 self-correction 场景的 stub chain。

    - corrector.try_rewrite 返回 rewrite_result（None 表示改写失败）
    - 第二次 _execute 返回 second_answer（None 表示仍拒答）
    """
    chain = _stub_chain()
    chain.corrector.reset()

    if rewrite_result is None:
        monkeypatch.setattr(chain.corrector, "try_rewrite",
                            lambda q, r: None)
    else:
        monkeypatch.setattr(chain.corrector, "try_rewrite",
                            lambda q, r: rewrite_result)

    if second_answer is not None:
        def fake_execute(q, history):
            docs = _inject_gate_ok(_make_docs(), passed=second_gate_passed)
            return {"context": docs, "answer": second_answer}
        monkeypatch.setattr(chain, "_execute", fake_execute)

    monkeypatch.setattr(chain, "_evaluate", lambda answer, ctx: answer)
    return chain


class TestSelfCorrection:
    def test_success_after_rewrite(self, monkeypatch):
        chain = _make_sc_chain(
            monkeypatch,
            second_answer=_qa_answer("改写后检索到的正确答案。"),
        )
        trace, t0 = _start_trace("sc-success")
        meta = {"can_answer": False, "reason": "no_evidence", "confidence": 0.1}

        msg = chain._handle_llm_reject(meta, trace, "原问题", "t1", t0)
        assert "改写后检索到的正确答案" in msg
        # 只重试 1 次（受 SELF_CORRECTION_MAX_RETRIES 约束）
        assert chain.corrector.retry_count == 1

    def test_final_failure_rejects_without_hallucination(self, monkeypatch):
        """第一次拒答 → 重试仍拒答 → 明确拒答，不产生幻觉，不无限循环。"""
        chain = _make_sc_chain(
            monkeypatch,
            second_answer=_qa_answer("资料未提及。", can_answer=False),
        )
        trace, t0 = _start_trace("sc-final-fail")
        meta = {"can_answer": False, "reason": "no_evidence", "confidence": 0.1}

        msg = chain._handle_llm_reject(meta, trace, "原问题", "t1", t0)
        # 明确拒答，不产生幻觉答案（拒答消息不含第二次生成的正文）
        assert "暂无" in msg or "未提及" in msg
        assert "资料未提及" not in msg  # 二次生成的正文未被输出
        # 重试恰好 1 次后停止（不会无限循环）
        assert chain.corrector.retry_count == 1

    def test_rewrite_failure_rejects(self, monkeypatch):
        """改写失败（try_rewrite=None）→ 直接拒答。"""
        chain = _make_sc_chain(monkeypatch, rewrite_result=None)
        trace, t0 = _start_trace("sc-rewrite-fail")
        meta = {"can_answer": False, "reason": "no_evidence", "confidence": 0.1}

        msg = chain._handle_llm_reject(meta, trace, "原问题", "t1", t0)
        assert "暂无" in msg or "未提及" in msg

    def test_max_retries_zero_disables_retry(self, monkeypatch):
        """max_retries=0 → can_retry()=False → 直接拒答，不触发 rewrite。"""
        from backend.rag.evidence_gate.self_correction import SelfCorrectionStrategy
        chain = _stub_chain()
        chain.corrector = SelfCorrectionStrategy(max_retries=0)
        called = {"rewrite": False}
        monkeypatch.setattr(chain.corrector, "try_rewrite",
                            lambda q, r: called.__setitem__("rewrite", True) or "x")
        trace, t0 = _start_trace("sc-max0")
        meta = {"can_answer": False, "reason": "no_evidence", "confidence": 0.1}

        msg = chain._handle_llm_reject(meta, trace, "原问题", "t1", t0)
        assert "暂无" in msg or "未提及" in msg
        assert called["rewrite"] is False  # 未尝试改写


# =====================================================
# 5. Trace：标准 span 全部产生
# =====================================================

class TestTraceSpans:
    def test_standard_spans_emitted(self, monkeypatch):
        from backend.rag.guardrails.scorer import FaithfulnessResult
        from backend.observability.tracer import SpanName

        chain = _stub_chain()
        docs = _inject_gate_ok(_make_docs())
        chain.chain = SimpleNamespace(invoke=lambda inp: {
            "input": inp, "context": docs,
            "answer": _qa_answer("退货窗口为 30 天。"),
        })

        # Faithfulness：替换 LLM 评估为确定性结果（避免真实 NLI 调用）
        def fake_check(answer, context_docs):
            return FaithfulnessResult(
                enabled=True, score=1.0, total_claims=1,
                supported_claims=1, unsupported_claims=0,
                cleaned_answer="",
            )
        monkeypatch.setattr("backend.rag.guardrails.check_faithfulness",
                            fake_check)

        trace, t0 = _start_trace("trace-spans")
        monkeypatch.setattr(chain, "_start", lambda q, sid: (trace, t0))

        chain.ask("退货窗口是多久？", session_id="t1")

        names = {sp.name for sp in trace.spans}
        expected = [
            SpanName.RETRIEVAL, SpanName.MULTI_QUERY, SpanName.META_PARSE,
            SpanName.CITATION, SpanName.CLAIM_VERIFY, SpanName.EVALUATE,
        ]
        for name in expected:
            assert name in names, f"缺少标准 trace span: {name}"
        assert len(names & set(expected)) == len(expected)  # 无中英文双名


# =====================================================
# 6. Gate 1 只执行一次
# =====================================================

class TestGateSingleExecution:
    def _count_gate1(self, monkeypatch):
        """包装 evidence_gate_retrieval 计数并返回 (counting, calls)。"""
        from backend.rag import evidence_gate as eg
        orig = eg.evidence_gate_retrieval
        calls = []

        def counting(*a, **kw):
            calls.append(1)
            return orig(*a, **kw)
        monkeypatch.setattr(eg, "evidence_gate_retrieval", counting)
        return calls

    def test_injected_decision_not_reexecuted(self, monkeypatch):
        """非空召回 + hybrid 已注入 decision → chain 层不重复执行 Gate 1。"""
        monkeypatch.setattr("backend.config.EVIDENCE_GATE_ENABLED", True)
        calls = self._count_gate1(monkeypatch)
        chain = _stub_chain()
        docs = _inject_gate_ok(_make_docs())

        decision = chain._run_evidence_gates("问题", docs)
        assert decision.passed is True
        assert len(calls) == 0  # 复用注入值，Gate 1 未重复计算

    def test_empty_recall_runs_gate_once(self, monkeypatch):
        """空召回 → chain 层补跑 Gate 1 恰好一次（NO_EVIDENCE 拒答）。"""
        monkeypatch.setattr("backend.config.EVIDENCE_GATE_ENABLED", True)
        calls = self._count_gate1(monkeypatch)
        chain = _stub_chain()

        decision = chain._run_evidence_gates("不存在的问题", [])
        assert decision.passed is False
        assert len(calls) == 1  # 恰好一次

    def test_gate_disabled_returns_passthrough_without_computation(self, monkeypatch):
        """总开关关闭 → 直接 passthrough，不执行 Gate 1。"""
        monkeypatch.setattr("backend.config.EVIDENCE_GATE_ENABLED", False)
        calls = self._count_gate1(monkeypatch)
        chain = _stub_chain()

        decision = chain._run_evidence_gates("问题", _make_docs())
        assert decision.passed is True
        assert len(calls) == 0


# =====================================================
# 7. Evaluation Gate：Faithfulness 低分拒答（P0 修复）
# =====================================================

class TestEvaluationGate:
    """is_groundedness_acceptable 接入主链路：低分拒答、高分放行。"""

    def _run_ask(self, monkeypatch, faithfulness_score):
        """全链路 ask：mock LLM 输出（META can_answer=true）+ 确定性 Faithfulness。"""
        from backend.rag.guardrails.scorer import FaithfulnessResult
        chain = _stub_chain()
        docs = _inject_gate_ok(_make_docs())
        chain.chain = SimpleNamespace(invoke=lambda inp: {
            "input": inp, "context": docs,
            "answer": _qa_answer("退货窗口为 30 天。"),
        })

        def fake_check(answer, context_docs):
            return FaithfulnessResult(
                enabled=True, score=faithfulness_score, total_claims=1,
                supported_claims=1 if faithfulness_score >= 0.5 else 0,
                unsupported_claims=0 if faithfulness_score >= 0.5 else 1,
                cleaned_answer="",
            )
        monkeypatch.setattr("backend.rag.guardrails.check_faithfulness",
                            fake_check)

        trace, t0 = _start_trace("eval-gate")
        monkeypatch.setattr(chain, "_start", lambda q, sid: (trace, t0))
        return chain, trace, chain.ask("退货窗口是多久？", session_id="t1")

    def test_low_faithfulness_rejects(self, monkeypatch):
        """Faithfulness 0.4 < 0.5 → Evaluation Gate 拒答（layer=evaluation）。"""
        chain, trace, msg = self._run_ask(monkeypatch, faithfulness_score=0.4)
        assert "自动拒答" in msg or "未经资料支撑" in msg
        rejection = (trace.metadata.get("rejection") or {})
        assert rejection.get("rejected") is True
        assert rejection.get("layer") == "evaluation"
        assert rejection.get("reason") == "hallucination"

    def test_high_faithfulness_passes(self, monkeypatch):
        """Faithfulness 0.9 ≥ 0.5 → 放行正常回答。"""
        _, _, msg = self._run_ask(monkeypatch, faithfulness_score=0.9)
        assert "退货窗口为 30 天" in msg
        assert "自动拒答" not in msg

    def test_no_faithfulness_result_passes(self, monkeypatch):
        """_last_faithfulness=None（评估被跳过）→ 不触发 Evaluation Gate。"""
        chain = _stub_chain()
        docs = _inject_gate_ok(_make_docs())
        chain.chain = SimpleNamespace(invoke=lambda inp: {
            "input": inp, "context": docs,
            "answer": _qa_answer("退货窗口为 30 天。"),
        })
        monkeypatch.setattr(chain, "_evaluate", lambda answer, ctx: answer)
        trace, t0 = _start_trace("eval-gate-none")
        monkeypatch.setattr(chain, "_start", lambda q, sid: (trace, t0))
        msg = chain.ask("退货窗口是多久？", session_id="t1")
        assert "退货窗口为 30 天" in msg


# =====================================================
# 8. Self-Correction 二次生成不绕过 ClaimVerifier（P0 修复）
# =====================================================

class TestSelfCorrectionClaimVerify:
    def test_second_generation_fabrication_rejects(self, monkeypatch):
        """二次生成的答案经 _verify_claims 拦截 → 拒答，不输出编造内容。"""
        chain = _make_sc_chain(
            monkeypatch,
            second_answer=_qa_answer("二次生成的答案。"),
        )
        # 模拟二次生成被程序化事实校验拦截（数字编造零容忍）
        monkeypatch.setattr(chain, "_verify_claims", lambda answer, ctx: None)

        trace, t0 = _start_trace("sc-claim-block")
        meta = {"can_answer": False, "reason": "no_evidence", "confidence": 0.1}
        msg = chain._handle_llm_reject(meta, trace, "原问题", "t1", t0)
        assert "暂无" in msg or "未提及" in msg
        assert "二次生成的答案" not in msg
        assert chain.corrector.retry_count == 1

    def test_second_generation_passes_claim_verify(self, monkeypatch):
        """二次生成通过 Claim 校验 → self-correction 救活。"""
        chain = _make_sc_chain(
            monkeypatch,
            second_answer=_qa_answer("改写后检索到的正确答案。"),
        )
        trace, t0 = _start_trace("sc-claim-pass")
        meta = {"can_answer": False, "reason": "no_evidence", "confidence": 0.1}
        msg = chain._handle_llm_reject(meta, trace, "原问题", "t1", t0)
        assert "改写后检索到的正确答案" in msg


# =====================================================
# 9. corrector 状态按请求重置（P1 修复）
# =====================================================

class TestCorrectorResetPerRequest:
    def test_ask_resets_retry_count(self, monkeypatch):
        """RAGChain 单例下，ask() 入口重置 corrector，防跨请求污染。"""
        chain = _stub_chain()
        docs = _inject_gate_ok(_make_docs())
        chain.chain = SimpleNamespace(invoke=lambda inp: {
            "input": inp, "context": docs,
            "answer": _qa_answer("退货窗口为 30 天。"),
        })
        monkeypatch.setattr(chain, "_evaluate", lambda answer, ctx: answer)
        # 模拟上一请求留下的脏状态
        chain.corrector.record_attempt(success=False)
        chain.corrector.record_attempt(success=False)
        assert chain.corrector.retry_count == 2

        trace, t0 = _start_trace("sc-reset")
        monkeypatch.setattr(chain, "_start", lambda q, sid: (trace, t0))
        chain.ask("退货窗口是多久？", session_id="t1")
        assert chain.corrector.retry_count == 0  # 入口已重置
