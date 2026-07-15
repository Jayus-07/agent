"""
异步工具模块 - 异步调用包装器（带超时保护）
"""
import asyncio
from typing import Callable, Any
from backend.utils.logger import logger


async def async_safe_call_with_timeout(
    func: Callable,
    timeout: int,
    default_value: Any = None,
    error_message: str = "操作超时",
    *args,
    **kwargs,
) -> Any:
    """异步安全调用（带超时保护）。同步函数在线程池中执行。"""
    try:
        if asyncio.iscoroutinefunction(func):
            result = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
        else:
            # 同步函数 - 在线程池中执行
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: func(*args, **kwargs)),
                timeout=timeout,
            )
        return result
    except asyncio.TimeoutError:
        logger.warning(f"⚠️ {error_message}")
        return default_value
    except Exception as e:
        logger.error(f"❌ 异步调用失败: {e}")
        return default_value