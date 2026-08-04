"""P1 专项测试：Markdown+META Prompt + LLM 自报拒答 + Self-Correction

覆盖：
  1. QA_PROMPT 包含 META 注释指示词
  2. _verify 解析 META → _last_meta
  3. _finalize_llm_rejection 拒绝路径 + RejectInfo 写回
  4. _try_self_correct fallback 行为
"""
import pytest
from langchain_core.documents import Document


# =====================================================
# 1. QA_PROMPT 包含 META 注释指示（避免 P1 改造退化）
# =====================================================

class TestQAPromptShape:
    """P1.1 验证：QA_PROMPT 已改为 Markdown+META 格式"""

    def test_prompt_mentions_mETA_html(self):
        from backend.rag.chain import QA_PROMPT
        text = QA_PROMPT.messages[0].prompt.template  # system 段
        assert "<!--META" in text, "QA_PROMPT 应包含 <!--META--> 注释说明"
        assert "can_answer" in text

    def test_prompt_does_not_require_json(self):
        """§C2/C3 修复：不应再强制输出纯 JSON（与 Citation 体系冲突）"""
        from backend.rag.chain import QA_PROMPT
        text = QA_PROMPT.messages[0].prompt.template
        # 旧的 JSON 格式（要求 can_answer 是顶层 JSON）已被替换
        assert "JSON:" not in text or "JSON 注释" in text, \
            "QA_PROMPT 应不再要求纯 JSON 输出"

    def test_prompt_lists_valid_reasons(self):
        from backend.rag.chain import QA_PROMPT
        text = QA_PROMPT.messages[0].prompt.template
        # 4 个 reason（与 RejectReason 5 类对齐：no_evidence/low_relevance/insufficient/out_of_scope）
        for reason in ("no_evidence", "low_relevance", "insufficient", "out_of_scope"):
            assert reason in text, f"QA_PROMPT 缺 reason 标签: {reason}"

    def test_prompt_requires_citation(self):
        """保留 Citation 强制（与现有 _verify_support 一致）"""
        from backend.rag.chain import QA_PROMPT
        text = QA_PROMPT.messages[0].prompt.template
        assert "[" in text and "]" in text, "QA_PROMPT 保留 [1]/[2] 引用指示"


# =====================================================
# 2. _verify META 解析 → _last_meta
# =====================================================

def _stub_chain():
    """构造 RAGChain（mock，不起真实 chain）以测 _verify 等方法。"""
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
    # PR-1.4: 策略对象（绕过 __init__，需手动初始化）
    chain.gate = EvidenceGateController()
    chain.corrector = SelfCorrectionStrategy()
    chain.formatter = CitationFormatter()
    return chain


class TestVerifyMetaParse:
    def _start_trace(self):
        """起一个 trace 让 _verify 内部 start_span 不报错。"""
        from backend.rag.tracer import trace_collector
        trace_collector.start("test-meta", session_id="t1")
        # 起 root span 满足后续 start_span(parent_id=None) 约束
        try:
            trace_collector.start_span("root", parent_id=None, name="test",
                                       type="agent")
        except RuntimeError:
            pass  # root 已存在就跳过

    def test_meta_can_answer_true(self):
        chain = _stub_chain()
        self._start_trace()
        raw = "这是答案 [1]。<!--META{\"can_answer\":true,\"citations\":[1],\"confidence\":0.85}-->"
        result = {"answer": raw, "context": [
            Document(page_content="x", metadata={"chunk_id": "c1", "rerank_score": 0.8,
                                                  "doc_type": "general",
                                                  "source_file": "x.md", "index": 1}),
        ]}
        answer = chain._verify(result, "问题", session_id="t1")
        # chain._last_meta 应当被填充
        assert chain._last_meta.get("can_answer") is True
        assert chain._last_meta.get("citations") == [1]
        assert chain._last_meta.get("confidence") == 0.85
        # answer 不应包含 META 注释（剥离）
        assert "<!--META" not in answer
        assert "这是答案" in answer

    def test_meta_can_answer_false(self):
        chain = _stub_chain()
        self._start_trace()
        raw = "资料未提及。<!--META{\"can_answer\":false,\"reason\":\"no_evidence\",\"confidence\":0.1}-->"
        result = {"answer": raw, "context": []}
        answer = chain._verify(result, "未覆盖问题", session_id="t1")
        assert chain._last_meta.get("can_answer") is False
        assert chain._last_meta.get("reason") == "no_evidence"

    def test_no_meta_annotation(self):
        """LLM 没输出 META（降级：按原文处理，meta 为空 dict）"""
        chain = _stub_chain()
        self._start_trace()
        result = {"answer": "纯文本答案", "context": []}
        chain._verify(result, "x", session_id="t1")
        assert chain._last_meta == {}

    def test_meta_invalid_json_does_not_crash(self):
        chain = _stub_chain()
        self._start_trace()
        raw = "纯文本。<!--META{not valid json}-->"
        result = {"answer": raw, "context": []}
        chain._verify(result, "x", session_id="t1")
        # 解析失败应 fallback 为空 meta
        assert chain._last_meta == {}


