"""读取供应商公开价目页并与有效计费价格比较。

该模块只观察和告警，不会导入、审批或修改任何计费价格。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import psycopg2.extras
import requests

from backend.config.budget import (
    BUDGET_BASE_CURRENCY,
    BUDGET_FX_USD_CNY,
    to_base_currency,
)
from backend.config.database import MEMORY_DB_CONFIG

MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
_MODEL_PRICE_STORAGE_QUANTUM = Decimal("0.000001")
_MODEL_PRICE_STORAGE_HALF_QUANTUM = _MODEL_PRICE_STORAGE_QUANTUM / 2
_ALLOWED_HOSTS = {
    "volcengine_ark_doubao_seed_2_0_mini": {
        "console.volcengine.com", "ark.volcengine.com",
    },
    "aliyun_qwen3_7_text_embedding": {"help.aliyun.com"},
    "aliyun_qwen3_7_text_rerank": {"help.aliyun.com"},
}

ARK_URL = "https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing"
EMBEDDING_URL = "https://help.aliyun.com/en/model-studio/embedding"
RERANK_URL = "https://help.aliyun.com/en/model-studio/qwen3-7-text-rerank"


@dataclass(frozen=True)
class PriceQuote:
    """单一供应商、模型、计费维度的公开刊例价。"""

    source_id: str
    model_name: str
    component: str
    dimension: str
    price_per_unit: Decimal
    unit: str
    currency: str
    source_url: str
    source_updated_at: str
    content_sha256: str


@dataclass(frozen=True)
class PriceComparison:
    """本轮新增快照及其与前次观测/系统价格的差异。"""

    recorded_count: int
    source_changes: list[dict[str, Any]]
    system_mismatches: list[dict[str, Any]]
    prices: list[dict[str, Any]]


class PriceMonitorStorageUnavailable(RuntimeError):
    """巡检状态或结果存储不可用。"""


class _PriceTableParser(HTMLParser):
    """提取 HTML 标题路径和表格单元格，避免用正则解析嵌套标记。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[tuple[int, str]] = []
        self.tables: list[tuple[str, list[list[str]]]] = []
        self._heading_level: int | None = None
        self._heading_parts: list[str] | None = None
        self._table_depth = 0
        self._table_context = ""
        self._table_rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self._heading_level = int(tag[1])
            self._heading_parts = []
        elif tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._table_context = " ".join(value for _, value in self.headings)
                self._table_rows = []
        elif tag == "tr" and self._table_depth:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []
        elif tag == "br" and self._cell_parts is not None:
            self._cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._heading_parts is not None:
            self._heading_parts.append(data)
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            if self._heading_parts is not None:
                level = self._heading_level or int(tag[1])
                text = _normalize(" ".join(self._heading_parts))
                if text:
                    self.headings = [item for item in self.headings if item[0] < level]
                    self.headings.append((level, text))
            self._heading_level = None
            self._heading_parts = None
        elif tag in {"td", "th"} and self._cell_parts is not None:
            if self._row is not None:
                self._row.append(_normalize(" ".join(self._cell_parts)))
            self._cell_parts = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._table_rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0:
                self.tables.append((self._table_context, self._table_rows))


def _normalize(value: str) -> str:
    return " ".join(value.split()).strip()


