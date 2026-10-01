"""RAG 授权服务单元测试（2026-10-01 权限旁路收口 A 阶段）。

钉死口径（backend/rag/authz.py 唯一权威）：
  - HR 用户读财务文档（KB 范围 + permission_scope 双层）→ 拒绝；
  - admin 跨部门读/管理全部非 test 库；test 库任何主体不可见；
  - 上传 department 自报越权 → 拒绝（department 只认主体自身，admin 除外）；
  - 授权计算异常 → RagAuthorizationError（fail-closed，绝不放行）；
  - 未认证主体不允许进入授权面。
"""

import pytest

from backend.rag.authz import (
    RagAuthorization,
    RagAuthorizationError,
    is_admin_roles,
    readable_kb_ids,
    retrieval_authorized_kbs,
)
from backend.security.principal import Principal


def _principal(**kw) -> Principal:
    defaults = dict(
        user_id="u-hr-1",
        user_name="hr用户",
        tenant_id="default",
        department="hr",
        roles=("editor",),
        permissions=None,
        subject_type="employee",
        authenticated=True,
        auth_type="jwt",
        source="header",
    )
    defaults.update(kw)
    return Principal(**defaults)


# ── KB 级口径 ──


def test_hr_editor_cannot_read_finance_kb():
    authz = RagAuthorization.build(_principal())
    assert not authz.can_search_kb("policy_finance")
    assert authz.can_search_kb("policy_general")  # owner_depts=all 公共库
    assert authz.can_search_kb("policy_hr")


def test_hr_editor_cannot_read_finance_doc_row():
    authz = RagAuthorization.build(_principal())
    finance_doc = {"kb_id": "policy_finance", "department": "finance",
                   "permission_scope": "general"}
    assert not authz.can_read_row(finance_doc)
    # 猜 doc_id 拿到的行也走同一裁决（KB 范围先行）
    restricted_hr = {"kb_id": "policy_hr", "department": "hr",
                     "permission_scope": "hr_confidential"}
    assert not authz.can_read_row(restricted_hr)


def test_permission_grant_unlocks_restricted_doc():
    authz = RagAuthorization.build(
        _principal(permissions=("hr_confidential",)))
    row = {"kb_id": "policy_hr", "department": "hr",
           "permission_scope": "hr_confidential"}
    assert authz.can_read_row(row)


def test_undeclared_permissions_fail_safe_on_restricted_doc():
    """permissions=None（身份未声明权限）→ 受限文档 fail-safe 拒绝。"""
    authz = RagAuthorization.build(_principal(permissions=None))
    row = {"kb_id": "policy_hr", "department": "hr",
           "permission_scope": "hr_confidential"}
    assert not authz.can_read_row(row)
    general_row = {"kb_id": "policy_hr", "department": "hr",
                   "permission_scope": "general"}
    assert authz.can_read_row(general_row)


def test_admin_cross_department_read_and_manage():
    authz = RagAuthorization.build(_principal(roles=("admin",)))
    assert authz.is_admin
    assert authz.can_search_kb("policy_finance")
    assert authz.can_search_kb("biz_inventory")
    finance_doc = {"kb_id": "policy_finance", "department": "finance",
                   "permission_scope": "finance_restricted"}
    # admin 对文档级 permission_scope 不设限（ROLE_DATA_SCOPE=all 语义延伸）
    assert authz.can_read_row(finance_doc)
    ok, _ = authz.can_manage_row(finance_doc)
    assert ok


def test_test_audience_kb_invisible_to_everyone_including_admin():
    for roles in (("editor",), ("admin",)):
        authz = RagAuthorization.build(_principal(roles=roles))
        assert not authz.can_search_kb("rag_eval_kb")


def test_customer_scope_only_cs_kbs():
    # customer 主体经 readable_kb_ids 单独验证（HTTP 授权面要求已认证，
    # 未认证 visitor 的 customer failsafe 走 Tool 通道，不进本授权面）
    kbs = readable_kb_ids("customer", "")
    assert "cs_faq" in kbs
    assert "policy_hr" not in kbs
    assert "policy_general" not in kbs
    # 未认证主体进入 HTTP 授权面 → 显式拒绝
    with pytest.raises(RagAuthorizationError):
        RagAuthorization.build(
            _principal(user_id="c-1", department="", roles=(),
                       subject_type="customer", authenticated=False))


# ── 上传/管理口径 ──


