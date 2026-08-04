"""向后兼容 shim — 超时保护已迁至 infra/timeout.py，资源监控已迁至 observability/resource.py。"""
from backend.infra.timeout import safe_call_with_timeout, TimeoutError  # noqa: F401
from backend.observability.resource import ResourceMonitor, resource_monitor  # noqa: F401
