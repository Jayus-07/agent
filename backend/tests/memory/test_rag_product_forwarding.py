"""rag-service 跨进程产品上下文转发回归（ADR-0012 第 1 期，审计 #68）。

remote 模式（出厂 compose RAG_MODE=remote）下记忆读写发生在 rag-service
进程：client /ask 请求体带调用方 product，rag_server 解析后
scoped_product_context 绑定——客服产品经 remote 链路的记忆不再串入 agent
命名空间。
"""
from __future__ import annotations

import json

import pytest


class TestClientForwardsProduct:
    def test_request_body_carries_product(self):
        """client.ask 的 /ask 请求体包含 product 字段（源码级锁定）。"""
        import inspect

        import backend.rag.client as client_mod

        src = inspect.getsource(client_mod)
        assert '"product": _caller_product_id()' in src
        assert '_caller_product_id' in src

    def test_caller_product_id_defaults_to_agent(self, monkeypatch):
        """无请求上下文（rag-service 内部直调）→ agent 兜底。"""
        from backend.rag.client import _caller_product_id

        import backend.core.request_context as rc

        # 模拟无产品上下文（ContextVar 缺省 internal）→ 归一 agent
        monkeypatch.setattr(rc, "get_current_product_id", lambda: "internal")
        assert _caller_product_id() == "agent"

    def test_caller_product_id_passthrough(self, monkeypatch):
        from backend.rag.client import _caller_product_id

        import backend.core.request_context as rc

        monkeypatch.setattr(rc, "get_current_product_id",
                            lambda: "customer_service")
        assert _caller_product_id() == "customer_service"


class TestServerBindsProduct:
    def test_ask_request_has_product_field_default_agent(self):
        """AskRequest.product 缺省 agent（兼容旧 client）。"""
        from backend.services.rag_server import AskRequest

        req = AskRequest(question="测试问题")
        assert req.product == "agent"
        req2 = AskRequest(question="q", product="customer_service")
        assert req2.product == "customer_service"

    def test_ask_endpoint_scopes_product_context(self):
        """/ask 在 scoped_product_context 内执行（源码级锁定）。"""
        import inspect

        import backend.services.rag_server as server_mod

        src = inspect.getsource(server_mod)
        assert "scoped_product_context(req.product)" in src

    def test_scoped_product_context_restores(self):
        """scoped_product_context 退出精确还原（嵌套安全）。"""
        import asyncio

        from backend.core.request_context import (
            get_current_product_id,
            scoped_product_context,
            set_product_context,
        )

        set_product_context("customer_service", "test")
        assert get_current_product_id() == "customer_service"

        async def _inner():
            with scoped_product_context("agent"):
                assert get_current_product_id() == "agent"
            assert get_current_product_id() == "customer_service"

        asyncio.run(_inner())
