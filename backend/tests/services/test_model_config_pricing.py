"""`_apply_model_pricing` / `_parse_model_price`：供应商页登记单价即生效。

2026-09-22 拍板：加模型时按量计费直接填单价，直通写入
`llm_models.pricing`（展示）与 `model_price`（计费生效条目，
approved + reviewer=操作人/system）。本文件用假会话锁住：

- llm 必须 input+output 齐备；embedding/rerank 只要 input；
- 非 metered 供应商填价直接拒绝；
- 同价生效条目跳过（不产生重复版本行），变价走关旧开新（append-only）；
- source='provider-page'、reviewer_2='system'，审计可追溯。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.services.model_config import (
    ModelConfigService,
    _apply_model_pricing,
    _parse_model_price,
)


class _Exec:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _PricingSession:
    """记录 SQL 的假会话；model_price 当前价查询按 dimension 分派。"""

    def __init__(self, current_prices: dict[str, str] | None = None):
        self.current_prices = current_prices or {}
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.params.append(dict(params or {}))
        if "SELECT price_per_unit FROM model_price" in sql:
            dim = dict(params or {}).get("dimension", "")
            current = self.current_prices.get(dim)
            return _Exec([{"price_per_unit": current}] if current else [])
        return _Exec([])

    async def commit(self):
        pass


METERED_PROVIDER = {"id": "custom-a", "billing": "metered"}


async def _run(session, **kwargs):
    await _apply_model_pricing(
        session,
        provider_row=kwargs.pop("provider_row", METERED_PROVIDER),
        model_name=kwargs.pop("model_name", "my-chat"),
        model_kind=kwargs.pop("model_kind", "chat"),
        input_price=kwargs.pop("input_price"),
        output_price=kwargs.pop("output_price"),
        operator=kwargs.pop("operator", "user:test-admin"),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_llm_prices_write_catalog_and_effective_rows():
    session = _PricingSession()
    await _run(
        session,
        input_price=Decimal("0.125000"),
        output_price=Decimal("0.500000"),
    )

    joined = "\n".join(session.statements)
    # 展示价：llm_models.pricing JSONB
    assert "UPDATE llm_models SET pricing" in joined
    pricing_param = next(
        p for p in session.params if "pricing" in p and "input_price_per_1m" in str(p.get("pricing"))
    )
    assert '"input_price_per_1m": 0.125' in str(pricing_param["pricing"])
    assert '"output_price_per_1m": 0.5' in str(pricing_param["pricing"])
    # 计费价：input/output 各关旧开新，直通生效
    assert joined.count("UPDATE model_price SET effective_to = now()") == 2
    assert joined.count("INSERT INTO model_price") == 2
    insert_sql = next(s for s in session.statements if "INSERT INTO model_price" in s)
    assert "'approved'" in insert_sql
    assert "'provider-page'" in insert_sql
    insert_param = next(p for p in session.params if p.get("operator") == "user:test-admin" and p.get("version", "").startswith("provider-page-"))
    assert insert_param["component"] == "llm"
    assert Decimal(str(insert_param["price"])) > 0


@pytest.mark.asyncio
async def test_unchanged_price_skips_close_and_insert():
    session = _PricingSession(current_prices={"input": "0.125000000", "output": "0.500000000"})
    await _run(session, input_price=Decimal("0.125"), output_price=Decimal("0.5"))

    joined = "\n".join(session.statements)
    assert "UPDATE llm_models SET pricing" in joined  # 展示价照常刷新
    assert "UPDATE model_price SET effective_to = now()" not in joined
    assert "INSERT INTO model_price" not in joined


@pytest.mark.asyncio
async def test_embedding_only_needs_input_price():
    session = _PricingSession()
    await _run(
        session,
        model_name="my-embedding",
        model_kind="embedding",
        input_price=Decimal("0.0005"),
        output_price=None,
    )

    joined = "\n".join(session.statements)
    assert joined.count("INSERT INTO model_price") == 1
    param = next(p for p in session.params if p.get("component") == "embedding" and "version" in p)
    assert param["dimension"] == "input"


@pytest.mark.asyncio
async def test_non_metered_provider_rejects_prices():
    session = _PricingSession()
    with pytest.raises(ValueError, match="按量计费"):
        await _run(
            session,
            provider_row={"id": "qwen_tp", "billing": "subscription"},
            input_price=Decimal("0.1"),
            output_price=Decimal("0.2"),
        )


@pytest.mark.asyncio
async def test_llm_requires_both_prices():
    session = _PricingSession()
    with pytest.raises(ValueError, match="输入与输出"):
        await _run(session, input_price=Decimal("0.1"), output_price=None)


@pytest.mark.asyncio
async def test_blank_prices_are_noop():
    session = _PricingSession()
    await _run(session, input_price=None, output_price=None)
    assert session.statements == []


def test_parse_model_price_validation():
    parsed = _parse_model_price({"inputPricePer1m": "0.125", "outputPricePer1m": 0.5})
    assert parsed == (Decimal("0.125000"), Decimal("0.500000"), None, "USD")
    assert _parse_model_price({}) == (None, None, None, "USD")
    assert _parse_model_price({"inputPricePer1m": ""}) == (None, None, None, "USD")
    with pytest.raises(ValueError, match="负数"):
        _parse_model_price({"inputPricePer1m": "-1"})
    with pytest.raises(ValueError, match="合法数字"):
        _parse_model_price({"inputPricePer1m": "abc"})


@pytest.mark.asyncio
async def test_service_add_provider_model_payload_schema_unchanged():
    """回归锚：服务方法仍接受原始 payload dict（路由 by_alias dump 兼容）。"""
    assert hasattr(ModelConfigService, "add_provider_model")
