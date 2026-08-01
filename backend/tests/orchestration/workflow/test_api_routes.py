"""test_api_routes.py — Workflow API 端点测试

覆盖：
- GET /workflows                       — 列出所有 workflow
- POST /workflows/{name}/trigger       — 手动触发
- GET /workflows/runs                  — 历史
- GET /workflows/runs/{run_id}         — 详情
- 错误处理：404 / 422

使用 FastAPI TestClient + httpx async（避免阻塞）
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch as mp

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import workflows as workflows_route
from backend.orchestration.workflow.context import WorkflowContext


@pytest.fixture
def app_with_workflows(monkeypatch, tmp_path, fresh_registry, patched_trace_collector):
    """构造一个 FastAPI app，只挂 workflows router + mock persistence"""
    from backend.orchestration.workflow.persistence import WorkflowRunStore
    from backend.orchestration.workflow.executor import WorkflowExecutor

    # mock persistence 切到 tmp_path
    store = WorkflowRunStore(db_path=str(tmp_path / "api_runs.db"))
    monkeypatch.setattr(
        "backend.orchestration.workflow.executor.get_workflow_run_store",
        lambda: store,
    )
    # mock persistence（API route 用）
    monkeypatch.setattr(
        "backend.app.api.routes.workflows.get_workflow_run_store",
        lambda: store,
    )

    # mock scheduler — run_now 是 async，必须用 AsyncMock
    fake_scheduler = MagicMock()
    fake_scheduler.list_jobs = MagicMock(return_value=[])
    fake_scheduler.run_now = AsyncMock()
    # run_now 返回值：先设个默认，再让具体测试覆盖
    async def _default_run_now(name, inputs=None):
        from backend.orchestration.workflow.executor import WorkflowExecutor
        executor = WorkflowExecutor(registry=fresh_registry)
        return await executor.run(name, inputs or {})
    fake_scheduler.run_now.side_effect = _default_run_now
    monkeypatch.setattr(
        "backend.app.api.routes.workflows.get_workflow_scheduler",
        lambda: fake_scheduler,
    )

    # mock registry（API route 用 global 单例）
    monkeypatch.setattr(
        "backend.app.api.routes.workflows.get_workflow_registry",
        lambda: fresh_registry,
    )
    # executor 也用 fresh_registry
    monkeypatch.setattr(
        "backend.orchestration.workflow.executor.get_workflow_registry",
        lambda: fresh_registry,
    )

    app = FastAPI()
    app.include_router(workflows_route.router)

    return app, store


# ─────────────────────────────────────────────────────────────
# GET /workflows
# ─────────────────────────────────────────────────────────────

class TestListWorkflows:
    """列出所有注册的 Workflow"""

    def test_list_returns_empty_when_no_workflows(self, app_with_workflows):
        """无 workflow 时返回空列表"""
        app, _ = app_with_workflows
        client = TestClient(app)

        resp = client.get("/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflows"] == []
        assert data["total"] == 0

    def test_list_returns_registered_workflows(self, app_with_workflows, fresh_registry):
        """注册的 workflow 出现在列表中"""
        from backend.orchestration.workflow import workflow, step

        @workflow(name="api_wf_1")
        class W1:
            @step()
            async def s(self, ctx): return {}

        @workflow(name="api_wf_2")
        class W2:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(W1)
        fresh_registry.register(W2)

        app, _ = app_with_workflows
        client = TestClient(app)

        resp = client.get("/workflows")
        data = resp.json()
        names = [w["name"] for w in data["workflows"]]
        assert "api_wf_1" in names
        assert "api_wf_2" in names


# ─────────────────────────────────────────────────────────────
# POST /workflows/{name}/trigger
# ─────────────────────────────────────────────────────────────

class TestTriggerWorkflow:
    """手动触发 workflow"""

    def test_trigger_unknown_workflow_returns_404(self, app_with_workflows):
        """未知 workflow 名 → 404"""
        app, _ = app_with_workflows
        client = TestClient(app)

        resp = client.post("/workflows/nonexistent/trigger")
        assert resp.status_code == 404
        assert "nonexistent" in resp.json()["detail"]

    def test_trigger_with_empty_body_does_not_422(self, app_with_workflows, fresh_registry):
        """不传 body 也不 422（Body(default_factory=dict)）"""
        from backend.orchestration.workflow import workflow, step

        @workflow(name="trigger_wf")
        class TWF:
            @step()
            async def s(self, ctx):
                return {"ok": True}

        fresh_registry.register(TWF)

        app, _store = app_with_workflows
        client = TestClient(app)

        # 用 MagicMock 替 save() 方法避免 MagicMock trace_id 类型问题
        save_mock = MagicMock()
        with mp(
            "backend.orchestration.workflow.persistence.WorkflowRunStore.save",
            save_mock,
        ):
            resp = client.post("/workflows/trigger_wf/trigger")
            assert resp.status_code == 200, resp.text

            data = resp.json()
            assert data["workflow_name"] == "trigger_wf"
            assert data["status"] == "success"
            assert "run_id" in data
            # save_mock 被调用
            assert save_mock.called

    def test_trigger_with_inputs(self, app_with_workflows, fresh_registry):
        """传 inputs 也接受"""
        from backend.orchestration.workflow import workflow, step

        @workflow(name="trigger_inputs_wf")
        class TWF:
            @step()
            async def s(self, ctx):
                return {"got": ctx.inputs}

        fresh_registry.register(TWF)

        app, _ = app_with_workflows
        client = TestClient(app)

        save_mock = MagicMock()
        with mp(
            "backend.orchestration.workflow.persistence.WorkflowRunStore.save",
            save_mock,
        ):
            resp = client.post(
                "/workflows/trigger_inputs_wf/trigger",
                json={"x": 1, "y": "hello"},
            )
            assert resp.status_code == 200

            data = resp.json()
            assert data["status"] == "success"
            # inputs 应该传到了 workflow
            assert data["outputs_keys"] == ["s"]


# ─────────────────────────────────────────────────────────────
# GET /workflows/runs
# ─────────────────────────────────────────────────────────────

class TestListRuns:
    """历史运行列表"""

    def test_list_runs_empty(self, app_with_workflows):
        """空 DB 返回空"""
        app, _ = app_with_workflows
        client = TestClient(app)

        resp = client.get("/workflows/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []
        assert data["page"] == 1

    def test_list_runs_filters_by_workflow_name(self, app_with_workflows):
        """按 workflow_name 过滤"""
        from backend.orchestration.workflow.persistence import WorkflowRunStore
        from backend.orchestration.workflow.context import WorkflowContext

        app, store = app_with_workflows
        # 直接往 store 写几条
        for name, rid in [("wf_a", "r1"), ("wf_a", "r2"), ("wf_b", "r3")]:
            ctx = WorkflowContext(name, rid)
            ctx.mark_success()
            store.save(ctx)

        client = TestClient(app)
        resp = client.get("/workflows/runs?workflow_name=wf_a")
        data = resp.json()
        assert len(data["runs"]) == 2
        for run in data["runs"]:
            assert run["workflow_name"] == "wf_a"

    def test_list_runs_pagination(self, app_with_workflows):
        """分页正确"""
        from backend.orchestration.workflow.context import WorkflowContext

        app, store = app_with_workflows
        for i in range(25):
            ctx = WorkflowContext("wf", f"run-{i:02d}")
            ctx.mark_success()
            store.save(ctx)

        client = TestClient(app)
        resp1 = client.get("/workflows/runs?page=1&page_size=20")
        resp2 = client.get("/workflows/runs?page=2&page_size=20")

        assert len(resp1.json()["runs"]) == 20
        assert len(resp2.json()["runs"]) == 5


# ─────────────────────────────────────────────────────────────
# GET /workflows/runs/{run_id}
# ─────────────────────────────────────────────────────────────

class TestGetRun:
    """单次运行详情"""

    def test_get_run_not_found(self, app_with_workflows):
        """不存在的 run_id → 404"""
        app, _ = app_with_workflows
        client = TestClient(app)

        resp = client.get("/workflows/runs/nonexistent")
        assert resp.status_code == 404

    def test_get_run_returns_full_detail(self, app_with_workflows):
        """包含 inputs / outputs"""
        from backend.orchestration.workflow.context import WorkflowContext

        app, store = app_with_workflows
        ctx = WorkflowContext("wf", "run-detail", inputs={"x": 1})
        ctx.outputs = {"step1": {"y": 2}}
        ctx.mark_success()
        store.save(ctx)

        client = TestClient(app)
        resp = client.get("/workflows/runs/run-detail")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflow_name"] == "wf"
        assert data["status"] == "success"
        assert data["inputs"] == {"x": 1}
        assert data["outputs"] == {"step1": {"y": 2}}