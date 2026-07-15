"""config/logging.py — 日志配置

从环境变量读取日志等级和文件路径。
注：LOG_LEVEL/LOG_FILE 也在 settings.py 中 re-export，
   这里独立提供以便未来扩展 logger 专属配置（如 formatter、handler 列表）。
"""
import os

from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "rag_system.log")