"""STOP G1/G2 — ConversationContextRepository 契约与原子 mutation 语义。

只测纯逻辑与 Memory backend（真 Redis 多 worker 实测见 test_stop_g4_matrix.py，
沿用 tests/test_task_admission.py 的 redis fixture 先例）。铁律：
- 强断言（具体值，不 assert True/False）
- 只 mock 外部边界（无——本文件零 mock，Redis 边界在 G4 实测）
- 每个 mutation 语义都对照 STOP G0 冻结 contract 断言
"""
from __future__ import annotations

import threading

import pytest

from backend.orchestration.context.context_repository import (
    ContextMutation,
    MutationType,
    MemoryConversationContextRepository,
    apply_mutation,
)
from backend.orchestration.context.conversation_context import (
    CONTEXT_SLOT_FIELDS,
    ConversationContext,
)

T, U, C = "tenant-g1", "user-g1", "conv-g1"


@pytest.fixture()
def repo():
    return MemoryConversationContextRepository(ttl_seconds=1800, max_entries=100)


def _mut(mtype: str, **payload) -> ContextMutation:
    return ContextMutation(type=mtype, payload=payload)


# ── 基础契约 ──


def test_get_peek_miss_returns_none_and_does_not_create(repo):
    assert repo.get(T, U, C) is None
    assert repo.peek(T, U, C) is None
    # 读路径零创建：miss 后仍无存储
    assert repo.get(T, U, C) is None


def test_mutate_mark_turn_creates_and_reads_back(repo):
    r = repo.mutate(T, U, C, _mut(MutationType.MARK_TURN, domain="travel",
                                  intent="travel", action="travel_graph_node"))
    assert r.status == "applied"
    assert r.version == 2  # 创建即 version=1，首 mutate 后 2
    ctx = repo.get(T, U, C)
    assert ctx is not None
    assert ctx.active_domain == "travel"
    assert ctx.last_action == "travel_graph_node"
    assert ctx.version == 2


def test_get_returns_snapshot_copy_not_live_reference(repo):
    repo.mutate(T, U, C, _mut(MutationType.MARK_TURN, domain="travel"))
    ctx = repo.get(T, U, C)
    ctx.active_domain = "cs"  # 改副本
    ctx.merge_slots({"destination": "黑名单外写入"})
    again = repo.get(T, U, C)
    assert again.active_domain == "travel"  # 未被污染
    assert again.destination == ""


def test_version_monotonic_and_noop_stale_do_not_bump(repo):
    r1 = repo.mutate(T, U, C, _mut(MutationType.MARK_TURN, domain="travel"))
    v1 = r1.version
    r2 = repo.mutate(T, U, C, _mut(MutationType.SET_TOPIC, topic="大阪"))
    assert r2.version == v1 + 1
    # noop：空 topic 无变化，版本不动
    r3 = repo.mutate(T, U, C, _mut(MutationType.SET_TOPIC, topic=""))
    assert r3.status == "noop"
    assert r3.version == v1 + 1


# ── G2 核心：T7 并发 merge 无 lost update ──


def test_t7_concurrent_budget_and_lodging_both_survive(repo):
    repo.mutate(T, U, C, _mut(MutationType.MERGE_TRAVEL_SUMMARY,
                              slots={"destination": "大阪", "days": 3}))
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def writer(key: str, value):
        try:
            barrier.wait()
            for _ in range(50):
                repo.mutate(T, U, C, _mut(MutationType.MERGE_TRAVEL_SUMMARY,
                                          slots={key: value}))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=writer, args=("budget_cny", 60000.0))
    t2 = threading.Thread(target=writer, args=("lodging", "难波"))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert errors == []
    ctx = repo.get(T, U, C)
    assert ctx.budget_cny == 60000.0
    assert ctx.lodging == "难波"
    assert ctx.days == 3
    assert ctx.destination == "大阪"


# ── G2 核心：T8 stale pending CAS ──


