"""CsvParser — 流式解析 CSV → Raw AST。

财务报表进入 RAG 后产出与 ExcelParser 对齐的 table 节点（rows=二维数组）。
解析逐行读取，只保留行数/单元格预算内的前缀；剩余内容继续流式计数，
以便质量记录准确报告截断量，同时不构造完整 DataFrame。
"""
from __future__ import annotations

import csv
import os

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger


def _read_bounded_rows(file_path: str, encoding: str, max_rows: int,
                       max_cells: int) -> tuple[list[list[str]], int, int, int, int, str]:
    """逐记录读取并只保存预算内的前缀，返回行/单元格截断统计。"""
    retained: list[list[str]] = []
    data_rows_total = 0
    cells_total = 0
    cells_kept = 0
    truncation_reasons: set[str] = set()
    retention_stopped = False

    with open(file_path, "r", encoding=encoding, newline="") as stream:
        reader = csv.reader(stream)
        header = next((row for row in reader if row and any(row)), None)
        if header is None:
            return [], 0, 0, 0, 0, ""

        cells_total += len(header)
        kept_header = header[:max_cells]
        retained.append(kept_header)
        cells_kept += len(kept_header)
        if len(kept_header) < len(header):
            truncation_reasons.add("cells")
            retention_stopped = True

        for row in reader:
            if not row or not any(row):
                continue
            data_rows_total += 1
            cells_total += len(row)

            exceeds_rows = len(retained) >= max_rows
            exceeds_cells = cells_kept + len(row) > max_cells
            if retention_stopped or exceeds_rows or exceeds_cells:
                if exceeds_rows:
                    truncation_reasons.add("rows")
                if exceeds_cells:
                    truncation_reasons.add("cells")
                retention_stopped = True
                continue

            retained.append(row)
            cells_kept += len(row)

    truncated_rows = max(0, data_rows_total - max(0, len(retained) - 1))
    truncated_cells = max(0, cells_total - cells_kept)
    reason = ",".join(sorted(truncation_reasons))
    return retained, data_rows_total, truncated_rows, truncated_cells, cells_total, reason


class CsvParser(BaseDocumentParser):
    PARSER_VERSION = "v2"

    def parse(self, file_path: str) -> DocumentAST:
        filename = os.path.basename(file_path)
        root = DocumentNode(type="section", text="", level=0)
        from backend.config.rag import RAG_TABLE_MAX_CELLS, RAG_TABLE_MAX_ROWS

        max_rows = max(1, int(RAG_TABLE_MAX_ROWS))
        max_cells = max(1, int(RAG_TABLE_MAX_CELLS))
        try:
            # utf-8-sig 去 BOM；若遇非法 UTF-8 字节，从文件头重开并尝试 GBK。
            result = _read_bounded_rows(
                file_path, "utf-8-sig", max_rows, max_cells,
            )
        except Exception as utf8_error:
            try:
                result = _read_bounded_rows(
                    file_path, "gbk", max_rows, max_cells,
                )
            except Exception as gbk_error:
                logger.error(
                    f"[CsvParser] 解析失败 {file_path}: "
                    f"utf-8={utf8_error}, gbk={gbk_error}"
                )
                return DocumentAST(
                    root=root, source_file=file_path, raw_text="",
                )

        (
            rows, total_data_rows, truncated_rows, truncated_cells,
            total_cells, reason,
        ) = result
        if not rows:
            logger.warning(f"[CsvParser] {file_path} 无表头或无数据")
            return DocumentAST(
                root=root, source_file=file_path, raw_text="",
            )

        # 列名 strip + 单元格原样保留，避免 SKU、订单号被数值推断改写。
        rows[0] = [str(cell).strip() for cell in rows[0]]
        from backend.rag.preprocessing.parser._table_nl import (
            make_table_chunk_text,
            normalize_table_rows,
        )

        if len(rows) > 1:
            flat_header, data_rows = normalize_table_rows(rows)
            normalized_rows = (
                [flat_header] + data_rows if flat_header and data_rows else rows
            )
        else:
            normalized_rows = rows

        section = DocumentNode(type="section", text=filename, level=1)
        section.source_locator = {"csv_file": filename}
        table_text = make_table_chunk_text(
            normalized_rows, section_title=filename,
        )
        table_node = DocumentNode(
            type="table", text=table_text, rows=normalized_rows,
        )
        table_node.source_locator = {
            "row_start": 1,
            "row_end": max(0, len(normalized_rows) - 1),
            "columns": [str(value) for value in normalized_rows[0]][:64],
        }
        section.children.append(table_node)
        root.children.append(section)

        if truncated_rows or truncated_cells:
            logger.warning(
                f"[CsvParser] {file_path} 触发行/单元格上限 "
                f"({reason or 'unknown'}): 共扫描 {total_data_rows} 行，"
                f"截去 {truncated_rows} 行、{truncated_cells} 个单元格"
            )

        return DocumentAST(
            root=root,
            source_file=file_path,
            raw_text=f"[{filename}]\n{table_text}",
            table_rows_truncated=truncated_rows,
            table_rows_total=total_data_rows,
            table_rows_total_known=True,
            table_cells_truncated=truncated_cells,
            table_cells_total=total_cells,
            table_cells_total_known=True,
            table_truncation_reason=reason,
        )
