"""ExcelParser — openpyxl 解析 Excel → Raw AST。

每个 sheet → section（标题=sheet 名）+ table 节点（rows=二维数组，
text=序列化文本，供 chunk 保留内容，避免空 chunk 丢数据）。

P0 超长文档收口（2026-10-13）：
- 单元格值保真：整数直接 str(int)（SKU/订单号不再出现 "123.0" 尾巴）；
  浮点走 safe_number_str 的 Decimal 规范化（财务口径不变）；文本单元格
  原样保留（前导零天然安全）。
- 行数/单元格数上限（RAG_TABLE_MAX_ROWS / RAG_TABLE_MAX_CELLS）：超大
  workbook 停止读取，截断行数留痕 AST（quality_issues 展示覆盖情况）。
"""
from __future__ import annotations

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger


def _cell_to_str(c) -> str:
    """单元格 → 字符串：整数去浮点尾巴，浮点走 Decimal 规范化，文本原样。"""
    if c is None:
        return ""
    if isinstance(c, bool):
        return str(c)
    if isinstance(c, int):
        return str(c)
    if isinstance(c, float):
        if c.is_integer():
            # Excel 数字单元格的 SKU/订单号（123456.0）→ "123456"
            return str(int(c))
        from backend.rag.preprocessing.financial_normalizer import safe_number_str
        return safe_number_str(c)
    return str(c).strip() if isinstance(c, str) else str(c)


class ExcelParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        # F7: openpyxl 惰性导入 — 顶层 import 会拖垮整个 parser 包导入链
        # （路由模块冷启动/未装 openpyxl 时收集失败）
        import openpyxl
        from backend.config.rag import RAG_TABLE_MAX_CELLS, RAG_TABLE_MAX_ROWS

        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        except Exception as e:
            logger.error(
                f"[ExcelParser] 打开失败 {file_path}: {type(e).__name__}: {e}"
            )
            return DocumentAST(
                root=DocumentNode(type="section", text="", level=0),
                source_file=file_path,
                raw_text="",
            )

        root = DocumentNode(type="section", text="", level=0)
        raw_lines: list[str] = []
        truncated_rows = 0
        total_rows = 0

        try:
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows: list[list[str]] = []
                cells_used = 0
                for row in ws.iter_rows(values_only=True):
                    total_rows += 1
                    if len(rows) >= RAG_TABLE_MAX_ROWS or (
                        cells_used >= RAG_TABLE_MAX_CELLS
                    ):
                        truncated_rows += 1
                        continue
                    cells = [_cell_to_str(c) for c in row]
                    if any(cells):  # 跳过全空行
                        rows.append(cells)
                        cells_used += len(cells)
                if not rows:
                    continue

                section = DocumentNode(type="section", text=sheet_name, level=1)
                # V1.1: 用 NL + CSV 双格式，让 CrossEncoder rerank 能识别表格语义
                from backend.rag.preprocessing.parser._table_nl import make_table_chunk_text
                table_text = make_table_chunk_text(rows, section_title=sheet_name)
                table_node = DocumentNode(type="table", text=table_text, rows=rows)
                section.children.append(table_node)
                root.children.append(section)
                raw_lines.append(f"[{sheet_name}]\n{table_text}")
        finally:
            wb.close()

        if truncated_rows:
            logger.warning(
                f"[ExcelParser] {file_path} 行数/单元格数超上限，"
                f"截断 {truncated_rows} 行（总扫描 {total_rows} 行）"
            )

        raw_text = "\n".join(raw_lines)
        return DocumentAST(
            root=root, source_file=file_path, raw_text=raw_text,
            table_rows_truncated=truncated_rows,
            table_rows_total=total_rows,
        )
