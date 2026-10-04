"""原文定位（P0）回归：页码从解析层到来源卡的全链透传。

链路：pdf_parser 叶子节点 page_number（§5.2 可追溯字段）→
chunking.stamp_chunk_pages 回映射 chunk.metadata["pages"]（标量逗号串，
向量库标量化惯例）→ citation 双出口（extract_sources 结构化 /
format_references 文本协议）→ reporter.parse_sources_from_text 反解析
→ done.sources → 前端来源卡「第 X 页 · 章节」。

锁定契约：
- stamp_chunk_pages 双向文本包含回映射（合并型 + 切片型），改写文本宁缺勿错；
- 参考文献行新格式解析 + 旧格式向后兼容（无定位段行不受影响）；
- extract_sources 与文本解析两出口的 pages/section 字段一致。
"""
import types

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import stamp_chunk_pages
from backend.rag.citation import CitationFormatter, _pages_label
from backend.agents.reporter.context_filter import parse_sources_from_text


def _ast_with_pages() -> DocumentAST:
    """三个叶子：p1 / p2 两段（p3），无页码叶子穿插。"""
    root = DocumentNode(type="section", text="", level=0)
    root.children = [
        DocumentNode(type="paragraph", text="第一条 报销范围为交通与住宿费用。",
                     page_number=1, bbox=(0, 0, 0, 0)),
        DocumentNode(type="paragraph", text="第二条 报销时限为行程结束后三十日内提交申请，逾期不予受理，特殊情况需部门负责人审批。",
                     page_number=3, bbox=(0, 0, 0, 0)),
        DocumentNode(type="paragraph", text="本条无页码信息的历史数据兜底，不应贡献页码。",
                     page_number=0),
    ]
    return DocumentAST(root=root, raw_text="".join(n.text for n in root.children))


def test_stamp_chunk_pages_merges_leaf_prefixes():
    """合并型 chunk：包含两个叶子前缀 → pages 收集两者并升序。"""
    ast = _ast_with_pages()
    chunks = [Document(page_content="第一条 报销范围为交通与住宿费用。\n第二条 报销时限为行程结束后三十日内提交申请，逾期不予受理，特殊情况需部门负责人审批。",
                       metadata={})]
    stamp_chunk_pages(chunks, ast)
    assert chunks[0].metadata["pages"] == "1,3"


def test_stamp_chunk_pages_maps_slice_back_to_leaf():
    """切片型 chunk：超长叶子二次切分，切片不含叶子开头 → 核心回源命中。"""
    long_text = "报销凭证必须为增值税发票原件，电子发票需附验真截图，" * 20  # 无页码段之外的长叶
    ast = _ast_with_pages()
    ast.root.children.insert(1, DocumentNode(
        type="paragraph", text=long_text, page_number=2, bbox=(0, 0, 0, 0)))
    piece = Document(page_content=long_text[400:600], metadata={})  # 中段切片
    stamp_chunk_pages([piece], ast)
    assert piece.metadata["pages"] == "2"


def test_stamp_chunk_pages_rewritten_text_gets_no_pages():
    """策略改写文本（双向都不含）→ 不标页码，宁缺勿错。"""
    ast = _ast_with_pages()
    chunk = Document(page_content="问题：报销范围是什么？\n答案：（模板重写内容）", metadata={})
    stamp_chunk_pages([chunk], ast)
    assert "pages" not in chunk.metadata


def test_stamp_chunk_pages_no_page_leaves_is_noop():
    """全部叶子无页码（非 PDF 文档）→ 任何 chunk 都不加 pages。"""
    ast = _ast_with_pages()
    for n in ast.root.children:
        n.page_number = 0
    chunk = Document(page_content="第一条 报销范围为交通与住宿费用。", metadata={})
    stamp_chunk_pages([chunk], ast)
    assert "pages" not in chunk.metadata


# ── citation 双出口 ────────────────────────────────────────

