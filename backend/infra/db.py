"""统一 SQLAlchemy Engine 工厂（查询层收口 P0）。

背景：backend 下 ~20 个 store 各自 ``psycopg2.connect(**CONFIG)`` 裸连、
用完即弃——每个操作都付一次 TCP 建连 + 认证的开销，高并发下连接风暴
（PG max_connections 很快被打满）。本模块把连接管理收口到 SQLAlchemy
Core 的 Engine 池：

  - 双库各一个 lazy 单例 Engine（agent_memory / agent_business），
    其余 ``*_PG_CONFIG``（OBS/RAG/WORKFLOW/SELECTION…）按 dbname 归属
    复用同库 Engine，连接参数与其 CONFIG 完全一致。
  - 池参数复用既有 ``DB_POOL_MIN_CONN / DB_POOL_MAX_CONN / DB_CONNECT_TIMEOUT /
    DB_KEEPALIVES_IDLE`` 环境变量（backend/config/database.py），不新增配置面。
  - ``pool_pre_ping=True``：借出前轻量探活，PG 重启后自动换新连接，不炸请求。
  - ``pool_use_lifo=True``：热点连接优先复用，闲时连接先被回收，压低空闲连接数。

调用方只改一行：``psycopg2.connect(**CFG)`` → ``engine_for(CFG).raw_connection()``。
返回的连接是池化包装（``_ConnectionFairy``），``cursor() / commit() / rollback() /
close()`` 语义与裸 psycopg2 一致，``close()`` 从「关连接」变为「归还池」。
查询仍写原生 SQL（psycopg2 占位符 ``%s``），**不引入 ORM 模型层**（2026-09-21 决策）。

测试隔离：表名级隔离走既有 ``*_TABLE_PREFIX`` 环境变量，连接参数进程内不变，
Engine 单例跨测试复用安全；需要强制重建时调 ``reset_engines()``。
"""

from __future__ import annotations

import threading

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL

from backend.config.database import (
    BUSINESS_DB_CONFIG,
    DB_CONNECT_TIMEOUT,
    DB_KEEPALIVES_IDLE,
    DB_POOL_MAX_CONN,
    DB_POOL_MIN_CONN,
    MEMORY_DB_CONFIG,
)

_ENGINES: dict[str, Engine] = {}
_ENGINES_LOCK = threading.Lock()

# 引擎按「库名」归一：同一库的多个 *_PG_CONFIG 共享一个池，
# 避免 OBS/RAG/WORKFLOW 各自开池导致连接总数 = 池数 × 池大小 超额。
_MEMORY_DBNAME = MEMORY_DB_CONFIG["dbname"]
_BUSINESS_DBNAME = BUSINESS_DB_CONFIG["dbname"]


def _make_engine(cfg: dict) -> Engine:
    """由既有连接参数 dict（_pg_cfg 五键）构造池化 Engine。"""
    url = URL.create(
        drivername="postgresql+psycopg2",
        username=cfg["user"],
        password=cfg["password"],
        host=cfg["host"],
        port=cfg["port"],
        database=cfg["dbname"],
    )
    return create_engine(
        url,
        pool_size=DB_POOL_MIN_CONN,
        max_overflow=max(DB_POOL_MAX_CONN - DB_POOL_MIN_CONN, 0),
        pool_pre_ping=True,
        pool_use_lifo=True,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": DB_CONNECT_TIMEOUT,
            "keepalives_idle": DB_KEEPALIVES_IDLE,
            "application_name": "agent-app",
        },
    )


def _engine_key(cfg: dict) -> str:
    """按 dbname 归属到 memory/business 两个池；未知库回退为其 dbname 独立池。"""
    if cfg.get("dbname") == _MEMORY_DBNAME:
        return "memory"
    if cfg.get("dbname") == _BUSINESS_DBNAME:
        return "business"
    return f"db:{cfg.get('dbname')}"


def engine_for(cfg: dict) -> Engine:
    """返回该连接配置归属库的池化 Engine（进程级单例，线程安全）。

    参数 ``cfg`` 是 ``backend/config/database.py`` 的 ``*_PG_CONFIG`` dict
    （host/port/dbname/user/password）。连接参数与 CONFIG 一致性由调用方
    保证——本函数只负责池化，不重复读取环境变量。
    """
    key = _engine_key(cfg)
    engine = _ENGINES.get(key)
    if engine is not None:
        return engine
    with _ENGINES_LOCK:
        engine = _ENGINES.get(key)
        if engine is not None:
            return engine
        engine = _make_engine(cfg)
        _ENGINES[key] = engine
        return engine


def get_memory_engine() -> Engine:
    """agent_memory 库的池化 Engine。"""
    return engine_for(MEMORY_DB_CONFIG)


def get_business_engine() -> Engine:
    """agent_business 库的池化 Engine。"""
    return engine_for(BUSINESS_DB_CONFIG)


def reset_engines() -> None:
    """销毁全部 Engine 单例（测试用；生产不要在请求路径调用）。"""
    with _ENGINES_LOCK:
        for engine in _ENGINES.values():
            engine.dispose()
        _ENGINES.clear()
