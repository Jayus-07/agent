"""test_principal_authorization.py — 统一主体与授权上下文（P1/P2/P10）

2026-09-23 授权生产收口的守护测试：
  - subject_type 唯一推导规则（department 永不参与认证类型判定）；
  - employee/no-dept 最小权限语义（永久守护——「误判 customer」回归防线）；
  - AuthorizationContext 单一计算点（KB 矩阵 / 权限点 / data_scope）；
  - HTTP 通道适配（网关注入头 → Principal；请求体身份字段永不参与）。
"""
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.app.api.identity import resolve_principal, require_principal
from backend.config import auth as auth_config
from backend.security.authorization import (
    AuthorizationContext,
    build_authorization_context,
)
from backend.security.principal import (
    Principal,
    derive_subject_type,
    resolve_tool_principal,
)


# ── subject_type 唯一规则 ─────────────────────────────────────

class TestDeriveSubjectType:
    def test_authenticated_is_employee_even_without_department(self):
        """永久守护：登录员工无部门 = employee，不是 customer
        （c4b865b 修正语义收口为唯一权威，department 是组织属性）。"""
        assert derive_subject_type(authenticated=True) == "employee"

    @pytest.mark.parametrize("failsafe", [True, False])
    def test_unauthenticated_never_employee(self, failsafe):
        assert derive_subject_type(authenticated=False,
                                   failsafe_customer=failsafe) != "employee"

    def test_unauthenticated_failsafe_customer(self):
        """未认证 → customer fail-safe（宁严勿漏：仅 cs_* 库）。"""
        assert derive_subject_type(authenticated=False,
                                   failsafe_customer=True) == "customer"

    def test_failsafe_off_is_undeclared(self):
        """RAG_TOOL_FAILSAFE_CUSTOMER=false 回滚为未声明主体（授权未启用）。"""
        assert derive_subject_type(authenticated=False,
                                   failsafe_customer=False) == ""


# ── Tool/contextvars 通道（图路径，c4b865b 语义迁移）──────────

class TestToolPrincipal:
    def test_employee_with_department(self):
        p = resolve_tool_principal(user_id="u-1", department="hr")
        assert p.subject_type == "employee" and p.department == "hr"
        assert p.authenticated is True

    def test_employee_without_department_keeps_empty_dept(self):
        p = resolve_tool_principal(user_id="u-1", department="")
        assert p.subject_type == "employee" and p.department == ""

    @pytest.mark.parametrize("uid", ["", "default", "anonymous"])
    def test_placeholder_users_not_authenticated(self, uid):
        p = resolve_tool_principal(user_id=uid)
        assert p.authenticated is False and p.subject_type == "customer"

    def test_failsafe_toggle(self, monkeypatch):
        monkeypatch.setenv("RAG_TOOL_FAILSAFE_CUSTOMER", "false")
        p = resolve_tool_principal(user_id="")
        assert p.subject_type == ""


# ── AuthorizationContext：KB 矩阵 / 权限点 / data_scope ───────

def _ctx(**kw) -> AuthorizationContext:
    return build_authorization_context(Principal(**kw))