def _decimal(value: str, *, context: str) -> Decimal:
    cleaned = value.replace(",", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        raise ValueError(f"价目页价格字段无法识别: {context}")
    price = Decimal(cleaned)
    if not price.is_finite() or price < 0:
        raise ValueError(f"价目页价格无效: {context}")
    return price


def _parse_tables(content: str) -> list[tuple[str, list[list[str]]]]:
    parser = _PriceTableParser()
    parser.feed(content)
    parser.close()
    return parser.tables


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _ark_updated_at(content: str) -> str:
    match = re.search(r'<time[^>]+datetime=["\']([^"\']+)', content, re.I)
    return match.group(1) if match else ""


def parse_ark_prices(content: str, *, content_sha256: str) -> list[PriceQuote]:
    """解析北京地域常规在线推理的 Doubao Seed 2.0 mini 价格。"""
    tables = [
        rows for context, rows in _parse_tables(content)
        if "在线推理（常规）" in context or "在线推理(常规)" in context
    ]
    matches: list[list[str]] = []
    for rows in tables:
        for row in rows:
            if (
                len(row) >= 8
                and row[0].casefold() == "doubao-seed-2.0-mini"
                and re.search(r"\[\s*0\s*,\s*32\s*\]", row[1])
            ):
                matches.append(row)
    if len(matches) != 1:
        raise ValueError("Doubao 常规在线价格行缺失或不唯一")

    row = matches[0]
    updated_at = _ark_updated_at(content)
    common = {
        "source_id": "volcengine_ark_doubao_seed_2_0_mini",
        "model_name": "doubao-seed-2.0-mini",
        "component": "llm",
        "unit": "per_1m_tokens",
        "currency": "CNY",
        "source_url": ARK_URL,
        "source_updated_at": updated_at,
        "content_sha256": content_sha256,
    }
    return [
        PriceQuote(
            dimension="input",
            price_per_unit=_decimal(row[2], context="Doubao input"),
            **common,
        ),
        PriceQuote(
            dimension="cache_read",
            price_per_unit=_decimal(row[5], context="Doubao cache"),
            **common,
        ),
        PriceQuote(
            dimension="output",
            price_per_unit=_decimal(row[7], context="Doubao output"),
            **common,
        ),
    ]


def parse_embedding_price(content: str, *, content_sha256: str) -> list[PriceQuote]:
    """解析阿里云北京同步 Embedding 价格，不采用批量 API 折扣价。"""
    matches: list[str] = []
    for context, rows in _parse_tables(content):
        if "Text embedding" not in context or "Beijing" not in context:
            continue
        for row in rows:
            if len(row) >= 5 and row[0].casefold() == "qwen3.7-text-embedding":
                matches.append(row[4])
    if len(matches) != 1:
        raise ValueError("Qwen3.7 Embedding 北京同步价格行缺失或不唯一")
    price_match = re.search(r"CNY\s*(\d+(?:\.\d+)?)", matches[0], re.I)
    if not price_match:
        raise ValueError("Qwen3.7 Embedding 价格币种/金额无法识别")
    price_per_1k = _decimal(price_match.group(1), context="Embedding per 1K")
    return [PriceQuote(
        source_id="aliyun_qwen3_7_text_embedding",
        model_name="qwen3.7-text-embedding",
        component="embedding",
        dimension="input",
        price_per_unit=price_per_1k * Decimal("1000"),
        unit="per_1m_tokens",
        currency="CNY",
        source_url=EMBEDDING_URL,
        source_updated_at="",
        content_sha256=content_sha256,
    )]


def parse_rerank_price(content: str, *, content_sha256: str) -> list[PriceQuote]:
    """解析阿里云北京 qwen3.7-text-rerank 文本输入价格。"""
    matches: list[Decimal] = []
    for context, rows in _parse_tables(content):
        if "Pricing" not in context or "China (Beijing)" not in context:
            continue
        for row in rows:
            if (
                len(row) >= 3
                and row[0].casefold() == "text input"
                and "per 1m tokens" in row[2].casefold()
            ):
                matches.append(_decimal(row[1], context="Rerank per 1M"))
    if len(matches) != 1:
        raise ValueError("Qwen3.7 Rerank 北京价格行缺失或不唯一")
    return [PriceQuote(
        source_id="aliyun_qwen3_7_text_rerank",
        model_name="qwen3.7-text-rerank",
        component="rerank",
        dimension="input",
        price_per_unit=matches[0],
        unit="per_1m_tokens",
        currency="CNY",
        source_url=RERANK_URL,
        source_updated_at="",
        content_sha256=content_sha256,
    )]


_SOURCE_PARSERS: tuple[tuple[str, str, Callable[..., list[PriceQuote]]], ...] = (
    ("volcengine_ark_doubao_seed_2_0_mini", ARK_URL, parse_ark_prices),
    ("aliyun_qwen3_7_text_embedding", EMBEDDING_URL, parse_embedding_price),
    ("aliyun_qwen3_7_text_rerank", RERANK_URL, parse_rerank_price),
)


def fetch_official_prices(
    client: Any | None = None,
) -> tuple[list[PriceQuote], list[dict[str, str]]]:
    """抓取固定白名单官方页面；单页失败会被记录，不伪装成零价格。"""
    close_client = client is None
    if client is None:
        client = requests.Session()
        client.trust_env = (
            os.getenv("MODEL_PRICE_MONITOR_USE_ENV_PROXY", "false").lower()
            in {"1", "true", "yes"}
        )
    quotes: list[PriceQuote] = []
    failures: list[dict[str, str]] = []
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "AgentPlatform-PriceMonitor/1.0",
    }
    try:
        for source_id, url, parser in _SOURCE_PARSERS:
            try:
                content, text = _fetch_official_page(
                    client, source_id=source_id, url=url, headers=headers
                )
                page_hash = _content_hash(content)
                quotes.extend(parser(text, content_sha256=page_hash))
            except Exception as exc:
                failures.append({
                    "source_id": source_id,
                    "error_type": type(exc).__name__,
                })
    finally:
        if close_client:
            client.close()
    return quotes, failures


