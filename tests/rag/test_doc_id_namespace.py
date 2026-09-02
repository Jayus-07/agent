"""doc_id 命名空间化回归测试。

核心断言:
  - 同一 (kb_id, department, basename) → 稳定且唯一的 doc_id
  - 不同 kb_id / 不同 department 下同名文件 → 不同 doc_id（消除跨目录碰撞）
  - loader 与 indexer 使用同一派生逻辑（三处协议一致）
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from backend.rag.indexing.doc_id import (
    derive_doc_id,
    derive_doc_id_from_path,
    parse_kb_dept_from_path,
    parse_kb_dept_subpath_from_path,
)


def test_deterministic():
    a = derive_doc_id(kb_id="biz_inventory", department="warehouse", basename="handbook.pdf")
    b = derive_doc_id(kb_id="biz_inventory", department="warehouse", basename="handbook.pdf")
    assert a == b
    assert len(a) == 10


def test_cross_kb_collision_eliminated():
    # 旧协议 md5(basename)[:10] 下，这两个会共享同一 doc_id；
    # 新协议必须不同。
    kb1 = derive_doc_id(kb_id="biz_inventory", department="warehouse", basename="handbook.pdf")
    kb2 = derive_doc_id(kb_id="biz_order", department="order_dept", basename="handbook.pdf")
    assert kb1 != kb2


def test_cross_department_collision_eliminated():
    # 通用公共知识库内，hr 与 finance 同名文件也必须隔离
    hr = derive_doc_id(kb_id="policy_general", department="hr", basename="handbook.pdf")
    fin = derive_doc_id(kb_id="policy_general", department="finance", basename="handbook.pdf")
    assert hr != fin


def test_same_namespace_same_file_is_same_doc():
    # 同一 (kb, dept) 内同名文件 = 同一物理文件 = 同一 doc（正确，无伪碰撞）
    x = derive_doc_id(kb_id="policy_general", department="hr", basename="leave.md")
    y = derive_doc_id(kb_id="policy_general", department="hr", basename="leave.md")
    assert x == y


def test_parse_kb_dept_from_path():
    docs_dir = "/data/docs"
    kb, dept = parse_kb_dept_from_path("/data/docs/biz_inventory/warehouse/a.pdf", docs_dir)
    assert kb == "biz_inventory"
    assert dept == "warehouse"
    # 缺部门子目录 → 回退 general
    kb2, dept2 = parse_kb_dept_from_path("/data/docs/biz_inventory/a.pdf", docs_dir)
    assert kb2 == "biz_inventory"
    assert dept2 == "general"


def test_subdir_collision_eliminated():
    # 平铺 rag_test_kb/general/README.md 与 rag_test_kb/general/写作规范反例/README.md
    # 在含子目录协议下必须不同（本次线上事故的根因）。
    flat = derive_doc_id(kb_id="rag_test_kb", department="general", basename="README.md")
    sub = derive_doc_id(
        kb_id="rag_test_kb",
        department="general",
        basename="README.md",
        subpath="写作规范反例",
    )
    assert flat != sub
    # 不同子目录下同名文件也应隔离
    sub2 = derive_doc_id(
        kb_id="rag_test_kb",
        department="general",
        basename="README.md",
        subpath="写作规范反例/附",
    )
    assert sub != sub2
    # 同一 (kb, dept, subpath) 内同名 = 同一物理文件 = 同一 doc
    same = derive_doc_id(
        kb_id="rag_test_kb",
        department="general",
        basename="README.md",
        subpath="写作规范反例",
    )
    assert sub == same


def test_subpath_backward_compat():
    # 空 subpath 必须与历史协议 md5(f"{kb}|{dept}|{basename}") 逐字一致，
    # 否则存量平铺文件的 doc_id 全部漂移、历史链接失效。
    raw = "a|b|c.md"
    assert derive_doc_id(kb_id="a", department="b", basename="c.md") == hashlib.md5(
        raw.encode("utf-8")
    ).hexdigest()[:10]
    # 显式空 subpath 与缺省一致
    assert derive_doc_id(
        kb_id="a", department="b", basename="c.md", subpath=""
    ) == derive_doc_id(kb_id="a", department="b", basename="c.md")
    # 纯斜杠/反斜杠噪声经归一后也应回退为平铺协议
    assert derive_doc_id(
        kb_id="a", department="b", basename="c.md", subpath="\\"
    ) == derive_doc_id(kb_id="a", department="b", basename="c.md")
    assert derive_doc_id(
        kb_id="a", department="b", basename="c.md", subpath="//"
    ) == derive_doc_id(kb_id="a", department="b", basename="c.md")


def test_parse_kb_dept_subpath_from_path():
    docs_dir = "/data/docs"
    # 四层: {kb}/{dept}/{subdirs...}/{filename}
    kb, dept, sub = parse_kb_dept_subpath_from_path(
        "/data/docs/rag_test_kb/general/写作规范反例/README.md", docs_dir
    )
    assert (kb, dept, sub) == ("rag_test_kb", "general", "写作规范反例")
    # 多层子目录
    _, _, sub2 = parse_kb_dept_subpath_from_path(
        "/data/docs/rag_test_kb/general/写作规范反例/附/tips.md", docs_dir
    )
    assert sub2 == "写作规范反例/附"
    # 三层（平铺）: {kb}/{dept}/{filename}
    kb3, dept3, sub3 = parse_kb_dept_subpath_from_path(
        "/data/docs/rag_test_kb/general/README.md", docs_dir
    )
    assert (kb3, dept3, sub3) == ("rag_test_kb", "general", "")
    # 两层: {kb}/{filename} → dept 回退 general
    kb4, dept4, sub4 = parse_kb_dept_subpath_from_path(
        "/data/docs/rag_test_kb/README.md", docs_dir
    )
    assert (kb4, dept4, sub4) == ("rag_test_kb", "general", "")
    # 一层 / 路径越界 / 异常 → 全部回退 default/general
    assert parse_kb_dept_subpath_from_path("/README.md", docs_dir) == (
        "default",
        "general",
        "",
    )
    assert parse_kb_dept_subpath_from_path("/elsewhere/x/README.md", "/data/docs") == (
        "default",
        "general",
        "",
    )


def test_loader_and_indexer_consistent():
    # loader 用 derive_doc_id_from_path，indexer 用 derive_doc_id(kb,dept,basename,subpath)
    # 二者对「同一存储路径」必须产出相同 doc_id —— 含子目录场景。
    docs_dir = "/data/docs"
    fpath = os.path.join(docs_dir, "policy_general", "hr", "leave_policy_hr.md")
    kb, dept = parse_kb_dept_from_path(fpath, docs_dir)
    from_loader = derive_doc_id_from_path(fpath, docs_dir)
    from_indexer = derive_doc_id(kb_id=kb, department=dept, basename=os.path.basename(fpath))
    assert from_loader == from_indexer

    # 子目录文件: 路径派生的 loader id 必须 = 显式传 subpath 的 indexer id
    fpath_sub = os.path.join(docs_dir, "policy_general", "hr", "子目录", "leave_policy_hr.md")
    kb_s, dept_s, sub_s = parse_kb_dept_subpath_from_path(fpath_sub, docs_dir)
    from_loader_sub = derive_doc_id_from_path(fpath_sub, docs_dir)
    from_indexer_sub = derive_doc_id(
        kb_id=kb_s,
        department=dept_s,
        basename=os.path.basename(fpath_sub),
        subpath=sub_s,
    )
    assert from_loader_sub == from_indexer_sub
    # 且与平铺同名文件不同（协议统一隔离）
    assert from_loader_sub != from_loader


def test_eval_protocol_matches_indexer():
    # 评测集 relevant_docs 必须用与 indexer 相同的命名空间协议，
    # 否则检索召回的 doc_id 与标注对不上。
    docs_dir = "/data/docs"
    fpath = os.path.join(docs_dir, "policy_general", "hr", "leave_policy_hr.md")
    kb, dept = parse_kb_dept_from_path(fpath, docs_dir)
    expected_in_eval = derive_doc_id(
        kb_id=kb, department=dept, basename=os.path.basename(fpath)
    )
    # 与脚本里计算的种子 doc_id 一致（防止协议漂移）
    assert expected_in_eval == derive_doc_id(
        kb_id="policy_general", department="hr", basename="leave_policy_hr.md"
    )
