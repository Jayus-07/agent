"""test_persistence.py — workflow_runs PG 持久化

覆盖：
- save / get round-trip
- list 分页 + workflow_name 过滤
- save 失败不抛（错误隔离）
- 不可序列化 outputs 安全降级
- 隔离：全部用例走 pgtest_biz_ 前缀测试表，前后删表，绝不触碰生产表
  （2026-09-17 SQLite 轨删除后，db_path 无文件语义，表名由 WORKFLOW_DB_PG_TABLE env 决定）
"""
from __future__ import annotations

import pytest

from backend.orchestration.workflow.context import WorkflowContext
from backend.orchestration.workflow.persistence import WorkflowRunStore
from backend.tests.fixtures.pg_env import pg_clean_tables  # noqa: F401


@pytest.fixture(autouse=True)
def _pg_iso(pg_clean_tables):
    """套用 pgtest_biz_ 测试表隔离（env 注入 + 前后清表）。"""
    yield


# ─────────────────────────────────────────────────────────────
# Save / Get round-trip
# ─────────────────────────────────────────────────────────────

class TestPersistenceRoundTrip:
    """save + get 数据一致性"""

    def test_save_and_get_basic(self):
        """保存 ctx 后 get 能拿到"""
        store = WorkflowRunStore()
        ctx = WorkflowContext(
            workflow_name="daily_report",
            run_id="run-abc",
            inputs={"x": 1},
            outputs={"step1": {"y": 2}},
        )
        ctx.mark_success()
        store.save(ctx)

        row = store.get("run-abc")
        assert row is not None
        assert row["workflow_name"] == "daily_report"
        assert row["status"] == "success"
        assert row["inputs"] == {"x": 1}
        assert row["outputs"] == {"step1": {"y": 2}}

    def test_save_failure_status(self):
        """failed status 能正确保存"""
        store = WorkflowRunStore()
        ctx = WorkflowContext("wf", "run-fail")
        ctx.mark_failed("step X 失败")
        store.save(ctx)

        row = store.get("run-fail")
        assert row["status"] == "failed"
        assert row["error"] == "step X 失败"
        assert row["finished_at"] is not None

    def test_partial_status_saves(self):
        """partial status 能正确保存"""
        store = WorkflowRunStore()
        ctx = WorkflowContext("wf", "run-partial")
        ctx.mark_partial()
        store.save(ctx)
        assert store.get("run-partial")["status"] == "partial"

    def test_save_updates_existing_run(self):
        """INSERT OR REPLACE：同 run_id 二次 save 会覆盖"""
        store = WorkflowRunStore()
        ctx1 = WorkflowContext("wf", "run-rep")
        ctx1.mark_success()
        store.save(ctx1)

        ctx2 = WorkflowContext("wf", "run-rep")
        ctx2.mark_failed("fail")
        store.save(ctx2)

        # 应只剩 1 行，状态是 failed
        row = store.get("run-rep")
        assert row["status"] == "failed"
        assert row["error"] == "fail"


# ─────────────────────────────────────────────────────────────
# List / 分页 / 过滤
# ─────────────────────────────────────────────────────────────

