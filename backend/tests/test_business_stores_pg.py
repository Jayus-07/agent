"""test_business_stores_pg.py — 迁移计划 2026-09-17 Batch C/D：编排族 + 业务族 PG 连接层测试。

覆盖：
  1. 工厂分发（workflow / inventory / market research / feedback → PG 子类）
  2. PostgresWorkflowRunStore：save/list/get 往返（outputs JSON 反序列化）
  3. PostgresInventoryStore：threshold 保存/更新、case upsert、event 插入与
     case_id 回填、policy 保存/列表、list_all_cases 过滤、get_stats
  4. PostgresMarketResearchStore：task + evidence 往返
  5. feedback PG：add_feedback/stats、非法 vote 拒绝

前置：本机 PostgreSQL 可达（config 默认 agent_memory / agent_business 双库）。
不可达时整文件 skip；unit-only 运行用 -m "not pg"。
隔离：专用测试表前缀（pgtest_biz_ / pgtest_biz_ 前缀表名），fixture 前后清空。
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras
import pytest

from backend.config.database import BUSINESS_DB_CONFIG, WORKFLOW_DB_PG_CONFIG
from backend.tests.fixtures.pg_env import _pop_store_modules, worker_scoped_prefix

# v14 run 41249581757：固定 pgtest_biz_ 前缀（无 worker 段）在全根文件
# xdist 4 worker 下与其他 store 测试跨 worker 触发表竞争 → UndefinedTable。
# 对齐 pg_iso_env 口径：前缀带 worker 段，setenv 后 pop 烘焙模块强制重载。
PG_PREFIX = worker_scoped_prefix()
PG_WF_TABLE = f"{PG_PREFIX}workflow_runs"
PG_FB_TABLE = f"{PG_PREFIX}feedback"

_MEMORY_TABLES = [PG_WF_TABLE]
_BUSINESS_TABLES = [
    f"{PG_PREFIX}inventory_threshold_rules",
    f"{PG_PREFIX}inventory_alert_cases",
    f"{PG_PREFIX}inventory_alert_events",
    f"{PG_PREFIX}notification_policies",
    f"{PG_PREFIX}mr_tasks",
    f"{PG_PREFIX}mr_evidence",
    PG_FB_TABLE,
]


def _pg_alive(cfg: dict) -> bool:
    try:
        conn = psycopg2.connect(**cfg, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(
        not (_pg_alive(BUSINESS_DB_CONFIG) and _pg_alive(WORKFLOW_DB_PG_CONFIG)),
        reason="No PostgreSQL reachable (agent_memory / agent_business)",
    ),
]


def _drop_tables(cfg: dict, tables: list[str]):
    conn = psycopg2.connect(**cfg)
    try:
        cur = conn.cursor()
        for t in tables:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
    finally:
        conn.close()


def _reset_singletons(monkeypatch):
    """重置全部工厂单例（缓存实例不会因删表而重建，见 Batch A/B 测试注释）。"""
    # 表名常量在 store_pg 模块导入时烘焙，setenv 后必须 pop 强制重载
    _pop_store_modules()
    import backend.market_research.store as mr_mod
    import backend.orchestration.inventory.store as inv_mod
    import backend.orchestration.workflow.persistence as wf_mod
    monkeypatch.setattr(wf_mod, "_store", None)
    monkeypatch.setattr(inv_mod, "_store", None)
    monkeypatch.setattr(mr_mod, "_store", None)


@pytest.fixture()
def pg_env(monkeypatch):
    """PG 模式 + 专用测试表名/前缀，重置全部单例。"""
    monkeypatch.setenv("WORKFLOW_DB_BACKEND", "postgres")
    monkeypatch.setenv("WORKFLOW_DB_PG_TABLE", PG_WF_TABLE)
    monkeypatch.setenv("INVENTORY_DB_BACKEND", "postgres")
    monkeypatch.setenv("INVENTORY_DB_PG_TABLE_PREFIX", PG_PREFIX)
    monkeypatch.setenv("MARKET_RESEARCH_BACKEND", "postgres")
    monkeypatch.setenv("MARKET_RESEARCH_PG_TABLE_PREFIX", PG_PREFIX)
    monkeypatch.setenv("FEEDBACK_BACKEND", "postgres")
    monkeypatch.setenv("FEEDBACK_PG_TABLE", PG_FB_TABLE)
    _reset_singletons(monkeypatch)
    yield monkeypatch


@pytest.fixture()
def clean_tables(pg_env):
    _drop_tables(BUSINESS_DB_CONFIG, _BUSINESS_TABLES)
    _drop_tables(WORKFLOW_DB_PG_CONFIG, _MEMORY_TABLES)
    yield
    _drop_tables(BUSINESS_DB_CONFIG, _BUSINESS_TABLES)
    _drop_tables(WORKFLOW_DB_PG_CONFIG, _MEMORY_TABLES)


# ── 工厂分发 ─────────────────────────────────────────────────────────


class TestFactoryDispatch:
    def test_default_also_pg(self, clean_tables):
        """2026-09-17 SQLite 轨删除：无 BACKEND 开关，工厂一律直连 PG 实现
        （clean_tables 提供测试表前缀隔离，不触生产表）。"""
        import backend.market_research.store as mr_mod
        import backend.orchestration.inventory.store as inv_mod
        import backend.orchestration.workflow.persistence as wf_mod
        from backend.market_research.store_pg import PostgresMarketResearchStore
        from backend.orchestration.inventory.store_pg import PostgresInventoryStore
        from backend.orchestration.workflow.persistence_pg import PostgresWorkflowRunStore
        assert isinstance(wf_mod.get_workflow_run_store(), PostgresWorkflowRunStore)
        assert isinstance(inv_mod.get_inventory_store(), PostgresInventoryStore)
        assert isinstance(mr_mod.get_market_research_store(), PostgresMarketResearchStore)

    def test_postgres_dispatch(self, clean_tables):
        import backend.market_research.store as mr_mod
        import backend.orchestration.inventory.store as inv_mod
        import backend.orchestration.workflow.persistence as wf_mod
        from backend.market_research.store_pg import PostgresMarketResearchStore
        from backend.orchestration.inventory.store_pg import PostgresInventoryStore
        from backend.orchestration.workflow.persistence_pg import (
            PostgresWorkflowRunStore,
        )

        assert isinstance(wf_mod.get_workflow_run_store(), PostgresWorkflowRunStore)
        assert isinstance(inv_mod.get_inventory_store(), PostgresInventoryStore)
        assert isinstance(mr_mod.get_market_research_store(), PostgresMarketResearchStore)


# ── workflow_runs（Batch C，agent_memory）────────────────────────────


class TestWorkflowRunStore:
    def test_save_list_get_roundtrip(self, clean_tables):
        from backend.orchestration.workflow.context import WorkflowContext
        from backend.orchestration.workflow.persistence import get_workflow_run_store

        store = get_workflow_run_store()
        ctx = WorkflowContext(
            workflow_name="test_wf", run_id="wfrun0001",
            inputs={"category": "宠物"},
        )
        ctx.outputs["step_a"] = {"ok": True, "n": 3}
        ctx.mark_success()
        store.save(ctx)

        # list
        rows = store.list(workflow_name="test_wf")
        assert len(rows) == 1
        assert rows[0]["run_id"] == "wfrun0001"
        assert rows[0]["status"] == "success"

        # get（JSON 反序列化）
        detail = store.get("wfrun0001")
        assert detail is not None
        assert detail["inputs"] == {"category": "宠物"}
        assert detail["outputs"] == {"step_a": {"ok": True, "n": 3}}
        assert detail["duration_ms"] is not None

        # 覆写（INSERT OR REPLACE → ON CONFLICT DO UPDATE）
        ctx.status = "failed"
        ctx.error = "boom"
        store.save(ctx)
        assert store.get("wfrun0001")["status"] == "failed"

        # 不存在
        assert store.get("nope") is None

    def test_save_is_soft_fail(self, clean_tables, monkeypatch):
        """保存失败只告警不抛（软失败原则）"""
        from backend.orchestration.workflow.context import WorkflowContext
        from backend.orchestration.workflow.persistence import get_workflow_run_store

        store = get_workflow_run_store()
        bad = WorkflowContext(workflow_name="x", run_id=None)  # id=None → NOT NULL 违反
        store.save(bad)  # 不应抛异常


# ── inventory（Batch C，agent_business）──────────────────────────────


class TestInventoryStore:
    def test_threshold_crud(self, clean_tables):
        from backend.orchestration.inventory.store import get_inventory_store

        store = get_inventory_store()
        rid = store.save_threshold({"rule_type": "global", "min_qty": 10})
        assert rid >= 1
        # 更新（带 id 走 upsert 分支）
        store.save_threshold({"id": rid, "rule_type": "global", "min_qty": 20})
        rules = store.list_thresholds(enabled_only=True)
        assert len(rules) == 1
        assert rules[0]["min_qty"] == 20

        # find_threshold 优先级（继承自基类的 Python 层逻辑）
        sku_id = store.save_threshold(
            {"rule_type": "sku", "product_id": "P1", "min_qty": 5})
        assert sku_id != rid
        hit = store.find_threshold(product_id="P1", category="宠物")
        assert hit["rule_type"] == "sku"
        assert store.find_threshold(category="数码")["rule_type"] == "global"

    def test_case_event_full_chain(self, clean_tables):
        from backend.orchestration.inventory.store import get_inventory_store

        store = get_inventory_store()
        # CREATE 时序：先插事件（case_id 先占位 0）→ 建 case → 回填
        event_id = store.insert_event({
            "case_id": 0, "event_type": "created",
            "from_state": "normal", "to_state": "low",
            "qty": 3, "stock_days": 2.5, "reason": ["销量下滑"],
            "notified": False,
        })
        case_id = store.upsert_case({
            "product_id": "P1", "current_state": "low",
            "current_level": "warning", "status": "open",
        })
        store.update_event_case_id(event_id, case_id)
        store.set_case_notified(case_id, "2026-09-17T12:00:00")

        # 事件回读（reason JSON 反序列化）
        events = store.list_events_by_case(case_id)
        assert len(events) == 1
        assert events[0]["reason"] == ["销量下滑"]
        assert events[0]["case_id"] == case_id

        # case 查询
        assert store.get_case_by_product("P1")["id"] == case_id
        assert store.get_case(case_id)["last_notified_at"] == "2026-09-17T12:00:00"
        assert len(store.list_open_cases()) == 1

        # upsert 更新路径（同 product_id 不新增行）
        store.upsert_case({
            "product_id": "P1", "current_state": "critical",
            "current_level": "critical", "status": "open",
        })
        assert store.get_case(case_id)["current_state"] == "critical"
        assert len(store.list_open_cases()) == 1

        # 状态流转
        store.update_case_status(case_id, "resolved", resolution_type="AUTO_RECOVERED")
        cases, total = store.list_all_cases(status="history")
        assert total == 1 and cases[0]["resolution_type"] == "AUTO_RECOVERED"

        # 批量接口
        store.upsert_case({"product_id": "P2", "current_state": "low",
                           "current_level": "warning", "status": "open"})
        cmap = store.get_cases_by_products(["P1", "P2"])
        assert set(cmap) == {"P1", "P2"}
        last = store.get_last_events_by_cases([case_id])
        assert last[case_id]["event_type"] == "created"
        assert store.get_last_event(case_id)["event_type"] == "created"

        # 统计
        stats = store.get_stats()
        assert stats["critical"] == 0  # P1 已 resolved
        assert stats["resolved"] == 1

    def test_policy_matching(self, clean_tables):
        from backend.orchestration.inventory.store import get_inventory_store

        store = get_inventory_store()
        store.save_policy({"policy_name": "all", "notify_email": "a@b.c"})
        store.save_policy({"policy_name": "crit", "alert_level": "critical",
                           "notify_email": "x@y.z"})
        matched = store.find_matching_policies("critical", "low")
        assert {p["policy_name"] for p in matched} == {"all", "crit"}
        matched2 = store.find_matching_policies("warning", "low")
        assert {p["policy_name"] for p in matched2} == {"all"}


# ── market_research（Batch D，agent_business）────────────────────────


class TestMarketResearchStore:
    def test_task_and_evidence(self, clean_tables):
        from backend.market_research.store import get_market_research_store

        store = get_market_research_store()
        task_id = store.create({"category": "宠物"})
        store.ensure_task(task_id, {"category": "别的"})  # 幂等不覆盖
        assert store.get(task_id)["inputs"] == {"category": "宠物"}

        n = store.add_evidence(task_id, [
            {"evidence_id": "e1", "title": "T1", "url": "https://a",
             "numbers": [{"k": "销量", "v": 100}]},
            {"evidence_id": "e2", "title": "T2", "source_type": "report"},
        ])
        assert n == 2
        # INSERT OR REPLACE 语义：同主键覆盖
        store.add_evidence(task_id, [
            {"evidence_id": "e1", "title": "T1-v2", "url": "https://a"},
        ])
        rows = store.list_evidence(task_id)
        assert len(rows) == 2
        assert rows[0]["title"] == "T1-v2"
        assert rows[0]["numbers"] == []  # 覆盖后 numbers 重置为 []

        store.update_result(task_id, status="success", report_md="# 调研",
                            trace_id="t2")
        got = store.get(task_id)
        assert got["status"] == "success" and got["report_md"] == "# 调研"
        rows = store.list()
        assert len(rows) == 1 and "report_md" not in rows[0]


# ── feedback（Batch D，agent_business）───────────────────────────────


class TestFeedback:
    def test_add_and_stats(self, clean_tables):
        from backend import feedback as fb

        fb.init_db()
        fb.add_feedback("s1", "positive", msg_id="m1", question="Q1")
        fb.add_feedback("s1", "negative", msg_id="m2", question="Q2",
                        reason="答非所问")
        fb.add_feedback("s2", "positive", msg_id="m3", question="Q3")

        s = fb.stats(days=7)
        assert s["total"] == 3
        assert s["positive"] == 2
        assert s["negative"] == 1
        assert abs(s["positive_rate"] - 2 / 3) < 1e-9
        assert s["top_failed_queries"][0]["question"] == "Q2"

    def test_invalid_vote_rejected(self, clean_tables):
        from backend import feedback as fb

        fb.init_db()
        with pytest.raises(ValueError):
            fb.add_feedback("s1", "neutral")
