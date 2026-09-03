"""customer_service/router — 客服路由子系统"""
from backend.customer_service.router.types import (
    CSDomain,
    CSRoutePath,
    CSDetection,
    CSRouteResult,
    IntentProfile,
)
from backend.customer_service.router.cs_router import CSRouter, get_cs_router

__all__ = [
    "CSDomain",
    "CSRoutePath",
    "CSDetection",
    "CSRouteResult",
    "IntentProfile",
    "CSRouter",
    "get_cs_router",
]