def _doc(meta: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(metadata=meta)


def test_extract_sources_carries_pages_and_section():
    formatter = CitationFormatter()
    docs = [_doc({"index": 1, "source_file": "报销制度.pdf", "doc_type": "policy",
                  "score": 0.83, "pages": "3,4", "section_title": "报销流程"})]
    sources = formatter.extract_sources(docs, "见 [E1]")
    assert sources[0]["pages"] == [3, 4]
    assert sources[0]["section"] == "报销流程"
    # 坏数据容错：非数字段跳过
    docs[0].metadata["pages"] = "3,x,5"
    sources = formatter.extract_sources(docs, "见 [E1]")
    assert sources[0]["pages"] == [3, 5]


def test_format_references_line_carries_locality():
    """文本协议出口：参考文献行携带「第 X 页 · 章节」定位段。"""
    formatter = CitationFormatter()
    docs = [_doc({"index": 1, "source_file": "报销制度.pdf", "doc_type": "policy",
                  "score": 0.83, "pages": "3,4", "section_title": "报销流程"})]
    text = formatter.format_references(docs, "")
    assert "### 参考文献" in text
    assert "第 3-4 页 · 报销流程 — 相关度: 0.83" in text


def test_format_references_legacy_line_without_locality():
    """无页码无章节 → 旧行格式（向后兼容）。"""
    formatter = CitationFormatter()
    docs = [_doc({"index": 1, "source_file": "faq.md", "doc_type": "manual",
                  "score": 0.7})]
    text = formatter.format_references(docs, "")
    assert "1. **faq.md**" in text
    assert "第" not in text.split("**faq.md**")[1]


def test_pages_label_variants():
    assert _pages_label([3]) == "第 3 页"
    assert _pages_label([3, 4, 5]) == "第 3-5 页"
    assert _pages_label([2, 5]) == "第 2、5 页"
    assert _pages_label([2, 5, 9, 12]) == "第 2、5、9 页 等"


# ── 文本协议反解析（done.sources 实际数据通道）──────────────

def test_parse_sources_with_locality():
    text = (
        "回答正文\n\n---\n\n### 参考文献\n\n"
        "1. **报销制度.pdf** (制度规范) — 第 3-4 页 · 报销流程 — 相关度: 0.83\n"
        "2. **使用指南.md** (通用) — 常见问题 — 相关度: 0.70\n"
    )
    sources = parse_sources_from_text(text)
    by_name = {s["filename"]: s for s in sources}
    assert by_name["报销制度.pdf"]["doc_type"] == "policy"
    assert by_name["报销制度.pdf"]["pages"] == [3, 4]
    assert by_name["报销制度.pdf"]["section"] == "报销流程"
    # section-only 行：无「·」且非页码 → 整段为章节
    assert by_name["使用指南.md"]["section"] == "常见问题"
    assert "pages" not in by_name["使用指南.md"]


def test_parse_sources_legacy_line_unchanged():
    """旧行格式解析结果与改造前一致（无 pages/section 键）。"""
    text = (
        "### 参考文献\n\n"
        "1. **faq.md** (FAQ) — 相关度: 0.94\n"
    )
    sources = parse_sources_from_text(text)
    assert sources == [{
        "filename": "faq.md",
        "doc_type": "faq",
        "type_label": "FAQ",
        "score": 0.94,
    }]


# ── 原文预览钥匙（P1）：doc_id 双出口 ───────────────────────

def test_extract_sources_carries_doc_id():
    formatter = CitationFormatter()
    docs = [_doc({"index": 1, "source_file": "报销制度.pdf", "doc_type": "policy",
                  "score": 0.83, "doc_id": "baoxiao-zhidu"})]
    sources = formatter.extract_sources(docs, "见 [E1]")
    assert sources[0]["doc_id"] == "baoxiao-zhidu"
    # 无 doc_id（历史索引）不下发
    docs[0].metadata["doc_id"] = ""
    sources = formatter.extract_sources(docs, "见 [E1]")
    assert "doc_id" not in sources[0]


def test_format_references_line_carries_doc_marker_and_parse_roundtrip():
    """参考文献行尾机器注释：Markdown 渲染不可见 + 文本解析可取回（往返一致）。"""
    formatter = CitationFormatter()
    docs = [_doc({"index": 1, "source_file": "报销制度.pdf", "doc_type": "policy",
                  "score": 0.83, "pages": "3", "doc_id": "baoxiao-zhidu"})]
    text = formatter.format_references(docs, "")
    assert "<!--doc:baoxiao-zhidu-->" in text
    sources = parse_sources_from_text(text)
    assert sources[0]["doc_id"] == "baoxiao-zhidu"
    assert sources[0]["pages"] == [3]
    # 旧行（无注释）解析不受影响
    legacy = formatter.format_references([_doc({"index": 1, "source_file": "faq.md",
                                                 "doc_type": "faq", "score": 0.7})], "")
    parsed = parse_sources_from_text(legacy)
    assert "doc_id" not in parsed[0]
