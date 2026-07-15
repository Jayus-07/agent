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