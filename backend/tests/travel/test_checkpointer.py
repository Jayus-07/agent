"""tests/travel/test_checkpointer.py — 跨轮持久化与 checkpoint TTL 清理

两条主线：
  1. checkpointer 决策矩阵（关闭 / 内存 / 后端不可用降级）—— 与 CS、主图同口径
  2. **跨轮需求变化必须重规划**。这是开启 checkpointer 后才会暴露的坑：
     状态跨轮保留，若不清掉上一轮的 itinerary，supervisor 会直接进 reporter，
     把旧行程当成新需求的结果输出。下面的用例就是钉住这个行为。
"""
from __future__ import annotations

import sys
import types

import pytest

import backend.orchestration.graph.checkpointer_cleanup as shared_cleanup
import backend.travel.graph_builder as travel_gb
from backend.travel.graph_state import new_travel_graph_input


# ============================================================
# 一、checkpointer 决策矩阵
# ============================================================
class TestCheckpointerSelection:
    # Phase 4 起 _build_checkpointer 返回 (checkpointer, status) 元组，
    # status 三态：healthy（postgres 就绪）/ degraded（MemorySaver 降级）/ disabled
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr("backend.config.travel.TRAVEL_CHECKPOINTER_ENABLED", False)
        saver, status = travel_gb._build_checkpointer()
        assert saver is None
        assert status == travel_gb.PERSISTENCE_DISABLED

    def test_memory_backend_returns_memory_saver(self, monkeypatch):
        from langgraph.checkpoint.memory import MemorySaver

        monkeypatch.setattr("backend.config.travel.TRAVEL_CHECKPOINTER_ENABLED", True)
        monkeypatch.setattr("backend.config.travel.TRAVEL_CHECKPOINTER_BACKEND", "memory")
        saver, status = travel_gb._build_checkpointer()
        assert isinstance(saver, MemorySaver)
        assert status == travel_gb.PERSISTENCE_DEGRADED

    def test_postgres_unavailable_falls_back_to_memory(self, monkeypatch):
        """连不上 Postgres 时必须降级续跑，而不是让整个域瘫掉。"""
        from langgraph.checkpoint.memory import MemorySaver

        monkeypatch.setattr("backend.config.travel.TRAVEL_CHECKPOINTER_ENABLED", True)
        monkeypatch.setattr("backend.config.travel.TRAVEL_CHECKPOINTER_BACKEND", "postgres")

        fake = types.ModuleType("psycopg")

        def _boom(*args, **kwargs):
            raise OSError("connection refused")

        fake.Connection = types.SimpleNamespace(connect=_boom)
        monkeypatch.setitem(sys.modules, "psycopg", fake)
        saver, status = travel_gb._build_checkpointer()
        assert isinstance(saver, MemorySaver)
        assert status == travel_gb.PERSISTENCE_DEGRADED

    def test_checkpointer_is_not_gated_by_domain_switch(self, monkeypatch):
        """域总开关与持久化开关是两件事：TRAVEL_ENABLED 关掉不代表
        checkpointer 也必须关（本地只想跑持久化调试时有用）。"""
        monkeypatch.setattr("backend.config.travel.TRAVEL_ENABLED", False)
        monkeypatch.setattr("backend.config.travel.TRAVEL_CHECKPOINTER_ENABLED", True)
        monkeypatch.setattr("backend.config.travel.TRAVEL_CHECKPOINTER_BACKEND", "memory")
        saver, _status = travel_gb._build_checkpointer()
        assert saver is not None


@pytest.fixture
def checkpointed_graph(monkeypatch):
    """带内存 checkpointer 的域图（跨轮用例专用）。"""
    monkeypatch.setattr("backend.config.travel.TRAVEL_CHECKPOINTER_ENABLED", True)
    monkeypatch.setattr("backend.config.travel.TRAVEL_CHECKPOINTER_BACKEND", "memory")
    monkeypatch.setattr(travel_gb, "_travel_graph", None)
    graph = travel_gb.get_travel_graph()
    yield graph
    monkeypatch.setattr(travel_gb, "_travel_graph", None)


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}


def _poi_count(state: dict) -> int:
    return sum(len(d["items"]) for d in state["itinerary"]["days"])


