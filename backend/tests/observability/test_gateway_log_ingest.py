"""test_gateway_log_ingest.py — 网关访问日志消费链路契约

覆盖（backend/observability/gateway_log_ingest.py 纯函数部分 + 查询 API 参数处理）：
  - Stream 条目解析：正常行、JSON 损坏丢弃、时间非法丢弃、超长字段截断
  - 时间语义：ngx.utctime 的无时区字符串必须按 UTC 解释（不然 TIMESTAMPTZ 按会话时区漂移）
  - 查询端点：PG 不可达/表缺失 → available=false 降级（不抛 500）

Redis XREAD / psycopg2 落库是外部边界，由端到端验证覆盖（网关真请求 → 入库 → API 查询），
不在单测范围。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.observability.gateway_log_ingest import _parse_ts, _row_from_entry


def _fields(payload: dict) -> dict:
    import json
    return {"data": json.dumps(payload, ensure_ascii=False)}


# ── 时间语义 ──

def test_parse_ts_naive_string_treated_as_utc():
    """lua ngx.utctime() 无时区后缀 → 必须挂 UTC，而非本地时区解释。"""
    dt = _parse_ts("2026-09-15 22:17:42")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 22  # 原样保留（不发生本地时区偏移）


def test_parse_ts_invalid_returns_none():
    assert _parse_ts("") is None
    assert _parse_ts("not-a-time") is None


# ── Stream 条目解析 ──

def test_row_from_entry_happy_path():
    row = _row_from_entry("1715-1-1", _fields({
        "time": "2026-09-15 22:17:42", "client_ip": "172.21.0.1",
        "user_id": "1001", "auth_type": "jwt", "trace_id": "t-1",
        "method": "GET", "uri": "/api/chat/stream", "query": "session_id=s1",
        "status": 200, "bytes": 555, "duration_ms": 35.2, "ua": "curl/8.19",
    }))
    assert row is not None
    assert row[0] == "1715-1-1"
    assert row[1] == datetime(2026, 9, 15, 22, 17, 42, tzinfo=timezone.utc)
    assert row[2] == "172.21.0.1"
    assert row[3] == "1001"
    assert row[9] == 200
    assert row[11] == 35.2


def test_row_from_entry_corrupted_json_dropped():
    assert _row_from_entry("e-1", {"data": "{not-json"}) is None
    assert _row_from_entry("e-2", {}) is None


def test_row_from_entry_bad_time_dropped():
    assert _row_from_entry("e-3", _fields({"status": 200})) is None


def test_row_from_entry_truncates_hostile_fields():
    """超长 uri/ua 截断到防御性上限，不让 pathological 值撑爆行。"""
    row = _row_from_entry("e-4", _fields({
        "time": "2026-09-15 22:17:42",
        "uri": "/" + "a" * 5000,
        "ua": "x" * 1000,
        "status": 404,
    }))
    assert row is not None
    assert len(row[7]) == 2048   # uri
    assert len(row[12]) == 512   # ua
    assert row[9] == 404


# ── 查询端点降级 ──

def test_query_endpoint_degrades_when_datasource_unavailable(monkeypatch):
    """PG 不可达时端点返回 available=false，而不是 500（表缺失走同一降级分支）。"""
    import asyncio

    from backend.app.api.routes import observability as obs

    class _BrokenSession:
        async def __aenter__(self):
            raise OSError("connection refused")

        async def __aexit__(self, *a):
            return False

    def _fake_session_local():
        # AsyncSessionLocal 是同步工厂（sessionmaker），调用即返回 session 实例；
        # 不能写成 async def——async with 拿到的是 coroutine，会报 __aenter__ 缺失
        return _BrokenSession()

    monkeypatch.setattr(obs, "AsyncSessionLocal", _fake_session_local)
    result = asyncio.run(obs.get_gateway_access_logs(hours=6))
    assert result["available"] is False
