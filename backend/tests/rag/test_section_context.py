"""2.3 C3 上下文携带 — Fixed/Recursive 的 section_title 注入。

契约：
- _iter_leaves_with_section：leaf 携带最近祖先 section 标题
- _merge_small_with_section：贪心合并 + 来源标题去重；跨 section 合并
  产出多标题列表（调用方打 section_mixed）
- Fixed/Recursive chunk：section_title 非空（有结构时）、section_mixed 标记
- chunk_id 仍为内容派生（anchor="leaf"），与标题无关
"""
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    FixedSizeChunkStrategy,
    RecursiveChunkStrategy,
    _iter_leaves_with_section,
    _merge_small_with_section,
)


def _para(text):
    return DocumentNode(type="paragraph", text=text)


def _sect(text, children):
    return DocumentNode(type="section", text=text, level=1, children=children)


class TestIterLeavesWithSection:

    def test_leaf_carries_ancestor_title(self):
        ast = DocumentAST(root=DocumentNode(type="section", text="", children=[
            _sect("一、总则", [_para("总则内容。")]),
            _para("无归属内容。"),
        ]))
        items = list(_iter_leaves_with_section(ast))
        assert items[0][1] == "一、总则"
        assert items[1][1] == ""

    def test_nested_section_nearest_title(self):
        ast = DocumentAST(root=DocumentNode(type="section", text="", children=[
            _sect("一、外层", [
                _sect("1.1 内层", [_para("深层内容。")]),
            ]),
        ]))
        items = list(_iter_leaves_with_section(ast))
        assert items[0][1] == "1.1 内层"  # 最近祖先优先


class TestMergeSmallWithSection:

    def test_merge_collects_titles_in_order(self):
        items = [("短甲。", "一、甲节"), ("短乙。", "一、甲节"), ("短丙。", "二、乙节")]
        merged = _merge_small_with_section(items, 1000)
        assert len(merged) == 1
        text, titles = merged[0]
        assert "短甲" in text and "短丙" in text
        assert titles == ["一、甲节", "二、乙节"]

    def test_oversized_item_stays_alone(self):
        big = "字" * 3000
        items = [(big, "一、大段"), ("短。", "二、尾")]
        merged = _merge_small_with_section(items, 500)
        assert len(merged) == 2
        assert merged[0][0] == big and merged[0][1] == ["一、大段"]

    def test_duplicate_titles_deduped(self):
        items = [("甲1。", "一、甲节"), ("甲2。", "一、甲节")]
        merged = _merge_small_with_section(items, 1000)
        assert merged[0][1] == ["一、甲节"]


class TestStrategyIntegration:

    def test_fixed_injects_section_title(self):
        ast = DocumentAST(root=DocumentNode(type="section", text="", children=[
            _sect("一、报销标准", [_para("报销上限每月两千元。")]),
        ]))
        chunks = FixedSizeChunkStrategy().split(ast, "/x.md")
        assert chunks[0].metadata["section_title"] == "一、报销标准"
        assert chunks[0].metadata["section_path"] == ["一、报销标准"]
        assert "section_mixed" not in chunks[0].metadata

    def test_recursive_mixed_sections_flagged(self):
        ast = DocumentAST(root=DocumentNode(type="section", text="", children=[
            _sect("一、甲节", [_para("甲节短句。")]),
            _sect("二、乙节", [_para("乙节短句。")]),
        ]))
        chunks = RecursiveChunkStrategy().split(ast, "/x.md")
        # 两个小节的小叶子合并进同一 chunk → section_mixed
        mixed = [c for c in chunks if c.metadata.get("section_mixed") == "true"]
        assert mixed, "跨 section 合并段应打 section_mixed 标记"
        assert mixed[0].metadata["section_title"] == "一、甲节"  # 首个来源

    def test_chunk_id_independent_of_section_title(self):
        """chunk_id 仍为内容派生：同文本在不同 section 下 id 相同。"""
        a = DocumentAST(root=DocumentNode(type="section", text="", children=[
            _sect("一、甲节", [_para("相同内容。")]),
        ]))
        b = DocumentAST(root=DocumentNode(type="section", text="", children=[
            _sect("九、乙节", [_para("相同内容。")]),
        ]))
        ca = FixedSizeChunkStrategy().split(a, "/x.md")
        cb = FixedSizeChunkStrategy().split(b, "/x.md")
        assert ca[0].metadata["chunk_id"] == cb[0].metadata["chunk_id"]
