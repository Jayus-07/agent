"""RAG 服务化（阶段 1）单测

覆盖:
  1. RAG_MODE 路由：remote → 代理单例 / local → 本地单例工厂
  2. 代理签名兼容：ask / retrieve_knowledge 与 RAGPipeline 同签名（鸭子类型契约）
  3. 代理 HTTP 行为：MockTransport 模拟 rag-service（meta 回填、错误指引）
  4. 索引管理面拒绝：__getattr__ 抛 RuntimeError 且含操作指引
  5. deps 适配：remote 下 get_rag_status 透传、_kick_pipeline_init 不起线程
  6. 状态查询：remote 返回 {"state": "remote"}，local 行为不变
"""
import inspect
import json

import httpx
import pytest


# ==================== 1. RAG_MODE 路由 ====================

class TestModeRouting:
    def test_remote_mode_returns_proxy(self, monkeypatch):
        import backend.rag.pipeline as pipeline_mod
        import backend.rag.client as client_mod

        monkeypatch.setattr("backend.config.rag.RAG_MODE", "remote")
        monkeypatch.setattr(client_mod, "_proxy_singleton", None)

        pipe = pipeline_mod.get_rag_pipeline()
        assert isinstance(pipe, client_mod.RAGServiceProxy)

    def test_local_mode_returns_local_factory(self, monkeypatch):
        import backend.rag.pipeline as pipeline_mod

        monkeypatch.setattr("backend.config.rag.RAG_MODE", "local")
        sentinel = object()
        monkeypatch.setattr(pipeline_mod, "_get_local_pipeline", lambda: sentinel)

        assert pipeline_mod.get_rag_pipeline() is sentinel

    def test_local_state_unchanged(self, monkeypatch):
        """local 分支保留历史行为：ready/initializing/error/not_started。"""
        import backend.rag.pipeline as pipeline_mod

        monkeypatch.setattr("backend.config.rag.RAG_MODE", "local")
        monkeypatch.setattr(pipeline_mod, "_pipeline_singleton", object())
        monkeypatch.setattr(pipeline_mod, "_pipeline_initializing", False)
        monkeypatch.setattr(pipeline_mod, "_pipeline_init_error", None)
        assert pipeline_mod.get_rag_pipeline_state() == {"state": "ready"}


# ==================== 2. 签名兼容（鸭子类型契约） ====================

class TestSignatureCompatibility:
    def test_ask_signature_matches_pipeline(self):
        from backend.rag.client import RAGServiceProxy
        from backend.rag.pipeline import RAGPipeline

        proxy_params = inspect.signature(RAGServiceProxy.ask).parameters
        real_params = inspect.signature(RAGPipeline.ask).parameters
        assert set(proxy_params) == set(real_params), (
            f"代理 ask 签名漂移: proxy={set(proxy_params)} real={set(real_params)}"
        )

    def test_retrieve_signature_matches_pipeline(self):
        from backend.rag.client import RAGServiceProxy
        from backend.rag.pipeline import RAGPipeline

        proxy_params = inspect.signature(RAGServiceProxy.retrieve_knowledge).parameters
        real_params = inspect.signature(RAGPipeline.retrieve_knowledge).parameters
        assert set(proxy_params) == set(real_params)


# ==================== 3. 代理 HTTP 行为 ====================

def _make_proxy(handler) -> "RAGServiceProxy":
    from backend.rag.client import RAGServiceProxy
    return RAGServiceProxy(
        base_url="http://test-rag:8090",
        transport=httpx.MockTransport(handler),
    )


