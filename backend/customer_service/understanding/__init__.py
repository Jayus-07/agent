# 已迁移（迁移 B11 结构整理，只做结构迁移不改业务）。本文件为 import 兼容 shim。
import importlib as _il
import sys as _sys
_PKG = "backend.customer_service.context.understanding"
for _n in ['types', 'entities', 'slots', 'signals', 'service']:
    _sys.modules[f"{__name__}.{_n}"] = _il.import_module(f"{_PKG}.{_n}")
from backend.customer_service.context.understanding import *  # noqa: F401,F403
