# 已迁移（迁移 B11 结构整理，只做结构迁移不改业务）。本文件为 import 兼容 shim。
import sys as _sys
import backend.customer_service.context.context_resolver as _impl  # noqa: E402
_sys.modules[__name__] = _impl