class TestProxyHTTP:
    def test_ask_returns_answer_and_meta(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/ask"
            body = request.read()
            assert b"session_id" in body and b"kb_ids" in body
            return httpx.Response(200, json={"answer": "答案", "meta": {"confidence": 0.9}})

        proxy = _make_proxy(handler)
        answer = proxy.ask("问题", session_id="s1", kb_id="cs_faq", kb_ids=["cs_faq"])
        assert answer == "答案"
        assert proxy.last_answer_meta == {"confidence": 0.9}

    def test_ask_propagates_and_links_remote_rag_trace(self):
        from backend.observability.tracer import trace_collector

        trace_collector.clear_for_test()
        parent = trace_collector.start(question="合成问题", workflow_name="agent")
        parent_span = trace_collector.start_span("rag.search", name="RAG Tool")
        child_trace_id = "abcdef123456"

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-Agent-Parent-Trace-Id"] == parent.id
            return httpx.Response(200, json={
                "answer": "合成答案",
                "meta": {"trace_id": child_trace_id},
            })

        proxy = _make_proxy(handler)
        try:
            assert proxy.ask("合成问题", persist_memory=False) == "合成答案"
            assert child_trace_id in parent.children_ids
            assert parent_span.metrics["child_trace_ids"] == [child_trace_id]
        finally:
            trace_collector.clear_for_test()

    def test_ask_reserves_child_trace_before_remote_timeout(self):
        """RAG read timeout 后，父 Trace 仍能按预分配 ID 找到远端子 Trace。"""
        import re

        from backend.observability.tracer import trace_collector

        trace_collector.clear_for_test()
        parent = trace_collector.start(question="合成问题", workflow_name="agent")
        parent_span = trace_collector.start_span("rag.search", name="rag:rag.search")
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["parent_id"] = request.headers["X-Agent-Parent-Trace-Id"]
            captured["child_id"] = request.headers["X-Agent-Child-Trace-Id"]
            raise httpx.ReadTimeout("synthetic timeout", request=request)

        proxy = _make_proxy(handler)
        try:
            with pytest.raises(RuntimeError, match="RAG 服务调用失败"):
                proxy.ask("合成问题", persist_memory=False)

            assert captured["parent_id"] == parent.id
            assert re.fullmatch(r"[0-9a-f]{12}", captured["child_id"])
            assert parent.children_ids == [captured["child_id"]]
            assert parent_span.metrics["child_trace_ids"] == [captured["child_id"]]
        finally:
            trace_collector.clear_for_test()

    def test_ask_passes_subject_context(self):
        """主体属性必须透传到服务端 —— remote 模式检索侧授权的前提。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.read()))
            return httpx.Response(200, json={"answer": "答案", "meta": {}})

        proxy = _make_proxy(handler)
        proxy.ask(
            "问题", session_id="s1", kb_id="cs_faq",
            subject_type="customer", department="after_sales",
        )
        assert captured["subject_type"] == "customer"
        assert captured["department"] == "after_sales"

    def test_ask_can_defer_memory_write(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.read()))
            return httpx.Response(200, json={"answer": "答案", "meta": {}})

        proxy = _make_proxy(handler)
        outcome = proxy.ask_result("问题", session_id="s1", persist_memory=False)
        assert outcome.answer == "答案"
        assert captured["persist_memory"] is False

    def test_rag_server_ask_forwards_memory_policy(self, monkeypatch):
        """HTTP 边界把是否落 Memory 的请求级策略传到 RAGPipeline。"""
        from backend.services import rag_server

        captured = {}

        class _Pipeline:
            last_answer_meta = {"can_answer": True}

            def ask(self, **kwargs):
                captured.update(kwargs)
                return "经证据核验的答案"

        monkeypatch.setattr(rag_server, "_get_pipeline", lambda: _Pipeline())
        req = rag_server.AskRequest(question="问题", persist_memory=False)

        response = rag_server.ask(req)

        assert response["answer"] == "经证据核验的答案"
        assert captured["persist_memory"] is False

    def test_rag_server_binds_valid_parent_trace_for_request(self, monkeypatch):
        from backend.services import rag_server
        from backend.observability.tracer import (
            get_external_child_trace_id,
            get_external_parent_trace_id,
        )

        captured = {}
        child_trace_id = "fedcba654321"

        class _Pipeline:
            last_answer_meta = {"trace_id": child_trace_id}

            def ask(self, **_kwargs):
                captured["parent_trace_id"] = get_external_parent_trace_id()
                captured["child_trace_id"] = get_external_child_trace_id()
                return "经证据核验的答案"

        monkeypatch.setattr(rag_server, "_get_pipeline", lambda: _Pipeline())
        response = rag_server.ask(
            rag_server.AskRequest(question="问题"),
            parent_trace_id="012345abcdef",
            child_trace_id="abcdef123456",
        )

        assert captured["parent_trace_id"] == "012345abcdef"
        assert captured["child_trace_id"] == "abcdef123456"
        assert response["meta"]["trace_id"] == child_trace_id
        assert get_external_parent_trace_id() is None
        assert get_external_child_trace_id() is None

    def test_rag_chain_uses_preallocated_remote_trace_id_and_parent(self, monkeypatch):
        import backend.observability.tracer as tracer_module
        from backend.rag.chain import RAGChain
        from backend.observability.tracer import (
            TraceCollector,
            bind_external_child_trace_id,
            bind_external_parent_trace_id,
        )

        collector = TraceCollector()
        monkeypatch.setattr(tracer_module, "trace_collector", collector)
        # 模拟服务线程里残留的另一个活动 trace：显式远端 ID 必须隔离，
        # 不能错误嵌入到这条无关 trace。
        unrelated = collector.start("other", "other-session", workflow_name="rag_agent")
        external_parent_id = "012345abcdef"
        external_child_id = "fedcba654321"
        parent_token = bind_external_parent_trace_id(external_parent_id)
        child_token = bind_external_child_trace_id(external_child_id)
        chain = RAGChain.__new__(RAGChain)
        monkeypatch.setattr(chain, "_record_runtime_prompt_usage", lambda: None)
        try:
            trace, _started_at = chain._start("合成问题", "synthetic-session")

            assert trace.id == external_child_id
            assert trace.parent_id == external_parent_id
            assert unrelated.children_ids == []
            assert trace.spans[0].span_id == "root"
        finally:
            tracer_module.reset_external_child_trace_id(child_token)
            tracer_module.reset_external_parent_trace_id(parent_token)
            collector.clear_for_test()

    def test_ask_subject_defaults_keep_wire_compat(self):
        """旧调用方不传主体属性时 wire 上带空串（服务端默认同值，行为不变）。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.read()))
            return httpx.Response(200, json={"answer": "答案", "meta": {}})

        proxy = _make_proxy(handler)
        proxy.ask("问题")
        assert captured["subject_type"] == ""
        assert captured["department"] == ""

    def test_retrieve_returns_result(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/retrieve"
            return httpx.Response(200, json={"result": "chunk1\n\n---\n\nchunk2"})

        proxy = _make_proxy(handler)
        assert "chunk1" in proxy.retrieve_knowledge("问题", kb_id="default", top_k=2)

    def test_service_error_raises_with_guidance(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"detail": "SERVICE_NOT_READY"})

        proxy = _make_proxy(handler)
        with pytest.raises(RuntimeError, match="rag-service"):
            proxy.ask("问题")


