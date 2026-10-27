"""资产一致性报告 · 动态注册表段（Batch 3）。

_check_dynamic_registry 的单元覆盖（快照注入 + 内存 sqlite 不可用时
DB 段的异常软失败路径）：派生失败 / bundle 悬空引用 / 对账异常软失败。
"""
from __future__ import annotations

import pytest

from backend.app.api.routes import consistency
from backend.infra.bundle_registry import store as bundle_store
from backend.infra.mcp_registry import store as mcp_store


@pytest.fixture(autouse=True)
def _reset():
    mcp_store.reset_for_tests()
    bundle_store.reset_for_tests()
    yield
    mcp_store.reset_for_tests()
    bundle_store.reset_for_tests()


def _inject_mcp(errors=(), tools=("mcp.a__x",)):
    snap = mcp_store.McpRegistrySnapshot(
        loaded=True,
        tools={
            cap: mcp_store.McpToolRuntime(
                capability=cap, tool_id=1, connection_id="c1",
                upstream_tool_name="x", tenant_id="t", spec=None)
            for cap in tools
        },
        errors=tuple(errors),
    )
    mcp_store.inject_snapshot_for_tests(snap)


def _inject_bundles(items: dict[str, list[str]]):
    snap = bundle_store.BundleSnapshot(
        loaded=True,
        bundles={bid: {"name": bid, "domain": "data", "enabled": True}
                 for bid in items},
        items={bid: frozenset(caps) for bid, caps in items.items()},
        grants=frozenset({("agent", bid, "default") for bid in items}),
        refreshed_at=1.0,
    )
    bundle_store.inject_snapshot_for_tests(snap)


class TestDynamicRegistrySection:
    def test_clean_snapshots_pass(self):
        _inject_mcp()
        _inject_bundles({"b1": ["mcp.a__x"]})
        issues, counts = [], {}
        consistency._check_dynamic_registry_sync(issues, counts)
        assert issues == []
        assert counts["runtime_tools"] == 1
        assert counts["bundles"] == 1

    def test_derive_errors_reported(self):
        _inject_mcp(errors=[("mcp.a__x", "governance 非法")])
        issues, counts = [], {}
        consistency._check_dynamic_registry_sync(issues, counts)
        assert any("派生失败" in i for i in issues)
        assert counts["derive_errors"] == 1

    def test_bundle_dangling_dynamic_reference(self):
        _inject_mcp(tools=())
        _inject_bundles({"b1": ["mcp.gone__tool"]})
        issues, _ = [], {}
        consistency._check_dynamic_registry_sync(issues, {})
        assert any("引用未生效的动态能力" in i for i in issues)

    def test_db_failure_soft_fails(self, monkeypatch):
        """DB 不可用：DB 段异常被捕获为 issue，同步段结果保留。"""
        _inject_mcp()

        async def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            consistency, "_check_dynamic_registry_db",
            lambda *a, **kw: _boom())

        import asyncio

        section = asyncio.run(consistency._check_dynamic_registry())
        assert section["status"] == "FAIL"
        assert any("对账异常" in i for i in section["issues"])
        assert section["counts"].get("runtime_tools") == 1
