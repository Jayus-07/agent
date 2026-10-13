from __future__ import annotations

import os
import re

from backend.config.database import MEMORY_DB_CONFIG

_TEST_INSTANCE_RE = re.compile(r"^[a-f0-9]{12}$")


def assert_isolated_travel_postgres() -> None:
    """在任何测试连接前拒绝非临时、非专用的数据库配置。"""
    if os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1":
        raise RuntimeError("TRAVEL_V2_TEST_POSTGRES must explicitly enable the isolated DB")
    instance_id = os.getenv("TRAVEL_V2_TEST_INSTANCE", "")
    if not _TEST_INSTANCE_RE.fullmatch(instance_id):
        raise RuntimeError("travel V2 tests require a generated isolation instance ID")
    expected_name = f"travel_v2_test_{instance_id}"
    expected_port = os.getenv("TRAVEL_V2_TEST_PORT", "")
    if not expected_port.isdigit() or not 1024 <= int(expected_port) <= 65535:
        raise RuntimeError("travel V2 tests require an explicit dynamic loopback port")
    if MEMORY_DB_CONFIG["host"] not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("travel V2 tests may connect only to loopback PostgreSQL")
    if MEMORY_DB_CONFIG["port"] != int(expected_port):
        raise RuntimeError("PostgreSQL port does not match the isolated test instance")
    if MEMORY_DB_CONFIG["dbname"] != expected_name:
        raise RuntimeError("travel V2 tests require their unique temporary database")
    if MEMORY_DB_CONFIG["user"] != expected_name:
        raise RuntimeError("travel V2 tests require their unique temporary database user")
    if not MEMORY_DB_CONFIG["password"]:
        raise RuntimeError("travel V2 tests require a non-empty temporary test credential")