def test_upload_cannot_self_declare_foreign_department():
    """editor 声明不属于自己的部门/KB 上传 → 拒绝（A 阶段验收）。"""
    authz = RagAuthorization.build(_principal())
    ok, reason = authz.can_upload_to("policy_finance", "finance")
    assert not ok
    # 即使 KB 允许该部门组合，非 admin 也不能落到别的部门目录
    ok, _ = authz.can_upload_to("policy_general", "finance")
    assert not ok
    ok, _ = authz.can_upload_to("policy_hr", "hr")
    assert ok


def test_admin_may_upload_for_any_department():
    authz = RagAuthorization.build(_principal(roles=("admin",)))
    ok, _ = authz.can_upload_to("policy_finance", "finance")
    assert ok


def test_manage_denied_for_foreign_dept_doc():
    authz = RagAuthorization.build(_principal())
    doc = {"kb_id": "policy_general", "department": "finance",
           "permission_scope": "general"}
    ok, _ = authz.can_manage_row(doc)
    assert not ok


# ── fail-closed 与未声明主体 ──


def test_unauthenticated_principal_rejected():
    with pytest.raises(RagAuthorizationError):
        RagAuthorization.build(_principal(authenticated=False, subject_type="employee"))


def test_undeclared_subject_yields_empty_scope_not_none():
    """HTTP 通道的未声明主体按空集拒绝，不得回退「授权未启用」。"""
    authz = RagAuthorization.build(
        _principal(subject_type="", roles=()))
    assert authz.kb_scope == frozenset()
    assert not authz.can_read_row({"kb_id": "policy_general",
                                   "permission_scope": "general"})


def test_authz_failure_is_fail_closed(monkeypatch):
    import backend.rag.authz as authz_mod

    def _boom(*a, **kw):
        raise RuntimeError("矩阵数据库不可用")

    monkeypatch.setattr(authz_mod, "readable_kb_ids", _boom)
    with pytest.raises(RagAuthorizationError):
        RagAuthorization.build(_principal())


# ── 检索链口径（ChunkLevelRetriever 消费点）──


def test_retrieval_authorized_kbs_admin_bypass():
    kbs = retrieval_authorized_kbs("employee", "hr", roles=("admin",))
    assert "policy_finance" in kbs
    assert "rag_eval_kb" not in kbs
    hr_kbs = retrieval_authorized_kbs("employee", "hr", roles=("editor",))
    assert "policy_finance" not in hr_kbs
    assert "policy_general" in hr_kbs


def test_retrieval_authorized_kbs_undeclared_subject_none_passthrough():
    """未声明主体（内部直调）保持 None = 授权未启用旧行为。"""
    assert retrieval_authorized_kbs("", "") is None


def test_readable_kb_ids_admin_parameter():
    assert "policy_finance" in readable_kb_ids("employee", "hr", is_admin=True)
    assert "rag_100_docs" not in readable_kb_ids("employee", "hr", is_admin=True)


def test_is_admin_roles_via_permission_codes():
    assert is_admin_roles(("admin",))
    assert not is_admin_roles(("editor", "viewer"))
    assert not is_admin_roles(())


# ── SQL 辅助与响应脱敏 ──


def test_sql_visible_scopes():
    authz = RagAuthorization.build(_principal())  # 未声明 permissions
    scopes = authz.sql_visible_scopes(["general", "hr_confidential",
                                       "finance_restricted", ""])
    assert scopes == ["general", "general"] or scopes == ["general", ""] or set(scopes) <= {"general", ""}


def test_sql_visible_scopes_with_grants():
    authz = RagAuthorization.build(_principal(permissions=("hr_confidential",)))
    scopes = set(authz.sql_visible_scopes(
        ["general", "hr_confidential", "finance_restricted"]))
    assert scopes == {"general", "hr_confidential"}


def test_sanitize_doc_row_strips_internal_fields():
    from backend.app.api.routes._rag_shared import sanitize_doc_row

    row = {
        "doc_id": "d1", "file_name": "a.pdf", "kb_id": "policy_hr",
        "file_path": "/app/data/docs/policy_hr/hr/a.pdf",
        "chunk_ids": '["x"]', "minhash_sig": "sig", "doc_db_id": "v1",
        "file_hash": "abc",
    }
    out = sanitize_doc_row(row)
    assert "file_path" not in out and "chunk_ids" not in out
    assert "minhash_sig" not in out and "doc_db_id" not in out
    assert out["doc_id"] == "d1" and out["file_hash"] == "abc"
    assert sanitize_doc_row(None) == {}
