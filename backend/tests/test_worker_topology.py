"""tests/test_worker_topology.py — Phase2 Step5：Worker Topology 契约测试。

覆盖（对齐 Step5 规格 §四十 Case A-R + §四十一 compose 静态检查）：
  A-E 五条 logical→physical mapping（QueueRouter 唯一事实源）
  F beat maintenance 全量映射；G report 任务映射
  H task_routes 完全由 registry 派生
  I/J 不存在 maintenance/report → agent 旧 mapping
  K-N retry/resume/recovery/admission-defer 队列亲和（dispatch 点静态断言）
  O/P/Q compose 静态检查（worker→queues 与 registry 对齐、
        每 physical queue ≥1 consumer、agent-worker 不消费 maintenance/report）
  R 全仓 Celery task 声明 ⊆ QueueRouter registry（未知任务不得静默落 default）

策略：纯静态/纯函数测试，不依赖 Docker daemon 与运行中 worker（compose
解析直接读仓库 yml，${VAR:-default} 先展开为内置默认值再提取）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.config import tasks as tasks_config
from backend.tasks import queue_router
from backend.tasks.queue_router import (
    _BEAT_TASK_ROUTES,
    _CELERY_TASK_ROUTES,
    _LOGICAL_QUEUES,
    celery_task_routes,
    resolve_for_workflow,
)

_REPO = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO / "docker-compose.yml"


def _resolve_default(text: str) -> str:
    """${VAR:-default} → default（compose 插值的静态默认；测试不依赖 daemon）。"""
    return re.sub(r"\$\{[^:}]+:-([^}]*)\}", r"\1", text)


@pytest.fixture(scope="module")
def compose_worker_queues() -> dict[str, list[str]]:
    """解析 docker-compose.yml → {service: [declared -Q queues]}（静态）。"""
    import yaml

    data = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    result: dict[str, list[str]] = {}
    for name, svc in (data.get("services") or {}).items():
        cmd = svc.get("command")
        cmd_str = _resolve_default(
            " ".join(cmd) if isinstance(cmd, list) else str(cmd or ""))
        if "celery" not in cmd_str or " worker" not in cmd_str:
            continue
        m = re.search(r"-Q\s+([A-Za-z0-9_,]+)", cmd_str)
        assert m, f"{name} 未声明 -Q 队列"
        result[name] = [q.strip() for q in m.group(1).split(",")]
    return result


# ── Case A-E：logical→physical mapping（G1）──────────────────
# 入口口径：interactive_agent/rag_index 有用户 workflow（graph_name=
# main/rag_index）；metadata_shadow/maintenance/report 只经 celery task
# name 入口——各 workload_class 取其真实代表入口断言物理队列。
def test_case_a_interactive_agent_to_agent():
    route = resolve_for_workflow("main")
    assert route.workload_class == "interactive_agent"
    assert route.physical_queue == tasks_config.CELERY_AGENT_QUEUE


def test_case_b_rag_index_to_rag_index():
    route = resolve_for_workflow("rag_index")
    assert route.workload_class == "rag_index"
    assert route.physical_queue == tasks_config.CELERY_RAG_INDEX_QUEUE


def test_case_c_metadata_shadow_to_shadow_queue():
    route = queue_router.resolve_for_celery_task("tasks.execute_metadata_shadow")
    assert route.workload_class == "metadata_shadow"
    assert route.physical_queue == tasks_config.CELERY_METADATA_SHADOW_QUEUE


def test_case_d_maintenance_to_maintenance():
    route = queue_router.resolve_for_celery_task("tasks.zombie_reconcile")
    assert route.workload_class == "maintenance"
    assert route.physical_queue == tasks_config.CELERY_MAINTENANCE_QUEUE


def test_case_e_report_to_report():
    route = queue_router.resolve_for_celery_task("cs.qa_daily_report")
    assert route.workload_class == "report"
    assert route.physical_queue == tasks_config.CELERY_REPORT_QUEUE


def test_physical_names_come_from_config():
    """物理队列名必须来自 config/tasks.py 常量，禁止散落硬编码（§七）。"""
    assert _LOGICAL_QUEUES["interactive_agent"] == tasks_config.CELERY_AGENT_QUEUE
    assert _LOGICAL_QUEUES["rag_index"] == tasks_config.CELERY_RAG_INDEX_QUEUE
    assert _LOGICAL_QUEUES["metadata_shadow"] == tasks_config.CELERY_METADATA_SHADOW_QUEUE
    assert _LOGICAL_QUEUES["maintenance"] == tasks_config.CELERY_MAINTENANCE_QUEUE
    assert _LOGICAL_QUEUES["report"] == tasks_config.CELERY_REPORT_QUEUE


# ── Case F/G：beat routing（G12）─────────────────────────────
def test_case_f_beat_maintenance_all_map_to_maintenance():
    maintenance_tasks = [name for name, (wc, _) in _BEAT_TASK_ROUTES.items()
                         if wc == "maintenance"]
    expected = {
        "cs.handoff_timeout_scan", "cs.confirmation_expiry_scan",
        "cs.event_outbox_compensation", "tasks.zombie_reconcile",
        "tasks.stale_execution_recovery", "model.health_scan",
        "model.health_check_one",
    }
    assert expected <= set(maintenance_tasks), "beat 维护任务必须全量登记 maintenance"
    for name in expected:
        route = queue_router.resolve_for_celery_task(name)
        assert route.physical_queue == tasks_config.CELERY_MAINTENANCE_QUEUE


def test_case_g_qa_daily_report_maps_to_report():
    route = queue_router.resolve_for_celery_task("cs.qa_daily_report")
    assert route.workload_class == "report"
    assert route.physical_queue == tasks_config.CELERY_REPORT_QUEUE


def test_beat_schedule_queues_derived_from_registry():
    """beat_schedule 每项的 options.queue 必须经 beat_queue() 派生（G2）。"""
    from backend.tasks.celery_app import celery_app as app

    for entry in app.conf.beat_schedule.values():
        task_name = entry["task"]
        assert entry["options"]["queue"] == queue_router.beat_queue(task_name), \
            f"beat 项 {task_name} 的 queue 偏离 registry 派生值"


# ── Case H：task_routes 全量由 registry 派生（G21）─────────────
def test_case_h_task_routes_fully_derived():
    routes = celery_task_routes()
    expected = {name: {"queue": _LOGICAL_QUEUES[wc]}
                for table in (_CELERY_TASK_ROUTES, _BEAT_TASK_ROUTES)
                for name, (wc, _r) in table.items()}
    assert routes == expected
    assert set(routes) >= {
        "tasks.execute_agent", "tasks.execute_index",
        "tasks.execute_metadata_shadow", "cs.qa_daily_report",
        "tasks.stale_execution_recovery", "tasks.zombie_reconcile",
        "cs.handoff_timeout_scan", "model.health_scan",
    }


# ── Case I/J：旧 mapping 禁止复活（G2/G3）─────────────────────
def test_case_i_no_maintenance_to_agent():
    assert queue_router.resolve_for_celery_task(
        "tasks.stale_execution_recovery").physical_queue != \
        tasks_config.CELERY_AGENT_QUEUE
    assert queue_router.resolve_for_celery_task(
        "cs.handoff_timeout_scan").workload_class == "maintenance"


def test_case_j_no_report_to_agent():
    assert queue_router.resolve_for_celery_task(
        "cs.qa_daily_report").physical_queue != tasks_config.CELERY_AGENT_QUEUE


# ── Case K-N：queue affinity（G8-G11）────────────────────────
def test_case_k_retry_affinity():
    """业务 retry 重投显式使用 resolve_for_task 的 physical_queue（agent_tasks）。"""
    from backend.tasks import agent_tasks

    src = Path(agent_tasks.__file__).read_text(encoding="utf-8")
    recheck = re.search(
        r"def _retry_after_state_recheck.*?raise task_self\.retry\([^)]*\)",
        src, re.S)
    assert recheck, "retry 壳函数缺失"
    body = recheck.group(0)
    assert "resolve_for_task(record)" in body
    assert "queue=route.physical_queue" in body


def test_case_l_resume_affinity():
    """resume 经 dispatch_task → _WORKFLOW_DISPATCHERS，队列决策在 QueueRouter。"""
    from backend.tasks import task_manager

    src = Path(task_manager.__file__).read_text(encoding="utf-8")
    dispatch = re.search(
        r"def dispatch_task.*?return dispatcher\(record, dispatch_type\)",
        src, re.S)
    assert dispatch and "resolve_for_task" in dispatch.group(0)
    assert "_WORKFLOW_DISPATCHERS" in dispatch.group(0)


def test_case_m_recovery_affinity():
    """recovery 重投走 dispatch_task（QueueRouter），不落 default queue。"""
    from backend.tasks import task_manager

    src = Path(task_manager.__file__).read_text(encoding="utf-8")
    sweep = re.search(r"def sweep_stale_executions.*?return \{", src, re.S)
    assert sweep and 'dispatch_task(record, dispatch_type="recovery")' in \
        sweep.group(0)


def test_case_n_admission_defer_affinity():
    """admission defer 重投保持原 workload 队列（agent: resolve_for_task；index: rag_index）。"""
    from backend.tasks import agent_tasks, index_tasks

    agent_src = Path(agent_tasks.__file__).read_text(encoding="utf-8")
    defer_fn = re.search(r"def _defer_admission.*?countdown=delay\)", agent_src, re.S)
    assert defer_fn, "agent defer 出口缺失"
    assert "resolve_for_task(record)" in defer_fn.group(0)
    assert "queue=route.physical_queue" in defer_fn.group(0)

    index_src = Path(index_tasks.__file__).read_text(encoding="utf-8")
    index_defer = re.search(
        r"except AdmissionDeferred as deferred:.*?countdown=deferred\.delay_seconds",
        index_src, re.S)
    assert index_defer, "index defer 壳层缺失"
    assert 'resolve_for_workflow("rag_index")' in index_defer.group(0)


# ── Case O/P/Q：compose 静态检查（G6/G7/G2/G3）────────────────
def test_case_o_p_every_physical_queue_has_consumer(compose_worker_queues):
    routed_queues = set(_LOGICAL_QUEUES.values())
    declared = {q for queues in compose_worker_queues.values() for q in queues}
    assert declared == routed_queues, (
        f"compose 声明与 registry 不一致：多出={declared - routed_queues} "
        f"缺失={routed_queues - declared}")


def test_case_q_no_cross_workload_consumption(compose_worker_queues):
    """无 worker 消费非目标 workload；agent-worker 不消费 maintenance/report。"""
    for name, queues in compose_worker_queues.items():
        assert len(queues) == 1, f"{name} 必须单队列（1 pool ≈ 1 workload）"
        assert queues[0] in set(_LOGICAL_QUEUES.values()), \
            f"{name} 消费未登记队列 {queues[0]}"
    agent_queues = compose_worker_queues.get("agent-worker", [])
    assert "maintenance" not in agent_queues and "report" not in agent_queues
    # 五 worker 与五 workload 一一对应
    assert set(compose_worker_queues) == {
        "agent-worker", "rag-index-worker", "metadata-shadow-worker",
        "maintenance-worker", "report-worker"}


def test_case_o_concurrency_explicit_per_worker():
    """每个 worker 的 --concurrency 均显式 env 化（G13），不依赖 Celery 默认。"""
    import yaml

    data = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    for name, svc in (data.get("services") or {}).items():
        cmd = svc.get("command")
        cmd_str = _resolve_default(
            " ".join(cmd) if isinstance(cmd, list) else str(cmd or ""))
        if "celery" not in cmd_str or " worker" not in cmd_str:
            continue
        assert "--concurrency=" in cmd_str, f"{name} concurrency 未显式配置"
    # prefetch 基线：celery_app 全局显式 worker_prefetch_multiplier=1（G14）
    from backend.tasks.celery_app import celery_app as app

    assert app.conf.worker_prefetch_multiplier == 1


# ── Case R：registry consistency（G21，防 default queue 静默兜底）──
def test_case_r_all_celery_tasks_registered():
    """全仓 @celery_app.task(name=...) 声明必须全部登记在 QueueRouter。

    例外清单 = framework/internal（显式登记，登记新例外须在此注明理由）。
    """
    declared: set[str] = set()
    pattern = re.compile(
        r"@celery_app\.task\(\s*name=[\"']([^\"']+)[\"']", re.S)
    for py in (_REPO / "backend" / "tasks").rglob("*.py"):
        if py.name.startswith("test"):
            continue
        declared |= set(pattern.findall(py.read_text(encoding="utf-8")))
    registered = set(_CELERY_TASK_ROUTES) | set(_BEAT_TASK_ROUTES)
    framework_internal: set[str] = set()  # 当前无 framework task
    unknown = declared - registered - framework_internal
    assert not unknown, (
        f"未登记 QueueRouter 的 Celery task（禁止静默落 default queue）: {sorted(unknown)}")
