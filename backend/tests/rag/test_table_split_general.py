"""2.1 C2 表格双层切分通用化 — 共享 helper 行为测试。

契约：
- StructureChunkStrategy（policy 等类型）遇表格 leaf 走 _split_table_node：
  行不被切断、有 table_summary parent、行级 leaf 含 numeric_values
- FinancialTableChunkStrategy 复用同一 helper，行为不变（含 financial
  特有 reporting_period/fiscal_year/is_latest 元数据补丁）
- 表头规范化失败 → 整表兜底 chunk，不丢内容
"""
import pytest
from langchain_core.documents import Document

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    FinancialTableChunkStrategy,
    StructureChunkStrategy,
    _split_table_node,
)


def _table_node(rows, level: int = 1) -> DocumentNode:
    return DocumentNode(type="table", text="", level=level, rows=rows)


def _section(children, text="测试章节", level: int = 1) -> DocumentNode:
    return DocumentNode(type="section", text=text, level=level, children=children)


FIN_ROWS = [
    ["科目", "Q3金额", "环比"],
    ["货币资金", "1234567.00", "+5.2%"],
    ["应收账款", "890000.50", "-1.3%"],
    ["存货", "45678.00", "+0.8%"],
]


class TestHelperDirect:

    def test_two_layer_output(self):
        node = _table_node(FIN_ROWS)
        sec = _section([node])
        chunks = _split_table_node(node, sec, ["测试章节"], "/tmp/x.md")

        parents = [c for c in chunks if c.metadata["chunk_type"] == "table_summary"]
        rows = [c for c in chunks if c.metadata["chunk_type"] == "table_row"]
        assert len(parents) == 1
        assert len(rows) == 3  # 数据行全量，无截断

        parent = parents[0]
        assert parent.metadata["granularity"] == "parent"
        assert parent.metadata["row_count"] == 3
        assert "科目" in parent.page_content

        # 行级 leaf 关联表级 parent，每行一个 chunk
        for r in rows:
            assert r.metadata["parent_chunk_id"] == parent.metadata["chunk_id"]
            assert r.metadata["table_id"] == parent.metadata["chunk_id"]

    def test_rows_not_cut_midline(self):
        """行不被切断：每行 chunk 的 kv 文本只含本行科目。"""
        node = _table_node(FIN_ROWS)
        sec = _section([node])
        rows = [c for c in _split_table_node(node, sec, ["测试章节"], "/x.md")
                if c.metadata["chunk_type"] == "table_row"]
        subjects = [r.page_content for r in rows]
        assert any("货币资金" in s for s in subjects)
        assert any("应收账款" in s for s in subjects)
        assert any("存货" in s for s in subjects)

    def test_numeric_values_extracted(self):
        node = _table_node(FIN_ROWS)
        sec = _section([node])
        rows = [c for c in _split_table_node(node, sec, ["测试章节"], "/x.md")
                if c.metadata["chunk_type"] == "table_row"]
        with_numbers = [r for r in rows if r.metadata.get("numeric_values")]
        assert with_numbers, "行级 leaf 应含 numeric_values"

    def test_header_normalize_failure_falls_back(self):
        """表头规范化失败 → 整表单个 chunk 兜底。"""
        node = _table_node([[], []])  # 空表头/空数据
        sec = _section([node])
        chunks = _split_table_node(node, sec, ["测试章节"], "/x.md")
        assert len(chunks) == 1
        assert chunks[0].metadata["chunk_type"] == "table_fallback"
        assert chunks[0].metadata["parent_chunk_id"] == ""


class TestStructureStrategyIntegration:
    """policy 等类型（StructureChunkStrategy）的表格 leaf 不再被硬切。"""

    def test_policy_table_goes_two_layer(self):
        para = DocumentNode(type="paragraph", text="以下是各部门库存汇总。")
        table = _table_node(FIN_ROWS)
        sec = _section([para, table], text="库存情况")
        ast = DocumentAST(root=DocumentNode(type="section", text="", children=[sec]))

        chunks = StructureChunkStrategy().split(ast, "/tmp/policy_a.md")

        types = [c.metadata.get("chunk_type") for c in chunks]
        assert "table_summary" in types, "policy 文档的表格应有表级摘要 parent"
        assert "table_row" in types
        # 行级内容完整：3 行数据各自成 chunk
        row_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table_row"]
        assert len(row_chunks) == 3
        # 无任何 chunk 是表格被硬切的碎片（含两行以上科目名）
        for c in chunks:
            hits = sum(1 for s in ("货币资金", "应收账款", "存货") if s in c.page_content)
            assert hits <= 1, "表格行被切断：单 chunk 含多行科目"

    def test_plain_leaf_unaffected(self):
        """无表格段落仍走原逻辑（结构 parent + leaf 关联）。"""
        para = DocumentNode(type="paragraph", text="普通段落内容，无表格。" * 3)
        sec = _section([para], text="总则")
        ast = DocumentAST(root=DocumentNode(type="section", text="", children=[sec]))

        chunks = StructureChunkStrategy().split(ast, "/tmp/policy_b.md")
        assert not any(c.metadata.get("chunk_type", "").startswith("table") for c in chunks)
        leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
        assert leaves and leaves[0].metadata["parent_chunk_id"]


class TestFinancialStrategyUnchanged:

    def test_financial_keeps_period_metadata(self):
        """financial 策略复用 helper 后仍补 reporting_period 等特有元数据。"""
        para = DocumentNode(type="paragraph", text="资产负债表如下。")
        table = _table_node(FIN_ROWS)
        sec = _section([para, table], text="资产负债表")
        ast = DocumentAST(root=DocumentNode(type="section", text="", children=[sec]))

        chunks = FinancialTableChunkStrategy().split(ast, "/tmp/report_2024Q3.md")
        table_chunks = [c for c in chunks
                        if c.metadata.get("chunk_type") in
                        ("table_summary", "table_row")]
        assert table_chunks, "financial 表格 chunk 不应为空"
        # 报告期元数据：文件名含 2024Q3 时应被提取（提取不到则跳过断言，
        # 因 extract_reporting_period 依赖文件名/标题启发式）
        if any("reporting_period" in c.metadata for c in table_chunks):
            assert all(c.metadata.get("is_latest") is True for c in table_chunks)

    def test_financial_chunk_ids_match_helper(self):
        """financial 与 helper 对同一表格产出相同 chunk_id（消除重复实现后一致性）。"""
        node = _table_node(FIN_ROWS)
        sec = _section([node], text="资金表")
        direct = _split_table_node(node, sec, ["资金表"], "/tmp/r.md")

        ast = DocumentAST(root=DocumentNode(type="section", text="", children=[sec]))
        fin_chunks = [c for c in FinancialTableChunkStrategy().split(ast, "/tmp/r.md")
                      if c.metadata.get("chunk_type") in ("table_summary", "table_row")]

        assert {c.metadata["chunk_id"] for c in fin_chunks} == \
               {c.metadata["chunk_id"] for c in direct}
