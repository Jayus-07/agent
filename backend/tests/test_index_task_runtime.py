"""tests/test_index_task_runtime.py — Phase1 Step8 rag_index 试点接入测试。

验收口径（Runtime 层收口，indexer 零侵入）：
- dispatch 创建 tasks 行并携带 db_task_id（身份不进 Celery 消息）
- 单文档索引 = 原子节点：pause/cancel 仅在节点边界生效
  · 开始前置位 → 不开跑（执行次数 0）
  · 完成后置位 → 结果保留 + settle 已做 + 状态 PAUSED/CANCELLED
- resume 重跑原子节点（幂等由 registry duplicate 吸收，stub 计数验证）
- 无 db_task_id 的存量消息 → 行为不变，不碰 tasks 表
- 失败 → FAILED + error_code；重投显式回 PENDING 后可续跑
- 租约被占 / PAUSED 重投 → skip 不执行
"""
from __future__ import annotations

import uuid

import pytest

from backend.models.task import TaskStatus
from backend.services.task_state import TaskManager
from backend.tasks.index_task_runtime import (
    IndexTaskCancelled, IndexTaskPaused, run_with_task_state)


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过 rag_index 试点测试: {e}")
    from backend.services import task_service

    return task_service


def test_task_service_connection_uses_configured_timeout(monkeypatch):
    """任务运行时直连必须受统一连接超时保护，避免故障时无限等待。"""
    import psycopg
    from backend.services import task_service

    captured = {}
    sentinel = object()

    def fake_connect(dsn, **kwargs):
        captured["dsn"] = dsn
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(task_service, "DB_CONNECT_TIMEOUT", 7,
                        raising=False)

    assert task_service._conn() is sentinel
    assert captured["kwargs"] == {"autocommit": True, "connect_timeout": 7}


def _new_record(pg) -> str:
    record = TaskManager.create(f"idx-user-{uuid.uuid4().hex[:8]}", "索引任务",
                                graph_name="rag_index",
                                biz_type="rag_index", biz_id=f"up-{uuid.uuid4().hex[:8]}")
    return record.id


def _ok_result() -> dict:
    return {"terminal": "done", "doc": {"doc_id": "doc-1"}, "trace_id": "t-1"}


@pytest.fixture()
def flags(monkeypatch):
    """可控 pause/cancel 标志（不依赖 Redis）。"""
    state = {"pause": False, "cancel": False}
    monkeypatch.setattr("backend.tasks.task_manager.is_pause_requested",
                        lambda tid: state["pause"])
    monkeypatch.setattr("backend.tasks.task_manager.is_cancel_requested",
                        lambda tid: state["cancel"])
    return state


# ═══════════════════════════════════════════════════
# 存量兼容：无 db_task_id
# ═══════════════════════════════════════════════════

def test_no_db_task_id_runs_directly(pg, flags):
    calls = {"n": 0}

    def run():
        calls["n"] += 1
        return _ok_result()

    result = run_with_task_state(None, "upload-x", run)
    assert result["terminal"] == "done"
    assert calls == {"n": 1}


# ═══════════════════════════════════════════════════
# 正常执行 / checkpoint / 终态落库
# ═══════════════════════════════════════════════════

def test_success_lands_taskstate_and_checkpoint(pg, flags):
    task_id = _new_record(pg)
    calls = {"n": 0}

    def run():
        calls["n"] += 1
        return _ok_result()

    result = run_with_task_state(task_id, "upload-1", run)
    assert calls == {"n": 1}
    row = pg.get_task(task_id)
    assert row.status == TaskStatus.SUCCESS
    assert row.output["terminal"] == "done"
    assert row.output["doc_id"] == "doc-1"
    assert row.current_node == "index_document"
    cps = pg.list_checkpoints(task_id, row.user_id)
    assert [c["node_name"] for c in cps] == ["index_document"]
    assert cps[0]["state_json"]["upload_id"] == "upload-1"


# ═══════════════════════════════════════════════════
# 节点边界控制：开始前 / 完成后
# ═══════════════════════════════════════════════════

