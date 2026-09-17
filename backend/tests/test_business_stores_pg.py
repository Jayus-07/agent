"""test_business_stores_pg.py — 迁移计划 2026-09-17 Batch C/D：编排族 + 业务族 PG 连接层测试。

覆盖：
  1. 工厂分发（WORKFLOW_DB_BACKEND / INVENTORY_DB_BACKEND / SELECTION_BACKEND /
     SELECTION_DECISION_BACKEND / MARKET_RESEARCH_BACKEND / COMPETITOR_BACKEND /
     FEEDBACK_BACKEND = postgres → PG 子类；默认 sqlite 不受影响）
  2. PostgresWorkflowRunStore：save/list/get 往返（outputs JSON 反序列化）
  3. PostgresInventoryStore：threshold 保存/更新、case upsert、event 插入与
     case_id 回填、policy 保存/列表、list_all_cases 过滤、get_stats
  4. PostgresSelectionStore：score 往返、weights 更新清缓存
  5. PostgresSelectionDecisionStore：task 生命周期、decision_log 留痕/拍板/反馈、
     快照不可变触发器（017 迁移语义，测试内显式建触发器验证）
  6. PostgresMarketResearchStore：task + evidence 往返
  7. PostgresCompetitorStore：watchlist upsert、snapshot latest/history、
     config 读写删、event 记录
  8. feedback PG：add_feedback/stats、非法 vote 拒绝

前置：本机 PostgreSQL 可达（config 默认 agent_memory / agent_business 双库）。
不可达时整文件 skip；unit-only 运行用 -m "not pg"。
隔离：专用测试表前缀（pgtest_biz_ / pgtest_biz_ 前缀表名），fixture 前后清空。
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras
import pytest

from backend.config.database import BUSINESS_DB_CONFIG, WORKFLOW_DB_PG_CONFIG

PG_PREFIX = "pgtest_biz_"
PG_WF_TABLE = f"{PG_PREFIX}workflow_runs"
PG_FB_TABLE = f"{PG_PREFIX}feedback"

_MEMORY_TABLES = [PG_WF_TABLE]
_BUSINESS_TABLES = [
    f"{PG_PREFIX}inventory_threshold_rules",
    f"{PG_PREFIX}inventory_alert_cases",
    f"{PG_PREFIX}inventory_alert_events",
    f"{PG_PREFIX}notification_policies",
    f"{PG_PREFIX}selection_scores",
    f"{PG_PREFIX}selection_weights",
    f"{PG_PREFIX}selection_tasks",
    f"{PG_PREFIX}decision_log",
    f"{PG_PREFIX}mr_tasks",
    f"{PG_PREFIX}mr_evidence",
    f"{PG_PREFIX}competitor_watchlist",
    f"{PG_PREFIX}competitor_snapshots",
    f"{PG_PREFIX}competitor_config",
    f"{PG_PREFIX}competitor_events",
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
    import backend.competitor.store as comp_mod
    import backend.market_research.store as mr_mod
    import backend.orchestration.inventory.store as inv_mod
    import backend.orchestration.workflow.persistence as wf_mod
    import backend.selection.store as sel_mod
    import backend.selection_decision.store as sd_mod
    monkeypatch.setattr(wf_mod, "_store", None)
    monkeypatch.setattr(inv_mod, "_store", None)
    monkeypatch.setattr(sel_mod, "_store", None)
    monkeypatch.setattr(sd_mod, "_store", None)
    monkeypatch.setattr(mr_mod, "_store", None)
    monkeypatch.setattr(comp_mod, "_store", None)


@pytest.fixture()
def pg_env(monkeypatch):
    """PG 模式 + 专用测试表名/前缀，重置全部单例。"""
    monkeypatch.setenv("WORKFLOW_DB_BACKEND", "postgres")
    monkeypatch.setenv("WORKFLOW_DB_PG_TABLE", PG_WF_TABLE)
    monkeypatch.setenv("INVENTORY_DB_BACKEND", "postgres")
    monkeypatch.setenv("INVENTORY_DB_PG_TABLE_PREFIX", PG_PREFIX)
    monkeypatch.setenv("SELECTION_BACKEND", "postgres")
    monkeypatch.setenv("SELECTION_PG_TABLE_PREFIX", PG_PREFIX)
    monkeypatch.setenv("SELECTION_DECISION_BACKEND", "postgres")
    monkeypatch.setenv("SELECTION_DECISION_PG_TABLE_PREFIX", PG_PREFIX)
    monkeypatch.setenv("MARKET_RESEARCH_BACKEND", "postgres")
    monkeypatch.setenv("MARKET_RESEARCH_PG_TABLE_PREFIX", PG_PREFIX)
    monkeypatch.setenv("COMPETITOR_BACKEND", "postgres")
    monkeypatch.setenv("COMPETITOR_PG_TABLE_PREFIX", PG_PREFIX)
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
        import backend.competitor.store as comp_mod
        import backend.market_research.store as mr_mod
        import backend.orchestration.inventory.store as inv_mod
        import backend.orchestration.workflow.persistence as wf_mod
        import backend.selection.store as sel_mod
        import backend.selection_decision.store as sd_mod
        from backend.competitor.store_pg import PostgresCompetitorStore
        from backend.market_research.store_pg import PostgresMarketResearchStore
        from backend.orchestration.inventory.store_pg import PostgresInventoryStore
        from backend.orchestration.workflow.persistence_pg import PostgresWorkflowRunStore
        from backend.selection.store_pg import PostgresSelectionStore
        from backend.selection_decision.store_pg import PostgresSelectionDecisionStore
        assert isinstance(wf_mod.get_workflow_run_store(), PostgresWorkflowRunStore)
        assert isinstance(inv_mod.get_inventory_store(), PostgresInventoryStore)
        assert isinstance(sel_mod.get_selection_store(), PostgresSelectionStore)
        assert isinstance(sd_mod.get_selection_decision_store(), PostgresSelectionDecisionStore)
        assert isinstance(mr_mod.get_market_research_store(), PostgresMarketResearchStore)
        assert isinstance(comp_mod.get_store(), PostgresCompetitorStore)

    def test_postgres_dispatch(self, clean_tables):
        import backend.competitor.store as comp_mod
        import backend.market_research.store as mr_mod
        import backend.orchestration.inventory.store as inv_mod
        import backend.orchestration.workflow.persistence as wf_mod
        import backend.selection.store as sel_mod
        import backend.selection_decision.store as sd_mod
        from backend.competitor.store_pg import PostgresCompetitorStore
        from backend.market_research.store_pg import PostgresMarketResearchStore
        from backend.orchestration.inventory.store_pg import PostgresInventoryStore
        from backend.orchestration.workflow.persistence_pg import (
            PostgresWorkflowRunStore,
        )
        from backend.selection.store_pg import PostgresSelectionStore
        from backend.selection_decision.store_pg import PostgresSelectionDecisionStore

        assert isinstance(wf_mod.get_workflow_run_store(), PostgresWorkflowRunStore)
        assert isinstance(inv_mod.get_inventory_store(), PostgresInventoryStore)
        assert isinstance(sel_mod.get_selection_store(), PostgresSelectionStore)
        assert isinstance(
            sd_mod.get_selection_decision_store(), PostgresSelectionDecisionStore
        )
        assert isinstance(mr_mod.get_market_research_store(), PostgresMarketResearchStore)
        assert isinstance(comp_mod.get_store(), PostgresCompetitorStore)


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


# ── selection（Batch D，agent_business）──────────────────────────────


class TestSelectionStore:
    def test_score_roundtrip(self, clean_tables):
        from backend.selection.store import get_selection_store

        store = get_selection_store()
        score = {"total": 0.87, "breakdown": {"heat": 0.9, "price": 0.8}}
        store.save_score("https://item.jd.com/1.html", score, snapshot_id=42)
        got = store.get_score("https://item.jd.com/1.html")
        assert got["score_json"] == score
        assert got["snapshot_id"] == 42

        # UPSERT 覆盖
        store.save_score("https://item.jd.com/1.html", {"total": 0.1}, snapshot_id=43)
        assert store.get_score("https://item.jd.com/1.html")["score_json"] == {"total": 0.1}

        assert len(store.all_scores()) == 1

    def test_weights_update_clears_cache(self, clean_tables):
        from backend.selection.store import DEFAULT_WEIGHTS, get_selection_store

        store = get_selection_store()
        assert store.get_weights() == DEFAULT_WEIGHTS
        store.save_score("https://x", {"total": 1}, snapshot_id=None)
        store.set_weights({"reputation": 0.5, "unknown_key": 9.9})
        w = store.get_weights()
        assert w["reputation"] == 0.5
        assert "unknown_key" not in w
        # 权重变更清空评分缓存
        assert store.all_scores() == []


# ── selection_decision（Batch D，agent_business）─────────────────────


class TestSelectionDecisionStore:
    def test_task_lifecycle(self, clean_tables):
        from backend.selection_decision.store import get_selection_decision_store

        store = get_selection_decision_store()
        task_id = store.create({"category": "宠物"})
        store.update_result(task_id, status="success", verdict="GO",
                            report_md="# 报告", trace_id="t1")
        got = store.get(task_id)
        assert got["status"] == "success" and got["verdict"] == "GO"
        assert got["inputs"] == {"category": "宠物"}
        assert got["report_md"] == "# 报告"

        # 列表不含 report_md
        rows = store.list()
        assert len(rows) == 1 and "report_md" not in rows[0]

        # ensure_task 幂等（直跑补建）
        store.ensure_task("direct0001", {"category": "x"})
        store.ensure_task("direct0001", {"category": "y"})  # 已存在不覆盖
        assert store.get("direct0001")["inputs"] == {"category": "x"}

        # mark_stale_running_failed
        n = store.mark_stale_running_failed(older_than_sec=0)
        assert n == 1  # direct0001 仍是 running
        assert store.get("direct0001")["status"] == "failed"

    def test_decision_log_flow(self, clean_tables):
        from backend.selection_decision.store import get_selection_decision_store

        store = get_selection_decision_store()
        d1 = store.record_decision(
            task_id=None, candidate_id="C1", category="宠物",
            evidence_snapshot={"price": 99}, score_snapshot={"total": 0.9},
            recommendation="GO",
        )
        d2 = store.record_decision(
            task_id=None, candidate_id="C1", category="宠物",
            evidence_snapshot={"price": 89}, score_snapshot={"total": 0.8},
            recommendation="HOLD",
        )
        # 同 candidate 版本递增
        rows = store.list_decisions(candidate_id="C1")
        assert [r["decision_version"] for r in rows] == [2, 1]

        # 拍板 + 反馈（快照列不被触碰）
        assert store.set_user_decision(d2, "adopted") is True
        assert store.set_feedback(d2, {"sales_30d": 120}) is True
        got = store.get_decision(d2)
        assert got["user_decision"] == "adopted"
        assert got["actual_metrics"] == {"sales_30d": 120}
        assert got["feedback_at"] is not None
        assert got["evidence_snapshot"] == {"price": 89}
        assert store.set_user_decision("nope", "adopted") is False

        # by_task 查询
        d3 = store.record_decision(
            task_id="task9", candidate_id="C2", category=None,
            evidence_snapshot={}, score_snapshot={}, recommendation="GO",
        )
        assert [r["decision_id"] for r in store.list_decisions_by_task("task9")] == [d3]

    def test_snapshot_immutable_trigger(self, clean_tables):
        """017 迁移的不可变触发器语义：快照列 UPDATE 必须被拒"""
        # 先让 store _init_db 建表，再挂触发器（触发器依赖表存在）
        from backend.selection_decision.store import get_selection_decision_store
        store = get_selection_decision_store()

        conn = psycopg2.connect(**BUSINESS_DB_CONFIG)
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE OR REPLACE FUNCTION trg_decision_log_snapshot_immutable_fn()
                RETURNS trigger AS $$
                BEGIN
                    IF OLD.evidence_snapshot IS DISTINCT FROM NEW.evidence_snapshot
                       OR OLD.score_snapshot IS DISTINCT FROM NEW.score_snapshot THEN
                        RAISE EXCEPTION 'decision_log 快照不可变';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """)
            cur.execute(f"""
                DROP TRIGGER IF EXISTS trg_decision_log_snapshot_immutable
                ON {PG_PREFIX}decision_log;
                CREATE TRIGGER trg_decision_log_snapshot_immutable
                BEFORE UPDATE ON {PG_PREFIX}decision_log
                FOR EACH ROW EXECUTE FUNCTION
                    trg_decision_log_snapshot_immutable_fn();
            """)
            conn.commit()
        finally:
            conn.close()

        d1 = store.record_decision(
            task_id=None, candidate_id="CX", category=None,
            evidence_snapshot={"v": 1}, score_snapshot={"s": 1}, recommendation="GO",
        )
        # 合法更新（非快照列）通过
        assert store.set_user_decision(d1, "rejected") is True
        # 非法更新（快照列）被触发器拒绝
        conn = psycopg2.connect(**BUSINESS_DB_CONFIG)
        try:
            cur = conn.cursor()
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute(
                    f"UPDATE {PG_PREFIX}decision_log "
                    "SET evidence_snapshot = %s WHERE decision_id = %s",
                    ('{"v": 999}', d1),
                )
        finally:
            conn.rollback()
            conn.close()


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


