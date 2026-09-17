"""文档级权限裁决（§4 权限范围消费方，2026-09-17 R3）。

设计原则与 knowledge_base.authorized_kbs（KB 级 ABAC）同构：
  - 授权是确定性计算（本模块），路由/LLM 只能在授权集合内挑选；
  - 主体属性在入口解析一次，下游只读；
  - fail-safe：身份未声明（user_permissions=None）时，受限文档一律拒绝，
    'general' 文档不受影响（存量语料无 permission_scope 字段 → 全部开放，
    旧行为完全保留）。

语义约定（与 rag_100_docs 标注一致，2026-09-17 拍板）：
  - 文档侧：registry/chunk metadata 的 permission_scope = **访问所需权限**，
    'general' 表示开放；受限值如 'finance_restricted'/'hr_confidential'。
  - 请求侧：user_permissions = 请求者**持有**的权限集合（不含 general——
    general 对所有主体隐式开放）。
  - 裁决：所需集合去掉 general 后 ⊆ 持有集合 → 可见，否则拒绝。
"""
from __future__ import annotations

from typing import Any, Iterable

GENERAL_PERMISSION = "general"


def normalize_permissions(value: Any) -> set[str]:
    """permission_scope 字段值 → 权限集合。

    兼容 'general' / 'a,b' / ['a','b'] / None / 空。结果不含 general
    语义上的特殊性（general 会被调用方剔除）。
    """
    if not value:
        return set()
    if isinstance(value, str):
        return {p.strip() for p in value.split(",") if p.strip()}
    if isinstance(value, Iterable):
        out: set[str] = set()
        for p in value:
            if isinstance(p, str) and p.strip():
                out.add(p.strip())
        return out
    return {str(value)}


def required_permissions(metadata: dict | None) -> set[str]:
    """chunk/doc metadata → 访问所需权限集合（缺省 = general 开放）。"""
    if not metadata:
        return set()
    perms = normalize_permissions(metadata.get("permission_scope"))
    return perms - {GENERAL_PERMISSION}


def is_accessible(metadata: dict | None, user_permissions: Iterable[str] | None) -> bool:
    """单条 chunk/doc metadata 的可见性裁决。

    user_permissions=None（身份未声明）→ fail-safe 拒绝受限文档；
    general 文档恒可见。
    """
    req = required_permissions(metadata)
    if not req:
        return True
    if user_permissions is None:
        return False
    return req <= set(user_permissions)


def partition_by_permission(
    metas: list[dict], user_permissions: Iterable[str] | None
) -> tuple[list[int], list[int]]:
    """按下标把 metadata 列表分为 (可见, 越权) 两组，保持原有顺序。"""
    allowed: list[int] = []
    denied: list[int] = []
    for i, m in enumerate(metas):
        (allowed if is_accessible(m, user_permissions) else denied).append(i)
    return allowed, denied
