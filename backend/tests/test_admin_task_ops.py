"""任务治理扩展测试（M10 / 台账 D10）

1. _audit 双写：日志（原有）+ ai.task_operation_audits INSERT（软失败）
2. reexecute 克隆：input 透传（query 剥离进 extra）/ parent_task_id 关联 /
   enqueue 失败不产生孤儿审计
3. /queues：Redis 不可达时返回骨架（waiting=None）不 500
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _bypass_admin(monkeypatch):
    from backend.app.api.routes import admin_tasks as mod

    async def _allow(request):
        return None

    monkeypatch.setattr(mod, "require_admin_operator", _allow)
    mod._op_last.clear()  # 操作冷却进程级状态，测试间清零防 429 串扰


class _FakeRecord:
    id = "src-task-1"
    user_id = "u1"
    tenant_id = "t1"
    graph_name = "main"
    conversation_id = "conv-1"
    biz_type = "report"
    biz_id = "b1"
    input = {"query": "生成周报", "kbs": ["default"]}
    status = type("S", (), {"value": "SUCCESS"})()


class TestAuditDualWrite:
    def test_insert_sql_and_soft_fail(self, monkeypatch):
        from backend.app.api.routes import admin_tasks as mod

        captured: dict = {}

        class _FakeCursor:
            def execute(self, sql, params=None):
                captured["sql"], captured["params"] = sql, params

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        import backend.infra.db as infra_db
        monkeypatch.setattr(infra_db, "engine_for", lambda cfg: type("E", (), {"raw_connection": lambda self: _FakeConn()})())
        mod._audit("op", "retry", "t-1", "reason=x",
                   reason="x", before_status="FAILED", after_status="PENDING")
        assert "INSERT INTO ai.task_operation_audits" in captured["sql"]
        assert captured["params"][2] == "retry"
        assert captured["params"][5] == "FAILED" and captured["params"][6] == "PENDING"

    def test_db_failure_does_not_raise(self, monkeypatch):
        from backend.app.api.routes import admin_tasks as mod

        import backend.infra.db as infra_db
        monkeypatch.setattr(infra_db, "engine_for", lambda cfg: (_ for _ in ()).throw(RuntimeError("pg down")))
        mod._audit("op", "revoke", "t-1", "ok")  # 不抛 = 审计软失败


class TestReexecute:
    @pytest.mark.asyncio
    async def test_clone_semantics(self, monkeypatch):
        from backend.app.api.routes import admin_tasks as mod

        created = {}

        def fake_create(user_id, query, **kw):
            created["user_id"], created["query"], created["kw"] = user_id, query, kw
            clone = type("C", (), {"id": "clone-1"})()
            return clone

        enqueued = []
        monkeypatch.setattr(mod.task_service, "get_task", lambda tid: _FakeRecord())
        monkeypatch.setattr(mod.task_service, "create_task", fake_create)
        monkeypatch.setattr(mod.task_manager, "enqueue_task", lambda rec: enqueued.append(rec.id))
        audited = {}
        monkeypatch.setattr(mod, "_audit", lambda actor, action, tid, result, **kw: audited.update(kw, action=action, tid=tid))

        class _Body:
            confirm = True
            reason = "重跑验证"

        class _Req:
            state = type("ST", (), {"actor": "op-1"})()

        resp = await mod.admin_reexecute_task("src-task-1", _Body(), _Req())
        assert resp["task_id"] == "clone-1" and resp["source_task_id"] == "src-task-1"
        # 克隆参数：身份/graph/biz 透传 + parent 关联 + query 剥离
        assert created["user_id"] == "u1" and created["query"] == "生成周报"
        assert created["kw"]["parent_task_id"] == "src-task-1"
        assert created["kw"]["extra_input"] == {"kbs": ["default"]}
        assert enqueued == ["clone-1"]
        assert audited["action"] == "reexecute" and audited["new_task_id"] == "clone-1"

    @pytest.mark.asyncio
    async def test_enqueue_failure_no_audit(self, monkeypatch):
        from backend.app.api.routes import admin_tasks as mod
        from backend.tasks.queue_router import QueueRoutingError

        monkeypatch.setattr(mod.task_service, "get_task", lambda tid: _FakeRecord())
        monkeypatch.setattr(mod.task_service, "create_task", lambda *a, **kw: type("C", (), {"id": "c2"})())
        monkeypatch.setattr(mod.task_manager, "enqueue_task",
                            lambda rec: (_ for _ in ()).throw(QueueRoutingError("no route")))
        audited = []
        monkeypatch.setattr(mod, "_audit", lambda *a, **kw: audited.append(a))

        class _Body:
            confirm = True
            reason = ""

        class _Req:
            state = type("ST", (), {"actor": "op"})()

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await mod.admin_reexecute_task("src-task-1", _Body(), _Req())
        assert ei.value.status_code == 400
        assert audited == []  # 失败路径不落审计（任务未创建成功）


class TestQueuesEndpoint:
    @pytest.mark.asyncio
    async def test_redis_unreachable_returns_skeleton(self, monkeypatch):
        from backend.app.api.routes import admin_tasks as mod

        import backend.infra.redis.client as redis_mod
        monkeypatch.setattr(redis_mod, "get_redis",
                            lambda: (_ for _ in ()).throw(RuntimeError("redis down")))

        class _Req:
            pass

        resp = await mod.admin_task_queues(_Req())
        assert len(resp["queues"]) == 5
        assert all(q["waiting"] is None for q in resp["queues"])
        assert "physical" in resp["queues"][0]

    @pytest.mark.asyncio
    async def test_workers_ping_uses_config_timeout(self, monkeypatch):
        """worker 存活探测超时来自 config（默认 5s），Celery app 单例直连。

        2026-09-30 实测双根因：①旧代码 task_manager.celery_app 引用不
        存在（task_manager 是模块非实例，hasattr 恒 False 静默吞成空列表
        → workers_online 恒 0）；②2s ping 在 worker 事件循环滞后时 0 响应。
        修后：celery_app 单例直连 + 超时入 config 单点可 env 覆盖。
        """
        from backend.app.api.routes import admin_tasks as mod
        from backend.config import tasks as tasks_cfg
        import sys
        # 注意：backend/tasks/__init__ 的 from-import 让包属性 celery_app
        # 遮蔽同名模块，import as 会拿到 Celery 实例——必须走 sys.modules
        celery_mod = sys.modules["backend.tasks.celery_app"]

        captured: dict = {}

        class _FakeControl:
            @staticmethod
            def ping(**kwargs):
                captured.update(kwargs)
                return [{"agent-worker@x": {"ok": "pong"}}]

        class _Req:
            pass

        import backend.infra.redis.client as redis_mod

        monkeypatch.setattr(redis_mod, "get_redis",
                            lambda: (_ for _ in ()).throw(RuntimeError("redis down")))
        monkeypatch.setattr(celery_mod, "celery_app",
                            type("App", (), {"control": _FakeControl})())
        monkeypatch.setattr(tasks_cfg, "TASK_WORKERS_PING_TIMEOUT", 5.0)
        resp = await mod.admin_task_queues(_Req())
        assert captured.get("timeout") == 5.0
        assert resp["workers_online"] == 1

    @pytest.mark.asyncio
    async def test_workers_ping_timeout_env_override(self, monkeypatch):
        """env TASK_WORKERS_PING_TIMEOUT 覆盖默认值（口径进 config 单点）。"""
        import importlib

        from backend.config import tasks as tasks_cfg

        monkeypatch.setenv("TASK_WORKERS_PING_TIMEOUT", "7.5")
        importlib.reload(tasks_cfg)
        try:
            assert tasks_cfg.TASK_WORKERS_PING_TIMEOUT == 7.5
        finally:
            monkeypatch.delenv("TASK_WORKERS_PING_TIMEOUT")
            importlib.reload(tasks_cfg)
