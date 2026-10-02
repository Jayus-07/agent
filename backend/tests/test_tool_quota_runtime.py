"""tests/test_tool_quota_runtime.py — Tool 额度治理（声明 + 用量读数）离线单测

覆盖三块（2026-10-02 落地）：
  1. mcp_client.call_tool 的 on_upstream_call 回调语义：只在真实上游调用
     成功后触发一次，缓存命中不触发（上游按真实调用计额）；回调异常软失败。
  2. 知乎月键计数器：成功搜索 HINCRBY tool_quota:zhihu_mcp:{YYYYMM}
     （field=上游工具名）；上游业务失败/基础设施失败不计。
  3. admin_tools._quota_runtime 三态诚实口径：usage_provider 走 travel
     live 软预算（unlimited/ok/exhausted）；usage_counter 走月键
     （tracked/untracked）；无额度声明返回 None。

全部离线：外部 MCP 调用被替换，Redis 用内存假实现。
"""
from __future__ import annotations

import pytest

from backend.config import mcp as MCP_CFG
from backend.infra import mcp_client
from backend.tools.search import zhihu as ZHIHU


# ── 1. call_tool 回调语义 ──────────────────────────


def test_on_upstream_call_fires_once_per_real_call(monkeypatch):
    """真实上游调用触发一次回调；同参数二次调用走缓存不再触发。"""
    mc = mcp_client
    mc.clear_cache()
    calls: list[int] = []

    async def fake_async(*a, **k):
        return {"code": 0}

    monkeypatch.setattr(mc, "_call_async", fake_async)
    mc.call_tool("http://t/mcp", "t1", {"q": "x"}, ttl=300, min_interval=0,
                 on_upstream_call=lambda: calls.append(1))
    mc.call_tool("http://t/mcp", "t1", {"q": "x"}, ttl=300, min_interval=0,
                 on_upstream_call=lambda: calls.append(1))
    assert len(calls) == 1, "缓存命中不得触发 on_upstream_call（不耗配额）"


def test_on_upstream_call_exception_swallowed(monkeypatch):
    """回调异常软失败：不污染业务返回值。"""
    mc = mcp_client
    mc.clear_cache()

    async def fake_async(*a, **k):
        return {"code": 0}

    def _boom():
        raise RuntimeError("quota stats down")

    monkeypatch.setattr(mc, "_call_async", fake_async)
    out = mc.call_tool("http://t/mcp", "t2", {"q": "y"}, ttl=0, min_interval=0,
                       on_upstream_call=_boom)
    assert out == {"code": 0}


# ── 2. 知乎月键计数器 ──────────────────────────────


class _BumpRecorder:
    """记录 pipeline HINCRBY/EXPIRE 的假 Redis。"""

    def __init__(self):
        self.commands: list[tuple] = []

    def pipeline(self):
        return self

    def hincrby(self, key, field, n):
        self.commands.append(("hincrby", key, field, n))

    def expire(self, key, ttl):
        self.commands.append(("expire", key, ttl))

    def execute(self):
        return []


def _enable_zhihu(monkeypatch):
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_ENABLED", True)
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_BASE_URL", "https://test/mcp")
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_API_KEY", "test-key")
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_CACHE_TTL", 0)


_OK_PAYLOAD = {"code": 0, "message": "success",
               "data": {"item_count": 1, "items": [{"title": "t", "url": "u"}]}}


def test_zhihu_success_bumps_month_counter(monkeypatch):
    """成功搜索：HINCRBY 月键，field = 上游工具名。"""
    _enable_zhihu(monkeypatch)
    r = _BumpRecorder()
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: r)

    def fake_call_tool(*a, **kw):
        kw["on_upstream_call"]()
        return _OK_PAYLOAD

    monkeypatch.setattr(ZHIHU, "call_tool", fake_call_tool)
    ZHIHU.zhihu_search_tool.invoke({"query": "q"})
    bumps = [c for c in r.commands if c[0] == "hincrby"]
    assert len(bumps) == 1
    assert "tool_quota:zhihu_mcp:" in bumps[0][1]
    assert bumps[0][2] == "zhihu_search"
    assert any(c[0] == "expire" and c[2] == 45 * 86400 for c in r.commands)


