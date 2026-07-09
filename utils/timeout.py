"""向后兼容 re-export（新代码请用 utils.monitoring.timeout）"""
from utils.monitoring.timeout import safe_call_with_timeout, TimeoutError
__all__ = ["safe_call_with_timeout", "TimeoutError"]
