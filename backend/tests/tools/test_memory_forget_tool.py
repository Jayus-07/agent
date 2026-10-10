import asyncio
from types import SimpleNamespace


def test_memory_forget_tool_uses_authenticated_context_and_reports_success(monkeypatch):
    from backend.memory import manager as memory_manager_module
    from backend.tools import memory as memory_tools

    calls = []

    class _Service:
        async def delete_profile_memory(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "memory_id": kwargs["memory_id"]}

    fake_manager = SimpleNamespace(
        service=_Service(),
        run_tool=lambda callback: asyncio.run(callback()),
    )
    monkeypatch.setattr(memory_manager_module, "memory_manager", fake_manager)
    monkeypatch.setattr(
        memory_tools, "_context_ids",
        lambda: ("session-safe", "user-safe", "tenant-safe"),
    )

    result = memory_tools.memory_forget_tool.invoke({"memory_id": "mem-123"})

    assert result == "✅ 已删除该长期记忆。"
    assert calls == [{
        "user_id": "user-safe",
        "tenant_id": "tenant-safe",
        "memory_id": "mem-123",
    }]