def _seed_pending(repo, run_id: str, question_id: str, slots=("days",)):
    repo.mutate(T, U, C, _mut(MutationType.SET_TRAVEL_PENDING, run_id=run_id,
                              requested_slots=list(slots)))
    # SET_TRAVEL_PENDING 的 question_id 是服务端生成的——为断言 CAS 需要
    # 固定 id，直接读出再覆盖（Memory 测试特技：save 全量提交固定 pending）
    ctx = repo.get(T, U, C)
    pending = dict(ctx.travel_pending)
    pending["question_id"] = question_id
    ctx.travel_pending = pending
    repo.save(ctx, expected_version=ctx.version)


def test_t8_stale_pending_resolution_does_not_clear_new_pending(repo):
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    run_id = repo.get(T, U, C).travel_run_id
    _seed_pending(repo, run_id, "tq_001")
    # 新一轮：pending 换成 tq_002
    _seed_pending(repo, run_id, "tq_002")
    assert repo.get(T, U, C).travel_pending["question_id"] == "tq_002"

    # 旧请求 tq_001 的 resolve 晚到：CAS 拒绝，tq_002 存活
    stale = repo.mutate(T, U, C, _mut(MutationType.RESOLVE_TRAVEL_PENDING,
                                      expected_question_id="tq_001"))
    assert stale.status == "stale"
    assert "tq_001" in stale.detail
    after = repo.get(T, U, C)
    assert after.travel_pending is not None
    assert after.travel_pending["question_id"] == "tq_002"

    # 正确的 resolve：清 pending 且 slot 阶段推进 planned
    ok = repo.mutate(T, U, C, _mut(MutationType.RESOLVE_TRAVEL_PENDING,
                                   expected_question_id="tq_002"))
    assert ok.status == "applied"
    final = repo.get(T, U, C)
    assert final.travel_pending is None
    assert final.travel_stage == "planned"


def test_resolve_pending_cas_tolerates_already_empty(repo):
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    r = repo.mutate(T, U, C, _mut(MutationType.RESOLVE_TRAVEL_PENDING,
                                  expected_question_id="tq_x"))
    assert r.status == "noop"  # 本就无 pending，合法终态


# ── G2 核心：T9 stale run CAS ──


def test_t9_stale_completion_does_not_touch_new_run(repo):
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    run_001 = repo.get(T, U, C).travel_run_id
    # 请求 B NEW_RUN
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    run_002 = repo.get(T, U, C).travel_run_id
    assert run_002.endswith("_002") and run_001.endswith("_001")

    # 请求 A 晚到 completed：CAS 拒绝，run_002 不被标 completed
    stale = repo.mutate(T, U, C, _mut(MutationType.MARK_TRAVEL_COMPLETED,
                                      expected_run_id=run_001))
    assert stale.status == "stale"
    ctx = repo.get(T, U, C)
    assert ctx.travel_stage != "completed"
    assert ctx.travel_run_id == run_002

    # 当前 run 正常收尾
    ok = repo.mutate(T, U, C, _mut(MutationType.MARK_TRAVEL_COMPLETED,
                                   expected_run_id=run_002))
    assert ok.status == "applied"
    assert repo.get(T, U, C).travel_stage == "completed"


def test_set_travel_pending_cas_to_current_run(repo):
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    run_001 = repo.get(T, U, C).travel_run_id
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    run_002 = repo.get(T, U, C).travel_run_id
    # 旧 run 的 pending 写入被拒
    r = repo.mutate(T, U, C, _mut(MutationType.SET_TRAVEL_PENDING,
                                  run_id=run_001, requested_slots=["days"]))
    assert r.status == "stale"
    assert repo.get(T, U, C).travel_pending is None
    # 当前 run 的 pending 正常写入
    r2 = repo.mutate(T, U, C, _mut(MutationType.SET_TRAVEL_PENDING,
                                   run_id=run_002, requested_slots=["days"]))
    assert r2.status == "applied"
    assert repo.get(T, U, C).travel_pending["requested_slots"] == ["days"]


