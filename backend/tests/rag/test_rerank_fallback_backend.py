"""FallbackRerankBackend 主备切换契约测试（2026-11-21 rerank 本地化收尾）。

契约（failure-degradation.md F-04/F-09/F-12）：
- 主成功 → 备零调用，主备全挂标记不写入；
- 主失败（超时/连接/HTTP 5xx/熔断 OPEN）→ 自动调备；备成功时清除主侧
  ``rerank_unavailable`` 条目（重排实际成功，检索链不得误拒答）；
- 主备全挂（或无备）→ 统一写入主备 provider key，检索链按既有消费
  如实降级拒答（rag_retrieval_degraded），不再静默透传原序；
- 迟到结果（RerankLateResultDiscarded）不计熔断、不触发备调用；
- 工厂按 llm_model_role_policy.fallback_model 派生备后端，未配置保持单后端。
"""
import pytest
from langchain_core.documents import Document
from types import SimpleNamespace

from backend.rag import reranker as reranker_mod
from backend.rag.context import get_context
from backend.rag.reranker import (
    FallbackRerankBackend,
    RerankLateResultDiscarded,
)


class _ScriptedBackend:
    """可控重排后端替身：不触 HTTP/budget/trace，只验证 wrapper 契约。"""

    def __init__(self, provider_id, behavior):
        self._provider_id = provider_id
        self._endpoint = f"https://{provider_id}/rerank"
        self._model = "scripted-model"
        self._billing_model = "scripted-model"
        self.behavior = behavior
        self.rank_calls = 0
        self.compress_calls = 0

    def rank(self, query, documents, top_k=8, *, timeout_s=None, deadline_at=None):
        self.rank_calls += 1
        result = self.behavior(query, list(documents), top_k)
        if isinstance(result, Exception):
            raise result
        return result

    def rank_all(self, query, documents, *, deadline_at=None):
        return self.rank(query, documents, top_k=len(documents))

    def rerank_text(self, doc, limit=2000):
        return (doc.page_content or "")[:limit]

    def compress_documents(self, documents, query, **kwargs):
        self.compress_calls += 1
        rows = self.rank(query, [d.page_content for d in documents], 8)
        return [documents[idx] for idx, _score in rows[:kwargs.get("top_k", 8)]]


def _scores(documents, score=0.9):
    return [(idx, score) for idx in range(len(documents))]


def _ok(behavior):
    def _inner(query, documents, top_k):
        return _scores(documents)
    return _inner


@pytest.fixture
def isolated_breakers(monkeypatch):
    """熔断器分区独立化：每个测试独享字典，避免进程级单例状态泄漏。"""
    from backend.infra import circuit_breaker as cb_mod

    store = {}
    monkeypatch.setattr(cb_mod, "_specialized_breakers", store)
    return store


@pytest.fixture
def primary_key():
    return "local-specialized|http://100.117.4.3:8088|bge-reranker-v2-m3"


@pytest.fixture
def fallback_key():
    return "specialized-api|https://dashscope.aliyuncs.com/api/v1|qwen3-rerank"


def _docs(n=4):
    return [
        Document(page_content=f"内容{i}", metadata={"chunk_id": f"c{i}"})
        for i in range(n)
    ]


