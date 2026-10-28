# -*- coding: utf-8 -*-
"""test_discovery_service.py — DiscoveryService 单测（决策 #18，B1）

覆盖：agent 产品域禁行根语义下 travel.* 候选被权限过滤排除（策略默认值
路径，不依赖 DB）；top_k 截断；planner 全集回退来源标记。
离线：不连 pgvector / LLM / 业务库（策略读取经 monkeypatch 走默认值）。
"""
from __future__ import annotations

import pytest

from backend.orchestration.discovery import (
    DEFAULT_POLICY,
    resolve_candidates,
)
from backend.orchestration.discovery import service as discovery_service


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch):
    """策略读取走代码内默认值路径（B1 单测不依赖 DB）。"""
    monkeypatch.setattr(
        discovery_service, "load_active_policy",
        lambda tenant_id: dict(DEFAULT_POLICY, tenant_id=str(tenant_id or "default")),
    )


def test_agent_product_excludes_travel_domain_candidates():
    """agent 产品下 travel 域候选被产品域禁行根全部过滤（product_allows_capability 语义）。"""
    result = resolve_candidates(
        "规划福州 2 天的行程", "agent", "t1", domain="travel", top_k=5)

    assert result["source"] == "domain_registry"
    # travel 域候选非空（registry 有 travel.poi_search / map.lookup），
    # 但 agent 产品禁行根含 travel → 全部被权限过滤剔除
    assert result["candidate_count"] > 0
    assert result["candidates"] == []
    assert result["excluded_count"] == result["candidate_count"]
    assert result["policy"]["mode"] == DEFAULT_POLICY["mode"]
    assert result["policy"]["tenant_id"] == "t1"


def test_top_k_truncates_candidates_and_internal_allows_all():
    """internal 产品全放行（零排除）；top_k 截断生效。"""
    full = resolve_candidates("查一下销售额", "internal", "t1", domain="", top_k=50)
    assert full["source"] == "planner_visible"
    assert full["candidate_count"] > 2
    assert full["excluded_count"] == 0
    assert {c["source"] for c in full["candidates"]} == {"planner_visible"}

    truncated = resolve_candidates("查一下销售额", "internal", "t1", domain="", top_k=2)
    assert len(truncated["candidates"]) == 2
    # 截断不改变排除口径：excluded_count 只统计权限过滤，不含 top_k 截断
    assert truncated["excluded_count"] == 0
    assert [c["name"] for c in truncated["candidates"]] == [
        c["name"] for c in full["candidates"][:2]]


def test_identity_contextvar_wrapped_and_reset(monkeypatch):
    """解析期间产品/租户身份经 ContextVar 注入，结束后复位（不泄漏到调用方）。"""
    from backend.core.request_context import (
        _current_product_id,
        _current_product_source,
        _current_tenant_id,
    )

    seen: dict[str, str] = {}

    def _fake_authorized(candidates, context, *, effective_domain=""):
        seen["product_id"] = context.get("product_id") or ""
        seen["tenant"] = _current_tenant_id.get()
        seen["product"] = _current_product_id.get()
        seen["source"] = _current_product_source.get()
        return list(candidates)

    from backend.orchestration.router import capability_router

    monkeypatch.setattr(
        capability_router.CapabilityRouter, "_authorized_candidates",
        staticmethod(_fake_authorized))

    assert _current_product_id.get() == "internal"  # 缺省值
    assert _current_product_source.get() == "internal_call"
    result = resolve_candidates("q", "agent", "tenant-x", domain="travel")
    assert result["candidates"]  # 桩放行 → 候选保留
    assert seen == {
        "product_id": "agent", "tenant": "tenant-x",
        "product": "agent", "source": "registered_route",
    }
    # 调用结束后 ContextVar 复位（reset），不残留请求身份
    assert _current_product_id.get() == "internal"
    assert _current_tenant_id.get() == ""
    assert _current_product_source.get() == "internal_call"


# ═══════════════════════════════════════════════════
# B2：运行链策略入口 load_policy_for_runtime（归一 + TTL 缓存）
# ═══════════════════════════════════════════════════

class TestLoadPolicyForRuntime:
    """运行链统一 loader：mode 白名单、数值钳制、进程内 TTL 缓存。"""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        discovery_service.reset_runtime_policy_cache()
        yield
        discovery_service.reset_runtime_policy_cache()

    def test_invalid_mode_falls_back_and_values_clamped(self):
        pol = discovery_service._policy_dict_to_runtime({
            "mode": "turbo", "top_k": "5", "max_rounds": 0,
            "cost_budget_milliunits": -3, "timeout_ms": "x",
        })
        assert pol.mode == DEFAULT_POLICY["mode"]  # 白名单外回退默认
        assert pol.top_k == 5
        assert pol.max_rounds == 1  # 下限钳制
        assert pol.cost_budget_milliunits == 0
        assert pol.timeout_ms == DEFAULT_POLICY["timeout_ms"]  # 非法值回落默认

    def test_ttl_cache_reads_db_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            discovery_service, "load_active_policy",
            lambda tenant_id: calls.append(tenant_id)
            or dict(DEFAULT_POLICY, mode="reasoning"))
        p1 = discovery_service.load_policy_for_runtime("t1")
        p2 = discovery_service.load_policy_for_runtime("t1")
        assert p1 is p2 and p1.mode == "reasoning"
        assert len(calls) == 1  # TTL 内第二读走缓存
        discovery_service.reset_runtime_policy_cache()
        discovery_service.load_policy_for_runtime("t1")
        assert len(calls) == 2  # 清缓存后重读

    def test_different_tenants_cached_separately(self, monkeypatch):
        seen = []

        def _fake_load(tenant_id):
            seen.append(tenant_id)
            return dict(DEFAULT_POLICY, tenant_id=tenant_id)

        monkeypatch.setattr(discovery_service, "load_active_policy", _fake_load)
        assert discovery_service.load_policy_for_runtime("a").mode == DEFAULT_POLICY["mode"]
        assert discovery_service.load_policy_for_runtime("b").mode == DEFAULT_POLICY["mode"]
        assert set(seen) == {"a", "b"}  # 租户各自缓存，互不串读
