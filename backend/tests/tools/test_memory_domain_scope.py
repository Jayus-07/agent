"""Memory search Tool 只使用可信请求域，不暴露可由模型选择的 domain 参数。"""
import asyncio
from types import SimpleNamespace


def test_memory_search_uses_bound_domain_hint(monkeypatch):
    from backend.core.request_context import _current_domain_hint
    from backend.memory import manager as manager_module
    from backend.tools import memory as memory_tools

    seen = []

    class _Service:
        async def search(self, *args, **kwargs):
            seen.append(kwargs["domain"])
            return []

    fake_manager = SimpleNamespace(
        service=_Service(),
        run_tool=lambda callback: asyncio.run(callback()),
    )
    monkeypatch.setattr(manager_module, "memory_manager", fake_manager)
    monkeypatch.setattr(
        memory_tools, "_context_ids",
        lambda: ("session-safe", "user-safe", "tenant-safe"),
    )
    token = _current_domain_hint.set("travel")
    try:
        assert "domain" not in memory_tools.memory_search_tool.args
        result = memory_tools.memory_search_tool.invoke({"query": "旅行节奏"})
    finally:
        _current_domain_hint.reset(token)

    assert result == "未找到相关长期记忆。"
    assert seen == ["travel"]
