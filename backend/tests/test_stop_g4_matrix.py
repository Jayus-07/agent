"""STOP G4 — 真 Redis 多 worker / 故障恢复实测矩阵（任务书 §20，T1-T16）。

「多 worker」模拟：每个 worker 持有**独立的** RedisConversationContextRepository
实例（各自独立 fallback Memory = 独立进程地址空间），共享同一个真实 Redis
（db15 专用测试库）——跨实例可见性只能来自 Redis，与生产语义同构。
Redis 不可达则整组 skip（本矩阵的价值就在真实例，不 mock repository）。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

import backend.config.travel as T
import backend.travel.graph_builder as gb
from backend.orchestration.context.context_repository import (
    ContextBackendUnavailable,
    ContextMutation,
    MemoryConversationContextRepository,
    MutationType,
    RedisConversationContextRepository,
    reset_conversation_context_repository,
)
from backend.orchestration.context.conversation_context import (
    mark_travel_run_completed,
    sync_travel_run_to_context,
)
from backend.orchestration.context.routing_context import (
    assemble_routing_context,
    mark_domain_turn,
)
from backend.orchestration.context.travel_pending_resolver import (
    resolve_travel_pending,
)
from backend.travel.graph_builder import build_travel_graph

# ── 真 Redis fixture（沿用 tests/test_task_admission.py 先例：db15 专用库）──


@pytest.fixture(scope="module")
def redis_client():
    try:
        import redis as redis_lib

        from backend.config.redis import REDIS_URL

        base = REDIS_URL.rsplit("/", 1)[0]
        if "@localhost:" in base:
            base = base.replace("@localhost:", "@127.0.0.1:")
        client = redis_lib.Redis.from_url(
            base + "/15", decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3)
        client.ping()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"本地 Redis 不可达，跳过 STOP G4 多 worker 实测: {e}")
    yield client
    try:
        client.flushdb()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _isolated_db(redis_client):
    redis_client.flushdb()
    yield
    redis_client.flushdb()


class _WorkerRepo(RedisConversationContextRepository):
    """worker 仓库：真实 Redis 逻辑 + client 钉到测试 db15（免污染 db0）。"""

    def __init__(self, client, **kw):
        super().__init__(**kw)
        self._test_client = client

    def _client(self):
        return self._test_client


@pytest.fixture()
def worker_a(redis_client):
    return _WorkerRepo(redis_client, fallback=MemoryConversationContextRepository())


@pytest.fixture()
def worker_b(redis_client):
    return _WorkerRepo(redis_client, fallback=MemoryConversationContextRepository())


@pytest.fixture()
def worker_c(redis_client):
    return _WorkerRepo(redis_client, fallback=MemoryConversationContextRepository())


def _bind_global(monkeypatch, repo):
    """把生产单例绑到指定 worker 仓库（模拟请求落哪个 worker 进程）。"""
    import backend.orchestration.context.context_repository as repo_mod

    monkeypatch.setattr(repo_mod, "_repo", repo)


def _tid() -> str:
    return f"t-g4-{uuid4().hex[:8]}"


# ============================================================
# T1：worker switch — pending continuation
# ============================================================

def test_t1_worker_switch_pending_continuation(monkeypatch, worker_a, worker_b):
    tid = _tid()
    _bind_global(monkeypatch, worker_a)
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "福州"},
                               missing_slots=["days"])
    mark_domain_turn("t-g4", "u-g4", tid, domain="travel",
                     action="travel_graph_node")

    # 切到 worker-B：全新进程地址空间，上下文必须从 Redis 命中
    _bind_global(monkeypatch, worker_b)
    run_on_a = worker_b.get("t-g4", "u-g4", tid).travel_run_id
    update = resolve_travel_pending("三天", assemble_routing_context(
        "t-g4", "u-g4", tid))
    assert update is not None
    assert update["travel_context"]["travel_route"]["resume_mode"] == "continue"

    # worker-B 补槽出单：same run
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "福州", "days": 3},
                               missing_slots=[])
    ctx = worker_b.get("t-g4", "u-g4", tid)
    assert ctx.travel_run_id == run_on_a
    assert ctx.travel_stage == "planned"
    assert ctx.travel_pending is None
    assert ctx.days == 3


# ============================================================
# T2：worker switch — PATCH
# ============================================================

def test_t2_worker_switch_patch_keeps_run(monkeypatch, worker_a, worker_b):
    tid = _tid()
    _bind_global(monkeypatch, worker_a)
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "大阪", "days": 3},
                               missing_slots=[])
    run_id = worker_a.get("t-g4", "u-g4", tid).travel_run_id

    _bind_global(monkeypatch, worker_b)
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "大阪", "days": 3,
                                      "budget_cny": 60000.0},
                               missing_slots=[])
    ctx = worker_b.get("t-g4", "u-g4", tid)
    assert ctx.travel_run_id == run_id            # same run
    assert ctx.budget_cny == 60000.0              # PATCH 落位


# ============================================================
# T3：worker switch — NEW_RUN
# ============================================================

def test_t3_worker_switch_new_run(monkeypatch, worker_a, worker_b):
    tid = _tid()
    _bind_global(monkeypatch, worker_a)
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "大阪", "days": 3},
                               missing_slots=[])
    old_run = worker_a.get("t-g4", "u-g4", tid).travel_run_id

    _bind_global(monkeypatch, worker_b)
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "杭州", "days": 2},
                               missing_slots=[], new_run=True)
    ctx = worker_b.get("t-g4", "u-g4", tid)
    assert ctx.travel_run_id != old_run
    assert ctx.destination == "杭州"
    assert ctx.days == 2


# ============================================================
# T4/T5/T6：多 worker 三元隔离（真 Redis key 契约）
# ============================================================

def test_t4_tenant_isolation_across_workers(monkeypatch, worker_a, worker_b):
    tid = _tid()
    _bind_global(monkeypatch, worker_a)
    sync_travel_run_to_context("tenant-A", "u-g4", tid,
                               brief={"destination": "福州"},
                               missing_slots=["days"])
    # 不同 tenant、同 user 同 conversation：worker-B 不可见
    assert worker_b.get("tenant-B", "u-g4", tid) is None
    assert worker_b.get("tenant-B", "u-g4", tid + "x") is None
    assert worker_a.get("tenant-A", "u-g4", tid).destination == "福州"


def test_t5_user_isolation_across_workers(monkeypatch, worker_a, worker_b):
    tid = _tid()
    _bind_global(monkeypatch, worker_a)
    sync_travel_run_to_context("t-g4", "user-1", tid,
                               brief={"destination": "福州"},
                               missing_slots=["days"])
    assert worker_b.get("t-g4", "user-2", tid) is None
    assert worker_a.get("t-g4", "user-1", tid).destination == "福州"


def test_t6_conversation_isolation_across_workers(monkeypatch, worker_a, worker_b):
    t1, t2 = _tid(), _tid()
    _bind_global(monkeypatch, worker_a)
    sync_travel_run_to_context("t-g4", "u-g4", t1,
                               brief={"destination": "福州"},
                               missing_slots=["days"])
    assert worker_b.get("t-g4", "u-g4", t2) is None
    assert worker_a.get("t-g4", "u-g4", t1).destination == "福州"


# ============================================================
# T7：并发 field merge（真 Redis 双 worker 线程交错）
# ============================================================

def test_t7_concurrent_budget_and_lodging_both_survive(worker_a, worker_b):
    tid = _tid()
    worker_a.mutate("t-g4", "u-g4", tid, ContextMutation(
        MutationType.MERGE_TRAVEL_SUMMARY,
        {"slots": {"destination": "大阪", "days": 3}}))
    barrier = threading_barrier = __import__("threading").Barrier(2)
    errors = []

    def writer(repo, key, value):
        try:
            barrier.wait()
            for _ in range(15):
                r = repo.mutate("t-g4", "u-g4", tid, ContextMutation(
                    MutationType.MERGE_TRAVEL_SUMMARY, {"slots": {key: value}}))
                assert r.status == "applied", r.detail
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    import threading

    t1 = threading.Thread(target=writer, args=(worker_a, "budget_cny", 60000.0))
    t2 = threading.Thread(target=writer, args=(worker_b, "lodging", "难波"))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert errors == []
    final = worker_a.get("t-g4", "u-g4", tid)
    assert final.budget_cny == 60000.0   # 无 lost update
    assert final.lodging == "难波"        # 无 lost update
    assert final.days == 3


# ============================================================
# T8/T9：stale pending / stale run（真 Redis CAS）
# ============================================================

def test_t8_stale_pending_resolution_on_real_redis(worker_a, worker_b):
    tid = _tid()
    worker_a.mutate("t-g4", "u-g4", tid, ContextMutation(
        MutationType.START_TRAVEL_RUN, {"conv_hash8": "deadbeef"}))
    run_id = worker_a.get("t-g4", "u-g4", tid).travel_run_id

    def seed(question_id: str):
        ctx = worker_b.get("t-g4", "u-g4", tid)
        ctx.travel_pending = {"question_id": question_id, "run_id": run_id,
                              "requested_slots": ["days"],
                              "reason": "missing_required"}
        worker_b.save(ctx, expected_version=ctx.version)

    seed("tq_001")
    seed("tq_002")  # 新一轮 pending
    stale = worker_a.mutate("t-g4", "u-g4", tid, ContextMutation(
        MutationType.RESOLVE_TRAVEL_PENDING, {"expected_question_id": "tq_001"}))
    assert stale.status == "stale"
    current = worker_a.get("t-g4", "u-g4", tid)
    assert current.travel_pending["question_id"] == "tq_002"  # 未被误清


def test_t9_stale_run_completion_on_real_redis(worker_a, worker_b):
    tid = _tid()
    worker_a.mutate("t-g4", "u-g4", tid, ContextMutation(
        MutationType.START_TRAVEL_RUN, {"conv_hash8": "deadbeef"}))
    run_001 = worker_a.get("t-g4", "u-g4", tid).travel_run_id
    worker_b.mutate("t-g4", "u-g4", tid, ContextMutation(
        MutationType.START_TRAVEL_RUN, {"conv_hash8": "deadbeef"}))
    run_002 = worker_b.get("t-g4", "u-g4", tid).travel_run_id
    assert run_002.endswith("_002")

    stale = worker_a.mutate("t-g4", "u-g4", tid, ContextMutation(
        MutationType.MARK_TRAVEL_COMPLETED, {"expected_run_id": run_001}))
    assert stale.status == "stale"
    ctx = worker_b.get("t-g4", "u-g4", tid)
    assert ctx.travel_run_id == run_002
    assert ctx.travel_stage != "completed"  # run_002 未被污染


# ============================================================
# T10：Redis 数据丢失（flush 模拟重启丢键/驱逐）→ 确定性降级
# ============================================================

def test_t10_context_missing_after_flush_is_fresh_not_500(
        monkeypatch, redis_client, worker_a, worker_b):
    tid = _tid()
    _bind_global(monkeypatch, worker_a)
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "大阪", "days": 3},
                               missing_slots=[])
    assert worker_a.get("t-g4", "u-g4", tid) is not None

    redis_client.flushdb()  # 模拟 Redis 数据丢失（重启无 AOF / LRU 驱逐）
    _bind_global(monkeypatch, worker_b)
    assert worker_b.get("t-g4", "u-g4", tid) is None   # shared ≠ durable

    # 丢失路径：无 checkpoint → fresh（确定性，不 500）
    graph = build_travel_graph(checkpointer=None)
    from backend.orchestration.graph.travel_graph_node import _detect_resume_mode
    mode, extra = _detect_resume_mode(
        graph, {"configurable": {"thread_id": f"travel:{tid}"}},
        {"tenant_id": "t-g4", "user_id": "u-g4", "session_id": tid}, tid)
    assert mode == "fresh"
    assert extra == {}


# ============================================================
# T11：Redis unavailable — REQUIRE_SHARED 两种策略
# ============================================================

def test_t11a_require_shared_true_fails_closed(monkeypatch):
    repo = RedisConversationContextRepository(
        fallback=MemoryConversationContextRepository(), require_shared=True)
    import backend.infra.redis.client as client_mod
    monkeypatch.setattr(client_mod, "get_redis", lambda: None)

    # fail-closed：写拒绝（绝不假写 memory）、读确定性 miss（raise 供软失败兜底）
    with pytest.raises(ContextBackendUnavailable):
        repo.mutate("t-g4", "u-x", "c-x", ContextMutation(
            MutationType.MARK_TURN, {"domain": "travel"}))
    assert repo._fallback.get("t-g4", "u-x", "c-x") is None  # memory 未被偷写


def test_t11b_require_shared_false_falls_back_observable(monkeypatch):
    repo = RedisConversationContextRepository(
        fallback=MemoryConversationContextRepository(), require_shared=False)
    import backend.infra.redis.client as client_mod
    monkeypatch.setattr(client_mod, "get_redis", lambda: None)

    r = repo.mutate("t-g4", "u-x", "c-y", ContextMutation(
        MutationType.MARK_TURN, {"domain": "travel"}))
    assert r.status == "applied"          # fallback memory 可用
    assert repo.status["status"] == "degraded"  # 可观测
    assert repo.get("t-g4", "u-x", "c-y").active_domain == "travel"


def test_t11c_connection_error_degrades(monkeypatch):
    class _BoomClient:
        def get(self, *a, **k):
            raise ConnectionError("boom")

        def pipeline(self, *a, **k):
            raise ConnectionError("boom")

        def delete(self, *a, **k):
            raise ConnectionError("boom")

    repo = _WorkerRepo(_BoomClient(), fallback=MemoryConversationContextRepository(),
                       require_shared=False)
    r = repo.mutate("t-g4", "u-x", "c-z", ContextMutation(
        MutationType.MARK_TURN, {"domain": "travel"}))
    assert r.status == "applied"
    assert repo.status["status"] == "degraded"
    assert repo.get("t-g4", "u-x", "c-z").active_domain == "travel"


# ============================================================
# T12/T13/T14：checkpoint × context 组合（真 Redis 语义）
# ============================================================

@pytest.fixture
def memory_graph(monkeypatch):
    monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_ENABLED", True)
    monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_BACKEND", "memory")
    monkeypatch.setattr(T, "TRAVEL_RAG_ENABLED", False)
    monkeypatch.setattr(T, "TRAVEL_PREFS_ENABLED", False)
    monkeypatch.setattr(T, "TRAVEL_REQUIRE_PERSISTENCE", False)
    monkeypatch.setattr(gb, "_travel_graph", None)
    graph = gb.get_travel_graph()
    yield graph
    monkeypatch.setattr(gb, "_travel_graph", None)


def _probe(graph, tid):
    from backend.orchestration.graph.travel_graph_node import _detect_resume_mode
    return _detect_resume_mode(
        graph, {"configurable": {"thread_id": f"travel:{tid}"}},
        {"tenant_id": "t-g4", "user_id": "u-g4", "session_id": tid}, tid)


def test_t12_checkpoint_hit_does_not_need_context(memory_graph, worker_a,
                                                  redis_client):
    """checkpoint 存在 + context 丢失 → checkpoint resume（不依赖 Redis）。"""
    tid = _tid()
    from backend.travel.graph_state import new_travel_graph_input
    memory_graph.invoke(
        new_travel_graph_input("想去福州玩两天", user_id="u-g4",
                               session_id=tid, conversation_id=tid),
        config={"recursion_limit": 40,
                "configurable": {"thread_id": f"travel:{tid}"}},
    )
    redis_client.flushdb()  # context 全丢，checkpoint 在（MemorySaver）
    mode, extra = _probe(memory_graph, tid)
    assert mode == "checkpoint"
    assert extra == {}


def test_t13_context_hit_checkpoint_missing_reconstructs(worker_a):
    """checkpoint miss + context 有摘要 → reconstruct（brief 基底）。"""
    tid = _tid()
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "福州", "days": 2},
                               missing_slots=[], new_run=False)
    graph = build_travel_graph(checkpointer=None)
    mode, extra = _probe(graph, tid)
    assert mode == "reconstruct"
    assert extra["reconstruct_brief"]["destination"] == "福州"
    assert extra["reconstruct_brief"]["days"] == 2


def test_t14_both_missing_is_fresh(worker_a, redis_client):
    tid = _tid()
    graph = build_travel_graph(checkpointer=None)
    mode, extra = _probe(graph, tid)
    assert mode == "fresh"
    assert extra == {}


# ============================================================
# T15/T16：cancel 语义（真 Redis 复验）
# ============================================================

def test_t15_cancel_lifecycle_on_real_redis(monkeypatch, worker_a, worker_b):
    tid = _tid()
    _bind_global(monkeypatch, worker_a)
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "大阪"},
                               missing_slots=["days"])
    ctx = worker_a.get("t-g4", "u-g4", tid)
    assert ctx.travel_stage == "slot"
    assert ctx.travel_pending is not None
    run_001 = ctx.travel_run_id

    # worker-B（另一进程）执行取消
    result = worker_b.mutate("t-g4", "u-g4", tid, ContextMutation(
        MutationType.CANCEL_TRAVEL_RUN, {"expected_run_id": run_001}))
    assert result.status == "applied"
    after = worker_b.get("t-g4", "u-g4", tid)
    assert after.travel_stage == "cancelled"
    assert after.travel_pending is None      # pending removed
    assert after.travel_run_id == ""         # run inactive
    assert after.destination == "大阪"       # summary preserved（契约）
    assert after.travel_run_seq == 1

    # 取消后新规划：seq 单调 → run_002
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "杭州", "days": 2},
                               missing_slots=[])
    final = worker_a.get("t-g4", "u-g4", tid)
    assert final.travel_run_id.endswith("_002")
    assert final.travel_run_id != run_001


def test_t16_cancel_vs_patch_distinction(monkeypatch, worker_a, worker_b):
    tid = _tid()
    _bind_global(monkeypatch, worker_a)
    sync_travel_run_to_context("t-g4", "u-g4", tid,
                               brief={"destination": "大阪"},
                               missing_slots=["days"])
    mark_domain_turn("t-g4", "u-g4", tid, domain="travel")

    # T16a：「不去海游馆了」不作为 cancel 短路路由
    update = resolve_travel_pending("不去海游馆了", assemble_routing_context(
        "t-g4", "u-g4", tid))
    if update is not None:
        assert update["travel_context"]["travel_route"][
            "resume_mode"] != "cancel"
    assert worker_b.get("t-g4", "u-g4", tid).travel_stage == "slot"

    # T16b：「这次旅行不规划了」必须 CANCEL
    update2 = resolve_travel_pending("这次旅行不规划了", assemble_routing_context(
        "t-g4", "u-g4", tid))
    assert update2 is not None
    assert update2["travel_context"]["travel_route"]["resume_mode"] == "cancel"
    run_id = worker_b.get("t-g4", "u-g4", tid).travel_run_id
    result = worker_b.mutate("t-g4", "u-g4", tid, ContextMutation(
        MutationType.CANCEL_TRAVEL_RUN, {"expected_run_id": run_id}))
    assert result.status == "applied"
    assert worker_b.get("t-g4", "u-g4", tid).travel_stage == "cancelled"


# ============================================================
# worker switch 高层语义收尾：单例恢复（防跨测试污染）
# ============================================================

def test_singleton_reset_after_matrix(monkeypatch, worker_a):
    _bind_global(monkeypatch, worker_a)
    reset_conversation_context_repository()
    import backend.orchestration.context.context_repository as repo_mod
    assert repo_mod._repo is None
