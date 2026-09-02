"""F2 加固回归测试：get_by_doc_id 的行选择优先级 + 重复 active 行检测。

背景（2026-09-02 线上事故复盘）：
  历史重索引 bug 曾为同一 doc_id 注册多条路径不同、归属不一的行。
  get_by_doc_id 原实现 fetchone() 无排序，命中哪行取决于 rowid 顺序，
  导致 reindex 路由读到损坏行（policy_general/general 副本）后：
    1. 把副本文件再次索引并注册到默认归属目录；
    2. mark_deleted_by_doc_id 连带软删健康行。
  加固后：
    - get_by_doc_id 固定 active 优先、updated_at 新者优先；
    - count_active_by_doc_id > 1 时调用方（reindex 路由）应拒绝操作。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from backend.rag.indexing.doc_registry import DocumentRegistry


def _make_registry() -> tuple[DocumentRegistry, str]:
    tmpdir = tempfile.mkdtemp(prefix="reg_f2_")
    db_path = os.path.join(tmpdir, "doc_registry.db")
    return DocumentRegistry(db_path), db_path


def _register(reg, file_path, doc_id, kb_id, department):
    reg.register(
        file_path=file_path,
        doc_id=doc_id,
        file_hash="hash-" + os.path.basename(file_path),
        kb_id=kb_id,
        chunk_ids=["c1"],
        doc_db_id="db-" + doc_id,
        metadata={"department": department, "doc_type": "general"},
    )


def test_get_by_doc_id_prefers_active_row():
    """deleted 行 rowid 更小（先插入）时，仍必须返回 active 行。"""
    reg, _ = _make_registry()
    doc_id = "abc1234567"
    # 先注册"损坏"行（模拟历史 bug 产物），再手动置为 deleted
    _register(reg, r"D:\docs\policy_general\general\dup.md", doc_id,
              "policy_general", "general")
    with reg._lock, reg._conn() as conn:
        conn.execute(
            "UPDATE doc_registry SET status = 'deleted' WHERE doc_id = ?", (doc_id,))
    # 后注册健康行（active，rowid 更大）
    _register(reg, r"D:\docs\biz_order\ops\dup.md", doc_id, "biz_order", "ops")

    row = reg.get_by_doc_id(doc_id)
    assert row is not None
    assert row["kb_id"] == "biz_order", "active 行必须优先于 deleted 行"
    assert row["department"] == "ops"
    assert reg.count_active_by_doc_id(doc_id) == 1


def test_count_active_by_doc_id_detects_duplicates():
    """两条 active 重复行（历史损坏形态）必须被计数暴露，供路由拒绝。"""
    reg, _ = _make_registry()
    doc_id = "def9876543"
    _register(reg, r"D:\docs\rag_test_kb\general\dup.md", doc_id,
              "rag_test_kb", "general")
    _register(reg, r"D:\docs\policy_general\general\dup.md", doc_id,
              "policy_general", "general")

    assert reg.count_active_by_doc_id(doc_id) == 2
    # get_by_doc_id 此时返回哪行不做承诺（数据本身已损坏），
    # 但必须返回"一行"而不是抛错——调用方先查 count 再决策。
    row = reg.get_by_doc_id(doc_id)
    assert row is not None and row["doc_id"] == doc_id


def test_get_by_doc_id_single_row_unchanged():
    """正常单行场景行为不变。"""
    reg, _ = _make_registry()
    _register(reg, r"D:\docs\policy_general\finance\budget.md", "aaa1112223",
              "policy_general", "finance")
    row = reg.get_by_doc_id("aaa1112223")
    assert row["kb_id"] == "policy_general"
    assert row["department"] == "finance"
    assert reg.count_active_by_doc_id("aaa1112223") == 1
    assert reg.get_by_doc_id("not-exist") is None
    assert reg.count_active_by_doc_id("not-exist") == 0
