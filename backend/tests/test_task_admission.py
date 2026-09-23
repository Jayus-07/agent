"""tests/test_task_admission.py — Phase2 Step4 Admission Control（组件层）。

覆盖（对齐 Step4 规格 §三十 Case A-T 的 admission 组件面）：
  A global limit / B tenant 隔离 / C user 隔离 / D workflow 隔离
  E 并发竞态原子性（多线程抢 limit=5，恰好 5 成功）
  F 多层零污染（tenant 满时 global/user/workflow counter 不动）
  G release 幂等 / H TTL 过期容量自愈
  I/J main 与 rag_index 经 QueueRouter 的 workload 口径
  L retry takeover 不双占 / M recovery owner CAS 不误删
  N/O/P cancel-success-failure 释放
  S Redis 不可用 fail-closed / fail-open break-glass
  T 跨租户并发压力四层 counter 归零

策略：真实 Redis（db15 + 专用前缀，测后清理；不可达则 skip 整组）——
Lua 原子性无法在 fake 上证明，必须真库验证。
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from backend.models.task import TaskRecord, TaskStatus
from backend.tasks.admission import AdmissionController
from backend.tasks.admission.models import (
    REASON_INTERNAL_ERROR,
    REASON_REDIS_UNAVAILABLE,
)
from backend.tasks.admission.policy import AdmissionPolicy
from backend.tasks.admission.store import AdmissionStore

_TEST_PREFIX = "agent:task:admission:test:"


def _record(workflow: str = "main", tenant_id: str = "t-default",
            user_id: str = "u-default", **kw) -> TaskRecord:
    return TaskRecord(id=kw.pop("task_id", str(uuid.uuid4())),
                      user_id=user_id, tenant_id=tenant_id,
                      graph_name=workflow, status=TaskStatus.PENDING)


def _policy(**kw) -> AdmissionPolicy:
    kw.setdefault("enabled", True)
    kw.setdefault("fail_mode", "closed")
    kw.setdefault("token_ttl_seconds", 120)
    return AdmissionPolicy(**kw)


@pytest.fixture(scope="module")
def redis_client():
    """真实 Redis（db15 专用库）；不可达则跳过整组（Lua 无法 fake）。"""
    try:
        import redis as redis_lib

        from backend.config.redis import REDIS_URL

        # 同实例换 db15（专用测试库）；host 钉死 127.0.0.1——localhost
        # 会双栈轮换解析，偶发连到不同端点（本机实测坑）
        base = REDIS_URL.rsplit("/", 1)[0]
        if "@localhost:" in base:
            base = base.replace("@localhost:", "@127.0.0.1:")
        client = redis_lib.Redis.from_url(
            base + "/15", decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3)
        client.ping()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"本地 Redis 不可达，跳过 admission 组件测试: {e}")
    yield client
    try:
        client.flushdb()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _isolated_db(redis_client):
    """用例级隔离：admission scope key 全局共享（global ZSET），
    每个用例前后 flush 专用测试库，杜绝跨用例计数残留。"""
    redis_client.flushdb()
    yield
    redis_client.flushdb()


@pytest.fixture()
def store(redis_client):
    return AdmissionStore(_TEST_PREFIX, redis_client=redis_client)


@pytest.fixture()
def ctrl(store):
    def _make(**policy_kw):
        return AdmissionController(policy=_policy(**policy_kw), store=store)
    return _make


def _acquire_ok(controller, task_id, *, workflow="main", tenant="t-default",
                user="u-default", owner="exec-1"):
    record = _record(workflow, tenant, user, task_id=task_id)
    return controller.acquire_for_execution(
        task_id, owner_execution_id=owner, record=record)


# ── Case A：global limit ─────────────────────────────────────
def test_case_a_global_limit(ctrl, store):
    c = ctrl(global_limit=2, tenant_limit=None, user_limit=None,
             workflow_limits={})
    t1, t2, t3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    assert _acquire_ok(c, t1).allowed
    assert _acquire_ok(c, t2).allowed
    d3 = _acquire_ok(c, t3)
    assert not d3.allowed and d3.reason == "global_limit"
    assert d3.scope == "global" and d3.current == 2 and d3.limit == 2
    assert store.scope_active("global") == 2


# ── Case B：tenant 隔离 ──────────────────────────────────────
def test_case_b_tenant_isolation(ctrl, store):
    c = ctrl(tenant_limit=2, global_limit=None, user_limit=None,
             workflow_limits={})
    a1, a2, a3, b1 = (str(uuid.uuid4()) for _ in range(4))
    assert _acquire_ok(c, a1, tenant="A").allowed
    assert _acquire_ok(c, a2, tenant="A").allowed
    d3 = _acquire_ok(c, a3, tenant="A")
    assert not d3.allowed and d3.reason == "tenant_limit" and d3.scope == "tenant"
    assert _acquire_ok(c, b1, tenant="B").allowed, "tenant B 不受 A 满载影响"
    assert store.scope_active("tenant", tenant_id="A") == 2
    assert store.scope_active("tenant", tenant_id="B") == 1


# ── Case C：user 隔离 ────────────────────────────────────────
def test_case_c_user_isolation(ctrl, store):
    c = ctrl(user_limit=1, global_limit=None, tenant_limit=None,
             workflow_limits={})
    u1a, u1b, u2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    assert _acquire_ok(c, u1a, user="U1").allowed
    d = _acquire_ok(c, u1b, user="U1")
    assert not d.allowed and d.reason == "user_limit" and d.scope == "user"
    assert _acquire_ok(c, u2, user="U2").allowed, "同租户 U2 不受 U1 满载影响"


# ── Case D：workflow 隔离 ────────────────────────────────────
def test_case_d_workflow_isolation(ctrl, store):
    c = ctrl(global_limit=None, tenant_limit=None, user_limit=None,
             workflow_limits={"main": 1, "rag_index": 1})
    m1, m2, r1 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    assert _acquire_ok(c, m1, workflow="main").allowed
    d = _acquire_ok(c, m2, workflow="main")
    assert not d.allowed and d.reason == "workflow_limit" and d.scope == "workflow"
    assert _acquire_ok(c, r1, workflow="rag_index").allowed, \
        "main 满不阻塞 rag_index"


# ── Case E：并发竞态原子性（规格：limit=5 时 100 并发恰好 5 成功）──
def test_case_e_concurrent_no_overcommit(ctrl, store):
    c = ctrl(global_limit=5, tenant_limit=None, user_limit=None,
             workflow_limits={})
    successes: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def _worker(n):
        task_id = str(uuid.uuid4())
        barrier.wait()  # 20 线程同时放行，最大化竞态窗口
        d = _acquire_ok(c, task_id, owner=f"exec-{n}")
        with lock:
            if d.allowed:
                successes.append(task_id)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(successes) == 5, f"并发超卖: {len(successes)} 成功（应为 5）"
    assert store.scope_active("global") == 5


# ── Case F：多层零污染（一次失败不留任何层写入）─────────────────
def test_case_f_rejection_pollutes_no_counter(ctrl, store):
    c = ctrl(tenant_limit=1, global_limit=10, user_limit=10,
             workflow_limits={"main": 10})
    ok_id, blocked = str(uuid.uuid4()), str(uuid.uuid4())
    assert _acquire_ok(c, ok_id, tenant="A", user="U1").allowed
    d = _acquire_ok(c, blocked, tenant="A", user="U1")
    assert not d.allowed and d.reason == "tenant_limit"
    # tenant 满拒绝后：global/user/workflow counter 均不得包含被拒 task
    assert store.scope_active("global") == 1
    assert store.scope_active("user", tenant_id="A", user_id="U1") == 1
    assert store.scope_active("workflow", workflow="main") == 1
    assert store.get_token(blocked) is None, "被拒任务不得留下 token"
    # 换租户后原任务可进入（证明拒绝非残留态）
    assert _acquire_ok(c, blocked, tenant="B", user="U2").allowed


# ── Case G：release 幂等 ─────────────────────────────────────
def test_case_g_release_idempotent(ctrl, store):
    c = ctrl(global_limit=2, tenant_limit=None, user_limit=None,
             workflow_limits={})
    task_id = str(uuid.uuid4())
    assert _acquire_ok(c, task_id).allowed
    assert c.release_for_execution(task_id, "exec-1", reason="success")
    assert not c.release_for_execution(task_id, "exec-1", reason="success"), \
        "二次 release 必须 no-op"
    assert store.scope_active("global") == 0, "重复 release 不得出现 -1"
    assert store.get_token(task_id) is None


# ── Case H：TTL 过期容量自愈（worker crash 无 finally）──────────
def test_case_h_ttl_expiry_reclaims_capacity(ctrl, store):
    c = ctrl(global_limit=1, tenant_limit=None, user_limit=None,
             workflow_limits={}, token_ttl_seconds=1)
    task_id = str(uuid.uuid4())
    assert _acquire_ok(c, task_id).allowed
    d2 = _acquire_ok(c, str(uuid.uuid4()))
    assert not d2.allowed
    time.sleep(1.3)  # token 过期（模拟 worker kill -9，无任何释放调用）
    assert store.scope_active("global") == 0, "TTL 过期后容量必须自愈"
    assert store.get_token(task_id) is None
    assert _acquire_ok(c, str(uuid.uuid4())).allowed, "过期后新任务可准入"


# ── Case I/J：QueueRouter workload 口径（main→agent / rag_index）──
def test_case_i_j_queue_router_workload_binding(ctrl):
    from backend.tasks.queue_router import resolve_for_workflow

    assert resolve_for_workflow("main").physical_queue == "agent"
    assert resolve_for_workflow("rag_index").physical_queue == "rag_index"
    # admission 消费 QueueRouter 的 workload_class，不建第二份映射
    from backend.tasks.admission.controller import _workload_class

    assert _workload_class("main") == "interactive_agent"
    assert _workload_class("rag_index") == "rag_index"


# ── Case L：retry takeover 不双占 ────────────────────────────
def test_case_l_retry_takeover_no_double_slot(ctrl, store):
    c = ctrl(global_limit=10, tenant_limit=None, user_limit=None,
             workflow_limits={})
    task_id = str(uuid.uuid4())
    d1 = _acquire_ok(c, task_id, owner="exec-first")
    assert d1.allowed and not d1.takeover
    d2 = _acquire_ok(c, task_id, owner="exec-second")
    assert d2.allowed and d2.takeover, "同任务重入 = takeover（计数不变）"
    assert store.scope_active("global") == 1, "takeover 不得 +1 计数"
    assert d2.token_id == d1.token_id, "takeover 继承既有 token_id"
    # 旧 owner 释放不得误删新 owner 的槽位（与 Case M 同源语义）
    assert not c.release_for_execution(task_id, "exec-first")
    assert store.scope_active("global") == 1
    assert c.release_for_execution(task_id, "exec-second")
    assert store.scope_active("global") == 0


# ── Case M：recovery owner CAS（旧 token 被接管、不误删）────────
def test_case_m_recovery_takeover_and_cas(ctrl, store):
    c = ctrl(global_limit=1, tenant_limit=None, user_limit=None,
             workflow_limits={})
    task_id = str(uuid.uuid4())
    assert _acquire_ok(c, task_id, owner="exec-crashed").allowed
    # worker crash 后 recovery 重投：新 execution acquire（takeover 同槽）
    d = _acquire_ok(c, task_id, owner="exec-recovered")
    assert d.allowed and d.takeover
    # 旧 owner（crash 前的 worker 复活竞态）release/renew 均无效
    assert not c.release_for_execution(task_id, "exec-crashed")
    assert not c.renew_for_execution(task_id, "exec-crashed")
    assert store.scope_active("global") == 1, "最终仅一个 active slot"
    assert c.renew_for_execution(task_id, "exec-recovered")
    assert c.release_for_execution(task_id, "exec-recovered")
    assert store.scope_active("global") == 0


# ── Case N/O/P：cancel / success / failure 释放 ──────────────
@pytest.mark.parametrize("reason", ["cancelled", "success", "failed"])
def test_case_n_o_p_terminal_release(ctrl, store, reason):
    c = ctrl(global_limit=1, tenant_limit=None, user_limit=None,
             workflow_limits={})
    task_id = str(uuid.uuid4())
    assert _acquire_ok(c, task_id).allowed
    assert store.scope_active("global") == 1
    assert c.release_for_execution(task_id, "exec-1", reason=reason)
    assert store.scope_active("global") == 0
    assert store.get_token(task_id) is None


# ── Case R：未知 workflow 不被 admission 吞（QueueRouter fail-closed）──
def test_case_r_unknown_workflow_fail_closed_before_admission():
    from backend.tasks.queue_router import QueueRoutingError, resolve_for_workflow

    with pytest.raises(QueueRoutingError):
        resolve_for_workflow("no-such-workflow"),


# ── Case S：Redis 不可用 fail-closed / fail-open ─────────────
def test_case_s_redis_unavailable_fail_closed(monkeypatch):
    def _no_redis():
        return None

    monkeypatch.setattr(
        "backend.infra.redis.client.get_redis", _no_redis)
    broken_store = AdmissionStore(_TEST_PREFIX, redis_client=None)
    closed = AdmissionController(policy=_policy(fail_mode="closed"),
                                 store=broken_store)
    d = closed.acquire_for_execution(
        str(uuid.uuid4()), owner_execution_id="exec-1",
        record=_record())
    assert not d.allowed
    assert d.reason in (REASON_REDIS_UNAVAILABLE, REASON_INTERNAL_ERROR)
    assert not d.fallback, "closed 模式不得放行"


def test_case_s_redis_unavailable_fail_open_break_glass(monkeypatch, caplog):
    monkeypatch.setattr(
        "backend.infra.redis.client.get_redis", lambda: None)
    broken_store = AdmissionStore(_TEST_PREFIX, redis_client=None)
    opener = AdmissionController(policy=_policy(fail_mode="open"),
                                 store=broken_store)
    d = opener.acquire_for_execution(
        str(uuid.uuid4()), owner_execution_id="exec-1", record=_record())
    assert d.allowed and d.fallback, "open 为显式 break-glass 放行"
    assert "fail_open" in caplog.text, "break-glass 必须可观测"


# ── Case T：跨租户并发压力，四层 counter 归零 ──────────────────
def test_case_t_cross_tenant_concurrent_all_counters_zero(ctrl, store):
    c = ctrl(global_limit=50, tenant_limit=20, user_limit=10,
             workflow_limits={"main": 30})
    lock = threading.Lock()

    def _cycle(tenant: str, user: str, n: int):
        task_id = f"{tenant}-{user}-{n}-{uuid.uuid4().hex[:8]}"
        d = _acquire_ok(c, task_id, tenant=tenant, user=user)
        if d.allowed:
            c.release_for_execution(task_id, "exec-1", reason="success")

    threads = []
    for i in range(30):
        tenant = f"T{i % 2}"
        threads.append(threading.Thread(
            target=_cycle, args=(tenant, f"U{i % 4}", i)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    time.sleep(0.05)
    assert store.scope_active("global") == 0
    for tenant in ("T0", "T1"):
        assert store.scope_active("tenant", tenant_id=tenant) == 0
    for i in range(4):
        assert store.scope_active(
            "user", tenant_id=f"T{i % 2}", user_id=f"U{i % 4}") == 0
    assert store.scope_active("workflow", workflow="main") == 0


# ── defer 退避与 budget ──────────────────────────────────────
def test_defer_backoff_bounded_with_jitter(ctrl, store, monkeypatch):
    from backend.tasks.admission import controller as controller_mod

    monkeypatch.setattr(controller_mod, "_env_defer_initial", lambda: 10)
    monkeypatch.setattr(controller_mod, "_env_defer_max", lambda: 60)
    monkeypatch.setattr(controller_mod, "_env_defer_jitter", lambda: True)
    c = ctrl()
    task_id = str(uuid.uuid4())
    delays = [c.note_deferred(task_id, workflow="main") for _ in range(8)]
    assert delays[0] <= 12.5, "首次 defer ≈ initial × jitter"
    assert all(d <= 75 for d in delays), "退避必须封顶（60 × jitter 上界）"
    assert delays[-1] > delays[0], "指数增长形态"
    # budget：独立 task_id（defer 计数按任务隔离），max_count=3
    monkeypatch.setattr(controller_mod, "_env_defer_max_count", lambda: 3)
    task_id2 = str(uuid.uuid4())
    for _ in range(3):
        c.note_deferred(task_id2)
    assert not c.defer_budget_exhausted(task_id2), "count=3 未超 max=3"
    c.note_deferred(task_id2)
    assert c.defer_budget_exhausted(task_id2), "count=4 > max=3 必须可判定"
    c.store.clear_deferred(task_id2)
    assert not c.defer_budget_exhausted(task_id2)


# ── disabled 开关（显式关闭 = 全放行）────────────────────────
def test_disabled_policy_allows_all(ctrl, store):
    c = AdmissionController(policy=_policy(enabled=False, global_limit=0),
                            store=store)
    d = _acquire_ok(c, str(uuid.uuid4()))
    assert d.allowed and d.reason == "disabled"
    assert store.scope_active("global") == 0, "disabled 不写任何计数"


# ── 对账：token 在但任务终态 → 释放；过期成员清理 ─────────────
def test_reconcile_releases_terminal_tokens(ctrl, store, monkeypatch):
    task_id = str(uuid.uuid4())
    record = _record(task_id=task_id)
    c = ctrl()
    assert c.acquire_for_execution(
        task_id, owner_execution_id="exec-1", record=record).allowed
    # DB 无此任务行 → reconcile 按"任务已终态/不存在"口径释放
    monkeypatch.setattr(
        "backend.services.task_service.get_task", lambda tid: None,
        raising=False)
    report = c.reconcile_admission_state()
    assert store.scope_active("global") == 0
    assert task_id in report["stale_released"]
