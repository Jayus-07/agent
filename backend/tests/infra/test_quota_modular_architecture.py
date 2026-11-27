"""架构守卫测试：验证 backend/infra/llm/quota.py 模块化拆解后门面兼容性与职责解耦质量。

验证重点：
1. 门面行数限制：quota.py 严格保持在 150 行以内（原 2215 行单体，瘦身 90% 以上）。
2. 公开契约与符号完整性：__all__ 与历史符号 100% 完整导出。
3. PostgresQuotaStore 类层次与 Mixin 组合架构：确保继承关系完备。
4. Monkeypatch 动态透明穿透验证：外部 mock 门面符号（如 budget_periods, _record_quota_metric）能穿透至具体实现层。
5. 子模块职责健康度：各子模块行数均保持在合理范围，无次级单体膨胀。
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import backend.infra.llm.quota as quota_facade
import backend.infra.llm.quota_service as quota_service
from backend.infra.llm.quota_service import (
    BudgetPeriods,
    BudgetPolicy,
    PostgresQuotaReservation,
    PostgresQuotaStore,
    QuotaConfigurationError,
    QuotaExceeded,
    QuotaManager,
    QuotaPolicyStoreMixin,
    QuotaReconciliationStoreMixin,
    QuotaReservation,
    QuotaReservationStoreMixin,
    QuotaStoreBase,
    ResolvedBudgetPolicies,
    budget_periods,
    enforce_side_effect_budget,
    policy_source_label,
    resolve_budget_policies,
)


def test_facade_line_count_within_budget():
    """断言门面文件 quota.py 行数在 150 行以内，削减率 > 90%（原 2215 行）。"""
    facade_path = Path(quota_facade.__file__)
    content = facade_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    total_lines = len(lines)
    assert total_lines < 150, f"quota.py 门面文件过大: {total_lines} 行 (预期 < 150 行)"
    assert total_lines <= 100, f"quota.py 行数推荐 < 100 行，当前 {total_lines} 行"


def test_symbols_and_contract_integrity():
    """断言所有历史公开契约与核心符号均可在门面中导入且与子包一致。"""
    expected_symbols = [
        "BudgetPeriods",
        "BudgetPolicy",
        "PostgresQuotaReservation",
        "PostgresQuotaStore",
        "QuotaConfigurationError",
        "QuotaExceeded",
        "QuotaManager",
        "QuotaPolicyStoreMixin",
        "QuotaReconciliationStoreMixin",
        "QuotaReservation",
        "QuotaReservationStoreMixin",
        "QuotaStoreBase",
        "ResolvedBudgetPolicies",
        "_IDENTITY_SCOPES",
        "_POLICY_SOURCE_LABELS",
        "_SIX_PLACES",
        "_VALID_ENFORCEMENT",
        "_VALID_SCOPES",
        "_insert_threshold_events",
        "_record_quota_metric",
        "budget_periods",
        "enforce_side_effect_budget",
        "policy_source_label",
        "resolve_budget_policies",
    ]
    for sym in expected_symbols:
        assert hasattr(quota_facade, sym), f"门面丢失符号: {sym}"
        facade_val = getattr(quota_facade, sym)
        sub_val = getattr(quota_service, sym)
        assert facade_val is sub_val, f"门面符号与子模块不一致: {sym}"


def test_postgres_quota_store_inheritance_and_methods():
    """断言 PostgresQuotaStore 正确组合了各个单一职责 Mixin 与 Base 类。"""
    assert issubclass(PostgresQuotaStore, QuotaStoreBase)
    assert issubclass(PostgresQuotaStore, QuotaPolicyStoreMixin)
    assert issubclass(PostgresQuotaStore, QuotaReservationStoreMixin)
    assert issubclass(PostgresQuotaStore, QuotaReconciliationStoreMixin)

    store = PostgresQuotaStore(connection_factory=MagicMock())
    expected_methods = [
        "get_policy",
        "get_budget_status",
        "list_policies",
        "list_events",
        "list_policy_audit",
        "list_subjects",
        "summary",
        "upsert_policy",
        "resolve",
        "resolve_domain_policy",
        "reserve",
        "settle",
        "release",
        "mark_needs_review",
        "sweep_stale_reservations",
        "resolve_needs_review",
        "dry_run_backlog_migration",
        "reconciliation_summary",
        "monthly_reconciliation",
        "monthly_reconciliation_csv",
        "list_pending_review",
    ]
    for m in expected_methods:
        assert hasattr(store, m), f"PostgresQuotaStore 缺失方法: {m}"
        assert callable(getattr(store, m)), f"{m} 不是可调用方法"


def test_monkeypatch_penetration_budget_periods(monkeypatch):
    """断言外部 monkeypatch 门面的 budget_periods 时，内部调用能动态感知。"""
    called = []
    fake_periods = SimpleNamespace(
        day_start_utc="2026-10-08",
        month_start_utc="2026-10-01",
    )

    def mock_budget_periods(*args, **kwargs):
        called.append(True)
        return fake_periods

    monkeypatch.setattr(quota_facade, "budget_periods", mock_budget_periods)

    store = PostgresQuotaStore(connection_factory=MagicMock())
    monkeypatch.setattr(store, "resolve", lambda u, t: SimpleNamespace(
        tenant=SimpleNamespace(timezone="Asia/Shanghai", enforcement="hard", audit_exempt=False),
        user=SimpleNamespace(timezone="Asia/Shanghai", enforcement="hard", audit_exempt=False),
    ))
    monkeypatch.setattr(store, "get_policy", lambda *a: None)
    monkeypatch.setattr(store, "_window", lambda *a: {"used_cny": 0, "reserved_cny": 0, "limit_cny": 10, "ratio": 0.0, "reset_at": ""})

    out = store.get_budget_status(user_id="u1", tenant_id="t1")
    assert called, "门面被 monkeypatch 的 budget_periods 未被底层子模块调用！"
    assert out["policy_source"]["label"]


def test_monkeypatch_penetration_quota_store_in_enforce_budget(monkeypatch):
    """断言外部 monkeypatch 门面的 PostgresQuotaStore 时，enforce_side_effect_budget 能动态感知。"""
    called_reserve = []

    class MockStore:
        def reserve(self, **kwargs):
            called_reserve.append(kwargs)
            return SimpleNamespace()

        def settle(self, *args, **kwargs):
            pass

    monkeypatch.setattr(quota_facade, "PostgresQuotaStore", MockStore)
    monkeypatch.setattr("backend.config.llm.LLM_BUDGET_MODE", "enforce")

    enforce_side_effect_budget(user_id="u123", tenant_id="t456")
    assert len(called_reserve) == 1
    assert called_reserve[0]["user_id"] == "u123"
    assert called_reserve[0]["tenant_id"] == "t456"
    assert called_reserve[0]["amount_cny"] == Decimal("0")


def test_submodules_line_counts_health():
    """断言子包内各模块均无超大次级单体（均 < 800 行）。"""
    sub_dir = Path(quota_facade.__file__).parent / "quota_service"
    py_files = list(sub_dir.glob("*.py"))
    assert len(py_files) >= 8, f"子模块数量过少: {len(py_files)}"

    for f in py_files:
        lines = len(f.read_text(encoding="utf-8").splitlines())
        assert lines < 800, f"子模块 {f.name} 过大: {lines} 行 (预期 < 800 行)"
