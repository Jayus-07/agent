"""pg_saver 统一构建回归（审计 P1-1，2026-11-03）。

病史：四处 PostgresSaver（主图/travel/CS/任务执行器）曾各自用单条裸 psycopg
连接作载体——PG 重启后连接死亡且永不重建，checkpoint/审批读写全部
"the connection is closed" 失败直至进程重启（F3 演练 P3 实锤）。

本文件钉死三件事：
  1. 连接载体必须是 psycopg_pool.ConnectionPool 且带借出前探活
     （check=check_connection）——PG 重启自愈的关键参数，漏配即退化同病；
  2. autocommit / 池容量参数与 task_executor 先行修复版同参；
  3. 三个站点（主图/travel/CS）都已接共享 builder，不得回退裸连接。
不连真 PG：以 spy 模块替换 psycopg_pool 与 langgraph.checkpoint.postgres。
"""
import sys
import types
from pathlib import Path

import pytest

from backend.orchestration.checkpoint import pg_saver


class _SpyPool:
    instances: list = []

    def __init__(self, *, kwargs, min_size, max_size, open, check):
        self.kwargs = kwargs
        self.min_size = min_size
        self.max_size = max_size
        self.open = open
        self.check = check
        type(self).instances.append(self)


class _SpySaver:
    conn = None
    setup_calls = 0

    def __init__(self, conn):
        _SpySaver.conn = conn

    def setup(self):
        _SpySaver.setup_calls += 1


@pytest.fixture
def spy_modules(monkeypatch):
    """以 spy 模块替换池与 PostgresSaver，记录构造参数，不触真 PG。"""
    fake_pool_mod = types.ModuleType("psycopg_pool")
    fake_pool_mod.ConnectionPool = _SpyPool
    fake_pool_mod.ConnectionPool.check_connection = object()  # 哨兵可比较
    spy_postgres = types.ModuleType("langgraph.checkpoint.postgres")
    spy_postgres.PostgresSaver = _SpySaver
    monkeypatch.setitem(sys.modules, "psycopg_pool", fake_pool_mod)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", spy_postgres)
    _SpyPool.instances.clear()
    _SpySaver.setup_calls = 0
    return fake_pool_mod


def test_pool_carries_liveness_check_and_autocommit(spy_modules):
    saver = pg_saver.create_pooled_postgres_saver(owner="UnitTest")

    assert isinstance(saver, _SpySaver)
    pool = _SpySaver.conn
    assert isinstance(pool, _SpyPool)
    # 借出前探活：PG 重启自愈的关键（漏配即退化回裸连接同病）
    assert pool.check is spy_modules.ConnectionPool.check_connection
    assert pool.kwargs["autocommit"] is True
    # 无超时建连在 vpnkit 半开路径会永久卡死池 worker（本仓库实测：
    # getconn 全量 PoolTimeout 且 worker 连 close 都停不下来）——锁定该参数
    assert pool.kwargs.get("connect_timeout") == 5
    for key in ("host", "port", "dbname", "user", "password"):
        assert key in pool.kwargs, f"连接参数缺 {key}"
    assert pool.min_size == pg_saver.POOL_MIN_SIZE == 1
    assert pool.max_size == pg_saver.POOL_MAX_SIZE == 4
    assert pool.open is True
    assert _SpySaver.setup_calls == 1  # 幂等建表仍执行


def test_task_executor_delegates_and_caches_singleton(spy_modules, monkeypatch):
    """任务执行器侧保留自己的进程级单例，但建池必须走共享 builder。"""
    import backend.orchestration.checkpoint.task_executor as te

    monkeypatch.setattr(te, "_task_checkpointer", None)
    cp1 = te.build_task_checkpointer()
    cp2 = te.build_task_checkpointer()
    assert cp1 is cp2  # 单例：不重复建池（连接耗尽防线）
    assert len(_SpyPool.instances) == 1
    assert isinstance(cp1, _SpySaver)


@pytest.mark.parametrize(
    "rel,owner",
    [
        ("orchestration/graph/checkpointer.py", 'owner="MainGraph"'),
        ("travel/graph_builder.py", 'owner="TravelGraph"'),
        ("customer_service/graph_builder.py", 'owner="CS Graph"'),
    ],
)
def test_sites_use_shared_builder_not_bare_connection(rel, owner):
    """防回退守卫：三个站点必须走共享 builder，且不得再出现裸 psycopg 建连。"""
    src = (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")
    assert owner in src, f"{rel} 未接共享 builder（owner 标记缺失）"
    assert "psycopg.Connection.connect" not in src, (
        f"{rel} 回退到单条裸 psycopg 连接（PG 重启即永久失效，审计 P1-1 同病）"
    )
