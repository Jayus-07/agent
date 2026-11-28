"""RBAC 路由集中挂载实例。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/sys/rbac", tags=["管理端-RBAC"])
