"""tests/travel/booking/conftest.py — Booking 测试隔离（STOP L8）

约定与 test_email_durable_idempotency 一致：**真 PG**（MEMORY_DB_CONFIG，
开发库 5433）+ 测试自建/自清表；PG 不可达 → skip（不假绿）。

隔离面：
  - travel.booking_* 四表：按 migration 052 DDL 建表（幂等）+ 每用例 TRUNCATE；
  - 幂等 ledger：独立测试表 travel.bt_ledger_test（与共享 ai.idempotency_records
    零交集），用例后 DROP；
  - config 开关 monkeypatch 模块属性（config import 时固化，测 env 无效）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS travel.bt_ledger_test (
    tenant_id varchar(64) NOT NULL,
    actor_id varchar(64) NOT NULL,
    operation varchar(64) NOT NULL,
    client_key varchar(128) NOT NULL,
    request_hash character(64) NOT NULL,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    result jsonb,
    error_code text,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_id uuid,
    lease_expires_at timestamptz,
    expires_at timestamptz,
    owner_execution_id varchar(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, actor_id, operation, client_key)
)
"""

_LEDGER_TABLE = "travel.bt_ledger_test"
_MIGRATION = Path(__file__).resolve().parents[3] / "sql" / "migrations" / "052_travel_booking.sql"

# 跨 xdist worker 的 booking 串行锁（会话级 advisory lock）。
# booking 四表在共享 travel schema（store._SCHEMA 硬编码）且 ledger 表名固定，
# 无 worker 后缀可用——worker B 的 setup TRUNCATE / teardown DROP 会清掉
# worker A 用例正在写的数据（xdist 下 in_doubt/幂等判定异常实证）。
# 用例执行期由本 fixture 持锁，其余 worker 的 setup 抢不到锁即阻塞等待，
# 互踩窗口（TRUNCATE vs 用例 SQL）被完全消除；会话断开锁自动释放。
_LOCK_KEY = "ci_travel_booking_tests"


def _factory():
    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    import psycopg

    return psycopg.connect(
        host=c["host"], port=c["port"], user=c["user"],
        password=c["password"], dbname=c["dbname"], connect_timeout=5)


@pytest.fixture()
def booking_env(monkeypatch):
    """跨 worker 串行 + 建表 + TRUNCATE + config 打桩；PG 不可达 skip。"""
    pytest.importorskip("psycopg")
    try:
        lock_conn = _factory()
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (_LOCK_KEY,))
        with _factory() as conn, conn.cursor() as cur:
            cur.execute(_MIGRATION.read_text(encoding="utf-8"))
            cur.execute(_LEDGER_DDL)
            cur.execute(
                "TRUNCATE travel.booking_orders, travel.booking_quotes, "
                "travel.booking_webhook_inbox, travel.booking_events RESTART IDENTITY CASCADE")
            cur.execute("DELETE FROM travel.bt_ledger_test")
            conn.commit()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PG 不可达，跳过 booking 测试: {e}")

    from backend.config import travel_booking as cfg

    monkeypatch.setattr(cfg, "TRAVEL_BOOKING_ENABLED", True)
    monkeypatch.setattr(cfg, "TRAVEL_BOOKING_PROVIDER", "fake_booking_native")
    monkeypatch.setattr(cfg, "TRAVEL_BOOKING_QUOTE_TTL_SECONDS", 900)
    # booking 的 Quote/复核复用 commerce 确定性搜索 → commerce 需 fake 模式
    from backend.config import travel_commerce as ccfg

    monkeypatch.setattr(ccfg, "TRAVEL_COMMERCE_ENABLED", True)
    monkeypatch.setattr(ccfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "fake")
    monkeypatch.setattr(
        ccfg, "TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS",
        ("fake-commerce.example.com",))
    yield cfg

    try:
        with _factory() as conn, conn.cursor() as cur:
            cur.execute(
                "TRUNCATE travel.booking_orders, travel.booking_quotes, "
                "travel.booking_webhook_inbox, travel.booking_events RESTART IDENTITY CASCADE")
            cur.execute("DROP TABLE IF EXISTS travel.bt_ledger_test")
            conn.commit()
    finally:
        # 会话级锁须在持锁连接上释放；连接已断时锁随会话自动消失
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (_LOCK_KEY,))
            lock_conn.close()
        except Exception:  # noqa: BLE001 — 锁随会话终止兜底
            pass


@pytest.fixture()
def native_factory(booking_env):
    """Model A（native idempotency）provider+contract 工厂。"""
    from backend.providers.travel.booking.fake import FakeTravelBookingProvider
    from backend.shared.provider_idempotency import get_provider_contract

    provider = FakeTravelBookingProvider(profile="native")

    def _factory(scenario: str = "success"):
        provider.scenario = scenario
        return provider, get_provider_contract(provider.name)

    return _factory


@pytest.fixture()
def clientref_factory(booking_env):
    """Model B（merchant ref + lookup）。"""
    from backend.providers.travel.booking.fake import FakeTravelBookingProvider
    from backend.shared.provider_idempotency import get_provider_contract

    provider = FakeTravelBookingProvider(profile="clientref")

    def _factory(scenario: str = "success"):
        provider.scenario = scenario
        return provider, get_provider_contract(provider.name)

    return _factory


@pytest.fixture()
def bare_factory(booking_env):
    """Model C（两者皆无）。"""
    from backend.providers.travel.booking.fake import FakeTravelBookingProvider
    from backend.shared.provider_idempotency import get_provider_contract

    provider = FakeTravelBookingProvider(profile="bare")

    def _factory(scenario: str = "success"):
        provider.scenario = scenario
        return provider, get_provider_contract(provider.name)

    return _factory


@pytest.fixture()
def booking_service(booking_env, native_factory):
    """预装配的 BookingService（Model A；独立 ledger 表）。"""
    from backend.travel.booking.service import BookingService
    from backend.travel.booking.store import BookingStore

    return BookingService(
        store=BookingStore(_factory),
        provider_factory=native_factory,
        ledger_table=_LEDGER_TABLE,
        conn_factory=_factory,
    )
