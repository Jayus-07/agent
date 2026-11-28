"""Tier 12 架构守卫测试：RBAC 模块化解耦与契约向后兼容性守护。

验证项：
1. 门面文件 backend/app/api/routes/rbac.py 严格收敛至 200 行以内（防大单体膨胀腐化）；
2. backend/app/api/routes/rbac_subroutes 子包完备性（按职责物理切分）；
3. 契约符号导出完备性（原单体公开与内部敏感契约 100% 重新导出）；
4. APIRouter 端点完备性（14 个原有端点完整挂载，路径、方法保持一致）；
5. 动态门面穿透能力（外部 monkeypatch 替换门面 _db 与 _session_service 时子路由感知生效）。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from backend.app.api.routes import rbac
from backend.app.api.routes.rbac_subroutes import (
    RbacAccess,
    UserUpdateOutcome,
    _ALLOWED_CS_ROLES,
    _ALLOWED_PLATFORM_ROLES,
    _DEPARTMENT_CODE_RE,
    _MISSING,
    _OPS_USERNAME_PREFIXES,
    _USERNAME_RE,
    _actor_user_id,
    _audit_item,
    _can_operate_super_admin,
    _change_department,
    _cs_public,
    _cs_role,
    _db,
    _department_active,
    _department_audit_ready,
    _department_public,
    _generate_temp_password,
    _http_error,
    _is_concurrency_conflict,
    _is_customer_service_department,
    _iso,
    _load_cs_agent,
    _manager_departments,
    _merge_agent,
    _parse_json,
    _rollback,
    _row_value,
    _session_service,
    _stable_display_name,
    _state,
    _tenant_id,
    _update_department_member_in_transaction,
    _validate_body,
    _validate_role_transition,
    _valid_new_username,
    _write_department_audit,
    _write_user_audit,
    access_context,
    create_department,
    create_user,
    deactivate_department,
    force_logout_user,
    get_user_detail,
    list_audit,
    list_customer_service_team,
    list_departments,
    list_users,
    require_rbac_access,
    require_rbac_admin,
    reset_password,
    router,
    update_customer_service_member,
    update_department,
    update_user,
    update_user_in_transaction,
)


def test_facade_line_count_within_budget():
    """断言门面 rbac.py 行数在预算内（< 200 行，原 1995 行）。"""
    facade_file = Path(rbac.__file__)
    lines = facade_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 200, f"rbac.py 行数超出预算: {len(lines)} > 200"
    assert len(lines) < 120, f"rbac.py 瘦身未达预期，当前行数: {len(lines)}"


def test_subroutes_package_structure():
    """断言 rbac_subroutes 专职子模块完整存在。"""
    subroutes_dir = Path(rbac.__file__).parent / "rbac_subroutes"
    expected_files = [
        "__init__.py",
        "models.py",
        "dynamic.py",
        "routes.py",
        "common.py",
        "service.py",
        "context_routes.py",
        "department_routes.py",
        "user_routes.py",
        "cs_team_routes.py",
        "audit_routes.py",
    ]
    for filename in expected_files:
        module_path = subroutes_dir / filename
        assert module_path.is_file(), f"缺少专职子模块: {filename}"


def test_symbol_contract_completeness():
    """断言所有公开契约、数据类与私有核心函数在门面上 100% 保持向后兼容。"""
    required_symbols = [
        "router",
        "RbacAccess",
        "UserUpdateOutcome",
        "require_rbac_admin",
        "require_rbac_access",
        "update_user_in_transaction",
        "_validate_role_transition",
        "_db",
        "_session_service",
        "_manager_departments",
        "_row_value",
        "_tenant_id",
        "_actor_user_id",
        "_parse_json",
        "_iso",
        "_http_error",
        "_stable_display_name",
        "_validate_body",
        "_department_active",
        "_is_customer_service_department",
        "_load_cs_agent",
        "_cs_role",
        "_cs_public",
        "_state",
        "_merge_agent",
        "_rollback",
        "_is_concurrency_conflict",
        "_department_public",
        "_department_audit_ready",
        "_write_department_audit",
        "_valid_new_username",
        "_generate_temp_password",
        "_write_user_audit",
        "_can_operate_super_admin",
        "_audit_item",
        "_change_department",
        "_update_department_member_in_transaction",
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
        "_ALLOWED_PLATFORM_ROLES",
        "_ALLOWED_CS_ROLES",
        "_DEPARTMENT_CODE_RE",
        "_MISSING",
        "_USERNAME_RE",
        "_OPS_USERNAME_PREFIXES",
    ]
    for sym in required_symbols:
        assert hasattr(rbac, sym), f"rbac 门面缺少导出符号: {sym}"


def test_router_configuration_and_endpoints():
    """断言路由配置与所有端点原生挂载完备性。"""
    assert rbac.router.prefix == "/sys/rbac"
    assert "管理端-RBAC" in rbac.router.tags

    endpoints = {}
    for route in rbac.router.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                endpoints[(method, route.path)] = route.endpoint

    expected_routes = [
        ("GET", "/sys/rbac/context"),
        ("GET", "/sys/rbac/departments"),
        ("POST", "/sys/rbac/departments"),
        ("PATCH", "/sys/rbac/departments/{code}"),
        ("DELETE", "/sys/rbac/departments/{code}"),
        ("GET", "/sys/rbac/users"),
        ("POST", "/sys/rbac/users"),
        ("GET", "/sys/rbac/users/{user_id}"),
        ("PATCH", "/sys/rbac/users/{user_id}"),
        ("POST", "/sys/rbac/users/{user_id}/reset-password"),
        ("POST", "/sys/rbac/users/{user_id}/force-logout"),
        ("GET", "/sys/rbac/customer-service/team"),
        ("PATCH", "/sys/rbac/customer-service/team/{user_id}"),
        ("GET", "/sys/rbac/audit"),
    ]

    for method, path in expected_routes:
        assert (method, path) in endpoints, f"缺少预期路由端点: {method} {path}"
    assert len(endpoints) == 14, f"端点总数不符合预期: {len(endpoints)} != 14"


def test_dynamic_facade_monkeypatch_penetration(monkeypatch):
    """断言外部测试对门面基础设施对象的 monkeypatch 能穿透至子模块。"""
    invoked_custom_db = False

    class FakeCustomDb:
        def __call__(self, *args, **kwargs):
            nonlocal invoked_custom_db
            invoked_custom_db = True
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(rbac, "_db", FakeCustomDb())

    from backend.app.api.routes.rbac_subroutes import common
    # 调用子模块中的 _db
    common._db()
    assert invoked_custom_db is True, "动态代理未能穿透感知门面上的 _db monkeypatch"
