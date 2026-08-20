"""
日志工具模块 — O2 结构化日志（PR-2.x）。

特性:
  - 自动注入 trace_id / session_id（从 contextvar 读取，无需手动传参）
  - 支持 JSON 格式（LOG_FORMAT=json），兼容 ELK / Grafana Loki
  - 保持原有 logger.info("msg") 调用方式不变
  - console handler 强制 UTF-8 编码（修复 Windows GBK stdout 中文乱码 + 静默丢失）

用法:
  from backend.shared.logger import logger, set_log_context
  set_log_context(trace_id="abc", session_id="s1")
  logger.info("开始检索")  # → {"ts":"...","level":"INFO","msg":"开始检索","trace_id":"abc",...}

设计要点（V2026-08-18 编码修复 + V2026-08-18 子类化）:
  1. UTF-8 console 输出(修复 GBK stdout 中文乱码 + 静默丢失)
     - 检测 sys.stdout 已是 UTF-8 → 直通
     - 否则用 io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
  2. ObservableLogger 子类化(替代全局 monkey-patch):
     - 旧实现 logging.Logger.handleError = ... 全污染所有项目 logger
     - 新实现:定义 ObservableLogger(Logger) 并 setLoggerClass()
     - 影响只限于 backend.shared.logger 创建的实例,不污染第三方 logger
  3. handleError 错误可观测:
     - 默认 raiseExceptions=False → UnicodeEncodeError 静默丢失消息
     - 新版:出错时 record.msg + level + traceback 写到 stderr
     - stderr 写不动时不无限递归,直接放弃
"""
import io
import json
import logging
import os
import sys
import traceback
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


class ObservableLogger(logging.Logger):
    """Logger 子类 — handleError 写入 stderr 而不是默认吞掉。

    通过 setLoggerClass() 注册到 logging 系统后,所有用 logging.getLogger()
    创建的 logger 都用这个类(后续实例,不影响已存在的)。
    """

    def handleError(self, record):  # type: ignore[override]
        """logging.Logger.handleError 的可观测版本。

        默认行为 (raiseExceptions=False):打到 stderr '--- Logging error ---',
        然后消息丢失。这里把 record 的关键信息 + 异常类型一起输出。

        Args:
            record: 出错的 LogRecord

        注意:连 stderr 都不可写时不能无限递归,直接放弃。
        """
        if sys.stderr and hasattr(sys.stderr, "write"):
            try:
                sys.stderr.write(
                    f"--- Logging error ---\n"
                    f"record.msg={record.getMessage()!r}\n"
                    f"record.levelname={record.levelname}\n"
                    f"record.name={record.name}\n"
                    f"exc_info:\n{''.join(traceback.format_exception(*sys.exc_info()))}\n"
                )
                sys.stderr.flush()
            except Exception:
                # 连 stderr 都不行就放弃,不无限递归
                pass


def _make_utf8_console_stream():
    """构造一个 UTF-8 编码的 console stream。

    策略:
      - 如果当前 stdout 已经是 UTF-8,直接返回 sys.stdout(避免双重包装)
      - 否则用 io.TextIOWrapper 包 sys.stdout.buffer,强制 UTF-8 + errors='replace'
    """
    cur_encoding = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
    if cur_encoding == "utf8":
        return sys.stdout
    # sys.stdout 可能没有 .buffer(例如 StringIO),回退到直接 sys.stdout
    raw = getattr(sys.stdout, "buffer", None)
    if raw is None:
        return sys.stdout
    # errors='replace' 防止任何不可编码字符触发 UnicodeEncodeError 导致消息丢失
    return io.TextIOWrapper(raw, encoding="utf-8", errors="replace", line_buffering=True)


def setup_logger(name: str = "rag_system", level: str = None) -> logging.Logger:
    """配置并返回日志记录器。

    Args:
        name: 日志记录器名称
        level: 日志级别（默认从配置读取）

    Returns:
        配置好的 ObservableLogger 实例,handleError 可观测

    设计:不调 logging.setLoggerClass() — 那是全局污染,影响所有第三方 logger。
    改为显式 ObservableLogger(name) 实例化,只本项目的 logger 是子类。
    """
    if level is None:
        level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    # 幂等:同一 name 返回同一实例(用模块级 cache,避免重复配置)
    cache_key = f"_observable_cache::{name}::{level}"
    cached = getattr(setup_logger, cache_key, None)
    if cached is not None:
        return cached

    # 显式实例化 ObservableLogger,不走 getLogger(避免影响全局 Logger 类)
    logger: logging.Logger = ObservableLogger(name)
    logger.setLevel(level)

    # 登记到全局 manager:不走 getLogger 的实例对 pytest caplog 等
    # 依赖 logging.Logger.manager 的工具不可见(日志抓不到),补登记修复。
    # 同名不同 level 的实例会覆盖登记,不影响各自 handler 输出。
    logging.Logger.manager.loggerDict[name] = logger
    # 直接实例化的 Logger parent 为 None,传播链断裂 → record 传不到 root,
    # caplog/全局 handler 抓不到日志。本项目 logger 都是顶级名(rag_system),
    # 直接挂 root 作父(_fixupParents 会遭遇旧 placeholder 报错,不可用)。
    logger.parent = logging.root

    if LOG_FORMAT == "json":
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )

    # console handler 走 UTF-8（修复 GBK stdout 中文乱码 + 静默丢失）
    console_stream = _make_utf8_console_stream()
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # file handler 强制 UTF-8 写入磁盘（无论 OS locale）
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    setattr(setup_logger, cache_key, logger)
    return logger


# 提供一个"清缓存" hook,供测试或代码热更新场景使用
def reset_logger_cache() -> None:
    """清空 setup_logger 的实例缓存(测试或代码热更新场景)。"""
    keys = [k for k in vars(setup_logger) if k.startswith("_observable_cache::")]
    for k in keys:
        delattr(setup_logger, k)


# 创建默认日志记录器
logger = setup_logger()


__all__ = [
    "logger",
    "setup_logger",
    "set_log_context",
    "clear_log_context",
    "LOG_FORMAT",
    "_JsonFormatter",
    "ObservableLogger",
    "_make_utf8_console_stream",
]