class TestFallbackRerankBackendContract:
    def test_primary_success_skips_fallback(self, isolated_breakers):
        fallback = _ScriptedBackend("specialized-api", _ok("fb"))
        primary = _ScriptedBackend("local-specialized", _ok("p"))
        wrapper = FallbackRerankBackend(primary, fallback)
        docs = _docs()

        result = wrapper.rank("q", [d.page_content for d in docs], top_k=4)

        assert result == _scores(docs)
        assert primary.rank_calls == 1
        assert fallback.rank_calls == 0
        assert not get_context().rerank_unavailable

    def test_primary_failure_falls_back_and_clears_unavailable(
        self, isolated_breakers, primary_key, fallback_key,
    ):
        """主超时（内部已写主 key）→ 备成功 → 主 key 被清除。"""
        primary = _ScriptedBackend(
            "local-specialized",
            lambda q, d, k: requests_timeout(),
        )
        fallback = _ScriptedBackend("specialized-api", _ok("fb"))
        wrapper = FallbackRerankBackend(
            primary, fallback,
            primary_provider_key=primary_key,
            fallback_provider_key=fallback_key,
        )
        state = get_context()
        # 模拟主后端（DashScopeReranker.rank）失败时写入的请求级标记
        state.rerank_unavailable.add(primary_key)

        result = wrapper.rank("q", [d.page_content for d in _docs()], top_k=4)

        assert result == _scores(_docs())
        assert fallback.rank_calls == 1
        assert primary_key not in state.rerank_unavailable

    def test_both_fail_marks_unavailable_and_raises(
        self, isolated_breakers, primary_key, fallback_key,
    ):
        primary = _ScriptedBackend(
            "local-specialized",
            lambda q, d, k: requests_timeout(),
        )
        fallback = _ScriptedBackend(
            "specialized-api",
            lambda q, d, k: RuntimeError("Rerank provider returned HTTP 500"),
        )
        wrapper = FallbackRerankBackend(
            primary, fallback,
            primary_provider_key=primary_key,
            fallback_provider_key=fallback_key,
        )

        with pytest.raises(RuntimeError):
            wrapper.rank("q", [d.page_content for d in _docs()], top_k=4)

        state = get_context()
        assert primary_key in state.rerank_unavailable
        assert fallback_key in state.rerank_unavailable

    def test_http_500_without_fallback_marks_unavailable(
        self, isolated_breakers, primary_key,
    ):
        """无备 + HTTP 5xx（非 Timeout/ConnError）也要统一写不可用标记。"""
        primary = _ScriptedBackend(
            "local-specialized",
            lambda q, d, k: RuntimeError("Rerank provider returned HTTP 500"),
        )
        wrapper = FallbackRerankBackend(primary, None, primary_provider_key=primary_key)

        with pytest.raises(RuntimeError):
            wrapper.rank("q", [d.page_content for d in _docs()], top_k=4)

        assert primary_key in get_context().rerank_unavailable

    def test_open_breaker_skips_primary(self, isolated_breakers, primary_key):
        """主熔断 OPEN → 主零调用，直接走备。"""
        from backend.infra.circuit_breaker import get_specialized_circuit_breaker

        primary = _ScriptedBackend("local-specialized", _ok("p"))
        fallback = _ScriptedBackend("specialized-api", _ok("fb"))
        breaker = get_specialized_circuit_breaker(
            "rerank", "local-specialized", "scripted-model",
        )
        for _ in range(3):
            breaker._on_failure(RuntimeError("预热失败计数"))
        assert breaker.state.value == "open"

        wrapper = FallbackRerankBackend(primary, fallback)
        result = wrapper.rank("q", [d.page_content for d in _docs()], top_k=4)

        assert result == _scores(_docs())
        assert primary.rank_calls == 0
        assert fallback.rank_calls == 1

    def test_late_result_not_counted_and_no_fallback(
        self, isolated_breakers, primary_key,
    ):
        """迟到结果：不计熔断失败、不触发备调用、原样上抛。"""
        def late(q, d, k):
            raise RerankLateResultDiscarded("late")

        primary = _ScriptedBackend("local-specialized", late)
        fallback = _ScriptedBackend("specialized-api", _ok("fb"))
        wrapper = FallbackRerankBackend(primary, fallback)

        with pytest.raises(RerankLateResultDiscarded):
            wrapper.rank("q", [d.page_content for d in _docs()], top_k=4)

        assert fallback.rank_calls == 0
        breaker = wrapper._provider_breaker(primary)
        assert breaker._stats.failures == 0
        assert not get_context().rerank_unavailable

    def test_compress_documents_primary_failure_falls_back(
        self, isolated_breakers,
    ):
        primary = _ScriptedBackend(
            "local-specialized",
            lambda q, d, k: requests_timeout(),
        )
        fallback = _ScriptedBackend("specialized-api", _ok("fb"))
        wrapper = FallbackRerankBackend(primary, fallback)
        docs = _docs()

        result = wrapper.compress_documents(docs, "q", top_k=4)

        assert primary.compress_calls == 1
        assert fallback.compress_calls == 1
        assert len(result) == 4

    def test_rerank_text_delegates_to_primary(self, isolated_breakers):
        primary = _ScriptedBackend("local-specialized", _ok("p"))
        fallback = _ScriptedBackend("specialized-api", _ok("fb"))
        wrapper = FallbackRerankBackend(primary, fallback)

        assert wrapper.rerank_text(Document(page_content="正文文本")) == "正文文本"


def requests_timeout():
    import requests

    return requests.exceptions.Timeout("模拟主重排超时")


