"""pg_saver.py — PostgresSaver over psycopg_pool 的统一构建（审计 P1-1）。

病史：主图 / travel / customer_service / 任务执行器四处 PostgresSaver 曾各自
用「单条裸 psycopg 连接」作连接载体——PG 重启后该连接死亡且永不重建，后续
所有 checkpoint 读写一律 "the connection is closed" 失败直至进程重启
（F3 演练 P3 实锤；任务执行器侧已先行修复，见 task_executor.build_task_checkpointer）。

本模块是四处共享的构建函数：连接载体用 psycopg_pool.ConnectionPool
（langgraph checkpoint 的 _internal.get_connection 原生支持 ConnectionPool
类型）——借出前探活（check=check_connection）、坏连接自动换新。

不在此处做进程级单例：各调用方的启用开关、降级判据与生命周期不同
（主图/CS 受开关控制可返回 None，travel 有三级持久化语义，任务执行器
强制开启），单例语义留在各自现场；本函数每次调用新建一池，**只允许在
进程级惰性初始化路径上调用**，禁止进入请求级/任务级循环（多池并存会
耗尽 PG max_connections，task_executor v27 run 实证）。
"""
from __future__ import annotations

from typing import Any

from backend.shared.logger import logger

#: 池容量：checkpointer 写入是低频短事务，1 常驻 + 4 上限已覆盖
#: 多线程图执行并发借出的峰值（与 task_executor 先行修复版同参）。
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 4


def create_pooled_postgres_saver(*, owner: str) -> Any:
    """新建「psycopg_pool 连接池 + PostgresSaver」并完成幂等建表。

    owner：仅用于日志归因（MainGraph / TravelGraph / CS Graph / TaskExecutor）。
    任何失败向上抛出，由调用方的既有降级判据（config.checkpointer）处置。
    """
    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        kwargs={
            "host": c["host"], "port": c["port"], "dbname": c["dbname"],
            "user": c["user"], "password": c["password"],
            "autocommit": True,  # checkpointer 写入需即时提交（官方建议）
            # vpnkit（Docker Desktop→宿主 PG）半开连接病态：TCP 瞬时成功但
            # 握手在 select 上无限等待（pyproject dev 依赖注释、pytest.ini
            # 有案）。无超时的后台建连会永久卡死池 worker 线程——表现为
            # getconn 全量 PoolTimeout 且线程连 close 都停不下来。快速失败
            # 交给池自动重试，这正是连接池的设计意图。
            "connect_timeout": 5,
        },
        min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE, open=True,
        check=ConnectionPool.check_connection,  # 借出前探活，坏连接自动重建
    )
    from langgraph.checkpoint.postgres import PostgresSaver

    checkpointer = PostgresSaver(pool)
    checkpointer.setup()  # 幂等建表
    logger.info("[%s] checkpointer enabled (PostgresSaver/pool %d-%d: %s/%s)",
                owner, POOL_MIN_SIZE, POOL_MAX_SIZE, c["host"], c["dbname"])
    return checkpointer
