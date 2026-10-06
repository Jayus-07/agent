"""cs_admin cutover 开关测试 — CS_ADMIN_SOURCE=java 时代理到 business-service"""
from __future__ import annotations

import pytest

from backend.app.api.routes import cs_admin


class TestCutoverSwitch:
    def test_default_is_local(self, monkeypatch):
        import backend.config.messaging as cfg
        monkeypatch.setattr(cfg, "CS_ADMIN_SOURCE", "local", raising=False)
        # 重新加载 router 内的延迟读取逻辑：_use_java_source 每次调用时 import
        assert cs_admin._use_java_source() is False

    def test_java_source_flag(self, monkeypatch):
        import backend.config.messaging as cfg
        monkeypatch.setattr(cfg, "CS_ADMIN_SOURCE", "java", raising=False)
        assert cs_admin._use_java_source() is True

    @pytest.mark.asyncio
    async def test_list_proxies_to_java(self, monkeypatch):
        import backend.config.messaging as cfg
        monkeypatch.setattr(cfg, "CS_ADMIN_SOURCE", "java", raising=False)

        captured = {}

        async def fake_proxy(path: str) -> dict:
            captured["path"] = path
            return {"items": [], "total": 0, "has_more": False}

        monkeypatch.setattr(cs_admin, "_proxy_to_java", fake_proxy)
        # STOP CS-A P0-1：路由挂 operator 闸 —— 直调需带 Request（api-key 服务通道）
        from starlette.requests import Request as _Req

        _request = _Req({"type": "http", "headers": [(b"x-auth-type", b"api-key")]})
        result = await cs_admin.list_conversations(
            _request, limit=20, cursor=None, status="open",
            handling_mode=None, user_id=None, q=None, tenant_id=None,
        )
        assert result == {"items": [], "total": 0, "has_more": False}
        assert "status=open" in captured["path"]
        assert captured["path"].startswith("/cs/conversations?")

    @pytest.mark.asyncio
    async def test_detail_proxies_to_java(self, monkeypatch):
        import backend.config.messaging as cfg
        monkeypatch.setattr(cfg, "CS_ADMIN_SOURCE", "java", raising=False)

        captured = {}

        async def fake_proxy(path: str) -> dict:
            captured["path"] = path
            return {"conversation_id": "conv-1"}

        monkeypatch.setattr(cs_admin, "_proxy_to_java", fake_proxy)
        from starlette.requests import Request as _Req

        _request = _Req({"type": "http", "headers": [(b"x-auth-type", b"api-key")]})
        result = await cs_admin.get_conversation("conv-1", _request)
        assert result["conversation_id"] == "conv-1"
        assert captured["path"] == "/cs/conversations/conv-1"
