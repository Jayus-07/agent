"""admin_mcp_subroutes/routes.py — 聚合主路由定义。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/admin/mcp", tags=["Admin-MCP"])

# 导入各子路由以触发端点注册
from backend.app.api.routes.admin_mcp_subroutes import (
    connection_routes as _connection_routes,
    overview_routes as _overview_routes,
    semantic_routes as _semantic_routes,
    tool_routes as _tool_routes,
)
