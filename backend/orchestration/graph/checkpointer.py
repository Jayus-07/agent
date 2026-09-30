"""checkpointer.py — 主图 checkpointer 构建

企业实践：主图状态持久化用 Postgres（跨进程/重启保留，多 worker 共享），
MemorySaver 仅作初始化失败时的降级兜底。开关 MAIN_GRAPH_CHECKPOINTER_ENABLED
（默认关——开启后 request_context 以 checkpoint 安全 dict 进状态，
Send 并行分支的流式/trace 绑定降级，见 orchestration/request_context.py）。

降级判据（结构病审查 P2-10）：不再「一条 warning 就地吞掉」。开发环境降级 + 告警；
生产环境（ENVIRONMENT=production）硬失败，除非显式 CHECKPOINTER_ALLOW_DEGRADE=true。
统一裁决见 config/checkpointer.py。

thread_id 语义：主图多轮记忆由 MemoryService 负责（不靠 checkpoint 回放），
thread_id 取每轮唯一值（session+毫秒时间戳）——checkpoint 定位是崩溃恢复/
审计/未来 interrupt-resume，不是跨轮状态合并（避免 messages 通道累积）。
"""
from typing import Any

from backend.config import MAIN_GRAPH_CHECKPOINTER_ENABLED
from backend.config.checkpointer import degrade_or_raise
from backend.shared.logger import logger


def build_main_checkpointer() -> Any:
    """按开关构建主图 checkpointer；未开启返回 None（与旧行为一致）。"""
    if not MAIN_GRAPH_CHECKPOINTER_ENABLED:
        return None

    try:
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver

        from backend.config.database import MEMORY_DB_CONFIG
        c = MEMORY_DB_CONFIG
        dsn = (f"postgresql://{c['user']}:{c['password']}"
               f"@{c['host']}:{c['port']}/{c['dbname']}")
        # autocommit：checkpointer 写入需即时提交（官方建议）
        conn = psycopg.Connection.connect(dsn, autocommit=True)
        checkpointer = PostgresSaver(conn)
        checkpointer.setup()  # 首次建表（幂等）
        logger.info("[MainGraph] checkpointer enabled (PostgresSaver: %s/%s)",
                    c["host"], c["dbname"])
        # checkpoint 膨胀防护：TTL 清理守护（同一 agent_memory 库、同一组
        # checkpoints 表，全进程单例；各域都幂等启动，谁先起谁定的 TTL 生效，
        # 因此三方统一用 DEFAULT_TTL_DAYS=7）
        try:
            from backend.orchestration.graph.checkpointer_cleanup import (
                DEFAULT_TTL_DAYS, start_cleanup_daemon,
            )
            start_cleanup_daemon(DEFAULT_TTL_DAYS, owner="main")
        except Exception:
            logger.debug("[MainGraph] cleanup daemon 启动失败（非致命）", exc_info=True)
        return checkpointer
    except Exception:
        # 降级判据统一走 config.checkpointer（结构病审查 P2-10）：
        # 开发环境降级 + 告警；生产环境 fail-loud，除非显式放行降级。
        degrade_or_raise(
            "MainGraph",
            "PostgresSaver 初始化失败（多为缺 psycopg v3 / "
            "langgraph-checkpoint-postgres，或 PG 连不上 / setup 建表失败）",
            exc_info=True,
        )

    try:
        from langgraph.checkpoint.memory import MemorySaver
        logger.info("[MainGraph] checkpointer enabled (MemorySaver)")
        return MemorySaver()
    except Exception:
        degrade_or_raise("MainGraph", "MemorySaver 也不可用（langgraph 安装不完整）")
        return None
