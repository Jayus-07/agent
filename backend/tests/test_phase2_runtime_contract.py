"""Phase2 异步任务运行时冻结契约（Final Closure，2026-09-24）。

只检查不可破坏的运行时不变量（不复制行为测试）：
  C1 registered task → QueueRouter 可解析路由（无 unknown fallback 漏网）
  C2 physical queue → compose 存在专属 consumer（worker -Q）
  C3 受控执行入口 → lease + admission 顺序接线（源码断言）
  C4 side-effect 路径 → guard / 天然幂等声明
  C5 beat 每项 queue 一律经 beat_queue 派生（单一事实源）
  C6 fencing / owner CAS 原语存在（旧 execution 不得破坏新 owner）

核心文件清单由当前 QueueRouter、队列配置与 compose 定义；对本清单文件的修改
必须触发本契约回归。
"""
from __future__ import annotations

import inspect
import pathlib
import re

# ── C1：全部注册 task 必须可路由 ──────────────────────────────

def _registered_task_names() -> set[str]:
    from backend.tasks.celery_app import celery_app

    # 纯 import 不触发 include 模块加载，必须显式 import 才能拿到全量注册表
    celery_app.loader.import_default_modules()
    tasks = dict(celery_app.tasks or {})
    return {
        name for name in tasks
        if not name.startswith("celery.")  # celery 内建（celery.backend_cleanup 等）
    }


def test_c1_every_registered_task_routes_via_queue_router():
    from backend.tasks import queue_router

    names = _registered_task_names()
    assert names, "celery task 注册表为空（include 失效？）"
    unroutable = []
    for name in sorted(names):
        try:
            queue_router.resolve_for_celery_task(name)
        except Exception as exc:  # noqa: BLE001
            unroutable.append(f"{name}: {exc}")
    assert not unroutable, (
        "存在未登记 QueueRouter 路由的 task（Step3 冻结面被破坏）: " + ", ".join(unroutable))


def test_c1_beat_entries_use_beat_queue():
    """beat 每项 options.queue 必须等于 beat_queue(task)（禁止手写队列）。"""
    from backend.tasks import queue_router
    from backend.tasks.celery_app import celery_app

    for entry_name, entry in (celery_app.conf.beat_schedule or {}).items():
        task = entry.get("task", "")
        expected = queue_router.beat_queue(task)
        actual = (entry.get("options") or {}).get("queue")
        assert actual == expected, (
            f"beat[{entry_name}] queue={actual} != beat_queue({task})={expected}")


# ── C2：物理队列必须有 consumer ───────────────────────────────

def test_c2_every_physical_queue_has_worker_consumer():
    compose = (
        pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml"
    ).read_text(encoding="utf-8")
    from backend.tasks import queue_router

    queues = set(queue_router._LOGICAL_QUEUES.values())
    missing = []
    for q in sorted(queues):
        # worker command 里的 -Q 队列名（${VAR:-default} 形式也命中 default）
        pattern = re.compile(r'"-Q",\s*"\$\{[A-Z_]+:-(?P<q>[a-z_]+)\}"')
        consumed = {m.group("q") for m in pattern.finditer(compose)}
        if q not in consumed:
            missing.append(q)
    assert not missing, f"无 worker 消费的物理队列（任务会永久滞留）: {missing}"


# ── C3：受控执行入口 lease → admission 顺序 ───────────────────

def test_c3_agent_impl_has_lease_then_admission():
    import backend.tasks.agent_tasks as mod

    src = inspect.getsource(mod.execute_agent_task_impl)
    lease_pos = src.index("try_acquire_lease")
    admission_pos = src.index("admission.acquire_for_execution")
    execute_pos = src.index("executor.execute(")
    assert lease_pos < admission_pos < execute_pos, (
        "execute_agent 顺序必须是 lease → admission → 业务执行（Gate C/D）")


def test_c3_index_runtime_has_lease_then_admission():
    import backend.tasks.index_task_runtime as mod

    src = inspect.getsource(mod.run_with_task_state)
    lease_pos = src.index("try_acquire_lease")
    admission_pos = src.index("admission.acquire_for_execution")
    assert lease_pos < admission_pos, (
        "index runtime 顺序必须是 lease → admission（Gate C/D）")


def test_c3_defer_releases_lease_before_redispatch():
    """admission 拒绝 → 释放租约再 defer 重投（容量不泄漏）。"""
    import backend.tasks.agent_tasks as mod

    src = inspect.getsource(mod)
    assert "release_lease_for_defer" in src, "defer 路径必须释放租约"


# ── C4：side-effect 路径 guard / 幂等声明 ────────────────────

def test_c4_cs_confirm_execution_guarded():
    import backend.customer_service.confirmation_flow as mod

    src = inspect.getsource(mod._handle_confirm)
    assert "claim_for_execution" in src, "确认认领闸门丢失"
    assert "_execute_confirmed_action" in src, "执行幂等边界丢失"
    guard_src = inspect.getsource(mod._execute_confirmed_action)
    assert "run_idempotent_side_effect" in guard_src, (
        "CS 确认执行必须走 PG durable ledger guard")


def test_c4_side_effect_tools_wired_to_global_idempotency():
    for module, expected in (
        ("backend.tools.email", "run_idempotent_operation"),
        ("backend.tools.export", "run_idempotent_operation"),
        ("backend.tools.data_collection", "run_idempotent_operation"),
        ("backend.tools.competitor", "run_idempotent_operation"),
    ):
        mod = __import__(module, fromlist=["x"])
        src = inspect.getsource(mod)
        assert expected in src, f"{module} 未接入全局幂等"


def test_c4_task_body_binds_identity():
    """任务体必须绑定租户身份（工具在 Celery 上下文获得全局幂等）。"""
    import backend.tasks.agent_tasks as mod

    src = inspect.getsource(mod.execute_agent_task_impl)
    assert "_bind_task_identity" in src


# ── C5：fencing / owner CAS 原语 ─────────────────────────────

def test_c5_fencing_primitives_present():
    from backend.services import task_service

    assert hasattr(task_service, "check_lease_active"), "fencing 只读校验丢失"
    assert hasattr(task_service, "renew_lease"), "租约续期丢失"
    src = inspect.getsource(task_service.update_status)
    assert "execution_id" in src, "状态写必须带 execution fencing"


def test_c5_idempotency_owner_cas():
    from backend.shared import idempotency as idem

    ledger_src = inspect.getsource(idem.PostgresIdempotencyLedgerStore._finish)
    assert "lease_id" in ledger_src and "owner_execution_id" in ledger_src, (
        "ledger 终态写必须做 (lease_id, owner) CAS")


def test_c5_stale_claim_never_blind_retry():
    """接管语义：running+租约过期无判定时必须保守阻断（IN_DOUBT）。"""
    from backend.shared import idempotency as idem

    src = inspect.getsource(idem.PostgresIdempotencyLedgerStore._decide_claim)
    assert "_UNCERTAIN_ERROR_CODE" in src, "stale claim 缺 IN_DOUBT 阻断分支"


# ── C6：recovery 保持 workload affinity ──────────────────────

def test_c6_recovery_redispatch_via_workflow_dispatchers():
    import backend.tasks.task_manager as mod

    src = inspect.getsource(mod.dispatch_task)
    assert "_WORKFLOW_DISPATCHERS" in src, (
        "recovery/resume 重投必须经 workflow dispatcher 表（QueueRouter 单一事实源）")
