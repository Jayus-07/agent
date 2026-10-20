"""Async database engine + session factory — 惰性初始化"""
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from backend.config import DB_CONFIG, MEMORY_ASYNC_POOL_SIZE, MEMORY_ASYNC_MAX_OVERFLOW
# 开关走 database 模块属性而非 from-import 值快照：pooled_pg_target 函数内
# 动态读取 backend.config.database 的全局，本模块必须与其同源，否则
# URL 路由与语句缓存开关可能各自取到不同代次的配置（测试 reload 场景实测）。
from backend.config import database as _db_config
from backend.shared.logger import logger

# 池化路径按开关可经 PgBouncer（transaction 模式）；会话级 PG 特性使用方
# （checkpointer_cleanup / travel 恢复锁的自建短连接）不经此 URL，始终直连。
_async_target = _db_config.pooled_pg_target(DB_CONFIG)
DATABASE_URL = (
    f"postgresql+asyncpg://{_async_target['user']}:{_async_target['password']}"
    f"@{_async_target['host']}:{_async_target['port']}/{_async_target['dbname']}"
)

_engine = None
_sessionmaker = None
_engine_loop: "asyncio.AbstractEventLoop | None" = None


class MemoryDatabaseUnavailable(RuntimeError):
    """记忆库不可用（配置缺失或连接失败）。

    单独定义类型是为了让路由层能把它映射成 503 而非 500，
    并与业务异常区分开。
    """


def _validate_config() -> None:
    """启用前校验必填连接参数，缺失时给出可操作的报错。

    动机：PGPASSWORD 缺省为空字符串时，asyncpg 报的是
    "connection was closed in the middle of operation"，
    完全看不出是配置问题（真因只能去翻 PostgreSQL 服务端日志）。
    """
    missing = [
        key
        for key, env in (("password", "PGPASSWORD"), ("user", "PGUSER"), ("dbname", "PGDATABASE"))
        if not DB_CONFIG.get(key)
    ]
    if missing:
        envs = ", ".join(
            env for key, env in (("password", "PGPASSWORD"), ("user", "PGUSER"), ("dbname", "PGDATABASE"))
            if key in missing
        )
        raise MemoryDatabaseUnavailable(
            f"PostgreSQL 连接配置缺失: {envs}（请在项目根目录 .env 中配置）。"
            f"当前 host={DB_CONFIG.get('host')} port={DB_CONFIG.get('port')} "
            f"dbname={DB_CONFIG.get('dbname') or '<空>'} user={DB_CONFIG.get('user') or '<空>'}"
        )


async def _ensure_engine():
    """确保 engine 在**当前** event loop 上可用。

    asyncpg 连接绑定创建时的 event loop，不可跨 loop 使用。本模块的
    engine 是模块级单例，而调用方可能来自不同 loop（MemoryManager 的
    后台线程 loop、pytest-asyncio 的每测试 loop、FastAPI 的主 loop）。
    旧实现「只建一次、永不换绑」：第一个 loop 关闭后，其余调用方要么
    撞 "attached to a different loop"，要么在进程退出时于错误的 loop
    上 dispose（teardown 崩进程的来源）。现在检测到 loop 切换就重建，
    旧 engine 交给它自己的 loop 释放（loop 已死则交给 GC 兜底）。
    """
    global _engine, _sessionmaker, _engine_loop
    loop = asyncio.get_running_loop()
    if _engine is not None and _engine_loop is not loop:
        old, old_loop = _engine, _engine_loop
        _engine, _sessionmaker = None, None
        logger.warning("[Database] event loop 已切换，重建 engine（旧 engine 交还原 loop 释放）")
        if old_loop is not None and old_loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(old.dispose(), old_loop)
            except Exception:  # noqa: BLE001
                logger.debug("[Database] 旧 engine 异步释放失败，交给 GC", exc_info=True)
    if _engine is None:
        _validate_config()
        _engine = create_async_engine(
            DATABASE_URL,
            pool_size=MEMORY_ASYNC_POOL_SIZE,
            max_overflow=MEMORY_ASYNC_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=3600,  # 1 小时回收连接，防止 LB/防火墙断开空闲连接
            echo=False,
            # PG 侧连接归因到组件（2026-10-21 连接耗尽事故的定位手段；
            # 同步统一引擎 infra/db.py 已有同款 application_name）。
            # PgBouncer transaction 模式下 prepared statements 不能跨真实
            # 连接复用，开关打开时必须关掉驱动侧语句缓存，否则报
            # "prepared statement ... does not exist"；直连时保持默认缓存。
            connect_args={
                "server_settings": {"application_name": "agent-memory-async"},
                **(
                    {"prepared_statement_cache_size": 0}
                    if _db_config.PG_VIA_PGBOUNCER
                    else {}
                ),
            },
        )
        _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
        _engine_loop = loop
        logger.info("[Database] Engine 初始化完成")


class AsyncSessionLocal:
    """向后兼容的 session 工厂 — 使用前自动初始化 engine"""
    async def __aenter__(self):
        await _ensure_engine()
        self._session = _sessionmaker()
        return await self._session.__aenter__()

    async def __aexit__(self, *args):
        return await self._session.__aexit__(*args)

    def __call__(self):
        """async with AsyncSessionLocal() 语法糖"""
        return self


# 保持向后兼容的访问方式
async_engine = None  # 模块级占位，实际通过 _ensure_engine() 访问


async def get_session():
    """Async context manager — yields AsyncSession"""
    await _ensure_engine()
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
