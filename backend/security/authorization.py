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

from dataclasses import dataclass

from backend.security.principal import Principal

# 角色 → 权限点（最小集合，与既有角色枚举 viewer/editor/admin 对齐；
# RBAC 管理面已在 deps/rbac.py 按角色闸，此处给业务层统一 code 语义）
ROLE_PERMISSION_CODES: dict[str, frozenset[str]] = {
    "viewer": frozenset({"rag.read"}),
    "editor": frozenset({"rag.read", "rag.upload", "rag.review"}),
    "admin": frozenset({
        "rag.read", "rag.upload", "rag.review", "rag.admin",
        "admin.users.read", "admin.users.write",
    }),
}

# 角色 → 数据范围（未来 SQL 行级收敛的表达位；多角色取最宽）
_DATA_SCOPE_RANK = {"self": 0, "department": 1, "all": 2}
ROLE_DATA_SCOPE: dict[str, str] = {
    "viewer": "self",
    "editor": "department",
    "admin": "all",
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