def test_set_travel_pending_same_turn_keeps_question_id(repo):
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    run_id = repo.get(T, U, C).travel_run_id
    r1 = repo.mutate(T, U, C, _mut(MutationType.SET_TRAVEL_PENDING,
                                   run_id=run_id, requested_slots=["days"]))
    q1 = r1.question_id or repo.get(T, U, C).travel_pending["question_id"]
    r2 = repo.mutate(T, U, C, _mut(MutationType.SET_TRAVEL_PENDING,
                                   run_id=run_id, requested_slots=["days"]))
    q2 = repo.get(T, U, C).travel_pending["question_id"]
    assert q1 == q2  # 同轮重发保留 question_id
    assert r2.status == "applied"


# ── G3：Cancel lifecycle（repository 层语义）──


def test_cancel_keeps_seq_preserves_summary_and_blocks_stale(repo):
    repo.mutate(T, U, C, _mut(MutationType.MERGE_TRAVEL_SUMMARY,
                              slots={"destination": "大阪", "days": 3}))
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    run_001 = repo.get(T, U, C).travel_run_id

    cancelled = repo.mutate(T, U, C, _mut(MutationType.CANCEL_TRAVEL_RUN,
                                          expected_run_id=run_001))
    assert cancelled.status == "applied"
    ctx = repo.get(T, U, C)
    assert ctx.travel_stage == "cancelled"
    assert ctx.travel_run_id == ""          # active run 清除
    assert ctx.travel_pending is None       # pending 清除
    assert ctx.travel_run_seq == 1          # seq 保留（单调，不撞号）
    assert ctx.destination == "大阪"        # 摘要槽位保留（STOP F 契约）
    assert ctx.days == 3

    # stale cancel：run 已清后再 cancel → noop
    again = repo.mutate(T, U, C, _mut(MutationType.CANCEL_TRAVEL_RUN,
                                      expected_run_id=run_001))
    assert again.status == "noop"

    # 取消后新规划：seq 递增 → run_002（任务书 §18）
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    assert repo.get(T, U, C).travel_run_id.endswith("_002")


def test_cancel_cas_rejects_wrong_run(repo):
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    repo.mutate(T, U, C, _mut(MutationType.START_TRAVEL_RUN, conv_hash8="aabbccdd"))
    run_002 = repo.get(T, U, C).travel_run_id
    r = repo.mutate(T, U, C, _mut(MutationType.CANCEL_TRAVEL_RUN,
                                  expected_run_id="trv_aabbccdd_001"))
    assert r.status == "stale"
    assert repo.get(T, U, C).travel_run_id == run_002


# ── merge / evidence / funnel / followup 语义 ──


def test_merge_summary_destination_change_clears_evidence(repo):
    repo.mutate(T, U, C, _mut(MutationType.MERGE_TRAVEL_SUMMARY,
                              slots={"destination": "大阪"}))
    ctx = repo.get(T, U, C)
    ctx.touch_evidence(["s1", "s2"])
    repo.save(ctx)
    assert repo.get(T, U, C).last_verified_source_ids == ["s1", "s2"]

    repo.mutate(T, U, C, _mut(MutationType.MERGE_TRAVEL_SUMMARY,
                              slots={"destination": "杭州"}))
    after = repo.get(T, U, C)
    assert after.destination == "杭州"
    assert after.last_verified_source_ids == []  # 旧 evidence 失效


def test_merge_summary_respects_whitelist_including_lodging(repo):
    assert "lodging" in CONTEXT_SLOT_FIELDS
    repo.mutate(T, U, C, _mut(MutationType.MERGE_TRAVEL_SUMMARY,
                              slots={"lodging": "难波", "evil_key": "x",
                                     "days": None}))
    ctx = repo.get(T, U, C)
    assert ctx.lodging == "难波"
    assert ctx.days is None  # None 不清既有值


def test_apply_followup_resolution_overwrite_precedence(repo):
    r = apply_mutation(
        ConversationContext(tenant_id=T, user_id=U, conversation_id=C),
        _mut(MutationType.APPLY_FOLLOWUP_RESOLUTION,
             overwrite_destination="成都", topic="t", resolved=True))
    assert r.status == "applied"
    assert r.context.destination == "成都"
    r2 = apply_mutation(
        r.context,
        _mut(MutationType.APPLY_FOLLOWUP_RESOLUTION, topic="查预算", resolved=True))
    assert r2.context.current_topic == "查预算"


