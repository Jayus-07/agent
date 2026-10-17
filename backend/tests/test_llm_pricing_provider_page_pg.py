"""provider-page 直登价与治理版本价的「当前有效价格」契约（#81 遗留问题）。

背景：模型页登记即生效（`services/model_config._apply_model_pricing`）写入
`source='provider-page'`、`approval_status='approved'` 的价格行，但不会写
`model_price_versions` 版本行。`PostgresPriceRepository.get_current` 使用
INNER JOIN 要求版本行 `status='active'`，导致这类合法生效价在在线计费与
价格巡检中全部不可见（表现为缺价 / registry_fallback）。

本文件在隔离 PostgreSQL 上验证兼容契约：
- 无版本行的 provider-page 生效价可见；
- 有 active 治理版本时以其为准，不与直登价混价；
- expired / rejected 版本永不入选；
- 冲突（跨版本、混币种）失败关闭，不静默取一条。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import pytest

from backend.config.database import MEMORY_DB_CONFIG


def _model(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _version(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _insert_price(
    cur,
    *,
    model: str,
    dimension: str,
    price: str,
    version: str,
    source: str,
    component: str = "llm",
    currency: str = "USD",
    approval_status: str = "approved",
    effective_from: datetime | None = None,
) -> None:
    """按 `_apply_model_pricing` 的列写入一条价格行（不含版本行）。"""
    cur.execute(
        """INSERT INTO model_price
           (model_name, component, dimension, price_per_unit, unit, currency,
            price_table_version, source, effective_from, approval_status,
            reviewer_1, reviewer_2)
           VALUES (%s, %s, %s, %s, 'per_1m_tokens', %s, %s, %s, %s, %s,
                   'operator:test', 'system')""",
        (
            model, component, dimension, price, currency, version, source,
            effective_from or (datetime.now(timezone.utc) - timedelta(minutes=1)),
            approval_status,
        ),
    )


def _insert_version(cur, *, version: str, source: str, status: str) -> None:
    cur.execute(
        """INSERT INTO model_price_versions
           (version, source, imported_by, status, effective_from, coverage_ratio)
           VALUES (%s, %s, 'importer:test', %s, %s, 1.0)""",
        (version, source, status, datetime.now(timezone.utc) - timedelta(days=1)),
    )


def test_provider_page_price_is_visible_to_billing():
    """A1-01：无版本行的 provider-page 生效价必须参与在线计费。"""
    from backend.infra.llm.pricing import NormalizedUsage, PostgresPriceRepository, price_usage

    model = _model("pp-visible")
    version = _version("provider-page")
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            _insert_price(cur, model=model, dimension="input", price="1.000000",
                          version=version, source="provider-page")
            _insert_price(cur, model=model, dimension="output", price="2.000000",
                          version=version, source="provider-page")

    table = PostgresPriceRepository().get_current(model, "llm")
    rows = table.require(model, "llm", enforce=True)
    assert {row.price_table_version for row in rows.values()} == {version}
    # 1000 input × 1.0/1M + 500 output × 2.0/1M = 0.001 + 0.001
    assert table.calculate_cost(model, "llm", {"input": 1000, "output": 500}) == Decimal("0.002000")

    result = price_usage(
        model, "llm",
        NormalizedUsage(input_tokens=1000, output_tokens=500),
        enforce=True,
    )
    assert result.pricing_source == "approved_price_table"
    assert result.cost_status == "exact"
    assert result.native_currency == "USD"
    assert result.native_cost == Decimal("0.002000")


def test_active_governed_version_wins_over_provider_page_rows():
    """A1-02：治理 active 版本优先，直登价不得与其混价。"""
    from backend.infra.llm.price_governance import PostgresPriceGovernanceRepository
    from backend.infra.llm.pricing import PostgresPriceRepository

    model = _model("pp-mixed")
    direct_version = _version("provider-page")
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            _insert_price(cur, model=model, dimension="input", price="100.000000",
                          version=direct_version, source="provider-page")
            _insert_price(cur, model=model, dimension="output", price="200.000000",
                          version=direct_version, source="provider-page")

    governed_version = _version("governed")
    governance = PostgresPriceGovernanceRepository()
    now = datetime.now(timezone.utc)
    governance.import_version(
        [
            {"model_name": model, "component": "llm", "dimension": "input",
             "price_per_unit": "3.000000", "unit": "per_1m_tokens", "currency": "USD"},
            {"model_name": model, "component": "llm", "dimension": "output",
             "price_per_unit": "4.000000", "unit": "per_1m_tokens", "currency": "USD"},
        ],
        version=governed_version, source="governance-test",
        imported_by="importer:test", effective_from=now - timedelta(days=2),
    )
    governance.review_version(governed_version, reviewer="reviewer:a", decision="approve")
    governance.review_version(governed_version, reviewer="reviewer:b", decision="approve")
    governance.canary_version(governed_version, action="start", now=now - timedelta(hours=26))
    governance.canary_version(governed_version, action="complete", now=now)

    table = PostgresPriceRepository().get_current(model, "llm")
    rows = table.require(model, "llm", enforce=True)
    assert {row.price_table_version for row in rows.values()} == {governed_version}
    assert table.calculate_cost(model, "llm", {"input": 1000, "output": 1000}) == Decimal("0.007000")


@pytest.mark.parametrize("status", ["expired", "rejected"])
def test_inactive_version_rows_are_never_selected(status):
    """A1-03：expired / rejected 版本价格永不入选。"""
    from backend.infra.llm.pricing import MissingModelPrice, PostgresPriceRepository

    model = _model(f"pp-{status}")
    version = _version(f"governed-{status}")
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            _insert_version(cur, version=version, source="governance-test", status=status)
            _insert_price(cur, model=model, dimension="input", price="1.000000",
                          version=version, source="governance-test")
            _insert_price(cur, model=model, dimension="output", price="2.000000",
                          version=version, source="governance-test")

    with pytest.raises(MissingModelPrice):
        PostgresPriceRepository().get_current(model, "llm").require(
            model, "llm", enforce=True,
        )


def test_conflicting_provider_page_versions_fail_closed():
    """A1-04：同一模型组件跨版本直登价冲突必须失败关闭。"""
    from backend.infra.llm.pricing import PostgresPriceRepository, PriceConflict

    model = _model("pp-cross-version")
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            _insert_price(cur, model=model, dimension="input", price="1.000000",
                          version=_version("provider-page"), source="provider-page")
            _insert_price(cur, model=model, dimension="output", price="2.000000",
                          version=_version("provider-page"), source="provider-page")

    with pytest.raises(PriceConflict):
        PostgresPriceRepository().get_current(model, "llm")


def test_mixed_currency_provider_page_price_fails_closed():
    """A1-05：同模型同组件混币种必须失败关闭（不按错误汇率计费）。"""
    from backend.infra.llm.pricing import PostgresPriceRepository, PriceConflict

    model = _model("pp-mixed-currency")
    version = _version("provider-page")
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            _insert_price(cur, model=model, dimension="input", price="1.000000",
                          version=version, source="provider-page", currency="USD")
            _insert_price(cur, model=model, dimension="output", price="2.000000",
                          version=version, source="provider-page", currency="CNY")

    with pytest.raises(PriceConflict):
        PostgresPriceRepository().get_current(model, "llm")


def test_hard_budget_rejects_when_no_price_at_all(monkeypatch):
    """A1-06：硬预算模式下「价格表无行 + 注册表无单价」必须拒绝调用。"""
    from backend.infra.llm import budget as budget_module
    from backend.infra.llm import pricing as pricing_module

    class _EmptyTable:
        def require(self, model_name, component, *, enforce):
            raise pricing_module.MissingModelPrice(model_name, component)

    class _QuotaStore:
        def reserve(self, **kwargs):
            return {"id": "r1"}

    monkeypatch.setattr(
        pricing_module, "get_current_price_table",
        lambda model_name, component: _EmptyTable(),
    )
    monkeypatch.setattr(
        "backend.infra.llm.models.get_model_pricing", lambda model_name: (0.0, 0.0),
    )

    limits = budget_module.RequestBudgetLimits(
        max_calls=8, max_total_tokens=32000, max_cost=Decimal("0.50"),
        max_retries=2, max_fallbacks=1,
    )
    budget = budget_module.RequestBudget(
        limits, mode="enforce", user_id="user-a", tenant_id="tenant-a",
        quota_store=_QuotaStore(),
    )
    with pytest.raises(budget_module.RequestBudgetExceeded):
        budget.reserve("primary", model_name="__no_price_model__")
