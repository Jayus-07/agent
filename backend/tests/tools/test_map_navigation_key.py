"""审计 #44 工具通道收口（评审 P1，2026-11-10）：
map_navigation_tool 不得把含后端 Key 的导航 URL 返回给模型/用户。

HTTP 路由（/map/navigate，test_map_navigate_key.py）已对 key_kind=backend
409；本用例锁死同一 LLM 工具通道——此前仅靠 docstring 提示「不要外发」，
无服务端拦截，模型会把 URL 转述给用户。
"""
from __future__ import annotations

import json

from backend.tools.map import _base  # noqa: F401  # 确认模块导入面
from backend.tools.map import route as map_route


def _invoke(monkeypatch, *, key_kind: str) -> str:
    monkeypatch.setattr(map_route.MAP, "is_configured", lambda: True)
    monkeypatch.setattr(
        map_route.api, "navigation_uri",
        lambda **_kw: {"url": "https://apis.map/nav?key=SECRET",
                       "key_kind": key_kind, "mode": "drive", "policy": ""},
    )
    return map_route.map_navigation_tool.invoke({"to_location": "30.1,120.1"})


def test_backend_key_url_is_rejected(monkeypatch):
    output = _invoke(monkeypatch, key_kind="backend")
    assert "SECRET" not in output, "含后端 Key 的 URL 不得进入工具返回"
    payload = json.loads(output)
    assert payload.get("status") == "failed", output
    assert "TENCENT_LBS_FRONTEND_KEY" in output


def test_frontend_key_url_is_returned(monkeypatch):
    output = _invoke(monkeypatch, key_kind="frontend")
    assert "SECRET" in output  # 桩 URL 原样返回（frontend 形态不拦截）
    payload = json.loads(output)
    assert payload.get("status") == "success", output
    assert payload["data"]["key_kind"] == "frontend"
