"""
fetchers/http_fetcher.py — HTTP 数据采集器

通过 requests 库获取远程 API 数据。
配合 Mock API Server 使用，后续可无缝替换为真实第三方 API。
"""

import time
from typing import Any

import requests

from backend.data_collection.fetchers.base import AbstractFetcher, RawData
from backend.shared.logger import logger


class HttpFetcher(AbstractFetcher):
    """HTTP API 采集器

    用法:
        fetcher = HttpFetcher(timeout=30, user_agent="DCC/1.0")
        raw = fetcher.fetch("http://localhost:8001/mock/products")
        raw = fetcher.fetch(
            "http://localhost:8001/mock/products?category=电子产品",
            headers={"Authorization": "Bearer xxx"},
        )

    source 格式:
      - "http://..."  / "https://..."  → 直接请求
      - 支持 query string 参数写在 URL 中
    """

    def __init__(
        self,
        timeout: int = 30,
        user_agent: str = "DataCollectionCenter/1.0",
        max_retries: int = 2,
    ):
        """
        Args:
            timeout: 请求超时（秒）
            user_agent: User-Agent 头
            max_retries: 最大重试次数
        """
        self._timeout = timeout
        self._user_agent = user_agent
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})

    @property
    def fetcher_type(self) -> str:
        return "http"

    def fetch(self, source: str, **kwargs: Any) -> RawData:
        """通过 HTTP GET 获取数据"""
        headers = kwargs.get("headers", {})
        started_at = time.time()
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.get(
                    source,
                    headers={**self._session.headers, **headers},
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                elapsed = time.time() - started_at

                # 推断格式
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type:
                    fmt = "json"
                elif "csv" in content_type:
                    fmt = "csv"
                else:
                    fmt = "json"  # 默认按 JSON 处理

                logger.info(
                    f"[HttpFetcher] GET {source} → "
                    f"HTTP {resp.status_code}, {len(resp.text)} 字节, "
                    f"{elapsed:.3f}s (第{attempt+1}次)"
                )

                return RawData(
                    source=source,
                    format=fmt,
                    content=resp.text,
                    metadata={
                        "fetcher": "http",
                        "status_code": resp.status_code,
                        "content_type": content_type,
                        "headers": dict(resp.headers),
                        "fetched_at": started_at,
                        "elapsed_ms": elapsed * 1000,
                        "retries": attempt,
                    },
                )

            except requests.Timeout as e:
                last_error = e
                logger.warning(
                    f"[HttpFetcher] 超时: {source} (第{attempt+1}次, timeout={self._timeout}s)"
                )
            except requests.RequestException as e:
                last_error = e
                logger.warning(
                    f"[HttpFetcher] 请求失败: {source} → {e} (第{attempt+1}次)"
                )

            if attempt < self._max_retries:
                delay = 1.5 ** (attempt + 1)
                time.sleep(delay)

        elapsed = time.time() - started_at
        raise RuntimeError(
            f"HTTP 请求失败 [{self._max_retries + 1}次]: {source}, "
            f"最后错误: {last_error}, 耗时 {elapsed:.3f}s"
        )

    def close(self) -> None:
        """释放 HTTP Session 连接"""
        self._session.close()
