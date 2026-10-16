"""公开模型价格页解析、变化比较和 Celery 调度契约。"""
from decimal import Decimal

import pytest

from backend.infra.llm.price_page_monitor import (
    _price_changed,
    _same_price,
    parse_ark_prices,
    parse_embedding_price,
    parse_rerank_price,
)


def test_ark_parser_selects_regular_beijing_tier_and_cached_input() -> None:
    page = """
    <time datetime="2026-10-15T06:13:32.000Z">2026-10-15</time>
    <h3>在线推理（常规）</h3>
    <table><tr><th>模型名称</th><th>条件</th><th>输入</th><th>音频</th>
      <th>缓存存储</th><th>缓存命中</th><th>缓存音频</th><th>输出</th></tr>
      <tr><td rowspan="3">doubao-seed-2.0-mini</td>
        <td>输入长度 [0, 32]</td><td>0.2</td><td>-</td><td>0.017</td>
        <td>0.04</td><td>-</td><td>2.0</td></tr></table>
    <h3>在线推理（低延迟）</h3>
    <table><tr><td>doubao-seed-2.0-mini</td><td>输入长度 [0, 32]</td>
      <td>0.4</td><td>-</td><td>0.017</td><td>0.08</td><td>-</td>
      <td>4.0</td></tr></table>
    """

    quotes = parse_ark_prices(page, content_sha256="a" * 64)

    assert [(quote.dimension, quote.price_per_unit) for quote in quotes] == [
        ("input", Decimal("0.2")),
        ("cache_read", Decimal("0.04")),
        ("output", Decimal("2.0")),
    ]
    assert all(quote.currency == "CNY" for quote in quotes)
    assert all(quote.unit == "per_1m_tokens" for quote in quotes)
    assert quotes[0].source_updated_at == "2026-10-15T06:13:32.000Z"


def test_embedding_parser_uses_standard_sync_price_not_batch_discount() -> None:
    page = """
    <h3>Text embedding</h3><h4>Beijing</h4>
    <table><tr><th>Model</th><th>Dimensions</th><th>Batch</th>
      <th>Max tokens</th><th>Price</th></tr>
      <tr><td>qwen3.7-text-embedding</td><td>1024</td><td>20</td>
        <td>128000</td><td><p>CNY 0.0005</p>
        <p>batch API call: CNY 0.00025</p></td></tr></table>
    <h4>Singapore</h4><table><tr><td>qwen3.7-text-embedding</td>
      <td>1024</td><td>20</td><td>128000</td><td>CNY 0.000525</td></tr></table>
    """

    quote = parse_embedding_price(page, content_sha256="b" * 64)[0]

    assert quote.price_per_unit == Decimal("0.5000")
    assert quote.currency == "CNY"
    assert quote.component == "embedding"


def test_rerank_parser_uses_beijing_per_million_price() -> None:
    page = """
    <h1>qwen3.7-text-rerank Model Info</h1>
    <h2>Pricing</h2><h3>China (Beijing)</h3>
    <table><tr><th>Billing Item</th><th>Price (CNY)</th><th>Unit</th></tr>
      <tr><td><p>Text Input</p></td><td><p>0.5</p></td>
      <td><p>Per 1M tokens</p></td></tr></table>
    <h3>Singapore</h3><table><tr><td>Text Input</td><td>0.6</td>
      <td>Per 1M tokens</td></tr></table>
    """

    quote = parse_rerank_price(page, content_sha256="c" * 64)[0]

    assert quote.price_per_unit == Decimal("0.5")
    assert quote.currency == "CNY"
    assert quote.dimension == "input"


def test_page_parser_fails_closed_when_expected_price_is_missing() -> None:
    with pytest.raises(ValueError, match="价格行缺失或不唯一"):
        parse_rerank_price("<h2>Pricing</h2><p>not a price table</p>",
                           content_sha256="d" * 64)


def test_page_fetch_failures_use_the_same_ids_as_persisted_sources() -> None:
    from requests import Timeout

    from backend.infra.llm.price_page_monitor import fetch_official_prices

    class _FailingClient:
        def get(self, *_args, **_kwargs):
            raise Timeout("isolated timeout")

    quotes, failures = fetch_official_prices(_FailingClient())

    assert quotes == []
    assert {item["source_id"] for item in failures} == {
        "volcengine_ark_doubao_seed_2_0_mini",
        "aliyun_qwen3_7_text_embedding",
        "aliyun_qwen3_7_text_rerank",
    }


