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
    """P0-01 新契约：带标题文档不再整篇转 FAQ——section 保留，
    Q/A 段落转 qa 节点并归属其所在 section（局部识别）。"""
    md = tmp_path / "faq_with_heading.md"
    md.write_text(
        "# FAQ 章节\n**Q: 怎么退货？**\n**A: 提交申请。**\n",
        encoding="utf-8",
    )
    ast = MarkdownParser().parse(str(md))
    # 结构保留：heading section 不再被 FAQ 识别吞掉
    sections = [
        n for n in ast.root.children
        if n.type == "section" and n.level > 0
    ]
    assert len(sections) == 1
    # Q/A 节点转出（归属 section 下），树中共 2 个
    from backend.rag.preprocessing.ast import walk
    qa_nodes = [
        n for n in walk(ast.root) if n.type in ("qa_question", "qa_answer")
    ]
    assert len(qa_nodes) == 2
    assert qa_nodes[0].type == "qa_question"
    assert qa_nodes[1].type == "qa_answer"


def test_markdown_parser_mixed_faq_keeps_prose(tmp_path):
    """P0-01 核心回归：混合文档（FAQ + 普通章节）不丢正文。

    旧行为：looks_like_qa_doc 命中（min_pairs=1，单组 Q/A 即命中）→
    整篇只产 qa 节点，普通段落/章节全部静默丢弃。新行为：FAQ 局部
    配对，普通结构原样保留。"""
    md = tmp_path / "mixed.md"
    md.write_text(
        "**Q: 怎么退货？**\n**A: 提交申请。**\n\n"
        "# 第一章 总则\n\n这是普通正文段落，必须保留。\n",
        encoding="utf-8",
    )
    ast = MarkdownParser().parse(str(md))
    from backend.rag.preprocessing.ast import walk
    all_nodes = list(walk(ast.root))
    qa_nodes = [n for n in all_nodes if n.type in ("qa_question", "qa_answer")]
    paragraphs = [n for n in all_nodes if n.type == "paragraph"]
    sections = [n for n in all_nodes if n.type == "section" and n.level > 0]
    assert len(qa_nodes) == 2
    assert len(sections) == 1
    assert any("必须保留" in p.text for p in paragraphs)


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


# ─── 真实格式（标签加粗、正文不加粗）───

def test_extract_qa_bold_no_closing_asterisks():
    """真实 FAQ 格式：**Q:** 问题（只有 Q/A 标签加粗，正文不闭合 **）。

    缺陷 C 根因：旧正则要求问题正文也闭合 **（**Q: 问题**），
    无法匹配真实文档的「标签加粗、正文不加粗」写法，导致 01_FAQ.md 产 0 chunk。
    """
    text = "**Q:** 下单后可以修改收货地址吗？\n**A:** 订单在待发货状态前可修改。\n"
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 1
    q, a, ptype = pairs[0]
    assert q == "下单后可以修改收货地址吗？"
    assert a == "订单在待发货状态前可修改。"
    assert ptype == "qa_bold"


def test_extract_qa_bold_multiple_no_closing():
    """真实 FAQ 格式多对。"""
    text = (
        "**Q:** 问题一？\n**A:** 答案一。\n\n"
        "**Q:** 问题二？\n**A:** 答案二。\n"
    )
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 2
    assert pairs[0] == ("问题一？", "答案一。", "qa_bold")
    assert pairs[1] == ("问题二？", "答案二。", "qa_bold")


def test_extract_qa_colon_pattern():
    """Q：/A： 全角冒号格式（DOCX FAQ 常见，无加粗无编号）。"""
    text = (
        "Q：签收后发现商品破损怎么办？\nA：请在签收后 48 小时内拍照留证。\n\n"
        "Q：换货的流程是怎样的？\nA：提交换货申请。\n"
    )
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 2
    assert pairs[0] == ("签收后发现商品破损怎么办？", "请在签收后 48 小时内拍照留证。", "qa_colon")
    assert pairs[1] == ("换货的流程是怎样的？", "提交换货申请。", "qa_colon")


def test_extract_qa_numbered_bold_colon_pattern():
    """`**Q1:问题**` + `A1:答案`（编号+冒号+加粗，真实网页导出 FAQ 形态）。

    回归：02_faq_mixed 实测该形态曾 0 识别，问答对被拆进普通段落。
    """
    text = (
        "**Q1: 为什么我会收到短信？**\n\nA1: 您会收到银行发送的短信。\n\n"
        "**Q2: 为什么必须完成授权？**\n\nA2: 印度储备银行有新法规。\n"
    )
    pairs = extract_qa_pairs(text)
    assert len(pairs) == 2
    assert pairs[0] == ("为什么我会收到短信？", "您会收到银行发送的短信。", "qa_numbered")
    assert pairs[1] == ("为什么必须完成授权？", "印度储备银行有新法规。", "qa_numbered")


def test_markdown_numbered_bold_colon_produces_qa_nodes():
    """Markdown 全链路：`**Q1:问题**` 段落识别为 qa 节点，加粗标记不入正文。"""
    text = (
        "# 帮助中心\n\n## 第一章 收款\n\nApple Pay 快捷结账说明。\n\n"
        "## 第二章 常见问题\n\n"
        "**Q1: 为什么我会收到短信？**\n\nA1: 您会收到银行发送的短信。\n\n"
        "**Q2: 为什么必须完成授权？**\n\nA2: 印度储备银行有新法规。\n"
    )
    from backend.rag.preprocessing.ast import walk
    ast = MarkdownParser().parse_from_text(text) if hasattr(
        MarkdownParser(), "parse_from_text"
    ) else _parse_md_text(text)
    qa_types = [n.type for n in walk(ast.root) if n.type in ("qa_question", "qa_answer")]
    assert len(qa_types) == 4  # 2 问 + 2 答
    for n in walk(ast.root):
        if n.type == "qa_question":
            assert n.text == "为什么我会收到短信？"
            break


def _parse_md_text(text):
    """MarkdownParser 无 parse_from_text 时的临时文件路径。"""
    import tempfile, os
    from backend.rag.preprocessing.parser import parse_file
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8",
    ) as f:
        f.write(text)
        path = f.name
    try:
        return parse_file(path)
    finally:
        os.unlink(path)