# ==================== 4. 索引管理面拒绝 ====================

class TestManagementSurface:
    def test_vectordb_access_rejected(self):
        proxy = _make_proxy(lambda req: httpx.Response(200, json={}))
        with pytest.raises(RuntimeError, match="RAG_MODE=remote"):
            _ = proxy.vectordb


# ==================== 5. deps 适配 ====================

class TestDepsRemoteAdaptation:
    def test_kick_pipeline_init_noop_in_remote(self, monkeypatch):
        """remote 下预热必须是 no-op：不起线程、不触碰本地单例。"""
        from backend.app.api import deps
        import threading

        started = []
        monkeypatch.setattr("backend.config.rag.RAG_MODE", "remote")
        monkeypatch.setattr(
            threading.Thread, "start",
            lambda self, *a, **kw: started.append(self),
        )
        deps._kick_pipeline_init()
        assert started == []

    def test_get_rag_status_remote_passthrough(self, monkeypatch):
        from backend.app.api import deps

        monkeypatch.setattr(
            "backend.rag.pipeline.get_rag_pipeline_state",
            lambda: {"state": "remote", "endpoint": "http://rag-service:8090"},
        )
        status = deps.get_rag_status()
        assert status["ready"] is True
        assert status["status"] == "remote"
        assert status["endpoint"] == "http://rag-service:8090"

    def test_state_query_remote(self, monkeypatch):
        import backend.rag.pipeline as pipeline_mod

        monkeypatch.setattr("backend.config.rag.RAG_MODE", "remote")
        state = pipeline_mod.get_rag_pipeline_state()
        assert state["state"] == "remote"
        assert "endpoint" in state


# ==================== 6. rag-server 强制本地 ====================

class TestRagServerGuard:
    def test_server_uses_local_factory_not_router(self):
        """服务端必须直连 _get_local_pipeline，防止误配 remote 成环。"""
        import backend.services.rag_server as server
        source = inspect.getsource(server)
        assert "_get_local_pipeline" in source
        assert server._get_pipeline.__doc__ is not None
