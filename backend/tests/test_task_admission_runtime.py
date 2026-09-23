"""tests/test_task_admission_runtime.py — Phase2 Step4 Admission（runtime 接入层）。

覆盖（对齐 Step4 规格 Case K/L/M/Q 的执行链路面）：
  全链路：impl 执行成功 → token acquired → 终态 released（counter 归零）
  Case K resume 满载不绕过：resume 后消息执行时满载 → defer → 任务保持
         PENDING（不突破 limit，容量恢复后自动准入）
  Case L retry countdown 不占容量：TaskRetryScheduled 出口 token 已释放
  Case M recovery defer：stale 认领回 PENDING 后满载 defer，sweep 不重扫
  Case Q defer 重投失败（broker 异常）→ 任务 PENDING、无 token、无泄漏
  defer budget 耗尽 → FAILED(admission_rejected) 终态
  index runtime defer：run_with_task_state 抛 AdmissionDeferred + 回 PENDING
  disabled → 行为与 Step3 基线一致（全放行）
  heartbeat 顺带续 admission token TTL

策略：真实 agent_memory + 真实 Redis（不可达 skip）；stub 图 +
apply_async recorder（broker 外部边界，允许 mock）。
"""
from __future__ import annotations

import time
import uuid

import pytest

from backend.models.task import TaskStatus
from backend.tasks.admission import AdmissionController
from backend.tasks.admission.policy import AdmissionPolicy
from backend.tasks.admission.store import AdmissionStore

from backend.tests.test_task_admission import _TEST_PREFIX  # 同前缀/同 db15


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过 admission runtime 测试: {e}")
    from backend.services import task_service

    return task_service


@pytest.fixture(scope="module")
def redis_client():
    try:
        import redis as redis_lib

        from backend.config.redis import REDIS_URL

        # 同实例换 db15（专用测试库）。host 显式 127.0.0.1：localhost 会
        # 在 ::1/127.0.0.1 间轮换解析，偶发连到不同端点（本机实测）。
        url = _redis_test_url(REDIS_URL)
        client = redis_lib.Redis.from_url(
            url, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3)
        client.ping()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"本地 Redis 不可达，跳过 admission runtime 测试: {e}")
    yield client
    try:
        client.flushdb()
    except Exception:  # noqa: BLE001
        pass


def _redis_test_url(url: str) -> str:
    """REDIS_URL 换 db15 + host 钉死 127.0.0.1（消 DNS 双栈轮换）。"""
    base = url.rsplit("/", 1)[0]
    if "@localhost:" in base:
        base = base.replace("@localhost:", "@127.0.0.1:")
    return base + "/15"


@pytest.fixture(autouse=True)
def _isolated_db(redis_client):
    redis_client.flushdb()
    yield
    redis_client.flushdb()


class _FakeFlags:
    """控制标志桩：一律不存在。"""

    def set(self, key, value, ex=None):
        pass

    def delete(self, *keys):
        pass

    def exists(self, key):
        return 0

    def publish(self, channel, message):
        pass


class _ApplyRecorder:
    """apply_async 替身：记录 (args/kwargs, queue, countdown)。"""

    def __init__(self, exc: Exception | None = None):
        self.calls: list[dict] = []
        self.exc = exc

    def apply_async(self, args=None, kwargs=None, queue=None, countdown=None):
        if self.exc is not None:
            raise self.exc
        self.calls.append({"args": args, "kwargs": kwargs,
                           "queue": queue, "countdown": countdown})

        class _R:
            id = "celery-fake-id"

        return _R()


@pytest.fixture()
def admission_env(redis_client, monkeypatch):
    """注入测试 AdmissionController（可调 policy）+ fake 控制标志。"""
    from backend.tasks import admission, task_manager

    store = AdmissionStore(_TEST_PREFIX, redis_client=redis_client)

    def _install(**policy_kw):
        policy_kw.setdefault("enabled", True)
        policy_kw.setdefault("fail_mode", "closed")
        policy_kw.setdefault("token_ttl_seconds", 120)
        controller = AdmissionController(policy=AdmissionPolicy(**policy_kw),
                                         store=store)
        monkeypatch.setattr("backend.tasks.admission.controller._controller",
                            controller)
        return controller

    monkeypatch.setattr(task_manager, "_redis", lambda: _FakeFlags())
    return {"install": _install, "store": store}


