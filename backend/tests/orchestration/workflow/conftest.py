"""workflow 测试 conftest — 共享 fixtures

设计原则（按 [docs/architecture/workflow-phase1.md](../../../docs/architecture/workflow-phase1.md)）：
- 测试不污染生产 DB：monkeypatch 切到 tmp_path
- 避免模块级单例串扰：reset singletons
- Trace 集成显式开：fresh_tracer
- Skill / LLM 全 mock：patched_skill_adapter + patched_llm

参考：
- reset_tracer 模式：[backend/tests/rag/test_progress_e2e.py:24](file:///D:/Program%20Files/workplace/agent/backend/tests/rag/test_progress_e2e.py#L24)
- mock 模式：[backend/tests/orchestration/test_supervisor.py](file:///D:/Program%20Files/workplace/agent/backend/tests/orchestration/test_supervisor.py)
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.orchestration.workflow.context import WorkflowContext
from backend.orchestration.workflow.meta import StepConfig
from backend.orchestration.workflow.registry import WorkflowRegistry


# ─────────────────────────────────────────────────────────────
# Workflow 测试用 StepConfig 工厂
# ─────────────────────────────────────────────────────────────

def make_step_config(
    depends_on: list[str] | None = None,
    retry: int = 0,
    timeout_sec: int = 60,
    on_error: str = "abort",
) -> StepConfig:
    """测试用 StepConfig 工厂（默认值合理）"""
    return StepConfig(
        depends_on=depends_on or [],
        retry=retry,
        timeout_sec=timeout_sec,
        on_error=on_error,
    )


# ─────────────────────────────────────────────────────────────
# Registry / 单例 fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_registry() -> WorkflowRegistry:
    """全新的 WorkflowRegistry（不污染全局单例）

    用法：
        def test_xxx(fresh_registry):
            reg = fresh_registry
            reg.register(MyWorkflow)
    """
    return WorkflowRegistry()


@pytest.fixture
def reset_singletons(monkeypatch):
    """重置所有 workflow 模块级单例（避免测试顺序污染）

    影响：
    - WorkflowRegistry._registry
    - WorkflowScheduler._scheduler
    - WorkflowRunStore._store
    """
    import backend.orchestration.workflow.registry as reg_mod
    import backend.orchestration.workflow.scheduler as sched_mod
    import backend.orchestration.workflow.persistence as persist_mod

    monkeypatch.setattr(reg_mod, "_registry", None)
    monkeypatch.setattr(sched_mod, "_scheduler", None)
    monkeypatch.setattr(persist_mod, "_store", None)
    yield


# ─────────────────────────────────────────────────────────────
# Trace fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_tracer():
    """干净的 TraceCollector（参考 test_progress_e2e.py:24 reset_tracer）

    关键：恢复时把所有字段都还原，否则测试间串扰
    """
    from backend.observability import tracer as t
    from backend.observability.tracer import _current_trace_var

    saved = {
        k: getattr(t.trace_collector, k)
        for k in ("_timers", "_thread_current", "_span_seq", "_listeners")
    }
    saved_var = _current_trace_var.get()
    t.trace_collector.clear()
    t.trace_collector._listeners = []
    _current_trace_var.set(None)

    try:
        yield t.trace_collector
    finally:
        # 恢复
        for k, v in saved.items():
            setattr(t.trace_collector, k, v)
        _current_trace_var.set(saved_var)


@pytest.fixture
def patched_trace_collector(monkeypatch, fresh_tracer):
    """mock trace_collector.start_span / end_span

    Executor 直接调 trace_collector；mock 它避免真实 trace 生成。
    """
    fake_span = MagicMock(name="Span")
    fake_span.span_id = "fake"
    fake_span.trace_id = "fake_trace_001"
    fake_span.metrics = {}

    collector = MagicMock()
    collector.start_span = MagicMock(return_value=fake_span)
    collector.end_span = MagicMock()
    collector.subscribe = MagicMock(return_value=lambda: None)

    # executor 内部用 from backend.observability.tracer import trace_collector
    # patch source 模块路径
    monkeypatch.setattr(
        "backend.observability.tracer.trace_collector",
        collector,
    )
    return collector


# ─────────────────────────────────────────────────────────────
# Persistence fixtures（避免污染 data/workflow_runs.db）
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def patched_persistence(monkeypatch, tmp_path):
    """把 WorkflowRunStore 切到 tmp_path

    作用：
    1. 防止测试写污染生产 DB
    2. executor.run() 末尾自动调 save() 写到 tmp_path

    注：executor.py 在模块顶部 import get_workflow_run_store，
    patch 任意一处都生效（executor 模块属性 / persistence 模块属性都 patch）
    """
    from backend.orchestration.workflow.persistence import WorkflowRunStore

    store = WorkflowRunStore(db_path=str(tmp_path / "runs.db"))
    # 同时 patch executor 模块属性 + persistence 模块属性（executor 顶部 import）
    monkeypatch.setattr(
        "backend.orchestration.workflow.persistence.get_workflow_run_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "backend.orchestration.workflow.executor.get_workflow_run_store",
        lambda: store,
    )
    return store


# ─────────────────────────────────────────────────────────────
# Skill Adapter fixtures（mock 现有 6 个 Skill）
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def patched_skill_adapter(monkeypatch):
    """mock call_skill / call_sql / call_rag / call_report / call_email

    返回结构化 dict，让 workflow 跑得通又不真查库/调 LLM。

    用 AsyncMock + 同步 side_effect — 让测试可以用 .side_effect 注入失败场景。
    """
    def _default(name: str, params: dict) -> dict:
        return {
            "sql": {"rows": [{"product_id": "p1", "total_qty": 10}]},
            "rag": {"answer": "模板内容"},
            "report": {"content": "# 报告\n## 销售\n..."},
            "email": {"sent": True, "to": params.get("to", [])},
        }.get(name, {})

    call_skill = AsyncMock(side_effect=lambda name, cap, params: _default(name, params))
    call_sql = AsyncMock(side_effect=lambda params: _default("sql", params))
    call_rag = AsyncMock(side_effect=lambda params: _default("rag", params))
    call_report = AsyncMock(side_effect=lambda params: _default("report", params))
    call_email = AsyncMock(side_effect=lambda params: _default("email", params))

    monkeypatch.setattr(
        "backend.orchestration.workflow.skill_adapter.call_skill",
        call_skill,
    )
    monkeypatch.setattr(
        "backend.orchestration.workflow.skill_adapter.call_sql",
        call_sql,
    )
    monkeypatch.setattr(
        "backend.orchestration.workflow.skill_adapter.call_rag",
        call_rag,
    )
    monkeypatch.setattr(
        "backend.orchestration.workflow.skill_adapter.call_report",
        call_report,
    )
    monkeypatch.setattr(
        "backend.orchestration.workflow.skill_adapter.call_email",
        call_email,
    )

    # 关键：daily_report.py 在模块顶部 from ... import call_rag 等，
    # 这创建了本地绑定。patch source module 不影响已经 import 的引用。
    # 所以也要 patch daily_report.py 模块的同名属性。
    try:
        monkeypatch.setattr(
            "backend.orchestration.workflows.daily_report.call_sql",
            call_sql,
        )
        monkeypatch.setattr(
            "backend.orchestration.workflows.daily_report.call_rag",
            call_rag,
        )
        monkeypatch.setattr(
            "backend.orchestration.workflows.daily_report.call_report",
            call_report,
        )
        monkeypatch.setattr(
            "backend.orchestration.workflows.daily_report.call_email",
            call_email,
        )
    except AttributeError:
        # daily_report.py 还没被 import（首次跑 smoke test 时）
        pass

    return {
        "call_skill": call_skill,
        "call_sql": call_sql,
        "call_rag": call_rag,
        "call_report": call_report,
        "call_email": call_email,
    }


# ─────────────────────────────────────────────────────────────
# LLM mock（Business Agent Skill 用）
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def patched_llm(monkeypatch):
    """mock backend.infra.llm.llm 用于 Business Agent Skill

    让 ainvoke 返回结构化 JSON 字符串。
    """
    class FakeResponse:
        content = '{"anomalies": [], "advice": [], "confidence": 0.9, "reasoning": "mock"}'

    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=FakeResponse())

    monkeypatch.setattr("backend.infra.llm.llm", fake_llm)
    monkeypatch.setattr("backend.infra.llm.proxy._last_call_meta", {})
    return fake_llm


# ─────────────────────────────────────────────────────────────
# WorkflowContext 工厂
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def make_workflow_context():
    """构造 WorkflowContext 的工厂"""
    def _make(
        workflow_name: str = "test_wf",
        run_id: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> WorkflowContext:
        import uuid as _uuid
        return WorkflowContext(
            workflow_name=workflow_name,
            run_id=run_id or _uuid.uuid4().hex[:12],
            inputs=inputs or {},
        )
    return _make


# ─────────────────────────────────────────────────────────────
# APScheduler mock（scheduler 测试用）
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def patched_apscheduler(monkeypatch):
    """让 APSCHEDULER_AVAILABLE=True，且 AsyncIOScheduler 是 MagicMock"""
    fake_scheduler = MagicMock()
    fake_scheduler.running = False
    fake_scheduler.add_job = MagicMock()

    monkeypatch.setattr(
        "backend.orchestration.workflow.scheduler.APSCHEDULER_AVAILABLE",
        True,
    )
    monkeypatch.setattr(
        "backend.orchestration.workflow.scheduler.AsyncIOScheduler",
        lambda: fake_scheduler,
    )
    return fake_scheduler