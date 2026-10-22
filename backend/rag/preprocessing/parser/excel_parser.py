"""ExcelParser — openpyxl 流式解析 Excel → Raw AST。

每个 Sheet 按原顺序成为 section。资源上限在整个 workbook 共享；达到上限
后不再读取后续行或 Sheet。Excel 的超限总量只记录已读下界，不扫描尾部。
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
            return str(int(c))
        from backend.rag.preprocessing.financial_normalizer import safe_number_str
        return safe_number_str(c)
    return str(c).strip() if isinstance(c, str) else str(c)


class ExcelParser(BaseDocumentParser):
    PARSER_VERSION = "v2"

    def parse(self, file_path: str) -> DocumentAST:
        import openpyxl
        from backend.config.rag import RAG_TABLE_MAX_CELLS, RAG_TABLE_MAX_ROWS

        max_rows = max(1, int(RAG_TABLE_MAX_ROWS))
        max_cells = max(1, int(RAG_TABLE_MAX_CELLS))
        wb = None
        formula_wb = None
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            # data_only 工作簿优先保留 Excel 计算缓存；空缓存的公式单元格会
            # 显示为 None，因此并行读取公式视图作为缺省值，避免静默漏格。
            formula_wb = openpyxl.load_workbook(
                file_path, read_only=True, data_only=False,
            )
        except Exception as e:
            if wb is not None:
                wb.close()
            if formula_wb is not None:
                formula_wb.close()
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
        rows_scanned = 0
        cells_scanned = 0
        truncated_rows = 0
        truncated_cells = 0
        rows_total_known = True
        cells_total_known = True
        truncation_reasons: set[str] = set()
        stop_workbook = False

        try:
            for sheet_name in wb.sheetnames:
                # 额度在 Workbook 级共享。达到任一上限后，不再打开后续 Sheet。
                if rows_scanned >= max_rows or cells_scanned >= max_cells:
                    truncated_rows += 1
                    if cells_scanned >= max_cells:
                        truncated_cells += 1
                        truncation_reasons.add("cells")
                    else:
                        truncation_reasons.add("rows")
                    rows_total_known = False
                    cells_total_known = False
                    stop_workbook = True
                    break

                ws = wb[sheet_name]
                rows: list[list[str]] = []
                sheet_max_row = int(ws.max_row or 0)
                sheet_max_col = int(ws.max_column or 0)
                if sheet_max_row <= 0 or sheet_max_col <= 0:
                    continue

                # 每次只允许读取当前剩余额度内的单元格。若本 Sheet 更宽，
                # 读取到上限即丢弃不完整行并停止，绝不把残行写入 AST。
                remaining_cells = max_cells - cells_scanned
                max_col = min(sheet_max_col, remaining_cells)
                width_limited = max_col < sheet_max_col

                row_iter = iter(ws.iter_rows(values_only=True, max_col=max_col))
                formula_row_iter = iter(
                    formula_wb[sheet_name].iter_rows(
                        values_only=False, max_col=max_col,
                    )
                )
                row_number = 0
                while row_number < sheet_max_row:
                    # 在请求下一行前检查全局资源预算，避免 iter_rows 额外
                    # 实例化超限行或在精确触顶后继续读取。
                    if rows_scanned >= max_rows:
                        truncated_rows += 1
                        truncation_reasons.add("rows")
                        rows_total_known = False
                        cells_total_known = False
                        stop_workbook = True
                        break
                    if cells_scanned + max_col > max_cells:
                        truncated_rows += 1
                        truncated_cells += 1
                        truncation_reasons.add("cells")
                        rows_total_known = False
                        cells_total_known = False
                        stop_workbook = True
                        break

                    try:
                        row = next(row_iter)
                        formula_row = next(formula_row_iter)
                    except StopIteration:
                        break
                    row_number += 1
                    cells = []
                    for value, formula_cell in zip(row, formula_row):
                        if value is None and formula_cell.data_type == "f":
                            value = formula_cell.value
                        cells.append(_cell_to_str(value))
                    cells_scanned += len(cells)

                    if width_limited:
                        # 当前行只能读取前缀；不把残缺行写入 AST，也不继续
                        # 读取后续内容。截断单元格数至少为未读取的列数。
                        rows_scanned += 1
                        truncated_rows += 1
                        truncated_cells += max(1, sheet_max_col - max_col)
                        truncation_reasons.add("cells")
                        rows_total_known = False
                        cells_total_known = False
                        stop_workbook = True
                        break

                    rows_scanned += 1
                    if any(cells):
                        rows.append(cells)

                if rows:
                    from backend.rag.preprocessing.parser._table_nl import (
                        make_table_chunk_text,
                    )

                    table_text = make_table_chunk_text(
                        rows, section_title=sheet_name,
                    )
                    section = DocumentNode(
                        type="section", text=sheet_name, level=1,
                        children=[DocumentNode(
                            type="table", text=table_text, rows=rows,
                        )],
                    )
                    section.source_locator = {"worksheet": sheet_name}
                    section.children[0].source_locator = {
                        "worksheet": sheet_name,
                        "row_start": 1,
                        "row_end": max(0, len(rows) - 1),
                        "columns": [str(value) for value in rows[0]][:64],
                    }
                    root.children.append(section)
                    raw_lines.append(f"[{sheet_name}]\n{table_text}")

                if stop_workbook:
                    break
        finally:
            wb.close()
            formula_wb.close()

        reason = ",".join(sorted(truncation_reasons))
        if reason:
            logger.warning(
                f"[ExcelParser] {file_path} 达到工作簿资源上限 "
                f"({reason}): 已扫描 {rows_scanned} 行、{cells_scanned} 个单元格，"
                f"至少未纳入 {truncated_rows} 行、{truncated_cells} 个单元格；"
                "后续内容未读取"
            )

        return DocumentAST(
            root=root,
            source_file=file_path,
            raw_text="\n".join(raw_lines),
            table_rows_truncated=truncated_rows,
            table_rows_total=rows_scanned,
            table_rows_total_known=rows_total_known,
            table_cells_truncated=truncated_cells,
            table_cells_total=cells_scanned,
            table_cells_total_known=cells_total_known,
            table_truncation_reason=reason,
        )
