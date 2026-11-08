"""数据采集出站 SSRF 与 target_table 注入收口（2026-11-09 审计）。

- HttpFetcher 出站前必须过 url_guard；重定向逐跳校验，不能靠 requests 自动跟随。
- target_table 是 LLM 可控参数，写入器不得把它直接拼进 SQL。
"""
from __future__ import annotations

import pytest

from backend.data_collection.fetchers.http_fetcher import HttpFetcher
from backend.data_collection.writers.sqlalchemy_writer import validate_target_table
from backend.tools.url_guard import UrlBlockedError


class _FakeResponse:
    def __init__(self, status_code: int, location: str | None = None):
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
        self.text = "{}"

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_http_fetcher_blocks_private_address_before_request():
    fetcher = HttpFetcher()
    try:
        with pytest.raises(UrlBlockedError):
            fetcher.fetch("http://127.0.0.1:8000/internal")
    finally:
        fetcher.close()


def test_http_fetcher_blocks_redirect_to_metadata(monkeypatch):
    fetcher = HttpFetcher()
    calls: list[str] = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        if len(calls) == 1:
            return _FakeResponse(302, "http://169.254.169.254/latest/meta-data/")
        return _FakeResponse(200)

    monkeypatch.setattr(fetcher._session, "get", fake_get)
    try:
        with pytest.raises(UrlBlockedError):
            fetcher.fetch("http://example.com/data")
        # 第二跳在发请求前就被拦截，只发出了一次请求
        assert calls == ["http://example.com/data"]
    finally:
        fetcher.close()


def test_target_table_rejects_sql_injection():
    for bad in (
        "stg_products; DROP TABLE users",
        "stg products",
        "stg_products--",
        "public.stg.products",
        "",
    ):
        with pytest.raises(ValueError):
            validate_target_table(bad)


def test_target_table_accepts_identifier_and_schema():
    assert validate_target_table("stg_products") == "stg_products"
    assert validate_target_table("analytics.stg_products") == "analytics.stg_products"
