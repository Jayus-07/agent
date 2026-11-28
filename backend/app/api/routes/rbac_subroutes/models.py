"""RBAC 领域数据模型与常量。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.app.api.deps import OperatorIdentity
from backend.security.session_service import SessionRef

_ALLOWED_PLATFORM_ROLES = {"viewer", "editor", "admin", "super_admin"}
_ALLOWED_CS_ROLES = {"agent", "supervisor"}
_DEPARTMENT_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")
_MISSING = object()
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,20}$")
_OPS_USERNAME_PREFIXES = ("ops_", "svc_")


@dataclass(frozen=True)
class RbacAccess:
    """平台管理员或当前操作者可管理的部门范围。"""

    operator: OperatorIdentity
    is_platform_admin: bool
    managed_departments: tuple[str, ...] = ()


@dataclass(frozen=True)
class UserUpdateOutcome:
    """一次已写入当前事务、等待提交的用户更新结果。"""

    user: dict[str, Any]
    revoked_sessions: list[SessionRef]