class TestSpecializedBreaker:
    def test_breaker_partitioned_by_role_provider_model(self, isolated_breakers):
        from backend.infra.circuit_breaker import get_specialized_circuit_breaker

        a = get_specialized_circuit_breaker("rerank", "p1", "m1")
        b = get_specialized_circuit_breaker("rerank", "p1", "m1")
        c = get_specialized_circuit_breaker("rerank", "p1", "m2")
        d = get_specialized_circuit_breaker("embedding", "p1", "m1")
        e = get_specialized_circuit_breaker("rerank", "p2", "m1")

        assert a is b
        assert len({id(a), id(c), id(d), id(e)}) == 4

    def test_breaker_ignore_exception_not_counted(self, monkeypatch):
        """ignore 异常透传且不计失败；普通异常计入并按阈值开路。"""
        from backend.infra.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker("test-ignore", fail_threshold=2, timeout=30.0)

        def late_fn():
            raise RerankLateResultDiscarded("late")

        for _ in range(5):
            with pytest.raises(RerankLateResultDiscarded):
                breaker.call(late_fn, ignore=(RerankLateResultDiscarded,))
        assert breaker._stats.failures == 0
        assert breaker.state.value == "closed"

        def failing_fn():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            breaker.call(failing_fn)
        with pytest.raises(RuntimeError):
            breaker.call(failing_fn)
        assert breaker.state.value == "open"


class TestFactoryFallbackDerivation:
    def _bind_primary(self, monkeypatch):
        monkeypatch.setattr(
            reranker_mod.specialized_mod,
            "resolve_binding",
            lambda _role: SimpleNamespace(
                provider_id="local-specialized",
                adapter="dashscope_rerank",
                model_name="bge-reranker-v2-m3",
                upstream_name="",
                base_url="http://100.117.4.3:8088",
                options={},
            ),
        )
        monkeypatch.setattr(
            reranker_mod.credentials_mod,
            "resolve_credentials",
            lambda provider_id, model_name=None: SimpleNamespace(api_key="sk-test"),
        )

    def test_factory_single_backend_without_fallback_policy(self, monkeypatch):
        """未配置 fallback_model → 单主后端（现状行为）。"""
        from backend.config import model_roles

        self._bind_primary(monkeypatch)
        monkeypatch.setattr(
            model_roles, "resolve_runtime_policy",
            lambda role: SimpleNamespace(fallback_model=""),
        )

        backend = reranker_mod.get_reranker_backend()
        assert isinstance(backend, reranker_mod.DashScopeReranker)

    def test_factory_builds_fallback_from_policy(self, monkeypatch):
        """fallback_model 已登记 → FallbackRerankBackend（主备 key 一致）。"""
        from backend.config import model_roles

        self._bind_primary(monkeypatch)
        monkeypatch.setattr(
            model_roles, "resolve_runtime_policy",
            lambda role: SimpleNamespace(fallback_model="qwen3-rerank"),
        )
        monkeypatch.setattr(
            reranker_mod.specialized_mod,
            "registered_binding",
            lambda role, name: SimpleNamespace(
                provider_id="specialized-api",
                adapter="dashscope_rerank",
                model_name=name,
                upstream_name="",
                base_url="https://dashscope.aliyuncs.com/api/v1",
                options={},
                enabled=True,
            ),
        )

        backend = reranker_mod.get_reranker_backend()

        assert isinstance(backend, FallbackRerankBackend)
        assert backend.primary._provider_id == "local-specialized"
        assert backend.fallback._provider_id == "specialized-api"
        assert backend.fallback._model == "qwen3-rerank"
        assert backend._primary_provider_key == (
            "local-specialized|"
            "http://100.117.4.3:8088/services/rerank/text-rerank/text-rerank|"
            "bge-reranker-v2-m3"
        )
        assert backend._fallback_provider_key == (
            "specialized-api|"
            "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank|"
            "qwen3-rerank"
        )

    def test_factory_keeps_single_backend_when_fallback_unregistered(
        self, monkeypatch,
    ):
        """备模型派生失败（未登记等）→ 单主后端，不阻断主链路。"""
        from backend.config import model_roles

        self._bind_primary(monkeypatch)
        monkeypatch.setattr(
            model_roles, "resolve_runtime_policy",
            lambda role: SimpleNamespace(fallback_model="ghost-model"),
        )

        def _raise(role, name):
            raise ValueError("模型未登记、用途不匹配或供应商配置不完整")

        monkeypatch.setattr(
            reranker_mod.specialized_mod, "registered_binding", _raise,
        )

        backend = reranker_mod.get_reranker_backend()
        assert isinstance(backend, reranker_mod.DashScopeReranker)
