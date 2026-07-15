"""向后兼容 re-export（新代码请用 utils.monitoring.resource_monitor）"""
from backend.utils.monitoring.resource_monitor import ResourceMonitor, resource_monitor
__all__ = ["ResourceMonitor", "resource_monitor"]
