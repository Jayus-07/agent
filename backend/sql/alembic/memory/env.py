"""Alembic 环境 — agent_memory 记忆库（P1-13）

连接配置复用 backend.config.database.MEMORY_DB_CONFIG
（PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD 环境变量驱动）。

用法：
  alembic -c alembic.ini -n memory upgrade head
  alembic -c alembic.ini -n memory revision -m "add xxx table"
  alembic -c alembic.ini -n memory current
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from backend.config.database import MEMORY_DB_CONFIG

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # 基线为纯 SQL 迁移；接入 autogenerate 时再绑定 MetaData


def _url() -> str:
    from sqlalchemy.engine import URL
    return URL.create(
        "postgresql+psycopg2",
        username=MEMORY_DB_CONFIG["user"],
        password=MEMORY_DB_CONFIG["password"],
        host=MEMORY_DB_CONFIG["host"],
        port=MEMORY_DB_CONFIG["port"],
        database=MEMORY_DB_CONFIG["dbname"],
    ).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_memory",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _url()
    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version_memory",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
