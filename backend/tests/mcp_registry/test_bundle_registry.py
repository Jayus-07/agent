"""能力包注册表与静态能力产品授权（Batch 2，快照注入，无 DB）。

覆盖：
  - bundle_registry 快照派生与签名（内容变化触发失效）；
  - product_allows_capability 静态分支的包授权白名单；
  - 表未加载 / 无授权行 → 回退既有域黑名单（部署兼容）；
  - 判定失败 fail-closed。
"""
from __future__ import annotations

import pytest

from backend.infra.bundle_registry import store as bundle_store


def _make_snapshot(
    bundles: dict[str, dict],
    items: dict[str, list[str]],
    grants: list[tuple[str, str, str]],
):
    return bundle_store.BundleSnapshot(
        loaded=True,
        bundles=bundles,
        items={bid: frozenset(caps) for bid, caps in items.items()},
        grants=frozenset(grants),
        refreshed_at=1.0,
    )


@pytest.fixture(autouse=True)
def _reset():
    bundle_store.reset_for_tests()
    yield
    bundle_store.reset_for_tests()


class TestBundleSnapshot:
    def test_product_capabilities_union(self):
        snap = _make_snapshot(
            {"b1": {"name": "数据分析", "domain": "data", "enabled": True},
             "b2": {"name": "知识问答", "domain": "knowledge", "enabled": True}},
            {"b1": ["sql.query", "mcp.rest__get"], "b2": ["rag.search"]},
            [("agent", "b1", "default"), ("customer_service", "b2", "default")],
        )
        assert snap.product_capabilities("agent", "t1") == frozenset(
            {"sql.query", "mcp.rest__get"})
        assert snap.product_capabilities("customer_service", "t1") == frozenset(
            {"rag.search"})
        assert snap.product_capabilities("selection", "t1") == frozenset()

    def test_disabled_bundle_and_revoked_grant_excluded(self):
        snap = _make_snapshot(
            {"b1": {"name": "A", "domain": "data", "enabled": False},
             "b2": {"name": "B", "domain": "data", "enabled": True}},
            {"b1": ["sql.query"], "b2": ["report.generate"]},
            # b1 的授权行存在但语义上已撤销的不进 grants（SQL 层过滤），
            # 这里验证禁用包不贡献成员
            [("agent", "b1", "default"), ("agent", "b2", "default")],
        )
        assert "sql.query" not in snap.product_capabilities("agent", "t1")
        assert "report.generate" in snap.product_capabilities("agent", "t1")

    def test_signature_changes_with_content(self):
        s1 = _make_snapshot(
            {"b1": {"name": "A", "domain": "data", "enabled": True}},
            {"b1": ["sql.query"]}, [("agent", "b1", "default")])
        s2 = _make_snapshot(
            {"b1": {"name": "A", "domain": "data", "enabled": True}},
            {"b1": ["sql.query", "rag.search"]},
            [("agent", "b1", "default")])
        assert s1.signature() != s2.signature()