def test_funnel_candidates_roundtrip_and_cap(repo):
    top = [{"product": f"p{i}"} for i in range(8)]
    repo.mutate(T, U, C, _mut(MutationType.SET_FUNNEL_CANDIDATES,
                              candidates=top, run_id="fn_1"))
    ctx = repo.get(T, U, C)
    assert len(ctx.funnel_candidates) == 5  # cap=5
    assert ctx.funnel_candidates[0]["funnel_run_id"] == "fn_1"


# ── missing / TTL / save CAS / delete ──


def test_cas_mutations_on_missing_context_return_missing(repo):
    for mtype, payload in (
        (MutationType.RESOLVE_TRAVEL_PENDING, {"expected_question_id": "tq_1"}),
        (MutationType.MARK_TRAVEL_COMPLETED, {"expected_run_id": "trv_x_001"}),
        (MutationType.CANCEL_TRAVEL_RUN, {"expected_run_id": "trv_x_001"}),
        (MutationType.SET_TRAVEL_PENDING, {"run_id": "trv_x_001",
                                           "requested_slots": ["days"]}),
    ):
        r = repo.mutate("t-miss", "u-miss", "c-miss", _mut(mtype, **payload))
        assert r.status == "missing", mtype
    assert repo.get("t-miss", "u-miss", "c-miss") is None  # 不创建


def test_ttl_expiry(repo):
    repo_fast = MemoryConversationContextRepository(ttl_seconds=0, max_entries=10)
    repo_fast.mutate(T, U, "c-ttl", _mut(MutationType.MARK_TURN, domain="travel"))
    import time as _t
    _t.sleep(0.05)  # Windows time.time() 精度 ~15.6ms，短 sleep 可能同 tick
    assert repo_fast.get(T, U, "c-ttl") is None  # 过期即弃


def test_save_cas_version_mismatch(repo):
    repo.mutate(T, U, C, _mut(MutationType.MARK_TURN, domain="travel"))
    ctx = repo.get(T, U, C)
    # 并发方先写了一版
    repo.mutate(T, U, C, _mut(MutationType.SET_TOPIC, topic="别人先写"))
    with pytest.raises(Exception):
        repo.save(ctx, expected_version=ctx.version)  # 过期版本拒绝
    # 最新版本成功
    fresh = repo.get(T, U, C)
    fresh.current_topic = "基于最新版写入"
    saved = repo.save(fresh, expected_version=fresh.version)
    assert saved.version == fresh.version + 1
    assert repo.get(T, U, C).current_topic == "基于最新版写入"


def test_delete_removes_context(repo):
    repo.mutate(T, U, C, _mut(MutationType.MARK_TURN, domain="travel"))
    repo.delete(T, U, C)
    assert repo.get(T, U, C) is None


# ── 序列化契约（Redis value schema）──


def test_to_dict_from_dict_roundtrip_preserves_fields():
    ctx = ConversationContext(tenant_id="t1", user_id="u1", conversation_id="c1")
    ctx.merge_slots({"destination": "大阪", "days": 3, "lodging": "难波"})
    ctx.begin_travel_run()
    ctx.set_travel_stage("slot")
    ctx.set_travel_pending({"question_id": "tq_1", "run_id": ctx.travel_run_id,
                            "requested_slots": ["days"]})
    ctx.mark_turn(domain="travel", intent="travel", action="travel_graph_node")
    data = ctx.to_dict()
    assert data["schema_version"] == 1
    assert data["version"] == ctx.version
    # JSON 兼容（禁 pickle）
    import json as _json
    raw = _json.dumps(data, ensure_ascii=False)
    revived = ConversationContext.from_dict(_json.loads(raw))
    assert revived.travel_run_id == ctx.travel_run_id
    assert revived.travel_pending["question_id"] == "tq_1"
    assert revived.lodging == "难波"
    assert revived.version == ctx.version
    assert revived.active_domain == "travel"


def test_from_dict_ignores_unknown_fields():
    revived = ConversationContext.from_dict({
        "tenant_id": "t", "user_id": "u", "conversation_id": "c",
        "future_field": "v", "schema_version": 99,
    })
    assert revived.destination == ""
    assert not hasattr(revived, "future_field")