@pytest.fixture()
def stub_graph(monkeypatch):
    """stub 任务图：step_a 成功返回 / 可注入异常。

    授权解析一并 stub（执行时授权属 STOP D 域，非本 Step 被测对象；
    未 stub 时 executor 按工作区 fail-closed 改动拒绝一切非 auth 口径
    user，会掩盖 admission 断言）。
    """
    from types import SimpleNamespace

    from backend.orchestration.checkpoint import task_executor as te
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    fake_ctx = SimpleNamespace(
        principal=SimpleNamespace(
            user_id="test-user", tenant_id="default", department="",
            roles=["user"], subject_type="user"),
        data_scope="all")
    monkeypatch.setattr(
        "backend.security.task_authorization.resolve_task_authorization",
        lambda uid, tid: fake_ctx)

    raise_spec = {"exc": None}

    def step_a(state):
        exc = raise_spec.get("exc")
        if exc is not None:
            raise exc
        return {"final_answer": "done"}

    wf = StateGraph(dict)
    wf.add_node("step_a", step_a)
    wf.add_edge(START, "step_a")
    wf.add_edge("step_a", END)
    monkeypatch.setattr(te, "build_task_graph",
                        lambda: wf.compile(checkpointer=MemorySaver()))
    return raise_spec


def _make_task(pg, *, workflow: str = "main", tenant: str = "t-adm",
               user: str = "u-adm") -> str:
    return pg.create_task(
        user, "admission runtime 测试", tenant_id=tenant,
        graph_name=workflow).id


def _run_impl(task_id: str, *, retries: int = 0):
    from backend.tasks.agent_tasks import execute_agent_task_impl

    return execute_agent_task_impl(task_id, retries=retries)


def _occupy(controller, task_id: str, *, workflow="main", tenant="t-adm",
            user="u-adm", owner="exec-holder"):
    from backend.models.task import TaskRecord

    record = TaskRecord(id=task_id, user_id=user, tenant_id=tenant,
                        graph_name=workflow, status=TaskStatus.PENDING)
    d = controller.acquire_for_execution(
        task_id, owner_execution_id=owner, record=record)
    assert d.allowed
    return d


# ── 全链路：acquire → 执行 → 终态 release ──────────────────────
def test_full_path_releases_on_success(pg, admission_env, stub_graph):
    controller = admission_env["install"](global_limit=5, tenant_limit=None,
                                          user_limit=None, workflow_limits={})
    store = admission_env["store"]
    task_id = _make_task(pg)
    result = _run_impl(task_id)
    assert result["status"] in ("SUCCESS", "done", "PENDING") or \
        result.get("output")
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.SUCCESS
    assert store.get_token(task_id) is None, "终态后 token 必须释放"
    assert store.scope_active("global") == 0
    assert controller.policy.enabled


# ── 满载 defer：任务保持 PENDING、无 token、计划重投 ─────────────
def test_capacity_full_defers_and_keeps_pending(pg, admission_env,
                                                stub_graph, monkeypatch):
    controller = admission_env["install"](global_limit=1, tenant_limit=None,
                                          user_limit=None, workflow_limits={})
    store = admission_env["store"]
    holder = str(uuid.uuid4())
    _occupy(controller, holder)  # global=1 已满

    from backend.tasks import agent_tasks

    recorder = _ApplyRecorder()
    monkeypatch.setattr(agent_tasks, "execute_agent_task", recorder)

    task_id = _make_task(pg)
    result = _run_impl(task_id)
    assert result == {"status": "ADMISSION_DEFERRED"}
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.PENDING, "defer 不得进入 RUNNING"
    assert store.get_token(task_id) is None, "defer 任务不得占 token"
    assert store.get_deferred(task_id) == 1
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["countdown"] >= 1, "按退避延迟 countdown 重投"
    assert recorder.calls[0]["queue"] == "agent"
    # 占位者释放后，重投消息执行即可准入（capacity 恢复自动放行）
    controller.release_for_execution(holder, "exec-holder", reason="success")
    assert store.scope_active("global") == 0


# ── Case Q：defer 重投失败（broker 异常）→ PENDING、无 token ─────
def test_defer_republish_failure_no_leak(pg, admission_env, stub_graph,
                                         monkeypatch):
    controller = admission_env["install"](global_limit=1, tenant_limit=None,
                                          user_limit=None, workflow_limits={})
    store = admission_env["store"]
    _occupy(controller, str(uuid.uuid4()))

    from backend.tasks import agent_tasks

    recorder = _ApplyRecorder(exc=ConnectionError("broker down"))
    monkeypatch.setattr(agent_tasks, "execute_agent_task", recorder)

    task_id = _make_task(pg)
    with pytest.raises(ConnectionError):
        _run_impl(task_id)
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.PENDING, "消息 unacked 由 broker 重投"
    assert store.get_token(task_id) is None, "重投失败不得泄漏 token"
    assert store.scope_active("global") == 1  # 仅 holder


