"""customer_service/router — 客服路由子系统"""
from backend.customer_service.router.cs_router import CSRouter, get_cs_router
from backend.customer_service.router.types import (
    CSDetection,
    CSDomain,
    CSRoutePath,
    CSRouteResult,
    IntentProfile,
)

__all__ = [
    "CSDomain",
    "CSRoutePath",
    "CSDetection",
    "CSRouteResult",
    "IntentProfile",
    "CSRouter",
    "get_cs_router",
]
