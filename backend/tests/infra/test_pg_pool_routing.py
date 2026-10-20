"""PgBouncer 路由与连接归因回归（2026-10-21 连接治理）。

覆盖三件事：
1. pooled_pg_target 按开关路由池化引擎目标；会话级特性直连路径不经过它。
2. memory 异步引擎 application_name 归因（连接耗尽事故的定位手段）。
3. 开关打开时 asyncpg 语句缓存关闭（transaction 模式兼容前提）。
"""
import importlib

import pytest


@pytest.fixture()
def reload_db_module(monkeypatch):
    """以指定 PG_VIA_PGBOUNCER 环境重载 memory.database 模块。

    DATABASE_URL 在模块加载期由开关决定，必须 reload 才能观察到路由。
    """
    def _reload(enabled: str):
        monkeypatch.setenv("PG_VIA_PGBOUNCER", enabled)
        import backend.config.database as cfg_db
        importlib.reload(cfg_db)
        import backend.memory.database as mem_db
        importlib.reload(mem_db)
        return cfg_db, mem_db

    yield _reload

    import backend.config.database as cfg_db
    importlib.reload(cfg_db)
    import backend.memory.database as mem_db
    importlib.reload(mem_db)


def test_pool_target_direct_by_default(reload_db_module):
    """开关关闭（默认）：池化目标 = 原始配置直连。"""
    cfg_db, _ = reload_db_module("false")
    assert cfg_db.PG_VIA_PGBOUNCER is False
    cfg = {"host": "postgres", "port": 5432, "dbname": "agent_memory",
           "user": "u", "password": "p"}
    assert cfg_db.pooled_pg_target(cfg) == cfg


def test_pool_target_routed_when_enabled(reload_db_module):
    """开关打开：host/port 换成 PgBouncer，dbname/user/password 原样保留。"""
    cfg_db, _ = reload_db_module("true")
    cfg = {"host": "postgres", "port": 5432, "dbname": "agent_memory",
           "user": "u", "password": "p"}
    target = cfg_db.pooled_pg_target(cfg)
    assert target["host"] == cfg_db.PGBOUNCER_HOST
    assert target["port"] == cfg_db.PGBOUNCER_PORT
    assert {k: target[k] for k in ("dbname", "user", "password")} == {
        k: cfg[k] for k in ("dbname", "user", "password")
    }


def test_memory_async_url_routes_with_switch(reload_db_module):
    """异步引擎 DATABASE_URL 的 host 随开关切换（事故泄漏源的入口）。"""
    _, mem_db = reload_db_module("false")
    direct_url = mem_db.DATABASE_URL
    _, mem_db = reload_db_module("true")
    routed_url = mem_db.DATABASE_URL

    import backend.config.database as cfg_db
    assert f"@{cfg_db.PGBOUNCER_HOST}:{cfg_db.PGBOUNCER_PORT}/" in routed_url
    assert routed_url != direct_url


def test_memory_async_engine_has_application_name(monkeypatch, reload_db_module):
    """引擎必须携带 application_name：PG 侧按组件归因连接（事故定位手段）。"""
    _, mem_db = reload_db_module("false")

    captured = {}

    def _fake_create_async_engine(url, **kwargs):
        captured.update(kwargs)
        class _FakeEngine:  # 最小鸭子类型：测试进程内不会真正建连
            def dispose(self):
                pass
        return _FakeEngine()

    monkeypatch.setattr(mem_db, "create_async_engine", _fake_create_async_engine)
    # conftest 的后台预热可能已抢建引擎，清空注册表以强制走构造分支
    with mem_db._engines_lock:
        mem_db._engines.clear()
    import asyncio

    async def _trigger():
        async def _noop():
            return None
        task = asyncio.create_task(_noop())
        # _ensure_engine 是私有入口，通过 AsyncSessionLocal.__aenter__ 触发
        sess = mem_db.AsyncSessionLocal()
        try:
            await sess.__aenter__()
        except Exception:
            pass  # 引擎构造成功即可，后续建连失败与本测试无关
        finally:
            task.cancel()

    asyncio.run(_trigger())
    server_settings = captured["connect_args"]["server_settings"]
    assert server_settings["application_name"] == "agent-memory-async"
    # 直连（默认）：不注入 statement cache 覆盖，保持驱动默认缓存
    assert "prepared_statement_cache_size" not in captured["connect_args"]