# ============================================================
# 二、跨轮：需求变化 → 重规划
# ============================================================
class TestCrossTurnInvalidation:
    def test_brief_change_replans_instead_of_reusing_old_itinerary(
        self, checkpointed_graph,
    ):
        """第二轮改节奏 → 必须按新需求重排，不能把上一版行程原样端出来。"""
        graph = checkpointed_graph
        tid = "cross-turn-change"
        first = graph.invoke(
            new_travel_graph_input("福州3天，2个人，紧凑一点，预算3000"),
            config=_cfg(tid))

        second = graph.invoke(
            new_travel_graph_input("改轻松一点"), config=_cfg(tid))

        # 需求合并：目的地/天数沿用上一轮，节奏被改成 relaxed
        assert second["brief"]["destination"] == "福州"
        assert second["brief"]["days"] == 3
        assert second["brief"]["pace"] == "relaxed"

        # 关键断言：重排发生了，且新行程满足新节奏的硬上限
        assert any("需求已变化" in n for n in second["notes"])
        for day in second["itinerary"]["days"]:
            assert day["active_minutes"] <= 240, "relaxed 档单日活动不得超过 240 分钟"
            assert len([i for i in day["items"] if i["kind"] == "visit"]) <= 4
        assert _poi_count(second) < _poi_count(first)

    def test_added_city_changes_destination_and_replans(self, checkpointed_graph):
        graph = checkpointed_graph
        tid = "cross-turn-city"
        graph.invoke(new_travel_graph_input("福州3天，1个人"), config=_cfg(tid))
        second = graph.invoke(new_travel_graph_input("换成杭州吧"), config=_cfg(tid))
        assert second["brief"]["destination"] == "杭州"
        names = [i["title"] for d in second["itinerary"]["days"] for i in d["items"]]
        assert any("西湖" in n for n in names)

    def test_unchanged_brief_keeps_previous_itinerary(self, checkpointed_graph):
        """需求没变就不该重排 —— 否则每轮都白算一遍，用户看到行程无故变化。"""
        graph = checkpointed_graph
        tid = "cross-turn-same"
        first = graph.invoke(new_travel_graph_input("福州3天，1个人，预算3000"),
                            config=_cfg(tid))
        second = graph.invoke(new_travel_graph_input("谢谢"), config=_cfg(tid))

        assert not any("需求已变化" in n for n in second["notes"])
        assert second["itinerary"] == first["itinerary"]

    def test_fingerprint_ignores_unrelated_fields(self):
        from backend.travel.graph_state import brief_fingerprint
        from backend.travel.models.brief import TravelBrief

        base = TravelBrief(destination="福州", days=3, party_size=2)
        noisy = base.model_copy(update={"origin": "北京"})  # 出发地不影响排程
        assert brief_fingerprint(base) == brief_fingerprint(noisy)

        changed = base.model_copy(update={"pace": "relaxed"})
        assert brief_fingerprint(base) != brief_fingerprint(changed)

    def test_first_turn_is_not_treated_as_change(self):
        """首次进入（无上一轮指纹）不算「需求变化」，不该冒出作废提示。"""
        from backend.travel.slot_filler import slot_filler_node

        update = slot_filler_node(new_travel_graph_input("福州3天，1个人"))
        assert not any("需求已变化" in n for n in update.get("notes", []))
        assert update["brief_fingerprint"]


# ============================================================
# 三、共享 TTL 清理
# ============================================================
@pytest.fixture
def clean_daemon_state(monkeypatch):
    """重置守护线程全局态，并让循环永不真的触发（避免测试里连库）。"""
    monkeypatch.setattr(shared_cleanup, "_started", False)
    monkeypatch.setattr(shared_cleanup, "_owner", "")
    monkeypatch.setattr(shared_cleanup, "_ttl_days", None)
    monkeypatch.setattr(shared_cleanup, "FIRST_RUN_DELAY_SECONDS", 3600)
    yield


class TestSharedCleanupDaemon:
    def test_first_owner_wins_and_second_is_noop(self, clean_daemon_state):
        assert shared_cleanup.start_cleanup_daemon(7, owner="main") is True
        assert shared_cleanup.start_cleanup_daemon(7, owner="travel") is False
        assert shared_cleanup.daemon_state() == {
            "started": True, "owner": "main", "ttl_days": 7}

    def test_conflicting_ttl_is_rejected_not_silently_applied(self, clean_daemon_state):
        """三张表共用 → 只能有一个 TTL。后来的不同 TTL 必须被拒绝，
        否则清理窗口会在两个守护之间来回撕扯。"""
        shared_cleanup.start_cleanup_daemon(7, owner="main")
        assert shared_cleanup.start_cleanup_daemon(30, owner="travel") is False
        assert shared_cleanup.daemon_state()["ttl_days"] == 7

    def test_cs_shim_still_delegates_with_cs_owner(self, clean_daemon_state):
        """过渡期兼容壳：老调用点（无参）仍要按面向对象语义工作。"""
        from backend.customer_service.checkpointer_cleanup import start_cleanup_daemon

        assert start_cleanup_daemon() is True
        assert shared_cleanup.daemon_state()["owner"] == "cs"

    def test_cs_shim_accepts_explicit_ttl(self, clean_daemon_state):
        from backend.customer_service.checkpointer_cleanup import start_cleanup_daemon

        start_cleanup_daemon(11)
        assert shared_cleanup.daemon_state()["ttl_days"] == 11


class TestCleanupSql:
    class _Cursor:
        def __init__(self, log):
            self._log = log
            self.rowcount = 2

        def execute(self, sql, params=None):
            self._log.append((" ".join(sql.split()), params))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Conn:
        def __init__(self, log):
            self._log = log

        def cursor(self):
            return TestCleanupSql._Cursor(self._log)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_issues_three_deletes_and_aggregates_rowcounts(self, monkeypatch):
        log: list = []
        fake = types.ModuleType("psycopg")
        fake.connect = lambda dsn, autocommit=False: TestCleanupSql._Conn(log)
        monkeypatch.setitem(sys.modules, "psycopg", fake)

        deleted = shared_cleanup.cleanup_stale_checkpoints(7)

        assert deleted == {"checkpoints": 2, "blobs": 2, "writes": 2}
        assert len(log) == 3
        sqls = [entry[0] for entry in log]
        assert sqls[0].startswith("DELETE FROM checkpoints")
        assert "checkpoint_blobs" in sqls[1]
        assert "checkpoint_writes" in sqls[2]
        # TTL 必须作为参数传入，不能被拼进 SQL（注入面）
        assert log[0][1] == ("7",)
        assert all(entry[1] is None for entry in log[1:])

    def test_orphan_delete_guards_on_parent_checkpoint(self, monkeypatch):
        """孤儿清理必须带 NOT EXISTS 关联条件，否则会把仍被引用的 blob 删掉。"""
        log: list = []
        fake = types.ModuleType("psycopg")
        fake.connect = lambda dsn, autocommit=False: TestCleanupSql._Conn(log)
        monkeypatch.setitem(sys.modules, "psycopg", fake)

        shared_cleanup.cleanup_stale_checkpoints(7)

        for sql in (log[1][0], log[2][0]):
            assert "NOT EXISTS" in sql
            assert "checkpoints c" in sql
