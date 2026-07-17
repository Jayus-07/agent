"""Async database engine + session factory — 惰性初始化"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from backend.config import DB_CONFIG, MEMORY_ASYNC_POOL_SIZE, MEMORY_ASYNC_MAX_OVERFLOW
from backend.shared.logger import logger

DATABASE_URL = (
    f"postgresql+asyncpg://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}"
)

_engine = None
_sessionmaker = None


async def _ensure_engine():
    """确保 engine 在当前 event loop 上初始化"""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(
            DATABASE_URL,
            pool_size=MEMORY_ASYNC_POOL_SIZE,
            max_overflow=MEMORY_ASYNC_MAX_OVERFLOW,
            pool_pre_ping=True,
            echo=False,
        )
        _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
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
