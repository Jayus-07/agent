"""config/settings.py — 通用配置

跨模块共用的运行时配置（超时、日志等级等）。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 日志配置（基础级别）
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "rag_system.log")

# 通用超时（秒）
# retrieval/pipeline.py 用作软超时告警阈值（elapsed > 0.8 * OVERALL_REQUEST_TIMEOUT 触发告警）
OVERALL_REQUEST_TIMEOUT = int(os.getenv("OVERALL_REQUEST_TIMEOUT", "60"))

# ── SSE 流式对话运行时配置（P1-14：自 chat.py 收敛到 config）──
# worker 数 = SSE 并发上限；第 N+1 路排队等待空闲 worker；0 = 按 CPU 自适应
CHAT_SSE_MAX_WORKERS = int(os.getenv("CHAT_SSE_MAX_WORKERS", "0")) or max(
    4, (os.cpu_count() or 4) * 2
)
# 队列容量 1024 → 100Hz 输出下可撑 ~10s；超出走 backpressure（记 metric + set stop）
CHAT_SSE_QUEUE_MAXSIZE = int(os.getenv("CHAT_SSE_QUEUE_MAXSIZE", "1024"))
# consumer 阻塞拉取超时（秒）→ CPU 占用从 100Hz 轮询降到 ~0.5Hz
CHAT_SSE_GET_TIMEOUT = float(os.getenv("CHAT_SSE_GET_TIMEOUT", "0.5"))