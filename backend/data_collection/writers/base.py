"""
writers/base.py — 数据写入抽象基类

所有 Writer 输入记录列表 + 表名，输出 WriteResult。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WriteResult:
    """写入结果，Writer 的统一产出格式"""
    table: str                           # 目标表名
    inserted: int = 0                    # 新增行数
    updated: int = 0                     # 更新行数 (upsert 模式)
    skipped: int = 0                     # 跳过行数 (重复/校验失败)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


class AbstractWriter(ABC):
    """数据写入抽象基类

    子类需实现:
      - write(records, table, mode) → WriteResult
    """

    @abstractmethod
    def write(
        self,
        records: list[dict[str, Any]],
        table: str,
        mode: str = "append",
    ) -> WriteResult:
        """将记录列表写入数据库

        Args:
            records: 待写入的记录列表（来自 AnalyzedData.records）
            table: 目标表名
            mode: 写入模式
              - "append": 仅追加
              - "replace": 先清空表再写入
              - "upsert": 冲突时更新

        Returns:
            WriteResult: 写入计数 + 耗时
        """
        ...
