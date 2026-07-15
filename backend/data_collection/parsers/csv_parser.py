"""
parsers/csv_parser.py — CSV 解析器

通过 pandas.read_csv 解析 CSV 文本为结构化记录列表。
"""

from io import StringIO
from typing import Any

import pandas as pd

from backend.data_collection.parsers.base import AbstractParser, ParsedData
from backend.data_collection.fetchers.base import RawData
from backend.utils.logger import logger


class CsvParser(AbstractParser):
    """CSV 数据解析器

    用法:
        parser = CsvParser()
        parsed = parser.parse(raw)
    """

    def __init__(self, encoding: str = "utf-8"):
        self._encoding = encoding

    def supports(self, fmt: str) -> bool:
        return fmt.lower() == "csv"

    def parse(
        self, raw: RawData, schema: dict[str, Any] | None = None
    ) -> ParsedData:
        """将 CSV 文本解析为 list[dict]"""
        errors: list[str] = []

        try:
            df = pd.read_csv(StringIO(raw.content), encoding=self._encoding)
        except Exception as e:
            logger.error(f"[CsvParser] CSV 解析失败: {e}")
            return ParsedData(
                source=raw.source,
                records=[],
                record_count=0,
                parse_errors=[f"CSV 解析失败: {str(e)}"],
            )

        # 列名 strip（去空格）
        df.columns = [str(c).strip() for c in df.columns]

        # NaN → None（JSON 兼容）
        records = df.astype(object).where(pd.notnull(df), None).to_dict(
            orient="records"
        )

        logger.info(f"[CsvParser] 解析 {len(df)} 行 × {len(df.columns)} 列")

        return ParsedData(
            source=raw.source,
            records=records,
            record_count=len(records),
            parse_errors=errors,
        )
