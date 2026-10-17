"""价格变化告警在隔离 PostgreSQL 中的崩溃恢复契约。"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg2
import pytest
from celery.exceptions import SoftTimeLimitExceeded

from backend.infra.llm.price_page_monitor import (
    PostgresPricePageMonitorRepository,
    PriceQuote,
)


def _replace_identifiers(sql: str, prefix: str) -> str:
    names = (
        "model_price_source_observations",
        "model_price_monitor_runs",
        "model_price_change_alert_events",
        "idx_model_price_source_observations_latest",
        "idx_model_price_monitor_runs_started",
        "idx_model_price_monitor_runs_celery_task",
        "idx_model_price_change_alert_events_pending",
        "model_price_monitor_runs_status_check",
    )
    for name in sorted(names, key=len, reverse=True):
        sql = re.sub(rf"\b{re.escape(name)}\b", prefix + name, sql)
    return sql


@pytest.fixture
def isolated_price_monitor_db(monkeypatch):
    dsn = os.getenv("PRICE_MONITOR_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("需显式指定隔离库 PRICE_MONITOR_TEST_DATABASE_URL")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("价格巡检集成测试只允许连接本机 PostgreSQL")
    if not re.search(r"_(?:test|testing|eval|evaluation)$", database):
        pytest.fail("价格巡检集成测试数据库名必须以 _test 或 _eval 结尾")

    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_database(), current_user, pg_is_in_recovery()"
            )
            target_database, _target_role, is_replica = cur.fetchone()
            assert target_database == database
            assert not is_replica, "目标必须为隔离测试主库"

    prefix = f"pm_{uuid4().hex[:10]}_"
    monkeypatch.setenv("BUDGET_PG_TABLE_PREFIX", prefix)

    @contextmanager
    def connect():
        conn = psycopg2.connect(dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    root = Path(__file__).resolve().parents[2]
    migrations = root / "backend" / "sql" / "migrations"
    with connect() as conn:
        with conn.cursor() as cur:
            for migration in (
                "099_model_price_source_monitor.sql",
                "100_model_price_change_alert_outbox.sql",
            ):
                source = (migrations / migration).read_text(encoding="utf-8")
                cur.execute(_replace_identifiers(source, prefix))
            cur.execute(
                f"""CREATE TABLE {prefix}model_price_versions (
                    version TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL
                )"""
            )
            cur.execute(
                f"""CREATE TABLE {prefix}model_price (
                    id BIGSERIAL PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    component TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    price_per_unit NUMERIC(18, 9) NOT NULL,
                    unit TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    price_table_version TEXT NOT NULL,
                    source TEXT NOT NULL,
                    effective_from TIMESTAMPTZ NOT NULL,
                    effective_to TIMESTAMPTZ,
                    approval_status TEXT NOT NULL
                )"""
            )

    yield connect, prefix

    with connect() as conn:
        with conn.cursor() as cur:
            for table in (
                "model_price",
                "model_price_versions",
                "model_price_change_alert_events",
                "model_price_monitor_runs",
                "model_price_source_observations",
            ):
                cur.execute(f"DROP TABLE IF EXISTS {prefix}{table} CASCADE")


def _quote(price: str, *, source: str, model: str, component: str,
           dimension: str, url: str) -> PriceQuote:
    from decimal import Decimal

    return PriceQuote(
        source_id=source,
        model_name=model,
        component=component,
        dimension=dimension,
        price_per_unit=Decimal(price),
        unit="per_1m_tokens",
        currency="CNY",
        source_url=url,
        source_updated_at="2026-10-15T00:00:00Z",
        content_sha256="a" * 64,
    )


def test_price_change_outbox_recovers_after_worker_failure(
    isolated_price_monitor_db, monkeypatch,
):
    connect, prefix = isolated_price_monitor_db
    repository = PostgresPricePageMonitorRepository(connection_factory=connect)
    alert_table = f"{prefix}model_price_change_alert_events"
    run_table = f"{prefix}model_price_monitor_runs"
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    yesterday = today - timedelta(days=1)
    source = "aliyun_qwen3_7_text_rerank"
    url = "https://help.aliyun.com/en/model-studio/qwen3-7-text-rerank"
    prior_quote = _quote(
        "0.5", source=source, model="qwen3.7-text-rerank",
        component="rerank", dimension="input", url=url,
    )
    rerank_quote = _quote(
        "0.6", source=source, model="qwen3.7-text-rerank",
        component="rerank", dimension="input", url=url,
    )
    quotes = [
        rerank_quote,
        _quote(
            "0.5", source="aliyun_qwen3_7_text_embedding",
            model="qwen3.7-text-embedding", component="embedding",
            dimension="input", url="https://help.aliyun.com/en/model-studio/embedding",
        ),
        _quote(
            "0.2", source="volcengine_ark_doubao_seed_2_0_mini",
            model="doubao-seed-2.0-mini", component="llm", dimension="input",
            url="https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing",
        ),
    ]
    repository.record_and_compare([prior_quote], observed_on=yesterday)

    from backend.tasks import model_price_monitor_tasks as task_module

    monkeypatch.setattr(
        task_module, "PostgresPricePageMonitorRepository", lambda: repository,
    )
    monkeypatch.setattr(task_module, "fetch_official_prices", lambda: (quotes, []))
    alerts = []
    alert_event_ids = []

    def fail_once_on_price_change(alert):
        alerts.append(alert.code)
        if alert.code == "MODEL_PRICE_SOURCE_CHANGED":
            alert_event_ids.append(alert.detail["event_ids"])
        if alert.code == "MODEL_PRICE_SOURCE_CHANGED" and alerts.count(
            "MODEL_PRICE_SOURCE_CHANGED"
        ) == 1:
            concurrent = repository.record_and_compare(
                quotes, observed_on=today,
            )
            assert concurrent.source_changes == []
            raise RuntimeError("worker exited after observation commit")

    monkeypatch.setattr(
        "backend.observability.alerts.log_degradation", fail_once_on_price_change,
    )

    with pytest.raises(task_module.ModelPriceMonitorExecutionFailed):
        task_module.monitor_model_price_pages()

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT event_id, alert_status, delivery_status "
                f"FROM {alert_table} "
                "WHERE source_id = %s AND observed_on = %s",
                (source, today),
            )
            pending_event = cur.fetchone()
            assert pending_event is not None
            event_id, alert_status, delivery_status = pending_event
            assert alert_status == "pending"
            assert delivery_status == "unknown"
            cur.execute(
                f"SELECT status FROM {run_table} ORDER BY started_at DESC LIMIT 1"
            )
            assert cur.fetchone()[0] == "failed"

    recovered = task_module.monitor_model_price_pages()
    assert recovered["status"] == "succeeded"
    assert recovered["source_changes"] == 1

    repeated = task_module.monitor_model_price_pages()
    assert repeated["status"] == "succeeded"
    assert repeated["source_changes"] == 0
    assert alert_event_ids == [[str(event_id)], [str(event_id)]]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*), min(alert_status), min(delivery_status) "
                f"FROM {alert_table} WHERE source_id = %s AND observed_on = %s",
                (source, today),
            )
            assert cur.fetchone() == (1, "triggered", "unknown")
            cur.execute(
                f"SELECT status FROM {run_table} ORDER BY started_at, run_id"
            )
            assert [row[0] for row in cur.fetchall()] == [
                "failed", "succeeded", "succeeded",
            ]


@pytest.mark.parametrize(
    ("exception", "expected_status"),
    [(SoftTimeLimitExceeded, "failed")],
)
def test_interrupted_celery_task_matches_database_terminal_state(
    isolated_price_monitor_db, monkeypatch, exception, expected_status,
):
    connect, prefix = isolated_price_monitor_db
    repository = PostgresPricePageMonitorRepository(connection_factory=connect)
    monkeypatch.setattr(
        "backend.tasks.model_price_monitor_tasks.PostgresPricePageMonitorRepository",
        lambda: repository,
    )

    def interrupt_fetch():
        raise exception("isolated task interruption")

    from backend.tasks import model_price_monitor_tasks as task_module

    monkeypatch.setattr(task_module, "fetch_official_prices", interrupt_fetch)
    with pytest.raises(exception):
        task_module.monitor_model_price_pages()

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT status, error_type FROM {prefix}model_price_monitor_runs "
                "ORDER BY started_at DESC LIMIT 1"
            )
            assert cur.fetchone() == (expected_status, exception.__name__)


@pytest.mark.parametrize(
    ("has_running_row", "terminated", "expected_status"),
    [
        (True, True, "cancelled"),
        (False, False, "cancelled"),
        (True, False, "running"),
    ],
)
def test_celery_revoke_signal_reconciles_monitor_runs(
    isolated_price_monitor_db, monkeypatch, has_running_row, terminated,
    expected_status,
):
    from types import SimpleNamespace

    from celery.signals import task_revoked

    from backend.tasks import model_price_monitor_tasks as task_module

    connect, prefix = isolated_price_monitor_db
    repository = PostgresPricePageMonitorRepository(connection_factory=connect)
    monkeypatch.setattr(
        task_module,
        "PostgresPricePageMonitorRepository",
        lambda: repository,
    )
    task_id = str(uuid4())
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if has_running_row:
        repository.start_run(
            str(uuid4()),
            observed_on=now.date(),
            started_at=now,
            sources_total=3,
            celery_task_id=task_id,
        )

    task_revoked.send(
        sender=task_module.monitor_model_price_pages,
        request=SimpleNamespace(id=task_id),
        terminated=terminated,
        expired=False,
    )
    task_revoked.send(
        sender=task_module.monitor_model_price_pages,
        request=SimpleNamespace(id=task_id),
        terminated=terminated,
        expired=False,
    )

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT status, error_type FROM {prefix}model_price_monitor_runs "
                "WHERE celery_task_id = %s",
                (task_id,),
            )
            assert cur.fetchone() == (
                expected_status,
                "TaskRevokedError" if expected_status == "cancelled" else None,
            )