def test_page_fetch_rejects_untrusted_redirect_before_following_it() -> None:
    from backend.infra.llm.price_page_monitor import fetch_official_prices

    class _RedirectResponse:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/internal"}

        def __init__(self, url):
            self.url = url
            self.closed = False

        def close(self):
            self.closed = True

    class _Client:
        def __init__(self):
            self.calls = []
            self.responses = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            response = _RedirectResponse(url)
            self.responses.append(response)
            return response

    client = _Client()
    quotes, failures = fetch_official_prices(client)

    assert quotes == []
    assert len(client.calls) == 3
    assert all(call[1]["allow_redirects"] is False for call in client.calls)
    assert all(call[1]["stream"] is True for call in client.calls)
    assert all(response.closed for response in client.responses)
    assert {item["error_type"] for item in failures} == {"ValueError"}


def test_page_fetch_stops_streaming_as_soon_as_size_limit_is_exceeded() -> None:
    from backend.infra.llm.price_page_monitor import (
        MAX_PAGE_BYTES,
        _fetch_official_page,
    )

    class _OversizedResponse:
        status_code = 200
        headers = {}
        encoding = "utf-8"

        def __init__(self, url):
            self.url = url
            self.chunks_seen = 0
            self.closed = False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            while True:
                self.chunks_seen += 1
                yield b"x" * chunk_size

        def close(self):
            self.closed = True

    class _Client:
        response = None

        def get(self, url, **kwargs):
            self.response = _OversizedResponse(url)
            return self.response

    from backend.infra.llm.price_page_monitor import ARK_URL

    client = _Client()
    with pytest.raises(ValueError, match="大小上限"):
        _fetch_official_page(
            client,
            source_id="volcengine_ark_doubao_seed_2_0_mini",
            url=ARK_URL,
            headers={},
        )

    assert client.response.chunks_seen == MAX_PAGE_BYTES // (64 * 1024) + 1
    assert client.response.closed


def test_comparison_detects_currency_and_amount_mismatches() -> None:
    quote = parse_rerank_price(
        "<h2>Pricing</h2><h3>China (Beijing)</h3><table>"
        "<tr><td>Text Input</td><td>0.5</td><td>Per 1M tokens</td></tr>"
        "</table>",
        content_sha256="e" * 64,
    )[0]

    exact_system_price = {
        "price_per_unit": Decimal("0.500000"),
        "unit": "per_1m_tokens",
        "currency": "CNY",
    }
    wrong_currency = {**exact_system_price, "currency": "USD"}
    wrong_amount = {**exact_system_price, "price_per_unit": Decimal("0.4")}

    assert _same_price(exact_system_price, quote)
    assert not _same_price(wrong_currency, quote)
    assert not _same_price(wrong_amount, quote)
    usd_system_price = {
        "price_per_unit": Decimal("0.069444"),
        "unit": "per_1m_tokens",
        "currency": "USD",
    }
    assert _same_price(usd_system_price, quote)
    usd_wrong_amount = {**usd_system_price, "price_per_unit": Decimal("0.069440")}
    assert not _same_price(usd_wrong_amount, quote)
    assert _price_changed(exact_system_price, quote) is False
    assert _price_changed(wrong_currency, quote) is True


def test_monitor_task_is_daily_and_registered_to_report_queue() -> None:
    import backend.tasks.model_price_monitor_tasks  # noqa: F401
    from backend.tasks.celery_app import celery_app
    from backend.tasks.queue_router import resolve_for_celery_task

    task_name = "model.price_page_monitor"
    entry = celery_app.conf.beat_schedule["model-price-page-monitor"]
    route = resolve_for_celery_task(task_name)

    assert task_name in celery_app.tasks
    assert entry["task"] == task_name
    assert entry["schedule"].hour == {8}
    assert entry["schedule"].minute == {0}
    assert celery_app.conf.timezone == "Asia/Shanghai"
    assert entry["options"]["queue"] == route.physical_queue
    assert route.workload_class == "report"


