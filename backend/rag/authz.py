"""RAG 授权服务 — 文档/知识库读写裁决的单一出口（2026-10-01 权限旁路收口 A）。

此前 RAG 各入口的授权散落且口径不一：/rag/search 本地分支完全绕过授权检索
链；文档管理面只有「已认证」门禁；上传的 department 由请求体自报。本模块把
Principal（security/principal.py）→ KB 级 ABAC（config/knowledge_base.
authorized_kbs，单一矩阵）与文档级 permission_scope（rag/permissions.py）
组合成四个可测试的裁决点，所有 RAG 读写入口只消费本模块，禁止自行推导。

口径（本文件为唯一权威，tests/rag/test_rag_authz.py 钉死）：
  - KB 可见集：employee 复用 authorized_kbs（owner_depts 部门矩阵）；
    customer 仅 audience=customer 库；audience=test 库对任何主体不可见。
  - admin（permission codes 含 rag.admin，由 roles 经
    security/authorization.ROLE_PERMISSION_CODES 推导）：**跨部门读 + 管理
    全部非 test 库**（ROLE_DATA_SCOPE admin="all" 的既有语义延伸到文档
    敏感级）；permission_scope 对 admin 不设限。
  - 非 admin 的文档级裁决：permission_scope 所需权限 ⊆ 用户持有权限；
    未声明 permissions（None）→ 受限文档 fail-safe 拒绝（general 不受影响）。
  - 上传/管理：KB 必须 ∈ 主体可见集；department 一律取 principal.department
    ——请求体自报部门只有 admin 可指定（且仍过 validate_kb_dept 组合校验）。
  - fail-closed：授权计算中的任何异常抛 RagAuthorizationError，调用方必须
    拒绝请求；已认证主体拿到的可见集为空集时按空集拒绝，绝不回退无过滤。
  - 未声明主体（subject_type 既非 employee 也非 customer，authorized_kbs
    返回 None）：仅限服务端内部调用（评测/批量任务）保持旧行为；HTTP 入口
    经 require_principal 后不可能出现，出现即按空集拒绝。
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.security.principal import Principal

# admin 判定的唯一权限点（ROLE_PERMISSION_CODES 中仅 admin 角色持有）
RAG_ADMIN_PERMISSION = "rag.admin"


class RagAuthorizationError(RuntimeError):
    """授权服务自身异常（矩阵缺失/属性非法等）——调用方必须拒绝请求。"""


def _permission_codes(roles: tuple[str, ...]) -> frozenset[str]:
    from backend.security.authorization import ROLE_PERMISSION_CODES

    codes: set[str] = set()
    for role in roles or ():
        codes |= ROLE_PERMISSION_CODES.get(role, frozenset())
    return frozenset(codes)


def is_admin_roles(roles: tuple[str, ...]) -> bool:
    """roles → 是否持有 rag.admin 权限点（角色语义唯一权威在 authorization.py）。"""
    return RAG_ADMIN_PERMISSION in _permission_codes(roles)


def readable_kb_ids(
    subject_type: str, department: str = "", *, is_admin: bool = False
) -> list[str] | None:
    """主体属性 → 可见 KB 集合。未声明主体透传 None（仅内部调用合法）。

    admin 跨部门：全部非 test 库。其余主体按 authorized_kbs（None =
    未声明主体，HTTP 入口视同空集拒绝）。
    """
    from backend.config.knowledge_base import KNOWLEDGE_BASES, authorized_kbs

    if is_admin:
        return [
            kb_id for kb_id, info in KNOWLEDGE_BASES.items()
            if info.get("audience") != "test"
        ]
    return authorized_kbs(subject_type, department)


def retrieval_authorized_kbs(
    subject_type: str, department: str = "", roles: tuple[str, ...] = ()
) -> list[str] | None:
    """检索链 KB 授权集合的单一计算点（ChunkLevelRetriever/retrieve_* 消费）。

    与 readable_kb_ids 同口径；None = 未声明主体 → 授权未启用（内部直调
    旧行为）。admin 跨部门口径在此收口，检索层禁止再自行判角色。
    """
    return readable_kb_ids(
        subject_type, department, is_admin=is_admin_roles(roles)
    )


@dataclass(frozen=True)
class RagAuthorization:
    """一次请求的 RAG 授权视图（构建即裁决，成员全部只读）。"""

    principal: Principal
    kb_scope: frozenset[str]
    is_admin: bool
    user_permissions: tuple[str, ...] | None

    @classmethod
    def build(cls, principal: Principal) -> "RagAuthorization":
        """Principal → 授权视图。任何异常包成 RagAuthorizationError（fail-closed）。"""
        try:
            is_admin = is_admin_roles(principal.roles)
            if not principal.authenticated:
                raise RagAuthorizationError("未认证主体不允许进入 RAG 授权面")
            allowed = readable_kb_ids(
                principal.subject_type, principal.department, is_admin=is_admin
            )
            # None 只可能来自未声明主体（authorized_kbs 的内部直调语义）；
            # HTTP 入口视为授权失效，按空集拒绝而不是放行。
            scope = frozenset(allowed or [])
            return cls(
                principal=principal,
                kb_scope=scope,
                is_admin=is_admin,
                user_permissions=(
                    None
                    if principal.permissions is None
                    else tuple(principal.permissions)
                ),
            )
        except RagAuthorizationError:
            raise
        except Exception as exc:  # noqa: BLE001 — 授权故障必须显式失败
            raise RagAuthorizationError(f"RAG 授权计算失败: {exc}") from exc

    # ---- 读 ----

    def can_search_kb(self, kb_id: str) -> bool:
        return kb_id in self.kb_scope

    def can_read_row(self, row: dict | None) -> bool:
        """registry/chunk 行 → 文档级可读裁决（KB 范围 + permission_scope）。"""
        if not row:
            return False
        kb_id = str(row.get("kb_id") or "")
        if kb_id not in self.kb_scope:
            return False
        if self.is_admin:
            return True
        from backend.rag.permissions import is_accessible

        return is_accessible(
            {"permission_scope": row.get("permission_scope") or "general"},
            self.user_permissions,
        )

    # ---- 写 ----

    def can_upload_to(self, kb_id: str, department: str) -> tuple[bool, str]:
        """上传目标裁决。返回 (ok, 拒绝原因)；department 是落库归属部门。"""
        from backend.config.knowledge_base import validate_kb_dept

        if not kb_id or not department:
            return False, "缺少目标知识库或部门"
        if not validate_kb_dept(kb_id, department):
            return False, f"知识库 '{kb_id}' 不允许选择部门 '{department}'"
        if kb_id not in self.kb_scope:
            return False, f"没有知识库 '{kb_id}' 的访问权限"
        # 部门归属只认 principal.department；admin 可代任意部门（仍受组合校验）
        if not self.is_admin and department != (self.principal.department or ""):
            return False, "只能上传到本部门目录"
        return True, ""

    def can_manage_row(self, row: dict | None) -> tuple[bool, str]:
        """文档管理（审核/重索引/删除）裁决 = 对该文档归属的上传权。"""
        if not row:
            return False, "文档不存在"
        return self.can_upload_to(
            str(row.get("kb_id") or ""), str(row.get("department") or "")
        )

    # ---- SQL 侧辅助 ----

    def sql_visible_scopes(self, existing_scopes: list[str]) -> list[str]:
        """registry 中实际存在的 permission_scope 值 → 当前主体可读子集。

        用于把文档级权限裁决下推到 SQL（列表 total 与行集同口径）：
        返回值配合 `permission_scope = ANY(%s)` 使用；空串/general 恒可读。
        """
        from backend.rag.permissions import is_accessible

        visible: list[str] = []
        for raw in existing_scopes:
            scope = (raw or "").strip() or "general"
            if self.is_admin or is_accessible(
                {"permission_scope": scope}, self.user_permissions
            ):
                visible.append(scope)
        return visible
