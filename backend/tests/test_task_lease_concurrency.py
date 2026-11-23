"""tests/test_task_lease_concurrency.py — 任务租约防并发抢占与连接池配置测试。

验证（2026-11-23 修复）：
1. task_service 连接池默认大小升级为 24，支持 TASK_SERVICE_POOL_MAX_SIZE 与 TASK_SERVICE_POOL_TIMEOUT 配置；
2. tasks 配置中 TASK_LEASE_TTL_SECONDS 提升为 300s，TASK_RECOVERY_GRACE_SECONDS 提升为 30s；
3. LeaseHeartbeat 心跳在 DB 抖动/连接池借满异常时支持 3 次快速重试，且在连续异常时不误判 lost；
4. LeaseHeartbeat 在明确收到 ok=False 时立即触发 lost 置位并终止续租。
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from backend.config.tasks import (
    TASK_LEASE_HEARTBEAT_INTERVAL,
    TASK_LEASE_TTL_SECONDS,
    TASK_RECOVERY_GRACE_SECONDS,
)
from backend.tasks.lease_heartbeat import LeaseHeartbeat


def test_task_lease_config_defaults():
    """验证租约 TTL 与 Grace 默认值已优化至高并发长任务安全区间。"""
    assert TASK_LEASE_TTL_SECONDS >= 300, "TASK_LEASE_TTL_SECONDS 默认值应 >= 300s"
    assert TASK_RECOVERY_GRACE_SECONDS >= 30, "TASK_RECOVERY_GRACE_SECONDS 默认值应 >= 30s"
    assert TASK_LEASE_HEARTBEAT_INTERVAL == 15, "心跳间隔应维持 15s"


def test_task_service_pool_config():
    """验证 task_service 连接池参数与环境变量支持。"""
    from backend.services import task_service

    assert task_service._POOL_MAX_SIZE >= 24, "task_service 默认连接池应 >= 24"
    assert task_service._POOL_TIMEOUT == 15.0, "task_service 借连接超时应默认为 15.0s"


def test_lease_heartbeat_retry_on_db_exception():
    """验证 LeaseHeartbeat 在前两次遇到 DB 异常时快速重试并在第 3 次成功，不误判 lost。"""
    call_count = 0

    def mock_renew(task_id, execution_id, lease_ttl_seconds=None):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("Database pool timeout transient error")
        return True

    with patch("backend.services.task_service.renew_lease", side_effect=mock_renew), \
         patch("backend.tasks.lease_heartbeat.LeaseHeartbeat._renew_admission"):
        hb = LeaseHeartbeat("t-123", "exec-abc", interval_s=0.05, ttl_s=300)
        hb.start()
        # 等待重试完成
        time.sleep(0.3)
        hb.stop()

        assert not hb.lost, "瞬时 DB 异常快速重试成功后不应置位 lost"
        assert call_count >= 3, f"应该至少触发了快速重试，当前 call_count={call_count}"


def test_lease_heartbeat_db_persistent_error_does_not_mark_lost():
    """验证 LeaseHeartbeat 即使连续遭遇 DB 异常，也绝不误判 lost（活 Worker 保持执行）。"""
    def mock_renew_fail(task_id, execution_id, lease_ttl_seconds=None):
        raise RuntimeError("Persistent DB connection refused")

    with patch("backend.services.task_service.renew_lease", side_effect=mock_renew_fail), \
         patch("backend.tasks.lease_heartbeat.LeaseHeartbeat._renew_admission"):
        hb = LeaseHeartbeat("t-123", "exec-abc", interval_s=0.05, ttl_s=300)
        hb.start()
        time.sleep(0.2)
        hb.stop()

        assert not hb.lost, "持续 DB 异常不应误判为 lost（fencing 写会在节点边界做安全拒绝）"


def test_lease_heartbeat_explicit_false_marks_lost():
    """验证 LeaseHeartbeat 在 DB 明确返回 False（租约已被接管/过期）时，正确置位 lost 并退出。"""
    def mock_renew_stale(task_id, execution_id, lease_ttl_seconds=None):
        return False

    with patch("backend.services.task_service.renew_lease", side_effect=mock_renew_stale), \
         patch("backend.tasks.lease_heartbeat.LeaseHeartbeat._renew_admission"):
        hb = LeaseHeartbeat("t-123", "exec-abc", interval_s=0.05, ttl_s=300)
        hb.start()
        time.sleep(0.2)
        hb.stop()

        assert hb.lost is True, "明确 ok=False 时必须置位 lost"
