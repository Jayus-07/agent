"""DocxParser — python-docx 解析 Word → Raw AST。

段落分类规则：
- style.name 匹配 `Heading {N}` → DocumentNode(type="section", level=N, text=title)
- 段落 style.name 含 `List` → DocumentNode(type="list")
- 表格 → DocumentNode(type="table", rows=...)
- 其他 → DocumentNode(type="paragraph")

章节归属：paragraph / list / table 挂在最近的祖先 section 下。

P0-01/P0-04（2026-10-13）：
- 不再整篇 FAQ 转换（旧实现 looks_like_qa_doc 命中即丢全部正文与表格）；
  一律走段落分类解析，再由 annotate_qa_nodes 做局部 Q/A 配对。
- 表格按 body 原始阅读顺序交错产出（旧实现先处理全部段落、再把全部
  表格追加到最后一个 section，表格-正文交错关系丢失）。
"""
from __future__ import annotations

import re

import docx

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser._qa_patterns import annotate_qa_nodes
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger

_HEADING_RE = re.compile(r"^Heading\s+(\d+)$", re.IGNORECASE)
_LIST_RE = re.compile(r"List\s+\w+", re.IGNORECASE)
# 中文编号章节标题（一、二、三、）——Normal 段落但属于章节标题
_CN_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、")
_LEGAL_CLAUSE_RE = re.compile(
    r"^(?P<number>第[一二三四五六七八九十百\d]+条)"
    r"(?:[ \t\u3000]+(?P<title>[^:：\n]{1,60}))?"
    r"(?:(?:[:：][ \t]*(?P<body>.*))|(?:\r?\n(?P<body_after_line>.*)))?$",
    re.DOTALL,
)


def _find_parent_section(
    stack: list[DocumentNode], level: int
) -> DocumentNode:
    """在 section 栈中找到新 section 应该挂的父节点。

    规则：弹出所有 level >= 当前 level 的祖先，让新 section 挂在
    第一个 level < 当前 level 的祖先下。栈底始终是 root（level=0）。
    """
    while len(stack) > 1 and stack[-1].level >= level:
        stack.pop()
    return stack[-1]


def _iter_body_items(document):
    """按 body 原始顺序产出 (kind, obj)，kind ∈ {"para", "table"}。

    python-docx 的 document.paragraphs / document.tables 是两个独立列表，
    交错顺序丢失（P0-04）；这里直接迭代 body XML 子元素还原阅读顺序。
    """
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "para", Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield "table", Table(child, document)


class DocxParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        try:
            d = docx.Document(file_path)
        except (docx.opc.exceptions.PackageNotFoundError, ValueError) as e:
            # 文件损坏 / 格式异常 → 返回空 AST
            logger.error(
                f"[DocxParser] 打开失败 {file_path}: "
                f"{type(e).__name__}: {e}"
            )
            return DocumentAST(
                root=DocumentNode(type="section", text="", level=0),
                source_file=file_path,
                raw_text="",
            )
        except Exception as e:
            logger.exception(
                f"[DocxParser] 打开异常 {file_path}: {type(e).__name__}"
            )
            raise

        root = DocumentNode(type="section", text="", level=0)
        section_stack: list[DocumentNode] = [root]
        raw_lines: list[str] = []
        skipped_tables: list[int] = []
        qa_pairs_total = 0

        def _section_title_for_table() -> str:
            return section_stack[-1].text if section_stack else ""

        # 段落与表格按 body 阅读顺序单遍处理（P0-04：交错关系保留）
        for tbl_idx, (kind, obj) in enumerate(_iter_body_items(d)):
            if kind == "para":
                para = obj
                style_name = para.style.name if para.style else ""
                text = para.text.strip()

                heading_match = _HEADING_RE.match(style_name)
                if heading_match and text:
                    level = int(heading_match.group(1))
                    section = DocumentNode(type="section", text=text, level=level)
                    parent = _find_parent_section(section_stack, level)
                    parent.children.append(section)
                    section_stack.append(section)
                    raw_lines.append(text)
                    continue

                # DOCX 合同经常把条款标题与正文写在同一个 Normal 段落。
                # 将明确的「第 N 条 标题：正文」恢复为 section + 正文叶子，
                # 让后续 Legal 策略能把短表格附着到所属条款。
                clause_match = _LEGAL_CLAUSE_RE.match(text)
                if clause_match:
                    clause_number = clause_match.group("number")
                    clause_title = (clause_match.group("title") or "").strip()
                    title = " ".join(
                        part for part in (clause_number, clause_title) if part
                    )
                    # 后续条款必须与前一条款同级。条款中可能还包含小节，
                    # 因此回到最近条款的父级，再复用该条款层级。
                    previous_clause_index = next((
                        index
                        for index in range(len(section_stack) - 1, 0, -1)
                        if _LEGAL_CLAUSE_RE.match(section_stack[index].text)
                    ), None)
                    if previous_clause_index is not None:
                        level = section_stack[previous_clause_index].level
                        del section_stack[previous_clause_index:]
                    else:
                        level = max(section_stack[-1].level + 1, 1)
                    section = DocumentNode(
                        type="section", text=title, level=level,
                    )
                    parent = _find_parent_section(section_stack, level)
                    parent.children.append(section)
                    section_stack.append(section)
                    body = (
                        clause_match.group("body")
                        if clause_match.group("body") is not None
                        else clause_match.group("body_after_line")
                    )
                    if body and body.strip():
                        section.children.append(DocumentNode(
                            type="paragraph", text=body.strip(),
                        ))
                    raw_lines.append(text)
                    continue

                # 中文编号标题（一、二、三、）——Normal 段落但属于章节标题
                if _CN_HEADING_RE.match(text) and not _LIST_RE.search(style_name):
                    section = DocumentNode(type="section", text=text, level=1)
                    parent = _find_parent_section(section_stack, 1)
                    parent.children.append(section)
                    section_stack.append(section)
                    raw_lines.append(text)
                    continue

                if _LIST_RE.search(style_name) and text:
                    list_node = DocumentNode(type="list", text=text)
                    section_stack[-1].children.append(list_node)
                    raw_lines.append(text)
                    continue

                if text:
                    para_node = DocumentNode(type="paragraph", text=text)
                    section_stack[-1].children.append(para_node)
                    raw_lines.append(text)
                continue

            # kind == "table"：挂到当前 section（此刻 = 阅读顺序中表格前面的
            # 最近章节，与文档视觉归属一致）
            table = obj
            try:
                rows = [
                    [cell.text.strip() for cell in row.cells]
                    for row in table.rows
                ]
            except (AttributeError, IndexError) as e:
                logger.warning(
                    f"[DocxParser] 表格 {tbl_idx} 解析失败: "
                    f"{type(e).__name__}: {e}"
                )
                skipped_tables.append(tbl_idx)
                continue
            # 表格内容同时写入 text 字段：否则 StructureChunkStrategy 用 text 切分
            # 会产出空 leaf chunk（ChunkFilter 拒绝），表格数据入库后彻底丢失
            # V1.1: 用自然语言 + CSV 双格式（NL 在前，让 CrossEncoder rerank 能识别
            # 表格语义；CSV 在后，保留结构信息供全文检索）
            from backend.rag.preprocessing.parser._table_nl import make_table_chunk_text
            sec_title = _section_title_for_table()
            table_text = make_table_chunk_text(rows, section_title=sec_title)
            table_node = DocumentNode(type="table", text=table_text, rows=rows)
            section_stack[-1].children.append(table_node)
            raw_lines.append(table_text)

        # 可观测：汇总报告
        if skipped_tables:
            logger.warning(
                f"[DocxParser] {file_path} 跳过 {len(skipped_tables)} 个表: "
                f"{skipped_tables}"
            )

        # 局部 Q/A 配对（P0-01：命中段落转 qa 节点，其余结构保留）
        qa_pairs_total = annotate_qa_nodes(root)
        if qa_pairs_total:
            logger.info(
                f"[DocxParser] {file_path} 局部识别 {qa_pairs_total} 个 Q/A 对"
                f"（其余结构保留）"
            )

        raw_text = "\n".join(raw_lines)
        return DocumentAST(root=root, source_file=file_path, raw_text=raw_text)
