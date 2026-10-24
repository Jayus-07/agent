import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.api.deps import OperatorIdentity
from backend.app.api.identity import Identity
from backend.app.api.routes import evaluation_v2 as routes


def _actor(role="super_admin"):
    return OperatorIdentity(role=role, actor="user:eval-admin")


def _identity(tenant="default"):
    return Identity(user_id="eval-admin", tenant_id=tenant)


def test_cancel_run_forwards_only_owned_rag_task_and_returns_current_state(monkeypatch):
    run_id = uuid.uuid4()
    task = SimpleNamespace(
        graph_name="rag_eval",
        tenant_id="default",
        status=SimpleNamespace(value="RUNNING"),
    )
    task_ids = []
    cancel_ids = []
    monkeypatch.setattr(
        routes.repository,
        "get_run",
        lambda tenant, selected: {"run_id": selected, "job_status": "running"},
    )

    from backend.services import task_service
    from backend.tasks import task_manager

    monkeypatch.setattr(task_service, "get_task", lambda selected: task_ids.append(selected) or task)
    monkeypatch.setattr(
        task_manager,
        "cancel_task",
        lambda selected: cancel_ids.append(selected) or {"ok": True, "status": "RUNNING"},
    )

    result = asyncio.run(routes.cancel_eval_run(
        run_id=run_id,
        identity=_identity(),
        actor=_actor(),
    ))

    assert cancel_ids == [str(run_id)]
    assert task_ids == [str(run_id), str(run_id)]
    assert result["accepted"] is True
    assert result["job_status"] == "running"


def test_cancel_run_rejects_non_super_admin_before_task_access(monkeypatch):
    run_id = uuid.uuid4()
    monkeypatch.setattr(
        routes.repository,
        "get_run",
        lambda *_: pytest.fail("角色检查失败前不应读取 Run"),
    )

    with pytest.raises(HTTPException) as captured:
        asyncio.run(routes.cancel_eval_run(
            run_id=run_id,
            identity=_identity(),
            actor=_actor("admin"),
        ))

    assert captured.value.status_code == 403


def test_cancel_run_does_not_hide_delivery_failure_as_accepted(monkeypatch):
    run_id = uuid.uuid4()
    task = SimpleNamespace(
        graph_name="rag_eval",
        tenant_id="default",
        status=SimpleNamespace(value="RUNNING"),
    )
    monkeypatch.setattr(
        routes.repository,
        "get_run",
        lambda tenant, selected: {"run_id": selected, "job_status": "running"},
    )

    from backend.services import task_service
    from backend.tasks import task_manager

    monkeypatch.setattr(task_service, "get_task", lambda _selected: task)
    monkeypatch.setattr(task_manager, "cancel_task", lambda _selected: {"ok": False})

    with pytest.raises(HTTPException) as captured:
        asyncio.run(routes.cancel_eval_run(
            run_id=run_id,
            identity=_identity(),
            actor=_actor(),
        ))

    assert captured.value.status_code == 503


def test_cancelled_queued_run_commits_gate_and_terminal_status_atomically(monkeypatch):
    run_id = uuid.uuid4()
    task_states = iter(("PENDING", "CANCELLED"))
    task = SimpleNamespace(
        graph_name="rag_eval",
        tenant_id="default",
        status=None,
    )
    monkeypatch.setattr(
        routes.repository,
        "get_run",
        lambda tenant, selected: {"run_id": selected, "job_status": "queued"},
    )

    from backend.services import task_service
    from backend.tasks import task_manager

    def get_task(_selected):
        task.status = SimpleNamespace(value=next(task_states))
        return task

    terminal_calls = []
    monkeypatch.setattr(task_service, "get_task", get_task)
    monkeypatch.setattr(task_manager, "cancel_task", lambda _selected: {"ok": True})
    monkeypatch.setattr(
        routes.repository,
        "terminate_run_with_gate",
        lambda *args, **kwargs: terminal_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        routes.repository,
        "set_run_status",
        lambda *_args, **_kwargs: pytest.fail("取消状态不能与 Gate 分事务写入"),
    )
    monkeypatch.setattr(
        routes.repository,
        "record_gate_decision",
        lambda *_args, **_kwargs: pytest.fail("取消状态不能与 Gate 分事务写入"),
    )

    result = asyncio.run(routes.cancel_eval_run(
        run_id=run_id,
        identity=_identity(),
        actor=_actor(),
    ))

    assert terminal_calls[0][0] == ("default", "user:eval-admin", str(run_id))
    assert terminal_calls[0][1]["status"] == "cancelled"
    assert terminal_calls[0][1]["invalid_reason"] == "cancelled_before_execution"
    assert terminal_calls[0][1]["verdict"] == "INVALID"
    assert result["accepted"] is True


def test_retry_failed_run_uses_existing_task_manager_admin_retry(monkeypatch):
    run_id = uuid.uuid4()
    task = SimpleNamespace(graph_name="rag_eval", tenant_id="default")
    resumed_task = SimpleNamespace(status=SimpleNamespace(value="PENDING"))
    task_reads = []
    resume_calls = []
    monkeypatch.setattr(
        routes.repository,
        "get_run",
        lambda tenant, selected: {"run_id": selected, "job_status": "failed"},
    )

    from backend.evaluation import managed_tasks
    from backend.services import task_service
    from backend.tasks import task_manager

    monkeypatch.setattr(
        task_service,
        "get_task",
        lambda selected: task_reads.append(selected) or (task if len(task_reads) == 1 else resumed_task),
    )
    monkeypatch.setattr(
        task_manager,
        "resume_task",
        lambda selected, **kwargs: resume_calls.append((selected, kwargs)) or resumed_task,
    )
    monkeypatch.setattr(
        managed_tasks,
        "task_view",
        lambda selected: {"task_id": run_id, "status": selected.status.value},
    )

    result = asyncio.run(routes.retry_eval_run(
        run_id=run_id,
        identity=_identity(),
        actor=_actor(),
    ))

    assert resume_calls == [(
        str(run_id),
        {"allow_failed": True, "dispatch_type": "admin_retry"},
    )]
    assert task_reads == [str(run_id), str(run_id)]
    assert result == {"run_id": str(run_id), "task": {"task_id": run_id, "status": "PENDING"}}


def test_retry_run_rejects_nonfailed_state_without_resuming(monkeypatch):
    run_id = uuid.uuid4()
    monkeypatch.setattr(
        routes.repository,
        "get_run",
        lambda tenant, selected: {"run_id": selected, "job_status": "completed"},
    )

    from backend.tasks import task_manager

    monkeypatch.setattr(task_manager, "resume_task", lambda *_a, **_kw: pytest.fail("终态 Run 不得恢复任务"))

    with pytest.raises(HTTPException) as captured:
        asyncio.run(routes.retry_eval_run(
            run_id=run_id,
            identity=_identity(),
            actor=_actor(),
        ))

    assert captured.value.status_code == 409
