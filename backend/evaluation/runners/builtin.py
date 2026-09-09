"""内置 Runner 兼容垫片 — 实际实现已拆分到独立模块。

此文件保留为向后兼容层。所有外部 import 均重导出到对应子模块：
  - _run_rag        → runners.rag
  - _run_planner    → runners.planner
  - _match_by_snippet / _extract_query_entities / _entities_all_present → runners._common

import 此文件会触发各子模块加载，从而执行 register_runner() 注册。
"""

# --- 导入即注册：各子模块在加载时调用 register_runner() ---
from backend.evaluation.runners.planner import _run_planner
from backend.evaluation.runners.rag import _run_rag

# --- 测试兼容：直接 import 的工具函数 ---
from backend.evaluation.runners._common import (
    normalize_snippet_text as _normalize_snippet_text,
    match_by_snippet as _match_by_snippet,
    extract_query_entities as _extract_query_entities,
    entities_all_present as _entities_all_present,
)

__all__ = [
    "_run_planner",
    "_run_rag",
    "_match_by_snippet",
    "_extract_query_entities",
    "_entities_all_present",
]
