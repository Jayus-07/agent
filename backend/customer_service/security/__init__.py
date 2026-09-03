"""customer_service/security/ — 客服安全模块

权限校验 + 输出防护。
"""
from backend.customer_service.security.output_guard import CSOutputGuard, get_output_guard
from backend.customer_service.security.permission import PermissionChecker

__all__ = ["PermissionChecker", "CSOutputGuard", "get_output_guard"]