# ── competitor（Batch D，agent_business）─────────────────────────────


class TestCompetitorStore:
    def test_watchlist_and_snapshots(self, clean_tables):
        from backend.competitor.store import get_store

        store = get_store()
        w = store.add_watch("竞品A", "https://p1", platform="jd", my_sku="SKU1")
        assert w["name"] == "竞品A"
        # upsert：同 URL 更新
        store.add_watch("竞品A-v2", "https://p1", platform="jd")
        assert store.get_watch_by_url("https://p1")["name"] == "竞品A-v2"
        assert len(store.list_watch()) == 1

        sid1 = store.save_snapshot({"watchlist_id": w["id"], "url": "https://p1",
                                    "title": "A", "price": 99.9})
        sid2 = store.save_snapshot({"url": "https://p1", "title": "A",
                                    "price": 89.9, "raw_excerpt": "raw"})
        assert sid2 != sid1
        latest = store.latest_snapshot("https://p1")
        assert latest["price"] == 89.9
        prev = store.latest_snapshot("https://p1", before_id=sid2)
        assert prev["id"] == sid1
        assert len(store.history("https://p1")) == 2
        assert len(store.list_snapshots()) == 2
        full = store.all_snapshots_full()
        assert "raw_excerpt" not in full[0]

        # toggle / remove
        store.toggle_watch("https://p1", enabled=False)
        assert store.list_watch(enabled_only=True) == []
        assert store.remove_watch("https://p1") is True
        assert store.remove_watch("https://p1") is False

    def test_config_and_events(self, clean_tables):
        from backend.competitor.store import get_store

        store = get_store()
        store.set_config("cookie_jd", "SECRET=1")
        assert store.get_config("cookie_jd") == "SECRET=1"
        store.set_config("cookie_jd", "SECRET=2")  # UPSERT
        assert store.get_config("cookie_jd") == "SECRET=2"
        assert store.delete_config("cookie_jd") is True
        assert store.get_config("cookie_jd") is None
        assert store.delete_config("cookie_jd") is False

        eid = store.log_event("jd", "https://p1", "blocked", "触发验证码")
        assert eid >= 1
        evs = store.recent_events()
        assert len(evs) == 1 and evs[0]["event_type"] == "blocked"


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
