"""
parsers/base.py — 数据解析抽象基类

所有 Parser 输入 RawData，输出 ParsedData（结构化记录列表）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedData:
    """解析后的结构化数据，Parser 的统一产出格式"""
    source: str                          # 透传数据源标识
    records: list[dict[str, Any]]        # 解析后的结构化记录列表
    record_count: int                    # 成功解析的行数
    parse_errors: list[str] = field(default_factory=list)
    # parse_errors: 解析跳过的行及原因


class AbstractParser(ABC):
    """数据解析抽象基类

    子类需实现:
      - supports(fmt) → bool
      - parse(raw, schema) → ParsedData
    """

    @abstractmethod
    def supports(self, fmt: str) -> bool:
        """该 parser 是否支持指定格式 ('json' | 'csv' | 'html')"""
        ...

    @abstractmethod
    def parse(self, raw: "RawData", schema: dict[str, Any] | None = None) -> ParsedData:
        """解析原始数据为结构化记录列表

        Args:
            raw: RawData 采集产出
            schema: 可选的字段映射/类型提示，如 {"price": "float", "quantity": "int"}

        Returns:
            ParsedData: 结构化记录列表 + 元信息
        """
        ...
