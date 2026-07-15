"""
fetchers/base.py — 数据获取抽象基类

所有 Fetcher 输入 source 标识，输出统一 RawData。
StaticFetcher / HttpFetcher / SeleniumFetcher 均实现此接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawData:
    """采集到的原始数据，Fetcher 的统一产出格式"""
    source: str                          # 数据源标识，如 "static://datasets/products.json"
    format: str                          # "json" / "csv" / "html"
    content: str                         # 原始文本内容
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata 示例: {"fetcher": "static", "file_path": "...", "fetched_at": 1234567890.0}


class AbstractFetcher(ABC):
    """数据获取抽象基类

    子类需实现:
      - fetcher_type: 属性, 返回采集器类型名
      - fetch(source, **kwargs) → RawData
    """

    @property
    @abstractmethod
    def fetcher_type(self) -> str:
        """返回采集器类型标识: 'static' | 'http' | 'selenium'"""
        ...

    @abstractmethod
    def fetch(self, source: str, **kwargs: Any) -> RawData:
        """从指定数据源获取原始数据

        Args:
            source: 数据源标识
                - "static://datasets/products.json"  本地数据集
                - "http://localhost:8001/mock/products"  Mock API
            **kwargs: 采集器特定参数 (headers, timeout 等)

        Returns:
            RawData: 包含原始内容和元数据
        """
        ...
