"""
fetchers/static_fetcher.py — 本地数据集采集器

读取 data_collection/datasets/ 目录中的 JSON/CSV 文件。
模拟企业内部数据导出的采集场景。
"""

import os
import time
from pathlib import Path
from typing import Any

from backend.data_collection.fetchers.base import AbstractFetcher, RawData
from backend.shared.logger import logger


class StaticDataFetcher(AbstractFetcher):
    """本地文件采集器

    支持的 source 格式:
      - "static://datasets/products.json"  → 读取 datasets/products.json
      - "static://datasets/orders.json"    → 读取 datasets/orders.json
      - 或直接传文件名简写: "products" / "orders"

    用法:
        fetcher = StaticDataFetcher()
        raw = fetcher.fetch("static://datasets/products.json")
        # raw.format == "json", raw.content == 文件原始文本
    """

    STATIC_PREFIX = "static://"

    def __init__(self, data_dir: str | None = None):
        """
        Args:
            data_dir: 数据集目录路径，默认 <模块目录>/../datasets/
        """
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(__file__), "..", "datasets")
        self._data_dir = Path(data_dir)

    @property
    def fetcher_type(self) -> str:
        return "static"

    def fetch(self, source: str, **kwargs: Any) -> RawData:
        """读取本地数据集文件"""
        file_path = self._resolve_path(source)
        started_at = time.time()

        if not file_path.exists():
            msg = f"数据集文件不存在: {file_path}"
            logger.error(f"[StaticFetcher] {msg}")
            raise FileNotFoundError(msg)

        fmt = file_path.suffix.lstrip(".").lower()
        content = file_path.read_text(encoding="utf-8")

        elapsed = time.time() - started_at
        file_size = len(content.encode("utf-8"))

        logger.info(
            f"[StaticFetcher] 读取 {file_path.name} → "
            f"{file_size} 字节, {elapsed:.3f}s"
        )

        return RawData(
            source=source,
            format=fmt,
            content=content,
            metadata={
                "fetcher": "static",
                "file_path": str(file_path),
                "file_size_bytes": file_size,
                "fetched_at": started_at,
                "elapsed_ms": elapsed * 1000,
            },
        )

    def _resolve_path(self, source: str) -> Path:
        """解析 source 为实际文件路径"""
        # 去掉前缀
        if source.startswith(self.STATIC_PREFIX):
            source = source[len(self.STATIC_PREFIX):]

        # 如果已经是完整路径
        candidate = Path(source)
        if candidate.exists():
            return candidate

        # 去掉可能重复的 datasets/ 前缀
        stripped_name = source
        for prefix in ("datasets/", "datasets\\"):
            if stripped_name.startswith(prefix):
                stripped_name = stripped_name[len(prefix):]
                break

        # data_dir 下的相对路径（先尝试原始名，再试去前缀后的名字）
        for name in (source, stripped_name):
            candidate = self._data_dir / name
            if candidate.exists():
                return candidate
            # 尝试自动补扩展名
            for ext in (".json", ".csv"):
                candidate = self._data_dir / f"{name}{ext}"
                if candidate.exists():
                    return candidate

        # 回退：data_dir / stripped_name
        return self._data_dir / stripped_name