def _validate_source_url(source_id: str, url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("供应商页面 URL 端口无效") from exc
    if (
        parsed.scheme != "https"
        or host not in _ALLOWED_HOSTS[source_id]
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("供应商页面 URL 不在 HTTPS 主机白名单内")


def _fetch_official_page(
    client: Any,
    *,
    source_id: str,
    url: str,
    headers: dict[str, str],
) -> tuple[bytes, str]:
    """逐跳校验 HTTPS 白名单重定向，并在流式读取时限制页面大小。"""
    current_url = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        _validate_source_url(source_id, current_url)
        response = client.get(
            current_url,
            timeout=(5, 20),
            headers=headers,
            allow_redirects=False,
            stream=True,
        )
        try:
            final_url = getattr(response, "url", current_url) or current_url
            _validate_source_url(source_id, final_url)
            status_code = response.status_code
            if 300 <= status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("供应商页面重定向缺少目标地址")
                if redirect_count >= MAX_REDIRECTS:
                    raise ValueError("供应商页面重定向次数超过上限")
                next_url = urljoin(current_url, location)
                _validate_source_url(source_id, next_url)
                current_url = next_url
                continue

            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and content_length.isdigit():
                if int(content_length) > MAX_PAGE_BYTES:
                    raise ValueError("供应商价目页超过大小上限")

            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > MAX_PAGE_BYTES:
                    raise ValueError("供应商价目页超过大小上限")
            raw_content = bytes(content)
            encoding = (
                getattr(response, "encoding", None)
                or getattr(response, "apparent_encoding", None)
                or "utf-8"
            )
            return raw_content, raw_content.decode(encoding, errors="replace")
        finally:
            response.close()
    raise ValueError("供应商页面重定向次数超过上限")


class PostgresPricePageMonitorRepository:
    """持久化每日公开报价，并读取同一时点的有效计费价格。"""

    def __init__(self, connection_factory=None) -> None:
        self._connection_factory = connection_factory or self._connect
        prefix = os.getenv("BUDGET_PG_TABLE_PREFIX", "")
        if prefix and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prefix):
            raise ValueError("BUDGET_PG_TABLE_PREFIX 格式无效")
        self._observation_table = f"{prefix}model_price_source_observations"
        self._run_table = f"{prefix}model_price_monitor_runs"
        self._price_table = f"{prefix}model_price"

    @staticmethod
    @contextmanager
    def _connect():
        from backend.infra.db import engine_for

        conn = engine_for(MEMORY_DB_CONFIG).raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_and_compare(
        self,
        quotes: list[PriceQuote],
        *,
        observed_on: date,
        now: datetime | None = None,
    ) -> PriceComparison:
        if not quotes:
            return PriceComparison(0, [], [], [])
        current_time = now or datetime.now(timezone.utc)
        filters = " OR ".join(
            "(model_name = %s AND component = %s AND dimension = %s)"
            for _ in quotes
        )
        filter_params = [
            value
            for quote in quotes
            for value in (quote.model_name, quote.component, quote.dimension)
        ]
        with self._connection_factory() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""SELECT DISTINCT ON (model_name, component, dimension)
                                model_name, component, dimension, price_per_unit,
                                unit, currency, price_table_version
                         FROM {self._price_table}
                         WHERE approval_status = 'approved'
                           AND effective_from <= %s
                           AND (effective_to IS NULL OR effective_to > %s)
                           AND ({filters})
                         ORDER BY model_name, component, dimension,
                                  effective_from DESC, id DESC""",
                    [current_time, current_time, *filter_params],
                )
                current_prices = {
                    (row["model_name"], row["component"], row["dimension"]): row
                    for row in cur.fetchall()
                }
                recorded_count = 0
                source_changes: list[dict[str, Any]] = []
                mismatches: list[dict[str, Any]] = []
                prices: list[dict[str, Any]] = []
                for quote in quotes:
                    key = (quote.model_name, quote.component, quote.dimension)
                    cur.execute(
                        f"""SELECT price_per_unit, unit, currency, observed_on
                             FROM {self._observation_table}
                             WHERE source_id = %s AND model_name = %s
                               AND component = %s AND dimension = %s
                               AND observed_on < %s
                             ORDER BY observed_on DESC LIMIT 1""",
                        (quote.source_id, *key, observed_on),
                    )
                    previous = cur.fetchone()
                    cur.execute(
                        f"""INSERT INTO {self._observation_table}
                             (source_id, model_name, component, dimension,
                              price_per_unit, unit, currency, source_url,
                              source_updated_at, content_sha256, observed_on)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                             ON CONFLICT (source_id, model_name, component,
                                          dimension, observed_on) DO NOTHING
                             RETURNING source_id""",
                        (
                            quote.source_id, quote.model_name, quote.component,
                            quote.dimension, quote.price_per_unit, quote.unit,
                            quote.currency, quote.source_url,
                            quote.source_updated_at, quote.content_sha256, observed_on,
                        ),
                    )
                    inserted = cur.fetchone() is not None
                    if inserted:
                        recorded_count += 1
                    if inserted and _price_changed(previous, quote):
                        source_changes.append({
                            **_quote_key(quote),
                            "previous": _price_value(previous),
                            "official": _price_value(quote),
                            "source_url": quote.source_url,
                        })
                    system_price = current_prices.get(key)
                    price_comparison = _price_comparison(system_price, quote)
                    matches_system = price_comparison["matches_system"]
                    prices.append({
                        **_quote_key(quote),
                        "source_id": quote.source_id,
                        "source_url": quote.source_url,
                        "source_updated_at": quote.source_updated_at,
                        "content_sha256": quote.content_sha256,
                        "official": _price_value(quote),
                        "system": (
                            _price_value(system_price)
                            if system_price is not None else None
                        ),
                        **price_comparison,
                        "matches_system": matches_system,
                    })
                    if not matches_system:
                        mismatches.append({
                            **_quote_key(quote),
                            "official": _price_value(quote),
                            "system": (
                                _price_value(system_price)
                                if system_price is not None else None
                            ),
                            **price_comparison,
                            "source_url": quote.source_url,
                        })
        return PriceComparison(recorded_count, source_changes, mismatches, prices)

    def start_run(
        self,
        run_id: str,
        *,
        observed_on: date,
        started_at: datetime,
        sources_total: int,
    ) -> None:
        """在抓取前写入运行中记录，Worker 异常退出后仍可被管理端发现。"""
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""INSERT INTO {self._run_table}
                             (run_id, observed_on, status, started_at, sources_total)
                             VALUES (%s::uuid, %s, 'running', %s, %s)""",
                        (run_id, observed_on, started_at, sources_total),
                    )
        except Exception as exc:
            raise PriceMonitorStorageUnavailable("无法创建巡检运行记录") from exc

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime,
        sources_failed: int,
        quotes_recorded: int,
        source_changes: int,
        system_mismatches: int,
        source_results: list[dict[str, Any]],
        comparison_results: dict[str, Any],
        alert_events: list[dict[str, Any]],
        error_type: str | None = None,
    ) -> None:
        """保存本次运行的最终状态、来源结果、差异与告警记录。"""
        if status not in {"succeeded", "partial", "failed"}:
            raise ValueError("巡检最终状态无效")
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""UPDATE {self._run_table}
                             SET status = %s, finished_at = %s,
                                 sources_failed = %s, quotes_recorded = %s,
                                 source_changes = %s, system_mismatches = %s,
                                 source_results = %s::jsonb,
                                 comparison_results = %s::jsonb,
                                 alert_events = %s::jsonb, error_type = %s
                             WHERE run_id = %s::uuid""",
                        (
                            status, finished_at, sources_failed, quotes_recorded,
                            source_changes, system_mismatches,
                            json.dumps(source_results, ensure_ascii=False),
                            json.dumps(comparison_results, ensure_ascii=False),
                            json.dumps(alert_events, ensure_ascii=False),
                            error_type, run_id,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError("巡检运行记录不存在")
        except Exception as exc:
            raise PriceMonitorStorageUnavailable("无法更新巡检运行记录") from exc

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        """返回管理端只读所需的最近运行记录，不暴露页面原文或凭据。"""
        if not 1 <= limit <= 100:
            raise ValueError("巡检历史条数超出范围")
        try:
            with self._connection_factory() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"""SELECT run_id, observed_on, status, started_at,
                                   finished_at, sources_total, sources_failed,
                                   quotes_recorded, source_changes, system_mismatches,
                                   source_results, comparison_results, alert_events,
                                   error_type
                            FROM {self._run_table}
                            ORDER BY started_at DESC, run_id DESC
                            LIMIT %s""",
                        (limit,),
                    )
                    rows = cur.fetchall()
        except Exception as exc:
            raise PriceMonitorStorageUnavailable("无法读取巡检运行记录") from exc
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["run_id"] = str(item["run_id"])
            for key in ("observed_on", "started_at", "finished_at"):
                if item[key] is not None:
                    item[key] = item[key].isoformat()
            items.append(item)
        return items


def _quote_key(quote: PriceQuote) -> dict[str, str]:
    return {
        "model_name": quote.model_name,
        "component": quote.component,
        "dimension": quote.dimension,
    }


def _price_value(value: Any) -> dict[str, str]:
    if isinstance(value, PriceQuote):
        return {
            "price_per_unit": str(value.price_per_unit),
            "unit": value.unit,
            "currency": value.currency,
        }
    return {
        "price_per_unit": str(value["price_per_unit"]),
        "unit": str(value["unit"]),
        "currency": str(value["currency"]),
        "price_table_version": str(value.get("price_table_version", "")),
    }


def _same_price(system_price: Any, quote: PriceQuote) -> bool:
    return bool(_price_comparison(system_price, quote)["matches_system"])


def _price_comparison(system_price: Any, quote: PriceQuote) -> dict[str, Any]:
    """按计费共用的 CNY 汇率折算，并尊重系统价格列的精度。"""
    official_cny = to_base_currency(quote.price_per_unit, quote.currency)
    official_fx = (
        Decimal("1") if quote.currency.upper() == BUDGET_BASE_CURRENCY
        else BUDGET_FX_USD_CNY
    )
    if system_price is None:
        return {
            "comparison_currency": BUDGET_BASE_CURRENCY,
            "official_price_cny": str(official_cny),
            "system_price_cny": None,
            "official_fx_to_cny": str(official_fx),
            "system_fx_to_cny": None,
            "comparison_tolerance_cny": None,
            "matches_system": False,
        }

    system_currency = str(system_price["currency"]).strip().upper()
    try:
        system_cny = to_base_currency(
            Decimal(str(system_price["price_per_unit"])), system_currency
        )
    except ValueError:
        return {
            "comparison_currency": BUDGET_BASE_CURRENCY,
            "official_price_cny": str(official_cny),
            "system_price_cny": None,
            "official_fx_to_cny": str(official_fx),
            "system_fx_to_cny": None,
            "comparison_tolerance_cny": None,
            "comparison_error": "unsupported_currency",
            "matches_system": False,
        }
    system_fx = (
        Decimal("1") if system_currency == BUDGET_BASE_CURRENCY
        else BUDGET_FX_USD_CNY
    )
    tolerance_cny = _MODEL_PRICE_STORAGE_HALF_QUANTUM * system_fx
    matches = (
        system_price["unit"] == quote.unit
        and abs(system_cny - official_cny) <= tolerance_cny
    )
    return {
        "comparison_currency": BUDGET_BASE_CURRENCY,
        "official_price_cny": str(official_cny),
        "system_price_cny": str(system_cny),
        "official_fx_to_cny": str(official_fx),
        "system_fx_to_cny": str(system_fx),
        "comparison_tolerance_cny": str(tolerance_cny),
        "matches_system": matches,
    }


def _price_changed(previous: Any, quote: PriceQuote) -> bool:
    if previous is None:
        return False
    return (
        Decimal(str(previous["price_per_unit"])) != quote.price_per_unit
        or previous["unit"] != quote.unit
        or previous["currency"] != quote.currency
    )


__all__ = [
    "PriceComparison",
    "PriceQuote",
    "PostgresPricePageMonitorRepository",
    "fetch_official_prices",
    "parse_ark_prices",
    "parse_embedding_price",
    "parse_rerank_price",
]
