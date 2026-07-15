"""
cleaners/base.py — 数据清洗抽象基类

所有 Cleaner 输入 list[dict]，输出 CleanedData。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CleanedData:
    """清洗后的数据，Cleaner 的统一产出格式"""
    source: str                          # 透传数据源标识
    records: list[dict[str, Any]]        # 清洗后的记录列表
    row_count: int                       # 清洗后行数
    dedup_removed: int = 0               # 去重移除行数
    null_filled: dict[str, int] = field(default_factory=dict)
    # null_filled: {"field_name": filled_count}
    type_converted: dict[str, str] = field(default_factory=dict)
    # type_converted: {"field_name": "str→float"}
    validation_failures: list[str] = field(default_factory=list)


class AbstractCleaner(ABC):
    """数据清洗抽象基类

    子类需实现:
      - clean(records, rules) → CleanedData
    """

    @abstractmethod
    def clean(
        self,
        records: list[dict[str, Any]],
        rules: dict[str, Any] | None = None,
        source: str = "",
    ) -> CleanedData:
        """清洗结构化记录

        Args:
            records: Parser 产出的记录列表
            rules: 清洗规则配置
            source: 数据源标识（透传）

        Returns:
            CleanedData: 清洗后数据 + 清洗报告
        """
        ...