def test_pause_before_start_skips_execution(pg, flags):
    task_id = _new_record(pg)
    flags["pause"] = True
    calls = {"n": 0}

    def run():
        calls["n"] += 1
        return _ok_result()

    result = run_with_task_state(task_id, "upload-2", run)
    assert calls == {"n": 0}                                  # 执行次数 0
    assert result["skipped"] and result["error"] == "paused"
    assert pg.get_task(task_id).status == TaskStatus.PAUSED


def test_cancel_before_start_skips_execution(pg, flags):
    task_id = _new_record(pg)
    flags["cancel"] = True

    result = run_with_task_state(task_id, "upload-3", lambda: _ok_result())
    assert result["skipped"] and result["error"] == "cancelled"
    assert pg.get_task(task_id).status == TaskStatus.CANCELLED


def test_pause_after_completion_keeps_result(pg, flags):
    """索引完成后（settle 已做）发现暂停：结果保留，状态 PAUSED，可 resume 重跑。"""
    task_id = _new_record(pg)
    calls = {"n": 0}

    def run():
        calls["n"] += 1
        return _ok_result()

    def run_pause_after():
        out = run()
        flags["pause"] = True       # 模拟索引进行中收到 pause 请求
        return out

    with pytest.raises(IndexTaskPaused):
        run_with_task_state(task_id, "upload-4", run_pause_after)
    assert calls == {"n": 1}                                  # 原子节点完成一次
    row = pg.get_task(task_id)
    assert row.status == TaskStatus.PAUSED
    assert [c["node_name"] for c in pg.list_checkpoints(task_id, row.user_id)] \
        == ["index_document"]                                 # checkpoint/结果保留

    # resume：PENDING → 租约 → 原子节点重跑（registry duplicate 幂等吸收）
    flags["pause"] = False
    pg.update_status(task_id, TaskStatus.PENDING, progress="重试回队")
    result = run_with_task_state(task_id, "upload-4", run)
    assert calls == {"n": 2}
    assert pg.get_task(task_id).status == TaskStatus.SUCCESS
    assert result["terminal"] == "done"


def test_cancel_after_completion_final(pg, flags):
    task_id = _new_record(pg)
    calls = {"n": 0}

    def run():
        calls["n"] += 1
        flags["cancel"] = True
        return _ok_result()

    with pytest.raises(IndexTaskCancelled):
        run_with_task_state(task_id, "upload-5", run)
    assert calls == {"n": 1}
    assert pg.get_task(task_id).status == TaskStatus.CANCELLED
    from backend.models.task import IllegalTaskTransition

    with pytest.raises(IllegalTaskTransition):
        TaskManager.mark_running(task_id)                     # 终态封闭


# ═══════════════════════════════════════════════════
# 失败 / 重试 / 重投竞态
# ═══════════════════════════════════════════════════

def test_failure_marks_failed_then_retry(pg, flags):
    task_id = _new_record(pg)
    calls = {"n": 0}

    def run():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("embedding down")
        return _ok_result()

    with pytest.raises(RuntimeError):
        run_with_task_state(task_id, "upload-6", run)
    row = pg.get_task(task_id)
    assert row.status == TaskStatus.FAILED
    # Phase2 Step2：error_type 落分类词表（embedding 属 provider 侧临时错误，
    # 可重试），不再落异常类名
    assert row.error_type == "provider_error"

    # Celery 重投：FAILED 显式回 PENDING（requeue_failed）→ 续跑成功
    TaskManager.requeue_failed(task_id)
    run_with_task_state(task_id, "upload-6", run)
    assert calls == {"n": 2}
    assert pg.get_task(task_id).status == TaskStatus.SUCCESS


