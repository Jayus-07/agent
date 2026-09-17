"""test_maintenance.py — P2.4 全局维护扫描测试

覆盖：
- confirmation_repo.expire_stale：原子条件 UPDATE（state='pending' AND expires_at<=now）
- handoff_repo.close_stale：原子条件 UPDATE（states IN + updated_at<cutoff → closed）
- maintenance 两个扫描函数：DB 异常降级（ok=False 不穿透）、正常路径计数
- beat 调度存在性：celery 不可导入的环境下以源码文本断言（本环境无 celery 包）
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =====================================================
# confirmation_repo.expire_stale
# =====================================================

class TestExpireStale:
    def _make_repo(self):
        from backend.customer_service.repository.confirmation_repo import (
            ConfirmationRepository,
        )
        return ConfirmationRepository(MagicMock())

    @pytest.mark.asyncio
    async def test_updates_only_pending_and_expired_rows(self):
        repo = self._make_repo()
        repo._s = AsyncMock()
        repo._s.execute.side_effect = _returning_rows([])

        await repo.expire_stale()

        stmt = repo._s.execute.call_args[0][0]
        where = _compile_where(stmt)
        # 前置条件：仅 pending 且已过期的行可被过期（幂等闸门）
        assert "confirmations.state = %(state_1)s" in where
        assert "confirmations.expires_at <= %(expires_at_1)s" in where

    @pytest.mark.asyncio
    async def test_returns_identifiers(self):
        repo = self._make_repo()
        row = MagicMock(
            confirmation_id="c-1", user_id="u1", conversation_id="conv-1",
        )
        repo._s = AsyncMock()
        repo._s.execute.side_effect = _returning_rows([row])

        expired = await repo.expire_stale()

        assert expired == [
            {"confirmation_id": "c-1", "user_id": "u1", "conversation_id": "conv-1"},
        ]

    @pytest.mark.asyncio
    async def test_sets_expired_state(self):
        from sqlalchemy.dialects import postgresql

        repo = self._make_repo()
        repo._s = AsyncMock()
        repo._s.execute.side_effect = _returning_rows([])

        await repo.expire_stale()

        stmt = repo._s.execute.call_args[0][0]
        compiled = stmt.compile(dialect=postgresql.dialect())
        # 终态写 expired（P1-14 口径：过期不再混入 cancelled）
        assert compiled.params.get("state") == "expired"


# =====================================================
# handoff_repo.close_stale
# =====================================================

class TestCloseStale:
    def _make_repo(self):
        from backend.customer_service.repository.handoff_repo import (
            HandoffRepository,
        )
        return HandoffRepository(MagicMock())

    @pytest.mark.asyncio
    async def test_updates_only_stale_open_handoffs(self):
        from datetime import datetime, timezone

        repo = self._make_repo()
        repo._s = AsyncMock()
        repo._s.execute.side_effect = _returning_rows([])
        cutoff = datetime.now(timezone.utc)

        await repo.close_stale(
            states=["handoff_requested", "waiting_human"], cutoff=cutoff,
        )

        stmt = repo._s.execute.call_args[0][0]
        where = _compile_where(stmt)
        # 前置条件：仅两开放态且早于 cutoff 的行
        assert "handoffs.handoff_state IN" in where
        assert "handoffs.updated_at < %(updated_at_1)s" in where

    @pytest.mark.asyncio
    async def test_sets_closed_with_timestamp(self):
        from sqlalchemy.dialects import postgresql

        repo = self._make_repo()
        repo._s = AsyncMock()
        repo._s.execute.side_effect = _returning_rows([])

        await repo.close_stale(
            states=["waiting_human"],
            cutoff=__import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc),
        )

        stmt = repo._s.execute.call_args[0][0]
        compiled = stmt.compile(dialect=postgresql.dialect())
        assert compiled.params.get("handoff_state") == "closed"

    @pytest.mark.asyncio
    async def test_returns_identifiers(self):
        from datetime import datetime, timezone

        repo = self._make_repo()
        row = MagicMock(
            handoff_id="h-1", user_id="u1",
            conversation_id="conv-1", handoff_state="waiting_human",
        )
        repo._s = AsyncMock()
        repo._s.execute.side_effect = _returning_rows([row])

        closed = await repo.close_stale(
            states=["waiting_human"],
            cutoff=datetime.now(timezone.utc),
        )

        assert closed == [{
            "handoff_id": "h-1", "user_id": "u1",
            "conversation_id": "conv-1", "handoff_state": "waiting_human",
        }]


# =====================================================
# maintenance 扫描函数（异常降级 + 正常路径）
# =====================================================

class TestMaintenanceScans:
    def test_handoff_scan_db_failure_degrades(self):
        """DB 异常 → ok=False，不向上穿透（beat 任务不应崩溃）。

        maintenance 在函数内 from-import run_sync（调用时解析），
        patch 源模块 backend.customer_service._db_loop.run_sync 生效。
        """
        from backend.customer_service import maintenance

        with patch(
            "backend.customer_service._db_loop.run_sync",
            side_effect=RuntimeError("db down"),
        ):
            result = maintenance.scan_handoff_timeouts()

        assert result["ok"] is False
        assert "db down" in result["error"]
        assert result["closed"] == []

    def test_confirmation_scan_db_failure_degrades(self):
        from backend.customer_service import maintenance

        with patch(
            "backend.customer_service._db_loop.run_sync",
            side_effect=RuntimeError("db down"),
        ):
            result = maintenance.scan_confirmation_expiries()

        assert result["ok"] is False
        assert result["expired"] == []

    def test_handoff_scan_normal_path(self):
        from backend.customer_service import maintenance

        with patch(
            "backend.customer_service._db_loop.run_sync",
            return_value=[{
                "handoff_id": "h-1", "user_id": "u1",
                "conversation_id": "conv-1", "handoff_state": "waiting_human",
            }],
        ) as mock_run:
            result = maintenance.scan_handoff_timeouts()

        assert result["ok"] is True
        assert result["count"] == 1
        assert mock_run.called

    def test_confirmation_scan_normal_path(self):
        from backend.customer_service import maintenance

        with patch(
            "backend.customer_service._db_loop.run_sync",
            return_value=[{
                "confirmation_id": "c-1", "user_id": "u1",
                "conversation_id": "conv-1",
            }],
        ):
            result = maintenance.scan_confirmation_expiries()

        assert result["ok"] is True
        assert result["count"] == 1


# =====================================================
# beat 调度存在性（源码断言：本环境无 celery 包，无法 import）
# =====================================================

class TestBeatScheduleWiring:
    def test_beat_schedule_declares_both_scans(self):
        import pathlib

        src = pathlib.Path(
            "backend/tasks/celery_app.py",
        ).read_text(encoding="utf-8")
        assert "cs-handoff-timeout-scan" in src
        assert "cs-confirmation-expiry-scan" in src
        assert "cs.handoff_timeout_scan" in src
        assert "cs.confirmation_expiry_scan" in src

    def test_maintenance_module_registered_in_include(self):
        import pathlib

        src = pathlib.Path(
            "backend/tasks/celery_app.py",
        ).read_text(encoding="utf-8")
        assert "backend.tasks.cs_maintenance_tasks" in src


# =====================================================
# 辅助
# =====================================================

def _returning_rows(rows):
    """构造 AsyncMock execute side_effect：返回带 .all() 的结果。"""
    result = MagicMock()
    result.all.return_value = rows
    result.scalars.return_value.all.return_value = [r for r in rows]

    def _side_effect(stmt, *a, **kw):
        return result

    return _side_effect


def _compile_where(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))
