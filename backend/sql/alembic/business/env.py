"""Alembic 环境 — agent_business 业务库（P1-13）

连接配置不写在 alembic.ini 里（避免密码入库），
而是复用 backend.config.database.BUSINESS_DB_CONFIG
（由 PGHOST / PGPORT / BUSINESS_PGDATABASE / PGUSER / PGPASSWORD 环境变量驱动，
与应用运行时同一来源，单一事实）。

用法：
  alembic -c alembic.ini -n business upgrade head
  alembic -c alembic.ini -n business revision -m "add xxx table"
  alembic -c alembic.ini -n business current
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ── 单一事实：复用应用配置 ──
from backend.config.database import BUSINESS_DB_CONFIG

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # 基线为纯 SQL 迁移；接入 autogenerate 时再绑定 MetaData


def _url() -> str:
    from sqlalchemy.engine import URL
    return URL.create(
        "postgresql+psycopg2",
        username=BUSINESS_DB_CONFIG["user"],
        password=BUSINESS_DB_CONFIG["password"],
        host=BUSINESS_DB_CONFIG["host"],
        port=BUSINESS_DB_CONFIG["port"],
        database=BUSINESS_DB_CONFIG["dbname"],
    ).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_business",
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
            version_table="alembic_version_business",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
