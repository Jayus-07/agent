"""cs-workstation-v2 P3：handoff SLA 分级扫描（warning/breach）回归。

真库集成：种 waiting_human handoff（created_at 控制进度），跑
scan_handoff_timeouts 断言分级、DB sla_level 只升不降、breach 兜底关闭、
Redis SETNX 幂等（同级别窗口内不重复告警）。p3t- 前缀行用后即清。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
from backend.customer_service.maintenance import scan_handoff_timeouts

TENANT = "tenant-p3t-sla"
_PREFIX = "p3t-sla-"


def _pg():
    # 统一走 config 层连接配置（与 test_p5_stopcs_a_realpg 同口径）：
    # 自行 open(".env") 解析在 CI 无 .env 文件时 FileNotFoundError
    # （v11 run 41245691574 实证 2 errors）；workflow env / 本地 .env
    # 两端都由 MEMORY_DB_CONFIG 消化。
    from backend.config.database import MEMORY_DB_CONFIG

    return psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2)


def _redis():
    try:
        from backend.infra.redis.client import get_redis

        return get_redis()
    except Exception:
        return None


def _cleanup():
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM customer_service.events WHERE conversation_id LIKE %s",
            (_PREFIX + "%",),
        )
        cur.execute(
            "DELETE FROM customer_service.handoffs WHERE handoff_id LIKE %s",
            (_PREFIX + "%",),
        )
        cur.execute(
            "DELETE FROM customer_service.conversations WHERE conversation_id LIKE %s",
            (_PREFIX + "%",),
        )
        conn.commit()
    client = _redis()
    if client is not None:
        try:
            for key in client.scan_iter("cs:sla:p3t-sla-*"):
                client.delete(key)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _quiet_hub(monkeypatch):
    """广播静默（loop 生命周期），SLA 幂等闸 mark_sla_alert_once 保留真实现。"""
    from types import SimpleNamespace

    import backend.customer_service.realtime as realtime_mod

    real = realtime_mod.get_agent_hub()
    stub = SimpleNamespace(
        mark_sla_alert_once=real.mark_sla_alert_once,
        publish=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(realtime_mod, "get_agent_hub", lambda: stub)


@pytest.fixture
def env():
    _cleanup()
    yield
    _cleanup()


def _seed(conversation_id: str, *, state: str, age_seconds: float) -> str:
    handoff_id = _PREFIX + uuid.uuid4().hex[:12]
    created_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.conversations"
            " (conversation_id, user_id, tenant_id, conversation_status,"
            "  handling_mode, channel)"
            " VALUES (%s, 'user-sla', %s, 'open', 'ai', 'web')"
            " ON CONFLICT (conversation_id) DO NOTHING",
            (conversation_id, TENANT),
        )
        cur.execute(
            "INSERT INTO customer_service.handoffs"
            " (handoff_id, conversation_id, user_id, tenant_id, handoff_state,"
            "  trigger_type, priority, assignment_version, attempt_count,"
            "  created_at, total_deadline_at)"
            " VALUES (%s, %s, 'user-sla', %s, %s, 'manual', 50, 0, 0, %s,"
            "  %s + make_interval(secs => %s))",
            (handoff_id, conversation_id, TENANT, state, created_at,
             created_at, float(CS_HANDOFF_TIMEOUT_SECONDS)),
        )
        conn.commit()
    return handoff_id


def _handoff_row(handoff_id: str):
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT handoff_state, sla_level FROM customer_service.handoffs"
            " WHERE handoff_id = %s",
            (handoff_id,),
        )
        return cur.fetchone()


def test_sla_warning_alert_idempotent(env):
    """P3 分级职责：warning（50% 窗口）由 maintenance 扫描发出（幂等）。

    breach（100% 到点）由 reaper 关闭路径发出（_close_handoff 置
    sla_level=breach + sla.breach 事件，见 test_reaper_*）；本扫描对超时
    行仅保留 close_stale 兜底。主栈 reaper 与本测试并行，超时行的关闭
    来源不定（reaper 或 close_stale），故只断言最终 closed。
    """
    timeout = CS_HANDOFF_TIMEOUT_SECONDS
    conv_w = _PREFIX + "conv-w"
    conv_b = _PREFIX + "conv-b"
    handoff_w = _seed(conv_w, state="waiting_human", age_seconds=timeout * 0.6)
    handoff_b = _seed(conv_b, state="waiting_human", age_seconds=timeout * 1.5)

    result = scan_handoff_timeouts()

    assert result["ok"] is True
    alerts = {a["handoff_id"]: a["level"] for a in result["alerts"]}
    # 60% 窗口 → warning（且 breach 行已过窗口，不再由扫描告警）
    assert alerts.get(handoff_w) == "warning"
    assert all(a["level"] == "warning" for a in result["alerts"])
    assert _handoff_row(handoff_w)[1] == "warning"
    # 超时行最终 closed（来源：reaper 或 close_stale 兜底）
    assert _handoff_row(handoff_b)[0] == "closed"

    # 幂等：DB 层 sla_level 已置 warning → 重扫不再产生该告警
    result2 = scan_handoff_timeouts()
    assert all(a["handoff_id"] != handoff_w for a in result2["alerts"])


def test_sla_level_only_escalates(env):
    timeout = CS_HANDOFF_TIMEOUT_SECONDS
    conv = _PREFIX + "conv-esc"
    handoff_id = _seed(conv, state="waiting_human", age_seconds=timeout * 0.6)

    first = scan_handoff_timeouts()
    assert any(a["handoff_id"] == handoff_id for a in first["alerts"])

    # DB 置回 NULL 但 Redis 幂等键仍在 → SETNX 命中，不再告警（不重置级别）
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE customer_service.handoffs SET sla_level = NULL"
            " WHERE handoff_id = %s",
            (handoff_id,),
        )
        conn.commit()
    second = scan_handoff_timeouts()
    assert all(a["handoff_id"] != handoff_id for a in second["alerts"])
    assert _handoff_row(handoff_id)[1] is None
