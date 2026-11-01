"""共享 PG 测试隔离 fixture（2026-09-17 SQLite 轨删除后统一使用）。

背景：
    存储层唯一实现为 PostgreSQL。测试需要隔离：所有表名走 `pgtest_biz_` 前缀，
    结束后统一删表，绝不触碰生产表。

机制（三步，缺一不可）：
1. monkeypatch.setenv 全部 *_PG_TABLE_PREFIX / *_PG_TABLE → pgtest_biz_* 测试表名
2. 强制从 sys.modules 弹出全部 *_pg 存储模块——部分模块（competitor/market_research/
   inventory/selection/selection_decision/feedback）在**模块导入时**烘焙表名常量，
   只 setenv 不重载不会生效。teardown 时再次弹出，让后续 import 回到生产表名。
3. teardown 按 `pgtest_%` 模式扫描 agent_memory / agent_business 两库并 DROP。

用法（测试文件内）：
    from backend.tests.fixtures.pg_env import pg_clean_tables  # noqa: F401

    @pytest.fixture(autouse=True)
    def _pg_iso(pg_clean_tables):
        yield

    # 测试体内直接构造：CompetitorStore() / InventoryStore() 等无参构造
    # （壳类 __new__ 返回 PG 实现），或在 fixture 依赖里声明 pg_clean_tables。
"""
from __future__ import annotations

import os
import sys

import psycopg2
import pytest

from backend.config.database import (
    BUSINESS_DB_CONFIG,
    COMPETITOR_PG_CONFIG,
    FEEDBACK_PG_CONFIG,
    INVENTORY_DB_PG_CONFIG,
    MARKET_RESEARCH_PG_CONFIG,
    SELECTION_DECISION_PG_CONFIG,
    SELECTION_PG_CONFIG,
    WORKFLOW_DB_PG_CONFIG,
)

TEST_TABLE_PREFIX = "pgtest_biz_"


def worker_scoped_prefix() -> str:
    """当前 xdist worker 的表名前缀；单进程（无 PYTEST_XDIST_WORKER）用裸前缀。

    xdist 下各 worker 独立建删同名 pgtest 表会互踩：CREATE TYPE 撞
    pg_type_typname_nsp_index（同 schema 同名类型，实测 orchestration
    setup error），teardown 宽 DROP 还会删掉别的工作进程正在用的表。
    表名带 worker 后缀后互不干扰；DROP 模式同步收窄到本 worker。
    """
    worker = os.getenv("PYTEST_XDIST_WORKER", "")
    return f"{TEST_TABLE_PREFIX}{worker}_" if worker else TEST_TABLE_PREFIX

# 表名常量在模块导入时烘焙的存储模块（setenv 后必须重载才生效）。
# 其余（trace/analytics/llm_usage/chunk/keyword/op_log/doc_registry/persistence_pg/
# pgvector）在 __init__ 读 env，setenv 即生效；一并弹出也无害。
_PG_STORE_MODULES = (
    "backend.competitor.store_pg",
    "backend.market_research.store_pg",
    "backend.orchestration.inventory.store_pg",
    "backend.selection.store_pg",
    "backend.selection_decision.store_pg",
    "backend.feedback.pg",
    "backend.orchestration.workflow.persistence_pg",
    "backend.observability.trace_store_pg",
    "backend.observability.analytics_store_pg",
    "backend.observability.llm_usage_store_pg",
    "backend.rag.indexing.chunk_store_pg",
    "backend.rag.preprocessing.keyword_store_pg",
    "backend.rag.indexing.operation_log_pg",
    "backend.rag.indexing.doc_registry_pg",
)

# 工厂单例属性（best-effort 重置；属性名不存在则跳过）
_SINGLETON_RESETS = (
    ("backend.competitor.store", "_store"),
    ("backend.market_research.store", "_store"),
    ("backend.orchestration.inventory.store", "_store"),
    ("backend.selection.store", "_store"),
    ("backend.selection_decision.store", "_store"),
    ("backend.orchestration.workflow.persistence", "_store"),
    ("backend.observability.trace_store", "_trace_store"),
    ("backend.observability.analytics_store", "_analytics_store"),
    ("backend.observability.llm_usage_store", "_llm_usage_store"),
    ("backend.rag.indexing.chunk_store", "_store"),
    ("backend.rag.preprocessing.keyword_store", "_store"),
    # RAG 候选发布运行记录仓储（2026-10-01 B 阶段）：表名随 pgtest 前缀切换，
    # 缓存实例必须随测试重置（否则引用上一测试已被 DROP 的表）
    ("backend.rag.indexing.index_run_store_pg", "_run_stores"),
)


def _pop_store_modules():
    for mod_name in _PG_STORE_MODULES:
        sys.modules.pop(mod_name, None)


def _reset_singletons():
    import importlib

    for mod_name, attr in _SINGLETON_RESETS:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        if hasattr(mod, attr):
            setattr(mod, attr, None)