class TestAuthorizationContext:
    def test_employee_no_dept_minimum_privilege(self):
        """employee 无部门 → 只见 owner_depts=all 的非 test 库（最小权限）。"""
        ctx = _ctx(user_id="u-1", subject_type="employee", department="",
                   authenticated=True)
        assert ctx.allowed_kb_ids is not None
        assert "policy_general" in ctx.allowed_kb_ids
        assert "policy_hr" not in ctx.allowed_kb_ids
        assert "policy_finance" not in ctx.allowed_kb_ids
        assert "rag_eval_kb" not in ctx.allowed_kb_ids  # test 库对任何主体不可见

    def test_hr_employee_kb_matrix(self):
        ctx = _ctx(user_id="u-1", subject_type="employee", department="hr",
                   roles=("viewer",), authenticated=True)
        assert "policy_hr" in ctx.allowed_kb_ids
        assert "policy_general" in ctx.allowed_kb_ids
        assert "policy_finance" not in ctx.allowed_kb_ids

    def test_finance_employee_kb_matrix(self):
        ctx = _ctx(user_id="u-1", subject_type="employee", department="finance",
                   roles=("viewer",), authenticated=True)
        assert "policy_finance" in ctx.allowed_kb_ids
        assert "policy_hr" not in ctx.allowed_kb_ids

    def test_customer_failsafe_only_customer_audience_kbs(self):
        """customer fail-safe：仅 audience=customer 库，内部库全不可见。

        旅游公共知识库也是 customer audience，但不以 cs_ 前缀命名；
        这里验证受众标签这一单一事实源，而不是把业务前缀当授权规则。
        """
        ctx = _ctx(user_id="", subject_type="customer", authenticated=False)
        assert ctx.allowed_kb_ids is not None and ctx.allowed_kb_ids
        from backend.config.knowledge_base import KNOWLEDGE_BASES

        assert all(
            KNOWLEDGE_BASES[kb].get("audience") == "customer"
            for kb in ctx.allowed_kb_ids
        )
        assert "travel" in ctx.allowed_kb_ids
        assert "policy_hr" not in ctx.allowed_kb_ids

    def test_undeclared_subject_allows_none(self):
        """未声明主体（failsafe off）→ allowed=None = 授权未启用（旧行为）。"""
        ctx = _ctx(user_id="", subject_type="", authenticated=False)
        assert ctx.allowed_kb_ids is None
        assert ctx.can_access_kb("policy_hr") is True  # 授权未启用=不收窄

    def test_can_access_kb_narrows_only(self):
        """显式 kb 请求只能收窄：授权集合外的 kb 一律拒绝（body spoof 防线）。"""
        ctx = _ctx(user_id="u-1", subject_type="employee", department="hr",
                   authenticated=True)
        assert ctx.can_access_kb("policy_hr") is True
        assert ctx.can_access_kb("policy_finance") is False

    def test_permission_codes_by_role(self):
        viewer = _ctx(user_id="u-1", subject_type="employee", roles=("viewer",),
                      authenticated=True)
        admin = _ctx(user_id="u-2", subject_type="employee", roles=("admin",),
                     authenticated=True)
        assert viewer.has_permission("rag.read")
        assert not viewer.has_permission("rag.upload")
        assert not viewer.has_permission("admin.users.write")
        assert admin.has_permission("admin.users.write")
        assert admin.has_permission("rag.upload")

    def test_unknown_role_grants_nothing(self):
        """未知角色不 silent 赋权（红线：不 silent allow unknown role）。"""
        ctx = _ctx(user_id="u-1", subject_type="employee", roles=("superadmin",),
                   authenticated=True)
        assert ctx.permission_codes == frozenset()

    def test_data_scope_by_role(self):
        admin = _ctx(user_id="u-1", subject_type="employee", roles=("admin",),
                     authenticated=True)
        editor = _ctx(user_id="u-2", subject_type="employee", roles=("editor",),
                      authenticated=True)
        assert admin.data_scope == "all"
        assert editor.data_scope == "department"

    def test_context_is_pure_no_io(self):
        """构建为纯内存计算（性能红线：权限处理毫秒级、无 DB 查询）。"""
        import time
        p = Principal(user_id="u-1", subject_type="employee", department="hr",
                      roles=("editor",), authenticated=True)
        t0 = time.perf_counter()
        for _ in range(1000):
            build_authorization_context(p)
        assert (time.perf_counter() - t0) < 1.0  # 1000 次构建 <1s


# ── HTTP 通道：网关头 → Principal（body 身份永不参与）─────────

def _app_client(identity_source="header"):
    app = FastAPI()

    @app.post("/whoami")
    async def whoami(request: Request):
        p = require_principal(request)
        return {"user_id": p.user_id, "department": p.department,
                "subject_type": p.subject_type, "roles": list(p.roles),
                "tenant_id": p.tenant_id, "authenticated": p.authenticated}

    @app.post("/whoami-optional")
    async def whoami_optional(request: Request):
        p = resolve_principal(request)
        return {"subject_type": p.subject_type,
                "authenticated": p.authenticated}

    return TestClient(app)


class TestHttpPrincipal:
    def test_gateway_headers_become_principal(self):
        client = _app_client()
        resp = client.post("/whoami", headers={
            "X-Auth-Type": "jwt", "X-User-Id": "42", "X-User-Name": "zhang",
            "X-User-Dept": "hr", "X-User-Roles": "editor,agent",
            "X-Tenant-Id": "tenant-a",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "42"
        assert body["subject_type"] == "employee"
        assert body["department"] == "hr"
        assert body["roles"] == ["editor", "agent"]
        assert body["tenant_id"] == "tenant-a"

    def test_missing_headers_is_401_on_require(self):
        client = _app_client()
        resp = client.post("/whoami")
        assert resp.status_code == 401

    def test_guest_headers_resolve_customer(self):
        """guest 占位身份（网关 GATEWAY_AUTH_MODE=guest 注入）→ customer fail-safe。"""
        client = _app_client()
        resp = client.post("/whoami-optional", headers={
            "X-Auth-Type": "anonymous", "X-User-Id": "anonymous",
        })
        assert resp.json()["subject_type"] == "customer"
        assert resp.json()["authenticated"] is False

    def test_body_identity_fields_never_become_principal(self):
        """红线（P7）：请求体声明身份字段在 header 模式下永不参与主体构造
        ——resolve_principal 根本不接受 body 参数（与 resolve_identity 的
        legacy 兼容通道不同，授权收口路径无 body 后门）。"""
        import inspect
        sig = inspect.signature(resolve_principal)
        assert "body_user_id" not in sig.parameters
        assert list(sig.parameters) == ["request"]


# ── Chat body department 不可提权（P7/P8 语义锁定）────────────

class TestBodySpoofSemantics:
    def test_chat_schema_department_is_legacy_hint_only(self):
        """ChatRequest.department 仅是兼容字段：chat 路由消费的是
        ident.department（JWT dept claim），body 值不参与授权。
        本测试锁定 schema 行为防止未来误接线。"""
        from backend.app.api.schemas import ChatRequest
        req = ChatRequest(question="q", department="finance")
        assert req.department == "finance"  # 字段可解析（兼容旧客户端）
        # 授权主体与 body 无关：employee/hr 的 allowed 集合不受 body 影响
        ctx = _ctx(user_id="u-1", subject_type="employee", department="hr",
                   authenticated=True)
        assert ctx.can_access_kb("policy_finance") is False
