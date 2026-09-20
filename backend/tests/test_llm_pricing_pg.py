"""Q8 价格表导入、双人审核、生效和追加式约束集成回归。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import psycopg2
import pytest

from backend.config.database import MEMORY_DB_CONFIG


def test_price_import_review_and_effective_cost():
    from backend.infra.llm.pricing import PostgresPriceRepository

    version = f"test-price-{uuid.uuid4().hex}"
    model = f"test-model-{uuid.uuid4().hex}"
    repo = PostgresPriceRepository()
    now = datetime.now(timezone.utc)
    rows = [
        {
            "model_name": model,
            "component": "llm",
            "dimension": "input",
            "price_per_unit": "1.000000",
        },
        {
            "model_name": model,
            "component": "llm",
            "dimension": "output",
            "price_per_unit": "2.000000",
        },
    ]

    assert repo.import_pending(
        rows, price_table_version=version, source="test-contract", effective_from=now
    ) == 2
    assert repo.approve_version(
        version, reviewer_1="reviewer-a", reviewer_2="reviewer-b"
    ) == 2
    table = repo.get_current(model, "llm")
    assert table.calculate_cost(
        model, "llm", {"input": 1000, "output": 500}
    ) == Decimal("0.002000")

    with pytest.raises(psycopg2.Error):
        with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE model_price SET price_per_unit = 9 WHERE price_table_version = %s",
                    (version,),
                )


def test_price_governance_requires_two_reviewers_and_24h_canary():
    from backend.infra.llm.price_governance import PostgresPriceGovernanceRepository

    version = f"test-governance-{uuid.uuid4().hex}"
    model = f"test-governance-model-{uuid.uuid4().hex}"
    repo = PostgresPriceGovernanceRepository()
    now = datetime.now(timezone.utc)
    rows = [
        {
            "model_name": model,
            "component": "llm",
            "dimension": "input",
            "price_per_unit": "1.000000",
            "unit": "per_1m_tokens",
            "currency": "USD",
        },
        {
            "model_name": model,
            "component": "llm",
            "dimension": "output",
            "price_per_unit": "2.000000",
            "unit": "per_1m_tokens",
            "currency": "USD",
        },
    ]

    imported = repo.import_version(
        rows,
        version=version,
        source="test-governance",
        imported_by="user:importer",
        effective_from=now,
    )
    assert imported["status"] == "pending"
    assert imported["coverage_ratio"] == 1.0

    with pytest.raises(ValueError, match="导入人不能审核"):
        repo.review_version(
            version, reviewer="user:importer", decision="approve"
        )

    assert repo.review_version(
        version, reviewer="user:reviewer-a", decision="approve"
    )["status"] == "reviewed_1"
    with pytest.raises(ValueError, match="同一审核人不能重复审核"):
        repo.review_version(
            version, reviewer="user:reviewer-a", decision="approve"
        )
    assert repo.review_version(
        version, reviewer="user:reviewer-b", decision="approve"
    )["status"] == "scheduled"

    assert repo.canary_version(version, action="start", now=now)["status"] == "canary"
    with pytest.raises(ValueError, match="灰度满 24 小时"):
        repo.canary_version(
            version, action="complete", now=now + timedelta(hours=23)
        )
    activated = repo.canary_version(
        version, action="complete", now=now + timedelta(hours=24, seconds=1)
    )
    assert activated == {"version": version, "status": "active", "row_count": 2}

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*), MIN(reviewer_1), MIN(reviewer_2)
                   FROM model_price
                   WHERE price_table_version = %s
                     AND approval_status = 'approved'""",
                (version,),
            )
            assert cur.fetchone() == (2, "user:reviewer-a", "user:reviewer-b")
