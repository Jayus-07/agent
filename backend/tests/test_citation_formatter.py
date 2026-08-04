"""PR-1.3 — CitationFormatter 单测。

覆盖：
- strip_think：完整 think 块 / 未闭合 think / 无 think
- verify_support：空 docs / 高分 doc / 低分 doc
- format_references：空 / 有引用 [1][2] / 兜底（无引用）
- extract_sources：同上 + 字段完整性
"""
from types import SimpleNamespace

import pytest

from backend.rag.citation import CitationFormatter


# ==========================================================
# 工具：构造 fake LangChain Document
# ==========================================================

def _doc(idx: int, fname: str, doc_type: str = "", rerank_score: float = 0.5):
    """构造一个 .metadata 类似 LangChain Document 的对象。"""
    return SimpleNamespace(
        page_content="...",
        metadata={
            "index": idx,
            "source_file": fname,
            "doc_type": doc_type,
            "rerank_score": rerank_score,
        },
    )


@pytest.fixture
def f():
    return CitationFormatter()


# ==========================================================
# 1. strip_think
# ==========================================================

class TestStripThink:
    def test_complete_think_block(self, f):
        text = "<think>推理过程</think>正式回答"
        assert f.strip_think(text) == "正式回答"

    def test_unclosed_think_drops_remainder(self, f):
        """未闭合 <think> 标签 → 丢弃后续所有内容（原 chain.py 行为，PR-1.3 不改）。

        行为契约来自原 _strip_think_blocks（chain.py:757-758）：
        若有 <think> 但没有 </think>，整个 <think> 之后的内容全删。
        这是原代码事实行为，PR-1.3 范围仅"抽类"不改语义。
        如需"保留后续"应单独开 PR 改 strip_think 实现。
        """
        text = "<think>开始推理但未结束\n正式回答继续"
        result = f.strip_think(text)
        assert result == ""  # 后续被丢弃
        assert "<think>" not in result
        assert "正式回答" not in result  # 业务答复也丢了

    def test_no_think_unchanged(self, f):
        text = "正常回答，无 think 块"
        assert f.strip_think(text) == "正常回答，无 think 块"

    def test_multiline_think(self, f):
        text = "<think>\n多行\n推理\n</think>\n答案"
        assert f.strip_think(text) == "答案"

    def test_empty_string(self, f):
        assert f.strip_think("") == ""


# ==========================================================
# 2. verify_support
# ==========================================================

class TestVerifySupport:
    def test_empty_docs_returns_answer(self, f):
        answer, verified = f.verify_support("answer", [])
        assert answer == "answer"
        assert verified == []

    def test_above_threshold_kept(self, f):
        d = _doc(1, "a.txt", rerank_score=0.8)
        answer, verified = f.verify_support("ans", [d])
        assert len(verified) == 1
        assert verified[0].metadata["support_score"] == 0.8

    def test_below_threshold_filtered(self, f):
        d = _doc(1, "a.txt", rerank_score=0.0)  # 默认 threshold=0
        answer, verified = f.verify_support("ans", [d])
        # threshold=0 时 0.0 > 0 是 False，所以过滤掉
        assert verified == []

    def test_writes_support_score(self, f):
        d = _doc(1, "a.txt", rerank_score=0.75)
        _, verified = f.verify_support("ans", [d])
        assert verified[0].metadata["support_score"] == 0.75


# ==========================================================
# 3. format_references
# ==========================================================

