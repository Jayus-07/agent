"""executor 连接池自愈（2026-09-23 D1-1 回归）

PG 重启/网络抖动会杀死池内连接。修复前死连接被原样归还并在池里永久
循环借出，SQL 链路直到进程重启前持续失败。修复后：

- 借出时 closed 检查 + SELECT 1 探活（有界 3 次），坏连接 close=True 丢弃
- 归还时按健康度决定 close（rollback 失败/已关闭 → 不回流池内）

假池用例锁定丢弃/重取/不回流语义；真实 PG 用例用 pg_terminate_backend
终止池内 backend（等价 PG 重启的连接杀伤，不影响共享实例上的其他库），
验证下一次查询自动恢复。
"""
import psycopg2
import pytest
from psycopg2 import OperationalError
from psycopg2.pool import PoolError

from backend.sql import executor as executor_mod


class _FakeCursor:
    from collections import namedtuple as _nt
    description = [_nt("_Col", "name")("ok")]

    def __init__(self, conn, **kw):
        self._conn = conn

    def execute(self, sql, params=None):
        if sql == "SELECT 1" and self._conn.probe_fails:
            raise OperationalError("server closed the connection unexpectedly")
        if sql != "SELECT 1" and self._conn.query_fails:
            raise OperationalError(
                "terminating connection due to administrator command")

    def fetchall(self):
        return [{"ok": 1}]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, name="c"):
        self.name = name
        self.closed = False
        self.probe_fails = False
        self.query_fails = False
        self.autocommit = False

    def cursor(self, **kw):
        return _FakeCursor(self)

    def set_session(self, readonly=False):
        pass

    def rollback(self):
        if self.query_fails:
            # 查询中途连接死亡：归还前 rollback 同样失败
            raise OperationalError("connection was closed in the meantime")

    def commit(self):
        pass


class _FakePool:
    def __init__(self, conns):
        self._conns = list(conns)
        self.maxconn = 10
        self.returned = []

    def getconn(self):
        if self._conns:
            return self._conns.pop(0)
        raise PoolError("connection pool exhausted")

    def putconn(self, conn, close=False):
        self.returned.append((conn, close))


@pytest.fixture()
def fake_pool(monkeypatch):
    def _install(conns):
        pool = _FakePool(conns)
        monkeypatch.setattr(executor_mod, "_pool", pool)
        return pool

    return _install


def test_healthy_connection_normal_return(fake_pool):
    conn = _FakeConn("ok")
    pool = fake_pool([conn])

    res = executor_mod.execute_sql_struct("SELECT 1 AS ok")

    assert res.status == "success"
    assert pool.returned == [(conn, False)]


def test_probe_failure_discards_and_recovers(fake_pool):
    """借出探活失败 → close=True 丢弃重取 → 下一个健康连接服务查询。"""
    dead1, dead2, good = _FakeConn("d1"), _FakeConn("d2"), _FakeConn("ok")
    dead1.probe_fails = dead2.probe_fails = True
    pool = fake_pool([dead1, dead2, good])

    res = executor_mod.execute_sql_struct("SELECT 1 AS ok")

    assert res.status == "success"
    # 两个死连接 close=True 丢弃；好连接正常归还（close=False）
    assert (dead1, True) in pool.returned
    assert (dead2, True) in pool.returned
    assert pool.returned[-1] == (good, False)


def test_broken_on_return_not_pooled_back(fake_pool):
    """查询中途连接死亡：rollback 失败 → close=True，死连接不回流池内。"""
    bad = _FakeConn("bad")
    bad.query_fails = True
    pool = fake_pool([bad])

    res = executor_mod.execute_sql_struct("SELECT 1 AS ok")

    assert res.status == "failed"
    assert pool.returned == [(bad, True)]


def test_all_unhealthy_raises_connection_error(fake_pool):
    """三个借出位全部探活失败 → 有界放弃，分类为 connection（不无限重试）。"""
    conns = [_FakeConn(f"d{i}") for i in range(3)]
    for c in conns:
        c.probe_fails = True
    pool = fake_pool(conns)

    res = executor_mod.execute_sql_struct("SELECT 1 AS ok")

    assert res.status == "failed"
    assert res.error_type == "connection"
    assert all(close for _c, close in pool.returned)


def test_real_pg_selfheal_after_backend_termination():
    """真实 PG：终止池内 backend（等价 PG 重启的连接杀伤）→ 下一次查询自愈。

    不重启共享 PG 容器：只 kill application_name='agent_sql_executor' 的
    本服务连接，对同实例其他业务零影响。无管理员权限则跳过。
    """
    pytest.importorskip("psycopg2")
    from backend.config import BUSINESS_DB_READONLY_CONFIG, MEMORY_DB_CONFIG

    first = executor_mod.execute_sql_struct("SELECT 1 AS warmup")
    assert first.status == "success"

    try:
        admin = psycopg2.connect(
            host=MEMORY_DB_CONFIG["host"], port=MEMORY_DB_CONFIG["port"],
            dbname=MEMORY_DB_CONFIG["dbname"], user=MEMORY_DB_CONFIG["user"],
            password=MEMORY_DB_CONFIG["password"], connect_timeout=5)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"无管理员连接可终止 backend，跳过真实自愈用例: {e}")
    try:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE application_name = 'agent_sql_executor' "
                "AND pid <> pg_backend_pid()")
            killed = cur.rowcount
        admin.commit()
    finally:
        admin.close()

    assert killed >= 1, "至少应终止一条池内连接"
    res = executor_mod.execute_sql_struct("SELECT 1 AS healed")
    assert res.status == "success", f"终止后应自愈，实际: {res.error}"
