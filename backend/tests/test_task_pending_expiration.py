"""过期且没有入队凭据的 PENDING 任务收口契约测试。"""

from contextlib import contextmanager


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_expiration_requires_old_unqueued_never_started_task(monkeypatch):
    from backend.services import task_service

    cursor = _Cursor([("task-old",)])
    connection = _Connection(cursor)

    @contextmanager
    def fake_pooled_conn():
        yield connection

    monkeypatch.setattr(task_service, "ensure_schema", lambda: None)
    monkeypatch.setattr(task_service, "_pooled_conn", fake_pooled_conn)

    expired = task_service.expire_abandoned_pending_without_dispatch(
        max_age_seconds=86400, limit=999)

    assert expired == ["task-old"]
    assert "status = %s" in cursor.query
    assert "COALESCE(celery_task_id, '') = ''" in cursor.query
    assert "queued_at IS NULL" in cursor.query
    assert "started_at IS NULL" in cursor.query
    assert "COALESCE(execution_id, '') = ''" in cursor.query
    assert "lease_heartbeat_at IS NULL" in cursor.query
    assert "lease_expires_at IS NULL" in cursor.query
    assert "created_at <= now()" in cursor.query
    assert "updated_at <= now()" in cursor.query
    assert "dispatch_not_before_at IS NULL" in cursor.query
    assert "FOR UPDATE SKIP LOCKED" in cursor.query
    assert "error_type = 'PENDING_DELIVERY_EXPIRED'" in cursor.query
    assert "finished_at = now()" in cursor.query
    assert cursor.params == (
        "PENDING", "86400", "86400", 500, "CANCELLED",
        "超过自动恢复期限且没有入队回执，系统已停止自动执行；请重新提交任务。",
        "PENDING",
    )


def test_pending_recovery_reports_expired_rows_and_publishes_terminal_event(monkeypatch):
    from backend.services import task_service
    from backend.tasks import pending_recovery

    events = []
    monkeypatch.setattr(pending_recovery, "_enabled", lambda: True)
    monkeypatch.setattr(
        task_service, "expire_abandoned_pending_without_dispatch",
        lambda **_kwargs: ["task-expired"],
    )
    monkeypatch.setattr(task_service, "find_stale_pending", lambda **_kwargs: [])
    monkeypatch.setattr(
        "backend.tasks.task_manager.publish_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    monkeypatch.setattr(pending_recovery, "_record_recovery_metric", lambda _name: None)

    result = pending_recovery.recover_stale_pending_tasks(
        limit=10, max_age_seconds=86400)

    assert result["expired"] == 1
    assert result["published"] == 0
    assert events == [(("task-expired", "cancelled"), {
        "node": "queued",
        "error_type": "PENDING_DELIVERY_EXPIRED",
        "message": "任务超过自动恢复期限且没有入队回执；系统已停止自动执行，请重新提交。",
    })]
