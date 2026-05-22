"""
日志工具模块
提供统一的日志记录功能
"""
import logging
import sys
from config import LOG_LEVEL, LOG_FILE


def setup_logger(name: str = "rag_system", level: str = None) -> logging.Logger:
    """
    配置并返回日志记录器
    
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
    
    # 避免重复添加 handler
    if logger.handlers:
        return logger
    
    # 日志格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 文件处理器（只记录 WARNING 及以上级别）
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger


# 创建默认日志记录器
logger = setup_logger()
