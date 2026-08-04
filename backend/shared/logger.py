"""
日志工具模块 — O2 结构化日志（PR-2.x）。

特性:
  - 自动注入 trace_id / session_id（从 contextvar 读取，无需手动传参）
  - 支持 JSON 格式（LOG_FORMAT=json），兼容 ELK / Grafana Loki
  - 保持原有 logger.info("msg") 调用方式不变

用法:
  from backend.shared.logger import logger, set_log_context
  set_log_context(trace_id="abc", session_id="s1")
  logger.info("开始检索")  # → {"ts":"...","level":"INFO","msg":"开始检索","trace_id":"abc",...}
"""
import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from backend.config import LOG_LEVEL, LOG_FILE

# ── 日志上下文（tracer 自动注入）──
_trace_id_ctx: ContextVar[str] = ContextVar("log_trace_id", default="")
_session_id_ctx: ContextVar[str] = ContextVar("log_session_id", default="")

# 日志格式: "text" (默认) | "json" (生产推荐)
LOG_FORMAT = os.getenv("LOG_FORMAT", "text")


def set_log_context(trace_id: str = "", session_id: str = "") -> None:
    """设置当前协程的日志上下文（由 tracer.start/finish 自动调用）。"""
    if trace_id:
        _trace_id_ctx.set(trace_id)
    if session_id:
        _session_id_ctx.set(session_id)


def clear_log_context() -> None:
    """清除当前协程日志上下文（请求结束调用）。"""
    _trace_id_ctx.set("")
    _session_id_ctx.set("")


class _JsonFormatter(logging.Formatter):
    """JSON 行格式 — 每行一条 JSON，含 trace_id/session_id。"""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": _trace_id_ctx.get() or None,
            "session_id": _session_id_ctx.get() or None,
            "module": record.module,
            "line": record.lineno,
        }, ensure_ascii=False, default=str)


def setup_logger(name: str = "rag_system", level: str = None) -> logging.Logger:
    """配置并返回日志记录器。

    Args:
        name: 日志记录器名称
        level: 日志级别（默认从配置读取）

    Returns:
        配置好的 Logger 实例
    """
    if level is None:
        level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    if LOG_FORMAT == "json":
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


# 创建默认日志记录器
logger = setup_logger()
