"""RBAC 领域子包，统一显式聚合导出全部契约与路由符号。"""
from __future__ import annotations

from backend.app.api.routes.rbac_subroutes.models import (
    RbacAccess,
    UserUpdateOutcome,
    _ALLOWED_PLATFORM_ROLES,
    _ALLOWED_CS_ROLES,
    _DEPARTMENT_CODE_RE,
    _MISSING,
    _USERNAME_RE,
    _OPS_USERNAME_PREFIXES,
)
from backend.app.api.routes.rbac_subroutes.dynamic import (
    DynamicDbProxy,
    DynamicSessionServiceProxy,
    current_func,
    get_facade_attr,
)
from backend.app.api.routes.rbac_subroutes.common import (
    _session_service_instance,
    _session_service,
    _db_raw,
    _db,
    require_rbac_admin,
    _manager_departments,
    require_rbac_access,
    _row_value,
    _tenant_id,
    _actor_user_id,
    _parse_json,
    _iso,
    _http_error,
    _validate_role_transition,
    _stable_display_name,
    _validate_body,
    _department_active,
    _is_customer_service_department,
    _load_cs_agent,
    _cs_role,
    _cs_public,
    _state,
    _merge_agent,
    _rollback,
    _is_concurrency_conflict,
    _department_public,
    _department_audit_ready,
    _write_department_audit,
    _valid_new_username,
    _generate_temp_password,
    _write_user_audit,
    _can_operate_super_admin,
    _audit_item,
)
from backend.app.api.routes.rbac_subroutes.service import (
    update_user_in_transaction,
    _change_department,
    _update_department_member_in_transaction,
)
from backend.app.api.routes.rbac_subroutes.routes import router

# 导入各子路由模块以完成向 router 的端点注册
from backend.app.api.routes.rbac_subroutes import context_routes as _context_routes
from backend.app.api.routes.rbac_subroutes import department_routes as _department_routes
from backend.app.api.routes.rbac_subroutes import user_routes as _user_routes
from backend.app.api.routes.rbac_subroutes import cs_team_routes as _cs_team_routes
from backend.app.api.routes.rbac_subroutes import audit_routes as _audit_routes

from backend.app.api.routes.rbac_subroutes.context_routes import access_context
from backend.app.api.routes.rbac_subroutes.department_routes import (
    list_departments,
    create_department,
    update_department,
    deactivate_department,
)
from backend.app.api.routes.rbac_subroutes.user_routes import (
    list_users,
    create_user,
    get_user_detail,
    update_user,
    reset_password,
    force_logout_user,
)
from backend.app.api.routes.rbac_subroutes.cs_team_routes import (
    list_customer_service_team,
    update_customer_service_member,
)
from backend.app.api.routes.rbac_subroutes.audit_routes import list_audit

__all__ = [
    "router",
    "RbacAccess",
    "UserUpdateOutcome",
    "_ALLOWED_PLATFORM_ROLES",
    "_ALLOWED_CS_ROLES",
    "_DEPARTMENT_CODE_RE",
    "_MISSING",
    "_USERNAME_RE",
    "_OPS_USERNAME_PREFIXES",
    "require_rbac_admin",
    "require_rbac_access",
    "update_user_in_transaction",
    "_validate_role_transition",
    "_db",
    "_session_service",
    "access_context",
    "list_departments",
    "create_department",
    "update_department",
    "deactivate_department",
    "list_users",
    "create_user",
    "get_user_detail",
    "update_user",
    "reset_password",
    "force_logout_user",
    "list_customer_service_team",
    "update_customer_service_member",
    "list_audit",
]