# ── defer budget 耗尽 → FAILED(admission_rejected) ─────────────
def test_defer_budget_exhausted_lands_failed(pg, admission_env, stub_graph,
                                             monkeypatch):
    from backend.tasks.admission import controller as controller_mod

    monkeypatch.setattr(controller_mod, "_env_defer_max_count", lambda: 2)
    controller = admission_env["install"](global_limit=1, tenant_limit=None,
                                          user_limit=None, workflow_limits={})
    store = admission_env["store"]
    _occupy(controller, str(uuid.uuid4()))

    from backend.tasks import agent_tasks

    monkeypatch.setattr(agent_tasks, "execute_agent_task", _ApplyRecorder())

    task_id = _make_task(pg)
    store.note_deferred(task_id)
    store.note_deferred(task_id)
    store.note_deferred(task_id)  # count=3 > max=2
    result = _run_impl(task_id)
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error_type == "admission_rejected"
    assert store.get_token(task_id) is None


# ── Case L：业务 retry 出口 token 已释放（countdown 不占容量）────
def test_business_retry_releases_token(pg, admission_env, stub_graph):
    controller = admission_env["install"](global_limit=10, tenant_limit=None,
                                          user_limit=None, workflow_limits={})
    store = admission_env["store"]

    class _RetryExc(Exception):
        status_code = 429

    stub_graph["exc"] = _RetryExc("rate limited")
    task_id = _make_task(pg, user="u-retry")
    from backend.tasks.retry_policy import TaskRetryScheduled

    with pytest.raises(TaskRetryScheduled):
        _run_impl(task_id)
    assert store.get_token(task_id) is None, \
        "retry countdown 期间不得占用 admission 容量"
    assert store.scope_active("global") == 0


# ── Case K：resume 后满载不绕过（defer，不突破 limit）────────────
def test_resume_full_capacity_not_bypassed(pg, admission_env, stub_graph,
                                           monkeypatch):
    controller = admission_env["install"](global_limit=1, tenant_limit=None,
                                          user_limit=None, workflow_limits={})
    store = admission_env["store"]
    _occupy(controller, str(uuid.uuid4()))

    from backend.tasks import agent_tasks

    recorder = _ApplyRecorder()
    monkeypatch.setattr(agent_tasks, "execute_agent_task", recorder)

    task_id = _make_task(pg, user="u-resume")
    pg.mark_paused_if_pending(task_id, progress="测试暂停")
    assert pg.get_task(task_id).status == TaskStatus.PAUSED
    # resume 链第一步：claim_for_resume 原子认领（PAUSED→PENDING），
    # 随后 dispatch；消息执行时满载 → admission defer → 保持 PENDING
    assert pg.claim_for_resume(task_id, TaskStatus.PAUSED)
    result = _run_impl(task_id)
    assert result == {"status": "ADMISSION_DEFERRED"}
    assert pg.get_task(task_id).status == TaskStatus.PENDING
    assert store.get_token(task_id) is None


# ── Case M：recovery 认领后满载 defer，任务 PENDING 不被 sweep 重扫 ─
def test_recovery_defer_pending_not_rescanned(pg, admission_env, stub_graph,
                                              monkeypatch):
    controller = admission_env["install"](global_limit=1, tenant_limit=None,
                                          user_limit=None, workflow_limits={})
    store = admission_env["store"]
    _occupy(controller, str(uuid.uuid4()))

    from backend.tasks import agent_tasks

    monkeypatch.setattr(agent_tasks, "execute_agent_task", _ApplyRecorder())

    task_id = _make_task(pg, user="u-recovery")
    # 模拟 stale RUNNING（租约过期）→ sweep 认领回 PENDING（Step1 语义）
    pg.try_acquire_lease(task_id, worker="w1",
                         lease_ttl_seconds=1)
    pg.get_task(task_id)  # RUNNING
    time.sleep(1.1)
    assert pg.claim_stale_for_recovery(
        task_id, grace_seconds=0, legacy_threshold_seconds=1,
        max_recoveries=3)
    assert pg.get_task(task_id).status == TaskStatus.PENDING
    # recovery 消息执行：满载 → defer，任务保持 PENDING
    result = _run_impl(task_id)
    assert result == {"status": "ADMISSION_DEFERRED"}
    stale_ids = pg.find_stale_executions(grace_seconds=0,
                                         legacy_threshold_seconds=1)
    assert task_id not in stale_ids, "PENDING 不在 stale 扫描范围（无重扫死循环）"


# ── disabled：行为与 Step3 基线一致 ───────────────────────────
def test_disabled_admission_baseline_behavior(pg, admission_env, stub_graph):
    admission_env["install"](enabled=False, global_limit=0)
    store = admission_env["store"]
    task_id = _make_task(pg, user="u-disabled")
    result = _run_impl(task_id)
    assert pg.get_task(task_id).status == TaskStatus.SUCCESS
    assert store.get_token(task_id) is None
    assert store.scope_active("global") == 0


