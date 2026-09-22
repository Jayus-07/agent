# -*- coding: utf-8 -*-
"""SQL Policy Guard 测试共享 fixture（STOP B）。

Guard/注入引擎全部纯内存（AST + policy），不连库；
fixture 表通过 schema_loader.register_table 注册并在用例后清理，
防止污染模块级单例影响其他测试。
"""
import pytest

from backend.security.authorization import AuthorizationContext
from backend.security.principal import Principal
from backend.security.authorization import build_tool_authorization_context
from backend.sql.policy import SQLPolicyContext, SQLPolicyGuard
from backend.sql.schema_loader import TablePolicy, schema_loader

# fixture 表（tenant / department 注入能力位验证，真实 18 表均无这些列）
FIXTURE_TENANT_TABLE = "demo.tenant_projects"
FIXTURE_DEPT_TABLE = "demo.dept_projects"


def _register(qname: str, columns: dict, policy: TablePolicy) -> None:
    schema_loader.register_table(qname, columns, description="policy fixture",
                                 policy=policy)


@pytest.fixture
def guard() -> SQLPolicyGuard:
    return SQLPolicyGuard()


@pytest.fixture(autouse=True)
def _no_real_audit_db(monkeypatch):
    """单元/策略测试不触达真实审计库（audit 是外部边界）。

    record_sql_audit 保留真实调用（TestAuditAttribution 捕获参数、
    TestAuditBestEffort 单独覆盖失败路径），仅把 DB 连接断开——
    防止宿主机测试环境把审计行写进 .env 指向的非权威 PG（双库坑）。
    """
    from backend.sql import audit as audit_mod

    def _no_conn():
        raise RuntimeError("audit db disabled in unit tests")

    monkeypatch.setattr(audit_mod, "_conn", _no_conn)


@pytest.fixture
def fixture_tables():
    """注册 tenant/department fixture 表，用例后清理单例状态。"""
    _register(
        FIXTURE_TENANT_TABLE,
        {"id": "PK", "project_name": "VARCHAR", "tenant_id": "VARCHAR"},
        TablePolicy(data_domain="shared", tenant_column="tenant_id"),
    )
    _register(
        FIXTURE_DEPT_TABLE,
        {"id": "PK", "project_name": "VARCHAR", "department": "VARCHAR",
         "owner_id": "INTEGER"},
        TablePolicy(data_domain="personal", department_column="department",
                    self_column="owner_id"),
    )
    yield
    # ── 清理（register_table 写入了 4 处单例状态）──
    for qname in (FIXTURE_TENANT_TABLE, FIXTURE_DEPT_TABLE):
        schema_loader.allowed_tables.discard(qname)
        schema_loader.table_policies.pop(qname, None)
        schema_loader._config["tables"].pop(qname, None)
    schema_loader.allowed_schemas.discard("demo")
    schema_loader._tables_by_schema.pop("demo", None)


def make_ctx(scope: str, *, department: str = "", tenant_id: str = "",
             user_id: str = "3", roles: tuple = ("editor",),
             permission_codes=None) -> SQLPolicyContext:
    """直接构造策略上下文（scope 矩阵用；permission_codes 显式给定时
    绕过角色推导，用于权限门单点测试）。"""
    principal = Principal(
        user_id=user_id, department=department, tenant_id=tenant_id,
        roles=tuple(roles), subject_type="employee", authenticated=True,
    )
    codes = (frozenset(permission_codes) if permission_codes is not None
             else frozenset({"sql.read"}))
    authz = AuthorizationContext(
        principal=principal, allowed_kb_ids=None,
        permission_codes=codes, data_scope=scope,
    )
    return SQLPolicyContext(principal=principal, authz=authz)


def build_ctx(*, user_id: str, department: str = "", tenant_id: str = "",
              roles: tuple = ()) -> SQLPolicyContext:
    """真实装配链（roles → permission_codes/data_scope 推导，单一来源）。"""
    authz = build_tool_authorization_context(
        user_id=user_id, department=department, tenant_id=tenant_id,
        roles=tuple(roles),
    )
    return SQLPolicyContext(principal=authz.principal, authz=authz)