class TestProductAllowsCapability:
    """静态分支：包授权表非空 → 白名单；空/未加载 → 黑名单回退。"""

    def _patch_tenant(self, monkeypatch, tenant="t1"):
        from backend.core import request_context

        monkeypatch.setattr(
            request_context, "get_tool_tenant_id", lambda: tenant)

    def test_bundle_grant_allows_member_and_denies_non_member(
            self, monkeypatch):
        from backend.core import product_context

        self._patch_tenant(monkeypatch)
        bundle_store.inject_snapshot_for_tests(_make_snapshot(
            {"b1": {"name": "数据", "domain": "data", "enabled": True}},
            {"b1": ["sql.query", "report.generate"]},
            [("agent", "b1", "default")]))
        assert product_context.product_allows_capability("agent", "sql.query")
        assert product_context.product_allows_capability("agent", "report.generate")
        # 未授权成员：即使域黑名单允许（data 不在黑名单）也拒绝
        assert not product_context.product_allows_capability("agent", "rag.search")

    def test_cross_domain_member_denied_even_when_granted(self, monkeypatch):
        """决策 #15 业务域硬隔离（PR #115 审计修复）：bundle 白名单只在
        域专属边界内放宽能力——cs 域成员即使被授权给 agent 也拒绝执行。"""
        from backend.core import product_context

        self._patch_tenant(monkeypatch)
        bundle_store.inject_snapshot_for_tests(_make_snapshot(
            {"b1": {"name": "客服包", "domain": "cs", "enabled": True}},
            {"b1": ["cs.ticket.manage"]},
            [("agent", "b1", "default")]))
        assert not product_context.product_allows_capability(
            "agent", "cs.ticket.manage")
        # 同一边界在无 bundle（回退黑名单）时同样生效
        bundle_store.reset_for_tests()
        assert not product_context.product_allows_capability(
            "agent", "cs.ticket.manage")
        # 边界内的能力不受影响：数据包成员照常放行
        bundle_store.inject_snapshot_for_tests(_make_snapshot(
            {"b1": {"name": "数据", "domain": "data", "enabled": True}},
            {"b1": ["sql.query"]},
            [("agent", "b1", "default")]))
        assert product_context.product_allows_capability("agent", "sql.query")

    def test_no_grant_rows_falls_back_to_domain_blacklist(self, monkeypatch):
        from backend.core import product_context

        self._patch_tenant(monkeypatch)
        bundle_store.inject_snapshot_for_tests(_make_snapshot(
            {"b1": {"name": "空包", "domain": "data", "enabled": True}},
            {"b1": []}, []))  # 无授权行 → bundle_allows 返回 None → 回退
        # 黑名单行为保持：agent 拒绝 travel 域
        assert not product_context.product_allows_capability(
            "agent", "travel.query", ("travel",))
        # 黑名单行为保持：agent 允许 data 域
        assert product_context.product_allows_capability("agent", "sql.query")

    def test_unloaded_snapshot_falls_back(self, monkeypatch):
        from backend.core import product_context

        self._patch_tenant(monkeypatch)
        bundle_store.reset_for_tests()  # loaded=False
        assert product_context.product_allows_capability("agent", "sql.query")
        assert not product_context.product_allows_capability(
            "agent", "travel.query", ("travel",))

    def test_tenant_scoped_grant_only_matches_same_tenant(self, monkeypatch):
        from backend.core import product_context

        self._patch_tenant(monkeypatch, tenant="tenant-b")
        bundle_store.inject_snapshot_for_tests(_make_snapshot(
            {"b1": {"name": "A", "domain": "data", "enabled": True}},
            {"b1": ["sql.query"]},
            [("agent", "b1", "tenant-a")]))  # 只授权 tenant-a
        # tenant-b 无匹配授权，但表有授权行 → 白名单判定（不回退）→ 拒绝
        assert not product_context.product_allows_capability("agent", "sql.query")

    def test_internal_and_public_unchanged(self, monkeypatch):
        from backend.core import product_context

        self._patch_tenant(monkeypatch)
        bundle_store.inject_snapshot_for_tests(_make_snapshot(
            {"b1": {"name": "A", "domain": "data", "enabled": True}},
            {"b1": ["sql.query"]}, [("agent", "b1", "default")]))
        assert product_context.product_allows_capability("internal", "anything")
        assert not product_context.product_allows_capability("public", "sql.query")


class TestBundleAllowsApi:
    def test_bundle_allows_tri_state(self, monkeypatch):
        self._patch = None
        bundle_store.reset_for_tests()
        assert bundle_store.bundle_allows("agent", "sql.query", "t1") is None
        bundle_store.inject_snapshot_for_tests(_make_snapshot(
            {"b1": {"name": "A", "domain": "data", "enabled": True}},
            {"b1": ["sql.query"]}, [("agent", "b1", "default")]))
        assert bundle_store.bundle_allows("agent", "sql.query", "t1") is True
        assert bundle_store.bundle_allows("agent", "rag.search", "t1") is False
