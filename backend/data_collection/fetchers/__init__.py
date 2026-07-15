"""数据获取层 — 所有 Fetcher 输入 source 标识，输出统一 RawData"""
from backend.data_collection.fetchers.base import AbstractFetcher, RawData
from backend.data_collection.fetchers.static_fetcher import StaticDataFetcher
from backend.data_collection.fetchers.http_fetcher import HttpFetcher

__all__ = ["AbstractFetcher", "RawData", "StaticDataFetcher", "HttpFetcher"]
