"""test_indexer_department_derivation.py — 批量索引时 department 必须按路径派生。

回归背景：IncrementalIndexer 是单实例跨多 KB/多部门构建的，self.department 只是
构造默认值（RAGPipeline 不传，即 "general"）。此前 doc_meta / chunk metadata /
chunk_store / registry / _derive_doc_id 五处都直接用 self.department，导致冷启动
全量重建后 policy_general 下 hr、finance 文档全部退化成 general 部门：
  - 部门隔离过滤失效（DEPT-001~005 评测用例）
  - md5(kb|dept|basename) 算出错误 doc_id
    （leave_policy_hr.md 应为 be99388a67，实际算成 52076caf73）
kb_id 早先已修成派生值（见 _index_file_inner 里 doc_meta 的注释），department 被漏掉。
"""
from __future__ import annotations

import hashlib

import pytest

from backend.rag.indexing.indexer import IncrementalIndexer


def _make_indexer(docs_dir, department: str = "general") -> IncrementalIndexer:
    """构造仅用于派生逻辑测试的 indexer。

    vectordb/doc_db/embedding 传 None：_derive_department 与 _derive_doc_id 不碰它们。
    registry 传 None：_derive_doc_id 里 get_by_path 抛 AttributeError 已被捕获，
    正好走"无 active 记录 → 按路径派生"分支。
    """
    return IncrementalIndexer(
        docs_dir=str(docs_dir),
        vectordb=None,
        doc_db=None,
        embedding=None,
        registry=None,
        kb_id="default",
        department=department,
    )


@pytest.fixture
def docs_tree(tmp_path):
    """{kb}/{dept}/[{sub}/]{file} 三级布局，覆盖多部门与子目录同名文件。"""
    for rel in (
        "policy_general/hr/leave_policy_hr.md",
        "policy_general/finance/budget_fin.md",
        "rag_test_kb/general/returns.md",
        "rag_test_kb/general/sub/README.md",
        "rag_test_kb/general/README.md",
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    return tmp_path


def test_derive_department_uses_path_not_instance_default(docs_tree):
    """department 取路径第二级子目录，而非构造时的 self.department。"""
    idx = _make_indexer(docs_tree, department="general")
    assert idx.department == "general"  # 实例默认值确实是 general

    hr = docs_tree / "policy_general" / "hr" / "leave_policy_hr.md"
    fin = docs_tree / "policy_general" / "finance" / "budget_fin.md"
    assert idx._derive_department(str(hr)) == "hr"
    assert idx._derive_department(str(fin)) == "finance"


def test_derive_doc_id_uses_path_department(docs_tree):
    """doc_id 命名空间必须含路径派生的 department。

    这是本 bug 的核心断言：用 self.department("general") 会算出完全不同的 ID。
    """
    idx = _make_indexer(docs_tree, department="general")
    hr = docs_tree / "policy_general" / "hr" / "leave_policy_hr.md"

    got = idx._derive_doc_id(str(hr), file_hash="h", kb_id="policy_general")
    want = hashlib.md5(
        b"policy_general|hr|leave_policy_hr.md"
    ).hexdigest()[:10]
    wrong = hashlib.md5(
        b"policy_general|general|leave_policy_hr.md"
    ).hexdigest()[:10]

    assert got == want
    assert got != wrong


def test_same_basename_in_different_departments_get_distinct_ids(docs_tree):
    """不同部门的同名文件必须得到不同 doc_id（命名空间隔离）。"""
    idx = _make_indexer(docs_tree, department="general")
    hr = docs_tree / "policy_general" / "hr" / "leave_policy_hr.md"
    fin = docs_tree / "policy_general" / "finance" / "budget_fin.md"
    assert idx._derive_department(str(hr)) != idx._derive_department(str(fin))


def test_subpath_same_basename_does_not_collide(docs_tree):
    """子目录下的同名文件与平铺同名文件不得撞 doc_id。

    线上事故：写作规范反例/README.md 每次重索引都撞回顶层 README 的 doc_id，
    产生重复 active 行 + 向量混淆。
    """
    idx = _make_indexer(docs_tree, department="general")
    top = docs_tree / "rag_test_kb" / "general" / "README.md"
    nested = docs_tree / "rag_test_kb" / "general" / "sub" / "README.md"

    id_top = idx._derive_doc_id(str(top), file_hash="h", kb_id="rag_test_kb")
    id_nested = idx._derive_doc_id(str(nested), file_hash="h", kb_id="rag_test_kb")
    assert id_top != id_nested
