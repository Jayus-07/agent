"""mcp_registry — 外部 MCP 动态工具注册表（DB 事实源 → 进程内快照）。

模块边界（ADR-0011）：
  specs.py  DB 行 → ToolSpec / CapabilityDecl 派生（纯函数，可单测）
  store.py  快照 + 轮询 + 失效回调（运行时唯一视图）
"""

from backend.infra.mcp_registry import specs, store

__all__ = ["specs", "store"]