def set_pg_test_env(monkeypatch):
    """设置全部表名 env（monkeypatch 负责 teardown 还原）+ 重载存储模块。"""
    prefix = worker_scoped_prefix()
    monkeypatch.setenv("WORKFLOW_DB_PG_TABLE", f"{prefix}workflow_runs")
    monkeypatch.setenv("INVENTORY_DB_PG_TABLE_PREFIX", prefix)
    monkeypatch.setenv("SELECTION_PG_TABLE_PREFIX", prefix)
    monkeypatch.setenv("SELECTION_DECISION_PG_TABLE_PREFIX", prefix)
    monkeypatch.setenv("MARKET_RESEARCH_PG_TABLE_PREFIX", prefix)
    monkeypatch.setenv("COMPETITOR_PG_TABLE_PREFIX", prefix)
    monkeypatch.setenv("FEEDBACK_PG_TABLE", f"{prefix}feedback")
    monkeypatch.setenv("OBS_DB_PG_TABLE_PREFIX", prefix)
    monkeypatch.setenv("RAG_STORES_PG_TABLE_PREFIX", prefix)
    monkeypatch.setenv("DOC_REGISTRY_PG_TABLE", f"{prefix}doc_registry")
    monkeypatch.setenv("VECTOR_PG_TABLE_PREFIX", prefix)
    _pop_store_modules()
    _reset_singletons()


def drop_pgtest_tables() -> int:
    """删除本 worker 的 pgtest_% 测试表，返回删除数量。

    遍历库必须覆盖全部建测试表的存储配置：INVENTORY_DB_PG_CONFIG 有独立
    的库名 env（INVENTORY_DB_PGDATABASE，CI 未设时默认 agent_business），
    与 BUSINESS_DB_CONFIG 可能不同库——漏掉会让 inventory 的 pgtest 表
    永远清不掉，同 worker 前序用例的策略行毒死后续用例（CI misc suite
    test_notification 10 例互踩实证，v2 run 41120872440）。COMPETITOR_
    PG_CONFIG 同理：CI 独立 competitor 库漏清时 anti_ban:state 计数
    跨用例累积（v14 backend-root run 41249581757，4 例 BudgetExhausted）。
    模式按 worker 后缀收窄（xdist 下不得删别的工作进程正在用的表）；
    单进程时与原宽匹配语义一致。连接失败静默跳过。
    """
    prefix = worker_scoped_prefix()
    # LIKE 模式里下划线是通配符，须转义；worker 名（gw0 等）无特殊字符
    like_pattern = prefix.replace("_", r"\_") + "%"
    dropped = 0
    # 库清单覆盖全部「表名烘焙自 worker_scoped_prefix 的存储配置」；缺省
    # 库名不同的（INVENTORY/COMPETITOR/SELECTION*/MARKET_RESEARCH/FEEDBACK）
    # 未显式 setenv 时掉进 agent_business 兼容库，漏扫会让 pgtest 表永不清。
    for cfg in (BUSINESS_DB_CONFIG, WORKFLOW_DB_PG_CONFIG, INVENTORY_DB_PG_CONFIG,
                COMPETITOR_PG_CONFIG, SELECTION_PG_CONFIG,
                SELECTION_DECISION_PG_CONFIG, MARKET_RESEARCH_PG_CONFIG,
                FEEDBACK_PG_CONFIG):
        try:
            conn = psycopg2.connect(**cfg, connect_timeout=2)
        except Exception:
            continue
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename LIKE %s",
                (like_pattern,),
            )
            tables = [r[0] for r in cur.fetchall()]
            for t in tables:
                cur.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
            conn.commit()
            dropped += len(tables)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()
    return dropped


def drop_all_pgtest_tables() -> int:
    """宽匹配清理全部 pgtest_% 残留（跨 worker/历史遗留；仅供手动调用）。"""
    dropped = 0
    for cfg in (BUSINESS_DB_CONFIG, WORKFLOW_DB_PG_CONFIG, INVENTORY_DB_PG_CONFIG):
        try:
            conn = psycopg2.connect(**cfg, connect_timeout=2)
        except Exception:
            continue
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename LIKE 'pgtest\\_%'"
            )
            tables = [r[0] for r in cur.fetchall()]
            for t in tables:
                cur.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
            conn.commit()
            dropped += len(tables)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()
    return dropped


@pytest.fixture()
def pg_iso_env(monkeypatch):
    """PG 测试表 env + 存储模块重载。teardown 恢复模块缓存与 env。"""
    set_pg_test_env(monkeypatch)
    yield monkeypatch
    _pop_store_modules()  # 让后续 import 回到生产表名（env 已被 monkeypatch 还原）


@pytest.fixture()
def pg_clean_tables(pg_iso_env):
    """完整隔离：env + 前后删 pgtest_% 表。"""
    drop_pgtest_tables()
    yield
    drop_pgtest_tables()
