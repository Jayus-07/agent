"""
超时保护装饰器
为函数调用添加超时保护，防止长时间卡死
"""
import signal
from typing import Callable, Any
from backend.shared.logger import logger


class TimeoutError(Exception):
    """超时异常"""
    pass


def _timeout_unix(func: Callable, seconds: int, error_message: str, *args, **kwargs):
    """Unix/Linux/Mac超时实现（使用signal）"""
    import threading

    def handler(signum, frame):
        raise TimeoutError(error_message)

    # ⚠️ signal 只能在主线程注册（2026-09-22 实机验证发现）：LangGraph 节点
    # 跑在线程本地 loop 的 worker 线程里，tool_selector 等节点内的
    # safe_call_with_timeout 曾 100% 抛 "signal only works in main thread"
    # → FC 选择静默全灭。非主线程退化为线程版（不能抢占，但能限时等待）。
    if threading.current_thread() is not threading.main_thread():
        return _timeout_windows(func, seconds, error_message, *args, **kwargs)

    old_handler = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)

    try:
        result = func(*args, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    return result


def _timeout_windows(func: Callable, seconds: int, error_message: str, *args, **kwargs):
    """
    Windows超时实现（使用threading）
    注意：这不会真正中断函数执行，只是提前返回
    """
    import threading

    result = [None]
    exception = [None]

    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            exception[0] = e

    thread = threading.Thread(target=target)
    thread.daemon = True
    thread.start()
    thread.join(timeout=seconds)

    if thread.is_alive():
        logger.warning(f"操作超时: {error_message} ({seconds}秒)")
        raise TimeoutError(error_message)

    if exception[0]:
        raise exception[0]

    return result[0]


def safe_call_with_timeout(
    func: Callable,
    timeout: int,
    default_value: Any = None,
    error_message: str = "操作超时",
    *args,
    **kwargs
) -> Any:
    """
    安全调用函数（带超时保护）

    Args:
        func: 要调用的函数
        timeout: 超时秒数
        default_value: 超时或出错时的默认返回值
        error_message: 超时错误消息
        *args: 函数位置参数
        **kwargs: 函数关键字参数

    Returns:
        函数返回值或默认值
    """
    try:
        import platform
        if platform.system() == 'Windows':
            return _timeout_windows(func, timeout, error_message, *args, **kwargs)
        else:
            return _timeout_unix(func, timeout, error_message, *args, **kwargs)
    except TimeoutError as e:
        logger.warning(f"超时返回: {e}")
        return default_value
    except Exception as e:
        logger.error(f"函数调用失败: {e}")
        return default_value