def test_monitor_migration_is_registered_for_memory_database() -> None:
    from scripts.init_db import MIGRATION_TARGETS

    assert MIGRATION_TARGETS["099_model_price_source_monitor.sql"] == "memory"


def test_monitor_task_persists_partial_source_results_and_alert_events(
    monkeypatch,
) -> None:
    from backend.infra.llm.price_page_monitor import PriceComparison
    from backend.tasks import model_price_monitor_tasks as task_module

    class _Repository:
        def __init__(self):
            self.started = None
            self.finished = None

        def start_run(self, run_id, **kwargs):
            self.started = (run_id, kwargs)

        def record_and_compare(self, quotes, **kwargs):
            assert len(quotes) == 2
            return PriceComparison(
                recorded_count=1,
                source_changes=[],
                system_mismatches=[{"model_name": "qwen3.7-text-rerank"}],
                prices=[{"model_name": "qwen3.7-text-rerank", "matches_system": False}],
            )

        def finish_run(self, run_id, **kwargs):
            self.finished = (run_id, kwargs)

    quote = parse_rerank_price(
        "<h2>Pricing</h2><h3>China (Beijing)</h3><table>"
        "<tr><td>Text Input</td><td>0.5</td><td>Per 1M tokens</td></tr>"
        "</table>",
        content_sha256="f" * 64,
    )[0]
    from backend.infra.llm.price_page_monitor import PriceQuote

    ark_quote = PriceQuote(
        "volcengine_ark_doubao_seed_2_0_mini", "doubao-seed-2.0-mini",
        "llm", "input", Decimal("0.2"), "per_1m_tokens", "CNY",
        "https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing",
        "", "e" * 64,
    )
    repo = _Repository()
    alerts = []
    monkeypatch.setattr(task_module, "PostgresPricePageMonitorRepository", lambda: repo)
    monkeypatch.setattr(task_module, "fetch_official_prices", lambda: (
        [quote, ark_quote],
        [{"source_id": "aliyun_qwen3_7_text_embedding", "error_type": "Timeout"}],
    ))
    monkeypatch.setattr(
        "backend.observability.alerts.log_degradation",
        lambda alert: alerts.append(alert.code),
    )

    result = task_module.monitor_model_price_pages()

    assert result["status"] == "partial"
    assert result["sources_failed"] == 1
    assert repo.started[0] == result["run_id"]
    assert repo.finished[0] == result["run_id"]
    assert repo.finished[1]["status"] == "partial"
    assert repo.finished[1]["source_results"][0]["name"]
    result_prices = repo.finished[1]["comparison_results"]["prices"]
    assert result_prices[0]["matches_system"] is False
    assert set(alerts) == {
        "MODEL_PRICE_SOURCE_UNAVAILABLE", "MODEL_PRICE_SYSTEM_MISMATCH",
    }


def test_all_sources_failed_persists_failure_and_fails_celery_task(monkeypatch):
    from backend.infra.llm.price_page_monitor import PriceComparison
    from backend.tasks import model_price_monitor_tasks as task_module

    class _Repository:
        def __init__(self):
            self.finished = None

        def start_run(self, *_args, **_kwargs):
            pass

        def record_and_compare(self, quotes, **_kwargs):
            assert quotes == []
            return PriceComparison(0, [], [], [])

        def finish_run(self, run_id, **kwargs):
            self.finished = (run_id, kwargs)

    repo = _Repository()
    alerts = []
    monkeypatch.setattr(task_module, "PostgresPricePageMonitorRepository", lambda: repo)
    monkeypatch.setattr(task_module, "fetch_official_prices", lambda: ([], [
        {"source_id": source_id, "error_type": "Timeout"}
        for source_id in task_module._SOURCES
    ]))
    monkeypatch.setattr(
        "backend.observability.alerts.log_degradation",
        lambda alert: alerts.append(alert.code),
    )

    with pytest.raises(task_module.ModelPriceMonitorExecutionFailed):
        task_module.monitor_model_price_pages()

    assert repo.finished[1]["status"] == "failed"
    assert repo.finished[1]["error_type"] == "NoPriceQuotes"
    assert "MODEL_PRICE_SOURCE_UNAVAILABLE" in alerts
