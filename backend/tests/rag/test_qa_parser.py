"""test_qa_parser.py — Q/A 节点识别 + Markdown/TXT 接入。

覆盖：
1. _extract_qa_pairs / looks_like_qa_doc / dominant_pattern 三种工具函数
2. MarkdownParser 识别 FAQ 文档 → 产 qa_question / qa_answer 节点
3. Q/A 节点挂在 root，不嵌套 section
4. TxtParser 识别 FAQ 文档
"""
from backend.rag.preprocessing.parser._qa_patterns import (
    dominant_pattern, extract_qa_pairs, looks_like_qa_doc,
)
from backend.rag.preprocessing.parser.markdown_parser import MarkdownParser
from backend.rag.preprocessing.parser.txt_parser import TxtParser


# ─── extract_qa_pairs 单元测试 ───

def test_extract_qa_bold_pattern():
    text = "**Q: 怎么退货？**\n**A: 提交申请后客服审核。**"
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 1
    q, a, ptype = pairs[0]
    assert q == "怎么退货？"
    assert a == "提交申请后客服审核。"
    assert ptype == "qa_bold"


def test_extract_qa_heading_pattern():
    text = (
        "## 问题\n退货怎么操作？\n\n## 答案\n提交申请。\n\n"
        "## 问题\n运费谁付？\n\n## 答案\n商家承担。\n"
    )
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 2
    assert pairs[0] == ("退货怎么操作？", "提交申请。", "qa_heading")
    assert pairs[1] == ("运费谁付？", "商家承担。", "qa_heading")


def test_extract_qa_numbered_pattern():
    text = "Q1. 怎么退货？\nA1. 提交申请。\nQ2. 运费谁付？\nA2. 商家承担。\n"
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 2
    assert pairs[0][0] == "怎么退货？"
    assert pairs[0][2] == "qa_numbered"


def test_extract_qa_no_match_returns_empty():
    text = "这是一段普通文字，没有任何 Q/A 模式。"
    assert extract_qa_pairs(text) == []


def test_extract_qa_multiple_patterns_dont_conflict():
    """同一文档混用 bold + numbered：两种 pattern 各自产出。"""
    text = (
        "**Q: 怎么退货？**\n**A: 提交申请。**\n\n"
        "Q1. 运费谁付？\nA1. 商家承担。\n"
    )
    pairs = extract_qa_pairs(text)
    ptypes = sorted(p[2] for p in pairs)
    assert ptypes == ["qa_bold", "qa_numbered"]


def test_looks_like_qa_doc_threshold():
    """looks_like_qa_doc: 至少 N 对才算 FAQ。"""
    text_one = "**Q: 单条？**\n**A: 是。**\n普通段落。"
    assert looks_like_qa_doc(text_one, min_pairs=2) is False
    assert looks_like_qa_doc(text_one, min_pairs=1) is True


def test_dominant_pattern():
    """dominant_pattern: 返回出现最多的 pattern_type。"""
    text = (
        "**Q: q1？**\n**A: a1。**\n"
        "**Q: q2？**\n**A: a2。**\n"
        "**Q: q3？**\n**A: a3。**\n"
    )
    assert dominant_pattern(text) == "qa_bold"

    no_qa = "普通段落"
    assert dominant_pattern(no_qa) == ""


# ─── MarkdownParser 集成测试 ───

def test_markdown_parser_qa_doc(tmp_path):
    md = tmp_path / "faq.md"
    md.write_text(
        "**Q: 怎么退货？**\n**A: 提交申请后客服审核。**\n\n"
        "**Q: 运费谁付？**\n**A: 商家承担。**\n",
        encoding="utf-8",
    )
    ast = MarkdownParser().parse(str(md))
    qa_nodes = [
        n for n in ast.root.children if n.type in ("qa_question", "qa_answer")
    ]
    assert len(qa_nodes) == 4  # 2 个问题 + 2 个答案
    assert qa_nodes[0].type == "qa_question"
    assert "怎么退货" in qa_nodes[0].text
    assert qa_nodes[1].type == "qa_answer"


def test_markdown_parser_qa_nodes_attach_to_root_not_section(tmp_path):
    """Q/A 节点直接挂 root，不嵌套在 section 里（避免重复切分）。"""
    md = tmp_path / "faq_with_heading.md"
    md.write_text(
        "# FAQ 章节\n**Q: 怎么退货？**\n**A: 提交申请。**\n",
        encoding="utf-8",
    )
    ast = MarkdownParser().parse(str(md))
    # 整个文档被识别为 FAQ → 没有普通 section，只有 qa_* 节点
    sections = [
        n for n in ast.root.children
        if n.type == "section" and n.level > 0
    ]
    qa_nodes = [
        n for n in ast.root.children if n.type in ("qa_question", "qa_answer")
    ]
    assert len(qa_nodes) == 2
    assert sections == []  # FAQ 文档不识别为章节文档


# ─── TxtParser 集成测试 ───

def test_txt_parser_qa_doc(tmp_path):
    t = tmp_path / "faq.txt"
    t.write_text(
        "Q1. 怎么退货？\nA1. 提交申请。\n\nQ2. 运费谁付？\nA2. 商家承担。\n",
        encoding="utf-8",
    )
    ast = TxtParser().parse(str(t))
    qa_nodes = [
        n for n in ast.root.children if n.type in ("qa_question", "qa_answer")
    ]
    assert len(qa_nodes) == 4