# =====================================================
# 3. _finalize_llm_rejection
# =====================================================

class TestFinalizeLlmRejection:
    def test_translates_meta_rejection(self):
        """META can_answer=False → RejectInfo 走 generation 层 + trace.metadata.rejection 写入"""
        chain = _stub_chain()
        chain._last_query = "测试问题"
        # 构造 mock trace
        chain2 = chain
        # 用真实的 trace_collector 起一个 trace
        from backend.rag.tracer import trace_collector
        trace = trace_collector.start("test-finalize", session_id="t1")
        trace_collector.start_span("root", parent_id=None, name="test", type="agent")

        # P1.3 字段
        chain2.corrector.reset()

        import time as _time
        meta = {"can_answer": False, "reason": "no_evidence", "confidence": 0.05}

        try:
            # 关闭 self-correction（关闭它避免 stub LLM 被调第二次）
            import backend.config as _cfg_mod
            orig = _cfg_mod.SELF_CORRECTION_ENABLED
            _cfg_mod.SELF_CORRECTION_ENABLED = False
            try:
                # 新 API: _build_decision_from_meta + _reject
                decision = chain2.gate.build_decision_from_meta(meta)
                msg = chain2._reject(decision, "generation", trace, _time.time())
                info = trace.metadata.get("rejection") or {}
            finally:
                _cfg_mod.SELF_CORRECTION_ENABLED = orig

            assert msg is not None
            assert "暂无" in msg or "未提及" in msg
            assert info is not None
            assert info.get("rejected") is True
            assert info.get("reason") == "no_evidence"
            assert info.get("layer") == "generation"
            # trace.metadata.rejection 写入
            assert trace.metadata.get("rejection", {}).get("rejected") is True
        finally:
            # 不强行 finish（避免污染 trace collector 全局）
            pass

    def test_translates_low_relevance_reason(self):
        chain = _stub_chain()
        chain._last_query = "weak query"
        from backend.rag.tracer import trace_collector
        trace = trace_collector.start("test-lr", session_id="t1")
        trace_collector.start_span("root", parent_id=None, name="test", type="agent")
        chain.corrector.reset()

        import time as _time
        meta = {"can_answer": False, "reason": "low_relevance", "confidence": 0.2}
        import backend.config as _cfg_mod
        orig = _cfg_mod.SELF_CORRECTION_ENABLED
        _cfg_mod.SELF_CORRECTION_ENABLED = False
        try:
            # 新 API
            decision = chain.gate.build_decision_from_meta(meta)
            chain._reject(decision, "generation", trace, _time.time())
            info = trace.metadata.get("rejection") or {}
            assert info.get("reason") == "low_relevance"
        finally:
            _cfg_mod.SELF_CORRECTION_ENABLED = orig

    def test_unknown_reason_falls_back_to_no_evidence(self):
        """LLM 自报不认识的 reason → 服务端翻译为 NO_EVIDENCE"""
        chain = _stub_chain()
        chain._last_query = "?"
        from backend.rag.tracer import trace_collector
        trace = trace_collector.start("test-ur", session_id="t1")
        trace_collector.start_span("root", parent_id=None, name="test", type="agent")
        chain.corrector.reset()

        import time as _time
        meta = {"can_answer": False, "reason": "i_dont_know_dude", "confidence": 0.0}
        import backend.config as _cfg_mod
        orig = _cfg_mod.SELF_CORRECTION_ENABLED
        _cfg_mod.SELF_CORRECTION_ENABLED = False
        try:
            # 新 API
            decision = chain.gate.build_decision_from_meta(meta)
            chain._reject(decision, "generation", trace, _time.time())
            info = trace.metadata.get("rejection") or {}
            assert info.get("reason") == "no_evidence"  # fallback
        finally:
            _cfg_mod.SELF_CORRECTION_ENABLED = orig