def test_paused_redelivery_skips(pg, flags):
    """PAUSED 任务被重投（消息早于 pause）：不执行、状态不被破坏。"""
    task_id = _new_record(pg)
    flags["pause"] = True
    result = run_with_task_state(task_id, "upload-7",
                                 lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    assert result["skipped"] and result["error"] == "paused"
    assert pg.get_task(task_id).status == TaskStatus.PAUSED


def test_lease_held_skips(pg, flags):
    task_id = _new_record(pg)
    assert pg.try_acquire_lease(task_id, worker="worker-a")
    result = run_with_task_state(task_id, "upload-8", lambda: _ok_result())
    assert result["skipped"] and result["error"] == "running_elsewhere"
    assert pg.get_task(task_id).status == TaskStatus.RUNNING   # 未被破坏


# ═══════════════════════════════════════════════════
# dispatch 接线：tasks 行创建 + db_task_id 进消息 + 身份不进消息
# ═══════════════════════════════════════════════════

def test_dispatch_creates_taskstate_row(pg, monkeypatch):
    from backend.app.api.routes import rag_upload

    captured: dict = {}

    class _FakeAsyncResult:
        id = "celery-xyz"

    def _fake_apply_async(*, kwargs, queue):
        captured["kwargs"] = kwargs
        captured["queue"] = queue
        return _FakeAsyncResult()

    from backend.tasks import index_tasks

    monkeypatch.setattr(index_tasks.execute_index_task, "apply_async",
                        _fake_apply_async)
    result = rag_upload._dispatch_index_to_celery(
        upload_id="up-dispatch-1", filepath="/tmp/x.pdf", filename="x.pdf",
        kb_id="policy_general", actor_id="user-1", tenant_id="tenant-1")

    assert result["queued"] and result["db_task_id"]
    assert captured["kwargs"]["db_task_id"] == result["db_task_id"]
    assert "actor_id" not in captured["kwargs"]              # 身份不进消息
    assert "tenant_id" not in captured["kwargs"]
    assert captured["queue"] == "rag_index"

    row = pg.get_task(result["db_task_id"])
    assert row is not None
    assert row.status == TaskStatus.PENDING
    assert row.graph_name == "rag_index"
    assert row.biz_id == "up-dispatch-1"
    assert row.user_id == "user-1"
    assert row.tenant_id == "tenant-1"
    assert row.celery_task_id == "celery-xyz"                # revoke 反查通道


# ═══════════════════════════════════════════════════
# impl 集成：execute_index_task_impl 全链路（stub 索引）
# ═══════════════════════════════════════════════════

def test_impl_full_flow_success(pg, monkeypatch, flags):
    from backend.app.api.routes import rag_upload
    from backend.tasks.index_tasks import execute_index_task_impl

    settle_calls = {"n": 0}
    monkeypatch.setattr(rag_upload, "_do_index_sync",
                        lambda *a, **k: {"terminal": "done",
                                         "doc": {"doc_id": "doc-9"},
                                         "trace_id": "tr-9"})
    monkeypatch.setattr(rag_upload, "_settle_index_result",
                        lambda *a, **k: settle_calls.__setitem__("n", settle_calls["n"] + 1))

    task_id = _new_record(pg)
    result = execute_index_task_impl(
        "upload-9", "/tmp/x.pdf", "x.pdf", db_task_id=task_id)
    assert result["status"] == "done"
    assert settle_calls["n"] == 1
    row = pg.get_task(task_id)
    assert row.status == TaskStatus.SUCCESS
    assert row.output["doc_id"] == "doc-9"


def test_impl_paused_after_settle_returns_paused(pg, monkeypatch, flags):
    """pause 语义经 impl：settle 完成、返回 paused（Celery 不重试用户意图）。"""
    from backend.app.api.routes import rag_upload
    from backend.tasks.index_tasks import execute_index_task_impl

    monkeypatch.setattr(rag_upload, "_do_index_sync",
                        lambda *a, **k: {"terminal": "done", "doc": {}})
    monkeypatch.setattr(rag_upload, "_settle_index_result", lambda *a, **k: None)
    flags["pause"] = True                                    # 全程置位：开始前拦截也可，
    # 此处验证"完成后置位"分支：先清掉开始前标志，节点内置位
    flags["pause"] = False

    state = {"n": 0}
    real_sync = rag_upload._do_index_sync

    def _sync(*a, **k):
        state["n"] += 1
        flags["pause"] = True                                # 索引进行中收到请求
        return real_sync(*a, **k)

    monkeypatch.setattr(rag_upload, "_do_index_sync", _sync)

    task_id = _new_record(pg)
    result = execute_index_task_impl(
        "upload-10", "/tmp/y.pdf", "y.pdf", db_task_id=task_id)
    assert result["status"] == "paused"
    assert state["n"] == 1                                   # 原子节点完成
    assert pg.get_task(task_id).status == TaskStatus.PAUSED
