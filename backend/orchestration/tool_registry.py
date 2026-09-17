"""
tool_registry.py — ⚠️ 已废弃别名（deprecated shim）

本模块原是 **Capability** 注册表本体，2026-09-17 改名为
``backend/orchestration/capability_registry.py``（与 ``backend/tools/tool_registry.py``
同名异物曾造成大量误判，详见新模块头部「命名沿革」）。

旧 import 路径暂保留兼容（容器内旧代码 / 外部脚本平滑过渡）。
**新代码一律 import 新模块**；守护测试扫描全仓，除本 shim 外出现
``backend.orchestration.tool_registry`` 引用即失败（见
backend/tests/orchestration/test_capability_registry_rename.py）。
"""
import warnings

warnings.warn(
    "backend.orchestration.tool_registry 已改名，请 import "
    "backend.orchestration.capability_registry",
    DeprecationWarning,
    stacklevel=2,
)

from backend.orchestration.capability_registry import (  # noqa: F401,E402
    ToolRegistry,
    _node_name,
    format_params_schema,
    tool_registry,
)

__all__ = ["ToolRegistry", "tool_registry", "format_params_schema", "_node_name"]
