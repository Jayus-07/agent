"""超管单人直发价格（2026-10-17 决策）：一步生效、CNY/USD 真实币种、热更新。

锁定契约：
1. 只有 super_admin 能调用（路由层）；
2. 直发后版本立刻 active，旧 active 版本 expired；
3. 价格行保留真实币种（CNY/USD），不得写死 USD；
4. model_price 仍 append-only：改价=关旧行+开新行；
5. 审计落 model_price_reviews；
6. 发布后价格缓存失效（后续登记/调用立即用新价）；
7. CI 的价格快照校验（build_price_snapshot）接受该审批形状。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import pytest

from backend.config.database import MEMORY_DB_CONFIG


def _rows(model: str, *, currency: str, input_price: str = "0.200000",
          output_price: str = "2.000000") -> list[dict]:
    return [
        {"model_name": model, "component": "llm", "dimension": "input",
         "price_per_unit": input_price, "unit": "per_1m_tokens", "currency": currency},
        {"model_name": model, "component": "llm", "dimension": "output",
         "price_per_unit": output_price, "unit": "per_1m_tokens", "currency": currency},
    ]


def _publish(repo, rows, version, *, actor="user:super-1", reason="官方刊例价校准"):
    return repo.publish_super_admin(
        rows, version=version, source="official-cny-2026-10", actor=actor, reason=reason,
    )


def test_super_admin_publish_is_active_immediately_with_cny(monkeypatch):
    from backend.infra.llm import pricing
    from backend.infra.llm.price_governance import PostgresPriceGovernanceRepository

    cleared = {"n": 0}
    monkeypatch.setattr(pricing, "clear_price_cache",
                        lambda: cleared.__setitem__("n", cleared["n"] + 1))

    model = f"sa-cny-{uuid.uuid4().hex[:10]}"
    version = f"sa-{uuid.uuid4().hex[:12]}"
    repo = PostgresPriceGovernanceRepository()

    result = _publish(repo, _rows(model, currency="CNY"), version)
    assert result["status"] == "active"
    assert result["mode"] == "super-admin-direct"
    assert result["coverage_ratio"] == 1.0
    assert cleared["n"] == 1, "发布后必须失效价格缓存（热更新）"

    from backend.infra.llm.pricing import PostgresPriceRepository
    table = PostgresPriceRepository().get_current(model, "llm")
    rows = table.require(model, "llm", enforce=True)
    assert {row.currency for row in rows.values()} == {"CNY"}
    assert table.calculate_cost(model, "llm", {"input": 1_000_000, "output": 1_000_000}) == Decimal("2.200000")

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, reviewer_1, reviewer_2, imported_by, coverage_ratio "
                    "FROM model_price_versions WHERE version=%s", (version,))
        status, r1, r2, importer, coverage = cur.fetchone()
        assert (status, r1, r2, importer) == ("active", "user:super-1", "system", "super-admin")
        assert float(coverage) == 1.0
        cur.execute("SELECT reviewer, decision, reason FROM model_price_reviews WHERE version=%s", (version,))
        review = cur.fetchone()
        assert review[0] == "user:super-1" and review[1] == "approve" and "直发" in review[2]


def test_republish_closes_previous_rows_and_expires_old_version():
    from backend.infra.llm.price_governance import PostgresPriceGovernanceRepository

    model = f"sa-reprice-{uuid.uuid4().hex[:10]}"
    v1 = f"sa-{uuid.uuid4().hex[:12]}"
    v2 = f"sa-{uuid.uuid4().hex[:12]}"
    repo = PostgresPriceGovernanceRepository()
    _publish(repo, _rows(model, currency="CNY", input_price="0.200000"), v1)
    _publish(repo, _rows(model, currency="CNY", input_price="0.300000"), v2)

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM model_price_versions WHERE version=%s", (v1,))
        assert cur.fetchone()[0] == "expired"
        cur.execute("SELECT count(*) FROM model_price WHERE model_name=%s "
                    "AND approval_status='approved' AND effective_to IS NULL", (model,))
        assert cur.fetchone()[0] == 2, "旧行必须关闭，只有新版本两行仍生效"
        cur.execute("SELECT count(*) FROM model_price WHERE model_name=%s", (model,))
        assert cur.fetchone()[0] == 4, "append-only：历史 2 行必须保留"


def test_publish_accepts_usd_and_rejects_mixed_currency():
    from backend.infra.llm.price_governance import PostgresPriceGovernanceRepository

    repo = PostgresPriceGovernanceRepository()
    model = f"sa-usd-{uuid.uuid4().hex[:10]}"
    version = f"sa-{uuid.uuid4().hex[:12]}"
    _publish(repo, _rows(model, currency="USD"), version)

    mixed = _rows(f"sa-mixed-{uuid.uuid4().hex[:8]}", currency="CNY")
    mixed[1]["currency"] = "USD"
    with pytest.raises(ValueError):
        _publish(repo, mixed, f"sa-{uuid.uuid4().hex[:12]}")


def test_publish_rejects_incomplete_coverage():
    from backend.infra.llm.price_governance import PostgresPriceGovernanceRepository

    repo = PostgresPriceGovernanceRepository()
    only_input = [{
        "model_name": f"sa-gap-{uuid.uuid4().hex[:8]}", "component": "llm",
        "dimension": "input", "price_per_unit": "1", "unit": "per_1m_tokens",
        "currency": "CNY",
    }]
    with pytest.raises(ValueError):
        _publish(repo, only_input, f"sa-{uuid.uuid4().hex[:12]}")


def test_ci_price_snapshot_accepts_super_admin_shape():
    """CI 的价格快照校验必须接受「单人直发」审批形状，否则真实评测永远起不来。"""
    from backend.scripts.seed_prompt_eval_db import build_price_snapshot

    model = "doubao-seed-2.0-mini"
    now = datetime.now(timezone.utc)
    base = {
        "price_table_version": "2026-10-cny-1", "source": "official-cny-2026-10",
        "approval_status": "approved", "version_status": "active",
        "reviewer_1": "user:super-1", "reviewer_2": "system",
        "version_reviewer_1": "user:super-1", "version_reviewer_2": "system",
        "version_imported_by": "super-admin", "version_source": "official-cny-2026-10",
        "version_effective_from": now - timedelta(days=1),
        "canary_started_at": now, "canary_completed_at": now,
        "coverage_ratio": Decimal("1.0"), "missing_price_count": 0,
        "price_calculation_error_count": 0,
        "effective_from": now - timedelta(hours=1), "effective_to": None,
        "unit": "per_1m_tokens", "currency": "CNY",
    }
    rows = [
        {**base, "model_name": model, "component": "llm", "dimension": "input",
         "price_per_unit": Decimal("0.200000")},
        {**base, "model_name": model, "component": "llm", "dimension": "output",
         "price_per_unit": Decimal("2.000000")},
    ]
    configs = {"eval_gen": {"model_name": model}}
    snapshot = build_price_snapshot(configs, rows)
    assert len(snapshot) == 2
    assert {row["currency"] for row in snapshot} == {"CNY"}