def test_zhihu_upstream_business_failure_not_counted(monkeypatch):
    """上游业务失败（code != 0，如配额尽）不计数——配额没消耗成功的搜索。"""
    _enable_zhihu(monkeypatch)
    r = _BumpRecorder()
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: r)

    def fake_call_tool(*a, **kw):
        # 不触发回调：call_tool 只在真实成功后回调
        return {"code": 429, "message": "quota exhausted", "data": {}}

    monkeypatch.setattr(ZHIHU, "call_tool", fake_call_tool)
    out = ZHIHU.zhihu_search_tool.invoke({"query": "q"})
    assert '"success"' not in out
    assert not [c for c in r.commands if c[0] == "hincrby"]


def test_zhihu_infra_failure_not_counted(monkeypatch):
    """基础设施失败（McpClientError）不计数。"""
    from backend.infra.mcp_client import McpClientError

    _enable_zhihu(monkeypatch)
    r = _BumpRecorder()
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: r)

    def fake_call_tool(*a, **kw):
        raise McpClientError("connection refused")

    monkeypatch.setattr(ZHIHU, "call_tool", fake_call_tool)
    ZHIHU.zhihu_search_tool.invoke({"query": "q"})
    assert not [c for c in r.commands if c[0] == "hincrby"]


# ── 3. _quota_runtime 三态诚实口径 ─────────────────


class _HGetRedis:
    def __init__(self):
        self.got: list[tuple[str, str]] = []

    def hget(self, key, field):
        self.got.append((key, field))
        return "7"


def test_quota_runtime_provider_path(monkeypatch):
    """软预算路径：unlimited / ok / exhausted 三态。"""
    from backend.app.api.routes import admin_tools as mod
    import backend.providers.travel.live.quota as q

    ds = {"type": "rest", "provider": "腾讯LBS",
          "quota": {"period": "day", "limit_env": "X",
                    "usage_provider": "tencent:lbs", "note": "n"}}

    monkeypatch.setattr(q, "daily_budget", lambda p: 0)
    monkeypatch.setattr(q, "current_usage", lambda p: 0)
    assert mod._quota_runtime(ds)["status"] == "unlimited"

    monkeypatch.setattr(q, "daily_budget", lambda p: 100)
    monkeypatch.setattr(q, "current_usage", lambda p: 40)
    out = mod._quota_runtime(ds)
    assert out["status"] == "ok" and out["usage"] == 40 and out["budget"] == 100

    monkeypatch.setattr(q, "current_usage", lambda p: 100)
    assert mod._quota_runtime(ds)["status"] == "exhausted"


def test_quota_runtime_counter_path(monkeypatch):
    """月键路径：tracked，键含 tool_quota:{counter}:{YYYYMM}、field=上游工具名。"""
    from datetime import date

    from backend.app.api.routes import admin_tools as mod

    r = _HGetRedis()
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: r)
    out = mod._quota_runtime({"type": "mcp", "upstream_tool": "zhihu_search",
                              "quota": {"period": "period", "usage_counter": "zhihu_mcp"}})
    assert out["status"] == "tracked" and out["usage"] == 7
    key, field = r.got[0]
    assert "tool_quota:zhihu_mcp:" in key and f"{date.today():%Y%m}" in key
    assert field == "zhihu_search"


def test_quota_runtime_none_and_untracked(monkeypatch):
    """无额度声明 → None；Redis 不可达 → untracked（不把 0 伪装成已用 0）。"""
    from backend.app.api.routes import admin_tools as mod

    assert mod._quota_runtime(None) is None
    assert mod._quota_runtime({"type": "internal"}) is None

    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: None)
    out = mod._quota_runtime({"type": "mcp", "upstream_tool": "zhihu_search",
                              "quota": {"period": "period", "usage_counter": "zhihu_mcp"}})
    assert out["status"] == "untracked" and out["usage"] is None
