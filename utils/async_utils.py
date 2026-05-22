"""
异步工具模块 - 提供 LLM 并发控制和异步包装器
"""
import asyncio
from typing import Callable, Any
from config import LLM_MAX_CONCURRENCY
from utils.logger import logger

# 全局信号量 - 控制 LLM 并发数
_llm_semaphore = None

def get_llm_semaphore():
    """获取 LLM 信号量（懒加载）"""
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(LLM_MAX_CONCURRENCY)
        logger.info(f"✅ LLM 并发控制已启用: 最大并发数={LLM_MAX_CONCURRENCY}")
    return _llm_semaphore

async def async_safe_call_with_timeout(
    func: Callable,
    timeout: int,
    default_value: Any = None,
    error_message: str = "操作超时",
    *args,
    **kwargs
) -> Any:
    """
    异步安全调用函数（带超时保护和并发控制）

    Args:
        func: 要调用的函数（可以是同步或异步）
        timeout: 超时时间（秒）
        default_value: 超时或失败时的默认返回值
        error_message: 错误日志消息
        *args, **kwargs: 传递给 func 的参数

    Returns:
        函数返回值或默认值
    """
    semaphore = get_llm_semaphore()

    try:
        async with semaphore:  # 限制并发数
            logger.debug(f"🔒 获取 LLM 信号量，当前等待队列: {semaphore._waiters}")

            if asyncio.iscoroutinefunction(func):
                # 异步函数
                result = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=timeout
                )
            else:
                # 同步函数 - 在线程池中执行
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: func(*args, **kwargs)),
                    timeout=timeout
                )

            logger.debug(f"✅ LLM 调用完成")
            return result

    except asyncio.TimeoutError:
        logger.warning(f"⚠️ {error_message}")
        return default_value

    except Exception as e:
        logger.error(f"❌ LLM 调用失败: {e}")
        return default_value