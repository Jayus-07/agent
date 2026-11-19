"""零售 ERP 试点 schema、租户策略与 20 条候选问数的离线契约。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.evaluation.dataset import load_dataset_file
from backend.evaluation.preflight import run_schema_preflight
from backend.sql import audit as audit_module
from backend.sql.policy import (
    SQLPolicyError,
    SQLPolicyGuard,
    SQL_SCOPE_UNAVAILABLE,
    SQL_TABLE_NOT_ALLOWED,
)
from backend.sql.schema_loader import schema_loader
from backend.sql.sql_validator import sql_validator
from tests.sql.conftest import make_ctx
from scripts.init_db import RETAIL_ERP_PILOT_SEED


DATASET = "sql_v6/cases.jsonl"
ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "sql" / "seeds" / RETAIL_ERP_PILOT_SEED


@pytest.fixture(autouse=True)
def _disable_external_sql_audit(monkeypatch):
    def _no_database_connection():
        raise RuntimeError("SQL audit database disabled in offline tests")

    monkeypatch.setattr(audit_module, "_conn", _no_database_connection)


def test_retail_schema_has_explicit_tenant_and_internal_policy():
    tables = schema_loader.get_tables_in_schema("retail")

    assert len(tables) == 21
    for table_name in tables:
        policy = schema_loader.get_table_policy(table_name)
        assert policy.tenant_column == "tenant_id", table_name
        assert policy.data_domain == "internal", table_name


def test_retail_sql_injects_tenant_even_for_all_scope():
    context = make_ctx("all", tenant_id="default")

    guarded = SQLPolicyGuard().validate_and_rewrite(
        "SELECT sku FROM retail.products", context
    )

    assert "tenant_id = %(sql_scope_tenant_id)s" in guarded.executable_sql
    assert guarded.params == {"sql_scope_tenant_id": "default"}
    assert "tenant" in guarded.applied_scopes


def test_retail_sql_fails_closed_without_tenant_or_all_scope():
    with pytest.raises(SQLPolicyError) as missing_tenant:
        SQLPolicyGuard().validate_and_rewrite(
            "SELECT sku FROM retail.products",
            make_ctx("all", tenant_id=""),
        )
    assert missing_tenant.value.code == SQL_SCOPE_UNAVAILABLE

    with pytest.raises(SQLPolicyError) as staff_scope:
        SQLPolicyGuard().validate_and_rewrite(
            "SELECT sku FROM retail.products",
            make_ctx("department", department="store", tenant_id="default"),
        )
    assert staff_scope.value.code == SQL_TABLE_NOT_ALLOWED


def test_retail_candidate_has_twenty_valid_sql_questions():
    cases = [
        case for case in load_dataset_file(DATASET, default_module="sql")
        if case.id.startswith("RE-")
    ]
    preflight = run_schema_preflight(["sql"], dataset_path=DATASET)

    assert len(cases) == 20
    assert len({case.id for case in cases}) == 20
    assert all(case.module == "sql" for case in cases)
    assert preflight.passed, preflight.render()

    for case in cases:
        _, actual_tables, _ = sql_validator.validate(case.expected["gold_sql"])
        assert actual_tables == set(case.expected["expected_tables"]), case.id
        assert case.expected["should_reject"] is False


def test_retail_seed_covers_candidate_tables_without_deleting_existing_data():
    """试点种子必须覆盖 20 条候选用到的表，并保持可重复、非破坏性。"""

    cases = [
        json.loads(line)
        for line in (ROOT / "evaluation" / "datasets" / DATASET).read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    expected_tables = {
        table
        for case in cases
        if case["id"].startswith("RE-")
        for table in case["expected"].get("expected_tables", [])
    }
    sql = SEED.read_text(encoding="utf-8")
    seeded_tables = {
        table.lower()
        for table in re.findall(r"INSERT\s+INTO\s+(retail\.[a-z_]+)", sql, re.I)
    }

    assert len([case for case in cases if case["id"].startswith("RE-")]) == 20
    assert expected_tables <= seeded_tables
    assert "STORE-PILOT-001" in sql
    assert "'default'" in sql
    assert "ON CONFLICT" in sql
    assert not re.search(r"\b(?:DELETE|DROP|TRUNCATE)\s", sql, re.I)
