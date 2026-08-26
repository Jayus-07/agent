"""CsvParser — pandas 解析 CSV → Raw AST。

CSV 财务报表（如导出的月度销售/利润表）直接进入 RAG 索引，
产出与 ExcelParser 对齐的 table 节点（rows=二维数组）。

多级表头检测与拍平复用 _table_nl.normalize_table_rows，
数值精度复用 financial_normalizer.safe_number_str。
"""
from __future__ import annotations

import os

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger


class CsvParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        try:
            import pandas as pd
        except ImportError as e:
            logger.error(f"[CsvParser] pandas 未安装: {e}")
            return DocumentAST(
                root=DocumentNode(type="section", text="", level=0),
                source_file=file_path,
                raw_text="",
            )

        filename = os.path.basename(file_path)
        root = DocumentNode(type="section", text="", level=0)
        raw_lines: list[str] = []

        try:
            # 兼容 BOM 编码（Excel 导出的 CSV 常含 UTF-8 BOM）
            df = pd.read_csv(file_path, encoding="utf-8-sig")
        except Exception as e:
            # 回退尝试 GBK（中文 Windows 环境常见编码）
            try:
                df = pd.read_csv(file_path, encoding="gbk")
            except Exception as e2:
                logger.error(
                    f"[CsvParser] 解析失败 {file_path}: "
                    f"utf-8={e}, gbk={e2}"
                )
                return DocumentAST(
                    root=DocumentNode(type="section", text="", level=0),
                    source_file=file_path,
                    raw_text="",
                )

        if df.empty:
            logger.warning(f"[CsvParser] {file_path} 无数据行")
            return DocumentAST(
                root=DocumentNode(type="section", text="", level=0),
                source_file=file_path,
                raw_text="",
            )

        # 列名 strip + 值安全转字符串（保留数值精度）
        from backend.rag.preprocessing.financial_normalizer import safe_number_str
        from backend.rag.preprocessing.parser._table_nl import (
            make_table_chunk_text,
            normalize_table_rows,
        )

        df.columns = [str(c).strip() for c in df.columns]
        rows: list[list[str]] = [list(df.columns)]
        for _, row in df.iterrows():
            cells = [safe_number_str(v) if v is not None else "" for v in row]
            if any(cells):
                rows.append(cells)

        if len(rows) < 2:
            logger.warning(f"[CsvParser] {file_path} 仅有表头无数据行")
            return DocumentAST(
                root=DocumentNode(type="section", text="", level=0),
                source_file=file_path,
                raw_text="",
            )

        # 多级表头规范化
        flat_header, data_rows = normalize_table_rows(rows)
        if flat_header and data_rows:
            normalized_rows = [flat_header] + data_rows
        else:
            normalized_rows = rows

        section = DocumentNode(type="section", text=filename, level=1)
        table_text = make_table_chunk_text(
            normalized_rows, section_title=filename,
        )
        table_node = DocumentNode(
            type="table", text=table_text, rows=normalized_rows,
        )
        section.children.append(table_node)
        root.children.append(section)
        raw_lines.append(f"[{filename}]\n{table_text}")

        raw_text = "\n".join(raw_lines)
        logger.info(
            f"[CsvParser] {file_path} 解析完成: "
            f"{len(data_rows)} 行 × {len(flat_header)} 列"
        )
        return DocumentAST(root=root, source_file=file_path, raw_text=raw_text)
