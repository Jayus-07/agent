"""托管评测任务取消与逐题预算边界。"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.models.task import TaskStatus


def _waiting_retry(*, error_type="provider_error", retry_count=1,
                   max_retries=3, retry_exhausted=False):
    return SimpleNamespace(
        id="managed-retry-probe", status=TaskStatus.FAILED,
        error_type=error_type, retry_count=retry_count,
        max_retries=max_retries, retry_exhausted=retry_exhausted,
        current_node="provider", progress="等待自动重试",
    )


def test_cancel_waiting_retry_uses_atomic_database_transition(monkeypatch):
    from backend.services import task_service
    from backend.tasks import task_manager

    waiting = _waiting_retry()
    cancelled = SimpleNamespace(
        id=waiting.id, status=TaskStatus.CANCELLED,
        error_type=waiting.error_type, retry_count=waiting.retry_count,
        max_retries=waiting.max_retries, retry_exhausted=False,
        current_node=waiting.current_node, progress="已取消",
    )
    records = iter((waiting, cancelled))
    expected = []
    flags_cleared = []
    monkeypatch.setattr(task_service, "get_task", lambda _: next(records))
    monkeypatch.setattr(
        task_service, "mark_cancelled_if_status",
        lambda task_id, status: expected.append((task_id, status)) or True,
    )
    monkeypatch.setattr(task_manager, "_revoke_queued", lambda _: None)
    monkeypatch.setattr(task_manager, "clear_flags", flags_cleared.append)
    monkeypatch.setattr(task_manager, "publish_event", lambda *a, **k: None)

    result = task_manager.cancel_task(waiting.id)

    assert result == {
        "ok": True, "already": False, "status": "CANCELLED", "mode": "db",
    }
    assert expected == [(waiting.id, TaskStatus.FAILED)]
    assert flags_cleared == [waiting.id]


@pytest.mark.parametrize(
    "record",
    [
        _waiting_retry(error_type="permission_denied"),
        _waiting_retry(retry_exhausted=True),
        _waiting_retry(retry_count=3, max_retries=3),
    ],
    ids=["permanent-error", "exhausted-flag", "retry-count-exhausted"],
)
def test_cancel_final_failed_task_is_idempotent(monkeypatch, record):
    from backend.services import task_service
    from backend.tasks import task_manager

    monkeypatch.setattr(task_service, "get_task", lambda _: record)
    monkeypatch.setattr(
        task_service, "mark_cancelled_if_status",
        lambda *a, **k: pytest.fail("最终失败任务不应改写为取消"),
    )
    monkeypatch.setattr(
        task_manager, "_revoke_queued",
        lambda *a, **k: pytest.fail("最终失败任务不应触发撤销"),
    )

    assert task_manager.cancel_task(record.id) == {
        "ok": True, "already": True, "status": "FAILED",
    }


@pytest.mark.parametrize(
    "raced_record, expected_mode, expected_status",
    [
        (SimpleNamespace(status=TaskStatus.RUNNING), "flag", "RUNNING"),
        (_waiting_retry(), "flag", "FAILED"),
        (SimpleNamespace(status=TaskStatus.CANCELLED), "already", "CANCELLED"),
    ],
    ids=["picked-up-by-worker", "still-waiting-retry", "cancelled-by-race"],
)
def test_cancel_retry_cas_race_uses_current_task_state(
    monkeypatch, raced_record, expected_mode, expected_status,
):
    from backend.services import task_service
    from backend.tasks import task_manager

    initial = _waiting_retry()
    raced_record.id = initial.id
    records = iter((initial, raced_record))
    flag_calls = []
    monkeypatch.setattr(task_service, "get_task", lambda _: next(records))
    monkeypatch.setattr(task_service, "mark_cancelled_if_status", lambda *a: False)
    monkeypatch.setattr(task_manager, "_revoke_queued", lambda _: None)
    monkeypatch.setattr(
        task_manager, "request_cancel", lambda task_id: flag_calls.append(task_id) or True,
    )

    result = task_manager.cancel_task(initial.id)

    if expected_mode == "already":
        assert result == {"ok": True, "already": True, "status": expected_status}
        assert not flag_calls
    else:
        assert result["mode"] == expected_mode
        assert result["status"] == expected_status
        assert flag_calls == [initial.id]


def test_failed_cancellation_sql_guards_retry_budget_and_error_type(monkeypatch):
    from backend.services import task_service
    from backend.tasks.error_taxonomy import _RETRYABLE_TYPES

    class Cursor:
        rowcount = 1

        def __init__(self):
            self.statement = ""
            self.params = ()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, statement, params):
            self.statement = statement
            self.params = params

    class Connection:
        def __init__(self, cursor):
            self._cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return self._cursor

    cursor = Cursor()
    monkeypatch.setattr(task_service, "ensure_schema", lambda: None)
    monkeypatch.setattr(task_service, "_conn", lambda: Connection(cursor))

    assert task_service.mark_cancelled_if_status(
        "managed-retry-probe", TaskStatus.FAILED,
    )
    assert "retry_exhausted = false" in cursor.statement
    assert "retry_count < max_retries" in cursor.statement
    assert "error_type = ANY(%s)" in cursor.statement
    assert cursor.params[-1] == list(_RETRYABLE_TYPES)


def test_managed_case_budget_is_stable_per_case_and_normalizes_actor(monkeypatch):
    from backend.core import request_context
    from backend.evaluation import managed_tasks
    from backend.infra.llm import budget

    observed = []

    @contextmanager
    def budget_scope(request_id, *, user_id, tenant_id):
        observed.append((request_id, user_id, tenant_id))
        yield

    monkeypatch.setattr(request_context, "get_tool_user_id", lambda: "user:279")
    monkeypatch.setattr(request_context, "get_tool_tenant_id", lambda: "tenant-a")
    monkeypatch.setattr(budget, "scoped_request_budget", budget_scope)

    cases = [
        SimpleNamespace(id="case-a", metadata={"managed_binding_id": "binding-a"}),
        SimpleNamespace(id="case-b", metadata={"managed_binding_id": "binding-a"}),
    ]
    for case in cases:
        with managed_tasks.managed_case_budget(case, "run-1"):
            pass
    with managed_tasks.managed_case_budget(cases[0], "run-1"):
        pass

    assert observed == [
        ("eval:run-1:case:case-a", "279", "tenant-a"),
        ("eval:run-1:case:case-b", "279", "tenant-a"),
        ("eval:run-1:case:case-a", "279", "tenant-a"),
    ]


def test_unmanaged_case_keeps_legacy_budget_scope(monkeypatch):
    from backend.evaluation import managed_tasks
    from backend.infra.llm import budget

    monkeypatch.setattr(
        budget, "scoped_request_budget",
        lambda *a, **k: pytest.fail("内置 Suite 不应进入托管题集预算"),
    )
    case = SimpleNamespace(id="legacy-case", metadata={})
    with managed_tasks.managed_case_budget(case, "run-legacy"):
        assert True