# ── index runtime：满载 defer → AdmissionDeferred + 回 PENDING ────
def test_index_runtime_defer(pg, admission_env):
    controller = admission_env["install"](global_limit=1, tenant_limit=None,
                                          user_limit=None, workflow_limits={})
    store = admission_env["store"]
    _occupy(controller, str(uuid.uuid4()), workflow="rag_index",
            tenant="t-idx", user="u-idx")

    from backend.services.task_state import TaskManager
    from backend.tasks.admission import AdmissionDeferred
    from backend.tasks.index_task_runtime import run_with_task_state

    task_id = _make_task(pg, workflow="rag_index", tenant="t-idx",
                         user="u-idx")
    with pytest.raises(AdmissionDeferred) as excinfo:
        run_with_task_state(task_id, "upload-x", lambda: {"status": "done"})
    assert excinfo.value.delay_seconds >= 1
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.PENDING, "defer 释放租约回 PENDING"
    assert record.execution_id == ""
    assert store.get_token(task_id) is None


def test_index_runtime_budget_exhausted_marks_failed(pg, admission_env,
                                                     monkeypatch):
    from backend.tasks.admission import controller as controller_mod

    monkeypatch.setattr(controller_mod, "_env_defer_max_count", lambda: 0)
    controller = admission_env["install"](global_limit=1, tenant_limit=None,
                                          user_limit=None, workflow_limits={})
    store = admission_env["store"]
    _occupy(controller, str(uuid.uuid4()), workflow="rag_index",
            tenant="t-idx2", user="u-idx2")

    from backend.tasks.index_task_runtime import run_with_task_state

    task_id = _make_task(pg, workflow="rag_index", tenant="t-idx2",
                         user="u-idx2")
    store.note_deferred(task_id)  # count=1 > max=0
    result = run_with_task_state(task_id, "upload-y",
                                 lambda: {"status": "done"})
    assert result["error"] == "admission_rejected"
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error_type == "admission_rejected"


# ── heartbeat 顺带续 admission token TTL ─────────────────────
def test_heartbeat_renews_admission_token(pg, admission_env):
    controller = admission_env["install"](global_limit=5, tenant_limit=None,
                                          user_limit=None,
                                          workflow_limits={},
                                          token_ttl_seconds=2)
    store = admission_env["store"]
    task_id = _make_task(pg, user="u-hb")

    from backend.config.redis import REDIS_URL
    from backend.tasks.lease_heartbeat import LeaseHeartbeat

    import redis as redis_lib

    # 心跳续期前提 = lease renew 成功（DB 行须为 RUNNING + 匹配 execution_id）
    lease_id = pg.try_acquire_lease(task_id, worker="w-hb",
                                    lease_ttl_seconds=5)
    assert lease_id
    d = controller.acquire_for_execution(
        task_id, owner_execution_id=lease_id, record=pg.get_task(task_id))
    assert d.allowed
    # 复用测试 db15 的同一连接参数（host 钉死 127.0.0.1）
    client = redis_lib.Redis.from_url(_redis_test_url(REDIS_URL),
                                      decode_responses=True)
    scope_keys = [f"{_TEST_PREFIX}count:global",
                  f"{_TEST_PREFIX}count:tenant:t-adm",
                  f"{_TEST_PREFIX}count:user:t-adm:u-hb",
                  f"{_TEST_PREFIX}count:workflow:main"]
    for key in scope_keys:
        client.zadd(key, {task_id: time.time() + 1})

    # 手动预热一轮 renew（线程首连 DB 建立在主线程完成，排除首连延迟
    # 对 0.3s 周期的干扰）；此时 token 续到 now+2
    assert controller.renew_for_execution(task_id, lease_id), \
        "手动 renew 必须成功（lease 活跃 + owner 匹配）"

    hb = LeaseHeartbeat(task_id, lease_id, interval_s=0.3, ttl_s=2)
    hb.start()
    time.sleep(2.5)  # 手动那轮的 score(+2) 在 2s 时过期；此后仍活跃 = hb 在续
    hb.stop()
    time.sleep(0.1)
    dbg_key = store._prefix + "count:global"
    raw = client.zrange(dbg_key, 0, -1, withscores=True)
    print("HB-DEBUG: lost=", hb.lost,
          "key=", dbg_key,
          "raw=", raw,
          "now=", time.time(),
          "scope_active=", store.scope_active("global"),
          "token_owner=", (store.get_token(task_id) or {}).get(
              "owner_execution_id", "MISSING"),
          "expected_owner=", lease_id)
    assert not hb.lost
    assert store.scope_active("global") == 1, "heartbeat 必须顺带续 token"
    controller.release_for_execution(task_id, lease_id, reason="success")
