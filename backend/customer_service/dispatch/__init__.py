# 已迁移（迁移 B11 结构整理，只做结构迁移不改业务）。本文件为 import 兼容 shim。
import importlib as _il
import sys as _sys
_PKG = "backend.customer_service.handoff.dispatch"
for _n in ['repository', 'presence', 'service', 'offers', 'reaper', 'outbox', 'event_relay', 'agent_busy']:
    _sys.modules[f"{__name__}.{_n}"] = _il.import_module(f"{_PKG}.{_n}")
from backend.customer_service.handoff.dispatch import *  # noqa: F401,F403
