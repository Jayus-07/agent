"""
analyzers/base.py — 数据分析抽象基类

所有 Analyzer 输入 CleanedData，输出 AnalyzedData（统计视图）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AnalyzedData:
    """分析后的数据视图，Analyzer 的统一产出格式

    包含 describe 统计、groupby 聚合结果、缺失值诊断。
    下游可直接提供给 Reporter 嵌入报告。
    """
    source: str                          # 透传数据源标识
    records: list[dict[str, Any]]        # 原始清洗后记录（透传给 Writer）
    summary: dict[str, Any] = field(default_factory=dict)
    # describe() 统计: {"price": {"mean": 35.5, "std": 22.1, "min": 9.99, "max": 89.99, ...}}
    aggregations: dict[str, Any] = field(default_factory=dict)
    # groupby 结果: {"by_渠道": {"Amazon": 8, "Shopify": 3, "eBay": 1}, ...}
    missing_report: dict[str, Any] = field(default_factory=dict)
    # 缺失值诊断: {"field": {"count": 2, "pct": 16.7, "strategy": "用中位数填充"}}


class AbstractAnalyzer(ABC):
    """数据分析抽象基类

    子类需实现:
      - analyze(cleaned, config) → AnalyzedData
    """

    @abstractmethod
    def analyze(
        self,
        cleaned: "CleanedData",
        config: dict[str, Any] | None = None,
    ) -> AnalyzedData:
        """对清洗后数据执行统计分析

        Args:
            cleaned: Cleaner 产出的清洗后数据
            config: 分析配置
              - groupby_keys: list[str]        分组维度
              - agg_funcs: dict[str, list]     聚合函数
              - numeric_fields: list[str]      需要统计的数值字段

        Returns:
            AnalyzedData: 统计视图 + 聚合结果 + 缺失值诊断
        """
        ...