def test_pgbouncer_mode_disables_statement_cache(monkeypatch, reload_db_module):
    """开关打开时 asyncpg 语句缓存必须关闭（transaction 模式兼容前提）。"""
    import asyncio

    captured = {}

    def _fake_create_async_engine(url, **kwargs):
        captured.update(kwargs)
        class _FakeEngine:
            def dispose(self):
                pass
        return _FakeEngine()

    _, mem_db = reload_db_module("true")
    monkeypatch.setattr(mem_db, "create_async_engine", _fake_create_async_engine)
    # conftest 后台预热可能已抢建引擎，清空注册表以强制走构造分支
    with mem_db._engines_lock:
        mem_db._engines.clear()

    async def _trigger():
        sess = mem_db.AsyncSessionLocal()
        try:
            await sess.__aenter__()
        except Exception:
            pass

    asyncio.run(_trigger())
    # 开关打开：驱动侧语句缓存必须关闭（transaction 模式兼容前提），
    # 且参数位于 connect_args（asyncpg 连接参数，非引擎 kwargs）
    assert captured["connect_args"]["prepared_statement_cache_size"] == 0


def test_engine_registry_isolates_loops(monkeypatch, reload_db_module):
    """不同 event loop 各自持有独立引擎（2026-10-21 事故根修的核心语义）。"""
    import asyncio

    captured = {}

    def _fake_create_async_engine(url, **kwargs):
        captured[id(kwargs)] = len(captured)
        class _FakeEngine:
            async def dispose(self):
                return None
        return _FakeEngine()

    _, mem_db = reload_db_module("false")
    monkeypatch.setattr(mem_db, "create_async_engine", _fake_create_async_engine)

    async def _touch():
        _, _ = await mem_db._ensure_engine()
        return id(asyncio.get_running_loop())

    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    try:
        loop_a.run_until_complete(_touch())
        loop_b.run_until_complete(_touch())
        assert len(mem_db._engines) == 2, "两个 loop 必须各自注册一套引擎"
        # 同一 loop 再次进入必须复用，不新增
        before = len(mem_db._engines)
        loop_a.run_until_complete(_touch())
        assert len(mem_db._engines) == before
    finally:
        loop_a.close()
        loop_b.close()


def test_dispose_loop_engines_clears_registry(monkeypatch, reload_db_module):
    """dispose_loop_engines 清空注册表并对运行中 loop 提交 dispose。"""
    import asyncio

    disposed = []

    class _FakeEngine:
        def __init__(self, tag):
            self.tag = tag
        async def dispose(self):
            disposed.append(self.tag)

    _, mem_db = reload_db_module("false")

    async def _register():
        loop = asyncio.get_running_loop()
        engine = _FakeEngine("a")
        with mem_db._engines_lock:
            mem_db._engines[id(loop)] = (loop, engine, None)
        return loop

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_register())
        import threading

        async def _spin():
            # dispose_loop_engines 只对「运行中」的 loop 提交 dispose：
            # 必须在 loop 已运行的状态下启动它，否则 is_running()=False 直接跳过
            t = threading.Thread(
                target=mem_db.dispose_loop_engines, kwargs={"timeout": 3})
            t.start()
            while t.is_alive():
                await asyncio.sleep(0.01)
            t.join()

        loop.run_until_complete(_spin())
        assert disposed == ["a"]
        assert len(mem_db._engines) == 0
    finally:
        loop.close()
