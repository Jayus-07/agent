"""Q8 价格表导入、双人审核、生效和追加式约束集成回归。"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import pytest

from backend.config.database import MEMORY_DB_CONFIG


def test_price_import_review_and_effective_cost():
    from backend.infra.llm.price_governance import PostgresPriceGovernanceRepository
    from backend.infra.llm.pricing import MissingModelPrice, PostgresPriceRepository

    now = datetime.now(timezone.utc)
    governance = PostgresPriceGovernanceRepository()
    version_v1 = f"test-price-v1-{uuid.uuid4().hex}"
    version_v2 = f"test-price-v2-{uuid.uuid4().hex}"
    model = f"test-model-{uuid.uuid4().hex}"
    repo = PostgresPriceRepository()

    def activate_version(version, input_price, output_price):
        rows = [
            {
                "model_name": model,
                "component": "llm",
                "dimension": "input",
                "price_per_unit": input_price,
                "unit": "per_1m_tokens",
                "currency": "USD",
            },
            {
                "model_name": model,
                "component": "llm",
                "dimension": "output",
                "price_per_unit": output_price,
                "unit": "per_1m_tokens",
                "currency": "USD",
            },
        ]
        imported_at = now - timedelta(days=2)
        governance.import_version(
            rows, version=version, source="test-contract",
            imported_by=f"importer-{version}", effective_from=imported_at,
        )
        governance.review_version(
            version, reviewer=f"reviewer-a-{version}", decision="approve"
        )
        governance.review_version(
            version, reviewer=f"reviewer-b-{version}", decision="approve"
        )
        canary_start = now - timedelta(hours=26)
        governance.canary_version(version, action="start", now=canary_start)
        governance.canary_version(
            version, action="complete", now=now,
        )

    activate_version(version_v1, "1.000000", "2.000000")
    activate_version(version_v2, "3.000000", "4.000000")
    table = repo.get_current(model, "llm")
    assert table.calculate_cost(
        model, "llm", {"input": 1000, "output": 500}
    ) == Decimal("0.005000")
    assert {
        line.price_table_version for line in table.require(model, "llm", enforce=True).values()
    } == {version_v2}

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM model_price
                   WHERE model_name = %s AND approval_status = 'approved'
                     AND price_table_version = %s""",
                (model, version_v1),
            )
            assert cur.fetchone()[0] == 2

    missing_model = f"test-model-no-active-{uuid.uuid4().hex}"
    with pytest.raises(MissingModelPrice, match="缺少已生效价格"):
        repo.get_current(missing_model, "llm").require(
            missing_model, "llm", enforce=True,
        )

    with pytest.raises(psycopg2.Error):
        with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE model_price SET price_per_unit = 9 WHERE price_table_version = %s",
                    (version_v2,),
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
