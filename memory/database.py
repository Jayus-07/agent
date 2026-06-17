"""Async database engine + session factory"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from config import DB_CONFIG
from utils.logger import logger

DATABASE_URL = (
    f"postgresql+asyncpg://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}"
)

async_engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False,
)

async def get_session():
    """Async context manager — yields AsyncSession"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

async def init_db():
    """Create all tables (idempotent). Call once at startup."""
    from memory.models.session import Base as SessionBase
    from memory.models.memory import Base as MemoryBase
    async with async_engine.begin() as conn:
        await conn.run_sync(SessionBase.metadata.create_all)
        await conn.run_sync(MemoryBase.metadata.create_all)
    logger.info("[Database] Schema verified")
