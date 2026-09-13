"""RAG 防线 fail-visible 回归测试（改造方案 R3）。

修复前事实：Gate 1/2 评估异常透传放行后 span 仍标 success，
与"评估成功通过"在 trace 上不可区分；ClaimVerifier/Faithfulness 异常
跳过只有 warning 日志无指标。

锁定修复后行为（fail-open 语义不变，只补可见性）：
1. Gate1 评估异常 → 放行（passed=True）但 span status=skipped
2. rag_gate_degraded_total{layer="gate1"} 递增
3. ClaimVerifier 异常 → span skipped + counter 递增
4. Faithfulness 异常路径不因 faith_span 未绑定而二次炸（NameError 修复）
"""
import types

import pytest

import backend.orchestration.graph  # noqa: F401  # 循环导入规避

from backend.rag import chain as chain_mod


class _FakeSpan:
    span_id = "fake-span"


class _FakeCollector:
    def __init__(self):
        self.ended = []

    def start_span(self, *a, **kw):
        return _FakeSpan()

    def end_span(self, span, metrics=None, status=None):
        self.ended.append({"span": span, "metrics": metrics, "status": status})


def _counter_value(layer: str) -> float:
    from backend.observability.metrics import rag_gate_degraded_total
    return rag_gate_degraded_total.labels(layer=layer)._value.get()


def _fake_chain_self() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        gate=types.SimpleNamespace(
            intent="knowledge",
            query_analysis=None,
            risk_level="low",
            set_risk_level=lambda v: None,
        ),
    )


class TestGate1DegradedVisible:
    def test_gate1_exception_passthrough_with_skipped_span(self, monkeypatch):
        import backend.observability.tracer as tracer_mod
        import backend.rag.evidence_gate as eg_mod

        def _boom(*a, **kw):
            raise RuntimeError("gate1 down")

        monkeypatch.setattr(eg_mod, "evidence_gate_retrieval", _boom)
        fake_collector = _FakeCollector()
        monkeypatch.setattr(tracer_mod, "trace_collector", fake_collector)

        # Gate2 用 passthrough，聚焦 Gate1 行为
        monkeypatch.setattr(eg_mod, "evidence_gate_rerank",
                            lambda *a, **kw: eg_mod.gate_retrieval_passthrough())
        # 实体校验关掉，避免 fake doc 结构缺失的干扰
        monkeypatch.setattr("backend.config.GATE_ENTITY_CHECK_ENABLED", False)

        before = _counter_value("gate1")
        decision = chain_mod.RAGChain._run_evidence_gates(
            _fake_chain_self(), "测试问题", [])

        assert decision.passed is True, "fail-open 语义必须保留：异常时透传放行"
        assert _counter_value("gate1") == before + 1, "gate1 软降级必须计数"
        statuses = [e["status"] for e in fake_collector.ended]
        assert "skipped" in statuses, "异常放行的 gate span 必须标 skipped"


class TestClaimVerifierDegradedVisible:
    def test_claim_verify_exception_counter(self, monkeypatch):
        import backend.observability.tracer as tracer_mod
        fake_collector = _FakeCollector()
        monkeypatch.setattr(tracer_mod, "trace_collector", fake_collector)

        chain = chain_mod.RAGChain.__new__(chain_mod.RAGChain)
        before = _counter_value("claim_verify")
        # verify_answer 在函数内延迟导入，monkeypatch 源模块
        import backend.rag.evidence_gate.claim_verifier as cv_mod
        def _boom(*a, **kw):
            raise RuntimeError("verifier down")
        monkeypatch.setattr(cv_mod, "verify_answer", _boom)

        result = chain._verify_claims("答案", [])
        assert result == "答案", "校验器异常放行原答案（fail-open）"
        assert _counter_value("claim_verify") == before + 1


class TestFaithfulnessNameErrorFix:
    def test_early_exception_no_nameerror(self, monkeypatch):
        """import 失败（faith_span 未赋值前抛异常）不得二次抛 NameError。"""
        import backend.observability.tracer as tracer_mod
        fake_collector = _FakeCollector()
        monkeypatch.setattr(tracer_mod, "trace_collector", fake_collector)

        chain = chain_mod.RAGChain.__new__(chain_mod.RAGChain)
        # check_faithfulness 延迟导入，patch 为抛错模拟 import 后失败；
        # 更早的失败（import 本身失败）由 faith_span=None 预绑定覆盖
        import backend.rag.guardrails as gr_mod
        def _boom(*a, **kw):
            raise RuntimeError("faithfulness down")
        monkeypatch.setattr(gr_mod, "check_faithfulness", _boom)

        result = chain._evaluate("答案正文", [])
        assert result == "答案正文", "异常时放行原答案"
