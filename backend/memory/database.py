"""Async database engine + session factory — 每 event loop 一套引擎，惰性初始化"""
import asyncio
import threading

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

_engine = None  # 兼容占位：实际引擎按 loop 注册在 _engines（经 _ensure_engine 访问）
_sessionmaker = None
_engine_loop: "asyncio.AbstractEventLoop | None" = None

# 每 event loop 一套 (loop, engine, sessionmaker)：asyncpg 连接绑定创建时的
# loop，同一 engine 实例不可跨 loop 使用。旧实现「检测到 loop 切换就整体
# 重建」在常驻进程内被多个 loop（uvicorn 主 loop、memory 后台 loop、
# cs-db loop、pg-sink loop、每请求/每任务的临时 loop）交替使用时，每次
# 换绑泄漏一个池的连接——2026-10-21 事故根因（maintenance-worker 4 小时
# 泄漏 47 连接打穿 max_connections；app 聊天主链路同机制持续泄漏）。
# 改为按 loop 注册后各 loop 的池互不干扰、生命周期与 loop 对齐；配合
# run_on_worker_loop 消灭临时 loop 后，生产进程内注册表稳定不增长。
_engines: dict[int, tuple["asyncio.AbstractEventLoop", object, object]] = {}
_engines_lock = threading.Lock()


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
    """确保**当前** event loop 拥有可用 engine，返回 (engine, sessionmaker)。

    asyncpg 连接绑定创建时的 event loop，不可跨 loop 使用。本模块按 loop
    注册独立引擎（见 _engines 注释）——同一 loop 内惰性建一次、之后复用；
    不同 loop 各建各的、互不换绑。构造在锁内同步完成，无并发重建窗口。
    """
    loop = asyncio.get_running_loop()
    with _engines_lock:
        entry = _engines.get(id(loop))
        if entry is not None and entry[0] is loop:
            return entry[1], entry[2]
        # 机会式清理：所属 loop 已关闭的引擎无法 dispose（asyncpg 需要活
        # loop），只能放弃引用交 GC。生产路径消灭临时 loop 后不应出现，
        # 出现即说明仍有临时 loop 泄漏源（pytest 每测试 loop 除外）。
        for key in [k for k, (l, _, _) in _engines.items() if l.is_closed()]:
            logger.warning(
                "[Database] 清理已关闭 loop 的遗留 engine（生产出现此日志 = 仍存在临时 loop 调用方）"
            )
            _engines.pop(key, None)
        _validate_config()
        engine = create_async_engine(
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
        sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        _engines[id(loop)] = (loop, engine, sessionmaker)
        logger.info(
            f"[Database] Engine 初始化完成（loop={id(loop):#x}，进程内共 {len(_engines)} 套）"
        )
        return engine, sessionmaker


def dispose_loop_engines(timeout: float = 3.0) -> None:
    """进程退出路径：对所有仍运行中的 loop 的引擎提交 dispose 并等待。

    供 manager 等持有后台 loop 的组件在 shutdown 时调用；loop 已关闭的
    引擎无法 dispose，直接放弃（连接随进程终止回收）。
    """
    with _engines_lock:
        entries = list(_engines.values())
        _engines.clear()
    for loop, engine, _sm in entries:
        if not loop.is_running():
            continue
        try:
            future = asyncio.run_coroutine_threadsafe(engine.dispose(), loop)
            future.result(timeout=timeout)
        except Exception:  # noqa: BLE001 - 退出路径尽力而为
            logger.debug("[Database] 引擎 dispose 失败（退出路径，忽略）", exc_info=True)


class AsyncSessionLocal:
    """向后兼容的 session 工厂 — 使用前自动初始化当前 loop 的引擎"""
    async def __aenter__(self):
        _, sessionmaker = await _ensure_engine()
        self._session = sessionmaker()
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
    _, sessionmaker = await _ensure_engine()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
