"""data_collection/tool.py — 向后兼容 re-export（PR-2.x 工具已迁至 tools/ 包）。"""
from backend.tools.data_collection import data_collection_tool, _format_result  # noqa: F401