class TestFormatReferences:
    def test_empty_docs(self, f):
        assert f.format_references([], "any answer") == ""

    def test_with_citation_numbers(self, f):
        """answer 含 [1] [2] → 仅显示对应 sources。"""
        d1 = _doc(1, "policy.txt", doc_type="policy", rerank_score=0.9)
        d2 = _doc(2, "sop.txt", doc_type="sop", rerank_score=0.8)
        d3 = _doc(3, "unused.txt", doc_type="faq", rerank_score=0.7)
        result = f.format_references([d1, d2, d3], "答案是 [1] 和 [2] 的内容")
        assert "policy.txt" in result
        assert "sop.txt" in result
        assert "unused.txt" not in result

    def test_without_citation_falls_back_to_all(self, f):
        """answer 无 [N] 引用 → 兜底显示所有 docs。"""
        d1 = _doc(1, "a.txt", rerank_score=0.8)
        d2 = _doc(2, "b.txt", rerank_score=0.7)
        result = f.format_references([d1, d2], "答案没有任何引用")
        assert "a.txt" in result
        assert "b.txt" in result

    def test_type_label_chinese(self, f):
        d = _doc(1, "doc.txt", doc_type="listing", rerank_score=0.8)
        result = f.format_references([d], "")
        assert "Listing" in result
        assert "1. **doc.txt**" in result

    def test_no_filename_filtered(self, f):
        """无 source_file 的 doc 跳过。"""
        d = SimpleNamespace(metadata={"index": 1, "doc_type": "sop"})  # 无 source_file
        result = f.format_references([d], "")
        assert result == ""

    def test_score_format(self, f):
        """score 用 .2f 格式。"""
        d = _doc(1, "a.txt", rerank_score=0.876)
        result = f.format_references([d], "")
        assert "相关度: 0.88" in result


# ==========================================================
# 4. extract_sources
# ==========================================================

class TestExtractSources:
    def test_empty_docs(self, f):
        assert f.extract_sources([], "any") == []

    def test_with_citations(self, f):
        d1 = _doc(1, "policy.txt", doc_type="policy", rerank_score=0.85)
        d2 = _doc(2, "sop.txt", doc_type="sop", rerank_score=0.7)
        result = f.extract_sources([d1, d2], "答案是 [1] 和 [2]")
        assert len(result) == 2
        assert result[0]["index"] == 1
        assert result[0]["filename"] == "policy.txt"
        assert result[0]["doc_type"] == "policy"
        assert result[0]["type_label"] == "制度规范"
        assert result[0]["score"] == 0.85
        assert result[1]["index"] == 2
        assert result[1]["type_label"] == "SOP"

    def test_without_citations_falls_back(self, f):
        d1 = _doc(1, "a.txt", rerank_score=0.8)
        d2 = _doc(2, "b.txt", rerank_score=0.7)
        result = f.extract_sources([d1, d2], "无引用 answer")
        assert len(result) == 2

    def test_sorted_by_index(self, f):
        d3 = _doc(3, "c.txt", rerank_score=0.5)
        d1 = _doc(1, "a.txt", rerank_score=0.8)
        d2 = _doc(2, "b.txt", rerank_score=0.7)
        result = f.extract_sources([d3, d1, d2], "[1] [2] [3]")
        assert [s["index"] for s in result] == [1, 2, 3]

    def test_dedup_same_filename(self, f):
        """同 filename 只保留一个（先到先得）。"""
        d1 = _doc(1, "same.txt", rerank_score=0.8)
        d2 = _doc(2, "same.txt", rerank_score=0.7)  # 重复
        result = f.extract_sources([d1, d2], "[1] [2]")
        assert len(result) == 1
        assert result[0]["index"] == 1  # 第一个

    def test_unknown_type_uses_raw(self, f):
        d = _doc(1, "a.txt", doc_type="custom_type")
        result = f.extract_sources([d], "")
        assert result[0]["type_label"] == "custom_type"


# ==========================================================
# 5. 集成
# ==========================================================

class TestIntegration:
    def test_full_pipeline_consistency(self, f):
        """完整链路：verify → format → extract 应一致。"""
        d1 = _doc(1, "a.txt", doc_type="policy", rerank_score=0.9)
        d2 = _doc(2, "b.txt", doc_type="sop", rerank_score=0.8)
        docs = [d1, d2]

        # 1. 验证支撑
        answer, verified = f.verify_support("答案 [1][2]", docs)
        assert len(verified) == 2

        # 2. 提取结构化来源
        sources = f.extract_sources(verified, answer)
        assert len(sources) == 2

        # 3. 格式化 Markdown
        ref_md = f.format_references(verified, answer)
        assert "a.txt" in ref_md
        assert "b.txt" in ref_md
        assert "制度规范" in ref_md
        assert "SOP" in ref_md