class TestPersistenceList:
    """list() 查询"""

    def test_list_empty(self):
        """空 DB 返回空列表"""
        store = WorkflowRunStore()
        assert store.list() == []

    def test_list_filter_by_workflow_name(self):
        """list(workflow_name=X) 只返回 X 的 run"""
        store = WorkflowRunStore()
        # 写入 3 条：2 daily + 1 inventory
        for name, run_id in [("daily_report", "d1"), ("daily_report", "d2"), ("inventory_alert", "i1")]:
            ctx = WorkflowContext(name, run_id)
            ctx.mark_success()
            store.save(ctx)

        assert len(store.list(workflow_name="daily_report")) == 2
        assert len(store.list(workflow_name="inventory_alert")) == 1
        assert len(store.list(workflow_name="nonexistent")) == 0

    def test_list_pagination(self):
        """list 分页正确"""
        store = WorkflowRunStore()
        # 写入 25 条
        for i in range(25):
            ctx = WorkflowContext("wf", f"run-{i:02d}")
            ctx.mark_success()
            store.save(ctx)

        # 默认 20 条/页
        page1 = store.list(page=1, page_size=20)
        page2 = store.list(page=2, page_size=20)

        assert len(page1) == 20
        assert len(page2) == 5
        # 不重复
        ids1 = {r["run_id"] for r in page1}
        ids2 = {r["run_id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_list_returns_descending_order(self):
        """list 按 started_at DESC 排序"""
        import time
        store = WorkflowRunStore()
        for i in range(3):
            ctx = WorkflowContext("wf", f"run-{i}")
            ctx.mark_success()
            store.save(ctx)
            time.sleep(0.01)  # 确保 started_at 不同

        rows = store.list()
        # 最新的在前面
        timestamps = [r["started_at"] for r in rows]
        assert timestamps == sorted(timestamps, reverse=True)


# ─────────────────────────────────────────────────────────────
# 序列化降级（不可序列化对象不崩）
# ─────────────────────────────────────────────────────────────

class TestPersistenceSafeSerialize:
    """_safe_serialize 对不可 JSON 对象的降级"""

    def test_save_unserializable_outputs_does_not_crash(self):
        """outputs 含不可序列化对象 → save 不抛"""
        store = WorkflowRunStore()
        ctx = WorkflowContext("wf", "run-unsafe")
        ctx.outputs = {"step": object()}  # 不可序列化
        ctx.mark_success()
        # 不抛
        store.save(ctx)
        # 可读回（输出降级为 str）
        row = store.get("run-unsafe")
        assert row is not None
        assert isinstance(row["outputs"]["step"], str)

    def test_save_with_object_in_inputs(self):
        """inputs 含不可序列化对象也降级"""
        store = WorkflowRunStore()
        ctx = WorkflowContext("wf", "run-unsafe-in")
        ctx.inputs = {"config": object()}
        ctx.mark_success()
        store.save(ctx)
        row = store.get("run-unsafe-in")
        assert isinstance(row["inputs"]["config"], str)

    def test_save_with_list_of_objects(self):
        """outputs 是 list of objects → list of str"""
        store = WorkflowRunStore()
        ctx = WorkflowContext("wf", "run-list")
        ctx.outputs = [object(), object()]
        ctx.mark_success()
        store.save(ctx)
        row = store.get("run-list")
        assert isinstance(row["outputs"], list)
        assert all(isinstance(v, str) for v in row["outputs"])


# ─────────────────────────────────────────────────────────────
# 错误隔离（save 失败不影响 workflow 结果）
# ─────────────────────────────────────────────────────────────

class TestPersistenceErrorIsolation:
    """save 失败不应冒泡（executor 已 try/except，但测试验证 storage 自身行为）"""

    def test_get_nonexistent_returns_none(self):
        """get 不存在的 run_id 返回 None（不抛）"""
        store = WorkflowRunStore()
        assert store.get("nonexistent-run") is None

    # test_db_path_creates_parent_dir 已随 SQLite 轨删除（2026-09-17）：
    # PG 模式下 db_path 仅作兼容保留，无文件路径语义。


# ─────────────────────────────────────────────────────────────
# Executor 集成验证（save 在 executor.run 末尾自动触发）
# ─────────────────────────────────────────────────────────────

class TestExecutorSaveIntegration:
    """Executor.run() 自动调用 persistence.save()"""

    def test_executor_run_calls_save(
        self, monkeypatch, fresh_registry, patched_trace_collector
    ):
        """Executor.run 末尾调 save（用 MagicMock 替 save 方法避免真实库副作用）"""
        import asyncio
        from unittest.mock import MagicMock, patch as mp
        from backend.orchestration.workflow import workflow, step
        from backend.orchestration.workflow.executor import WorkflowExecutor

        # MagicMock executor 的 store 工厂（类属性 patch 会因单例类身份漂移而失灵）
        save_mock = MagicMock()
        fake_store = MagicMock()
        fake_store.save = save_mock
        with mp(
            "backend.orchestration.workflow.executor.get_workflow_run_store",
            lambda: fake_store,
        ):
            @workflow(name="t_wf")
            class TWF:
                @step()
                async def step1(self, ctx):
                    return {"ok": True}

            fresh_registry.register(TWF)
            executor = WorkflowExecutor(registry=fresh_registry)

            ctx = asyncio.run(executor.run("t_wf"))

            # save 被调用，参数是 WorkflowContext
            assert save_mock.called
            call_arg = save_mock.call_args[0][0]
            assert call_arg.run_id == ctx.run_id
            assert call_arg.status == "success"
            assert call_arg.workflow_name == "t_wf"

    def test_executor_run_does_not_crash_on_save_failure(
        self, monkeypatch, fresh_registry, patched_trace_collector
    ):
        """save 抛异常时 executor 不挂"""
        import asyncio
        from unittest.mock import MagicMock, patch as mp
        from backend.orchestration.workflow import workflow, step
        from backend.orchestration.workflow.executor import WorkflowExecutor

        save_mock = MagicMock(side_effect=Exception("DB 不可用"))
        fake_store = MagicMock()
        fake_store.save = save_mock
        with mp(
            "backend.orchestration.workflow.executor.get_workflow_run_store",
            lambda: fake_store,
        ):
            @workflow(name="t_wf_fail")
            class TWF:
                @step()
                async def step1(self, ctx):
                    return {}

            fresh_registry.register(TWF)
            executor = WorkflowExecutor(registry=fresh_registry)

            ctx = asyncio.run(executor.run("t_wf_fail"))
            # workflow 仍正常完成
            assert ctx.status == "success"
            # save 仍被调用（异常被吞）
            assert save_mock.called
