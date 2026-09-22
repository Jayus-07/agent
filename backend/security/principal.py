"""security/principal.py — 统一主体身份（P1，2026-09-23 授权生产收口）

「谁是当前用户、属于哪个租户/部门、有什么角色、是不是员工」从此只有
一个权威答案：本模块的 Principal。此前 subject_type 推导散落三处且
口径不一（routes 内联 authenticated→employee、tools/rag.py 内联
部门→employee + failsafe），2026-09-22 c4b865b 修正过「已登录无部门
被误判 customer」后仍是复制逻辑；本模块把语义收口为单一推导规则，
HTTP 通道（identity.Identity → resolve_principal）与图/Tool 通道
（contextvars → resolve_tool_principal）共用同一规则函数。

subject_type 规则（department 是组织属性，永远不参与认证类型判定）：
  - 已认证企业用户            → employee（含 department 为空——组织资料
                                缺失不改变认证类型，只收窄授权范围）
  - 未认证（guest/anonymous） → customer（对客 fail-safe，宁严勿漏：
                                仅 audience=customer 的 cs_* 库可见；
                                authenticated=False 保留匿名标记）
  - failsafe 关闭的未声明主体 → ""（授权未启用，旧行为，可回滚开关）
  - service                   → 预留类型：服务凭据通道（X-Internal-Token）
                                本阶段不进入 RAG 主体判定，仅作类型占位
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

SUBJECT_EMPLOYEE = "employee"
SUBJECT_CUSTOMER = "customer"
SUBJECT_SERVICE = "service"
SUBJECT_ANONYMOUS = "anonymous"

# 「默认/匿名」占位用户不算已认证（与 tools/rag.py c4b865b 语义一致）。
_UNAUTHENTICATED_USER_IDS = frozenset({"", "default", "anonymous"})


@dataclass(frozen=True)
class Principal:
    """一次请求解析出的可信主体身份。

    只能来自网关验签后的身份头（HTTP 通道）或入口绑定的请求上下文
    （Tool 通道）；请求体里的 user_id/department/roles/tenant 永远
    不能参与构造（客户端字段只能做业务过滤条件，不能做安全身份）。
    """

    user_id: str = ""
    user_name: str = ""
    tenant_id: str = ""
    department: str = ""
    roles: tuple[str, ...] = ()
    # 文档级权限集合；None = 未声明（受限文档 fail-safe，语义同 permissions claim）
    permissions: tuple[str, ...] | None = None
    subject_type: str = SUBJECT_CUSTOMER
    authenticated: bool = False
    auth_type: str = ""   # jwt | api-key | guest | body（legacy）
    source: str = ""      # 调试可读：header|body|default|guest|tool-context


def _failsafe_customer_enabled() -> bool:
    """RAG_TOOL_FAILSAFE_CUSTOMER：未认证主体是否按 customer 收敛。

    默认 true（宁严勿漏）；false 回滚为未声明主体（授权未启用旧行为）。
    与 tools/rag.py 原实现同名同义，调用时读 env 便于不重启回滚。
    """
    return os.getenv("RAG_TOOL_FAILSAFE_CUSTOMER", "true").strip().lower() == "true"


def derive_subject_type(*, authenticated: bool,
                        failsafe_customer: bool = True) -> str:
    """subject_type 唯一推导规则（HTTP 与 Tool 通道共用）。

    已认证 → employee：department 只是组织属性，缺失不降级认证类型
    （c4b865b 修正的核心语义，此处成为唯一权威）。
    未认证 → customer fail-safe（failsafe_customer=False 时返回 ""
    表示未声明主体，授权未启用）。
    """
    if authenticated:
        return SUBJECT_EMPLOYEE
    if failsafe_customer:
        return SUBJECT_CUSTOMER
    return ""


def resolve_tool_principal(*, user_id: Any, department: Any = "",
                           permissions: tuple[str, ...] | None = None,
                           tenant_id: Any = "",
                           failsafe_customer: bool | None = None) -> Principal:
    """图/Tool 通道主体解析（contextvars → Principal）。

    c4b865b 在 tools/rag.py 的推导语义原样迁移：
      - 带部门 → employee（按 owner_depts 矩阵授权）
      - 已登录未声明部门 → employee + 空部门（只见 "all" 库）
      - 未登录 → customer fail-safe（RAG_TOOL_FAILSAFE_CUSTOMER=false
        回滚为未声明主体）
    """
    uid = str(user_id or "").strip()
    authenticated = uid not in _UNAUTHENTICATED_USER_IDS
    if failsafe_customer is None:
        failsafe_customer = _failsafe_customer_enabled()
    return Principal(
        user_id=uid,
        department=str(department or "").strip(),
        tenant_id=str(tenant_id or "").strip(),
        permissions=None if permissions is None else tuple(permissions),
        subject_type=derive_subject_type(
            authenticated=authenticated, failsafe_customer=failsafe_customer),
        authenticated=authenticated,
        auth_type="jwt" if authenticated else "guest",
        source="tool-context",
    )
