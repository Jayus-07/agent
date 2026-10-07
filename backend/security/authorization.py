"""security/authorization.py — 统一授权上下文（P2，2026-09-23 授权生产收口）

在 Principal 之上回答「你能做什么、能看什么」，业务层只消费
AuthorizationContext（allowed_kb_ids / has_permission / department /
data_scope），不再自行推导。单一计算点：

  - allowed_kb_ids：复用 config/knowledge_base.authorized_kbs（已验证
    的 owner_depts/audience 矩阵，检索层同一语义），本模块只做"授权
    上下文"形态暴露，不复制第二份矩阵；
  - permission_codes：由角色推导（JWT 只带 roles 不带权限点——体积小
    且权限策略可随 DB 更新）。稳定 code 命名 `<域>.<动作>`，当前只登记
    真正在用的最小集合（RAG 读/传、管理端用户读写），未来 SQL/CS 按
    同一命名演进；
  - data_scope：未来 SQL Agent 的行级范围表达位（all/department/self），
    本阶段只定义不实现。

构建成本：纯内存计算，无 DB/IO——每 HTTP/graph run 构建一次即为毫秒级。
"""
from __future__ import annotations

from dataclasses import replace
from dataclasses import dataclass

from backend.security.principal import Principal

# 角色 → 权限点（最小集合，与 RBAC 平台角色枚举 viewer/editor/admin/
# super_admin 对齐；RBAC 管理面已在 routes/rbac.py 按角色闸，此处给业务层
# 统一 code 语义）
# sql.read（2026-09-23 SQL Agent 生产收口 STOP B，决策 D3）：
#   editor/admin 可用 SQL 数据分析，viewer 不含 → SQL 层消费
#   has_permission("sql.read") 拒绝。映射只在此处，SQL 层禁止自判角色。
# super_admin（2026-10-01 管理端数据查询）：此前缺登记 → 未知角色落空
#   权限集，管理端全业务权限码不可用；补齐为 admin 超集（管理端引导页
#   与 bootstrap 都按"超集"语义使用该角色，见 routes/rbac.py 转移矩阵）。
ROLE_PERMISSION_CODES: dict[str, frozenset[str]] = {
    "viewer": frozenset({"rag.read"}),
    "editor": frozenset({"rag.read", "rag.upload", "rag.review", "sql.read"}),
    "admin": frozenset({
        "rag.read", "rag.upload", "rag.review", "rag.admin",
        "admin.users.read", "admin.users.write",
        "sql.read",
    }),
    "super_admin": frozenset({
        "rag.read", "rag.upload", "rag.review", "rag.admin",
        "admin.users.read", "admin.users.write",
        "sql.read",
    }),
    # service_workflow（2026-10-08 #10 定时工作流服务身份）：APScheduler
    # 触发的系统工作流（inventory_alert/daily_report）以 svc:workflow:<名>
    # 机器主体执行确定性 SQL。只授 sql.read；六层 SQL 安全（只读校验/
    # 表白名单/敏感列/函数白名单/LIMIT/readonly 连接角色）不因服务身份
    # 豁免。仅在 scheduler._run_async 绑定（security/service_identity.py），
    # 手动触发保留调用者真人身份。
    "service_workflow": frozenset({"sql.read"}),
}

# 角色 → 数据范围（未来 SQL 行级收敛的表达位；多角色取最宽）
_DATA_SCOPE_RANK = {"self": 0, "department": 1, "all": 2}
ROLE_DATA_SCOPE: dict[str, str] = {
    "viewer": "self",
    "editor": "department",
    "admin": "all",
    "super_admin": "all",
    # 服务主体按 all：定时工作流是全库扫描语义（库存预警/日报），
    # 行级 self/department 对机器主体无意义；仍受表白名单等其余层约束。
    "service_workflow": "all",
}


@dataclass(frozen=True)
class AuthorizationContext:
    """一次请求的授权上下文（主体之上、业务之下的唯一授权视图）。"""

    principal: Principal
    # 可见知识库集合；None = 未声明主体 → 授权未启用（旧行为，评测/直调场景）
    allowed_kb_ids: frozenset[str] | None
    permission_codes: frozenset[str]
    data_scope: str | None

    @property
    def department(self) -> str:
        return self.principal.department

    @property
    def tenant_id(self) -> str:
        return self.principal.tenant_id

    @property
    def subject_type(self) -> str:
        return self.principal.subject_type

    def has_permission(self, code: str) -> bool:
        return code in self.permission_codes

    def can_access_kb(self, kb_id: str) -> bool:
        """显式 kb 请求的授权判定：只能收窄，不能由用户扩大。"""
        if self.allowed_kb_ids is None:
            return True  # 授权未启用（未声明主体，旧行为）
        return kb_id in self.allowed_kb_ids


def _widest_data_scope(roles: tuple[str, ...]) -> str | None:
    scope, rank = None, -1
    for role in roles:
        candidate = ROLE_DATA_SCOPE.get(role)
        if candidate is not None and _DATA_SCOPE_RANK[candidate] > rank:
            scope, rank = candidate, _DATA_SCOPE_RANK[candidate]
    return scope


def widest_data_scope(roles: tuple[str, ...]) -> str | None:
    """角色 → data_scope 的公开推导入口（多角色取最宽）。

    图通道（runner 入口）用它把 roles 折算成可随 checkpoint 序列化的
    data_scope 字段；推导规则唯一存在于本模块，消费方不得自行判角色。
    """
    return _widest_data_scope(roles)


def build_tool_authorization_context(
    *,
    user_id: str,
    department: str = "",
    tenant_id: str = "",
    roles: tuple[str, ...] = (),
    data_scope: str | None = None,
) -> AuthorizationContext:
    """图/Tool 通道装配点：可信身份字段 → AuthorizationContext。

    无 HTTP Request 可用时（graph state / Tool contextvars）的唯一构建
    入口，语义与 HTTP 通道 build_authorization_context 完全一致：
    authenticated/subject_type 由 user_id 统一推导，权限点只从 roles 推导。

    data_scope：入口已按 roles 算好并随状态透传时直接采用（同一推导
    结果的透传，非第二套逻辑）；缺省回退 _widest_data_scope(roles)。
    """
    from backend.security.principal import resolve_tool_principal

    principal = resolve_tool_principal(
        user_id=user_id,
        department=department,
        tenant_id=tenant_id,
        roles=roles,
    )
    ctx = build_authorization_context(principal)
    effective_scope = data_scope or _widest_data_scope(principal.roles)
    if effective_scope != ctx.data_scope:
        ctx = replace(ctx, data_scope=effective_scope)
    return ctx


def build_authorization_context(principal: Principal) -> AuthorizationContext:
    """Principal → AuthorizationContext（每 HTTP/graph run 只构建一次）。"""
    from backend.config.knowledge_base import authorized_kbs

    allowed = authorized_kbs(principal.subject_type, principal.department)
    codes: set[str] = set()
    for role in principal.roles:
        codes |= ROLE_PERMISSION_CODES.get(role, frozenset())
    return AuthorizationContext(
        principal=principal,
        allowed_kb_ids=None if allowed is None else frozenset(allowed),
        permission_codes=frozenset(codes),
        data_scope=_widest_data_scope(principal.roles),
    )
