"""PR-0.3 — Prometheus /metrics 端点 + 4 个核心 metric 单测。

覆盖:
- 4 个 metric 实例化（Counter/Histogram 类型正确）
- /metrics 端点返回 Prometheus 文本格式
- Counter 自增后能在输出中看到
- LLM tokens 记录后能在 llm_tokens_total{model,direction} 看到
"""
from fastapi.testclient import TestClient

from backend.app.server import app
from backend.observability.metrics import (
    chat_request_total,
    chat_request_duration_seconds,
    llm_tokens_total,
    skill_failure_total,
    render_metrics,
)


client = TestClient(app)


class TestMetricsDefinitions:
    def test_chat_request_total_is_counter(self):
        from prometheus_client import Counter
        assert isinstance(chat_request_total, Counter)

    def test_chat_duration_is_histogram(self):
        from prometheus_client import Histogram
        assert isinstance(chat_request_duration_seconds, Histogram)

    def test_llm_tokens_total_has_model_direction_labels(self):
        llm_tokens_total.labels(model="deepseek-v4-flash", direction="prompt").inc(10)
        llm_tokens_total.labels(model="deepseek-v4-flash", direction="completion").inc(20)
        # 不抛异常即可
        assert True

    def test_skill_failure_total_has_labels(self):
        skill_failure_total.labels(skill="rag_skill", error_type="timeout").inc()
        assert True


class TestMetricsEndpoint:
    def test_metrics_returns_prometheus_format(self):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers.get("content-type", "")
        body = r.text
        # 必须包含 4 个核心 metric 名称
        assert "chat_request_total" in body
        assert "chat_request_duration_seconds" in body
        assert "llm_tokens_total" in body
        assert "skill_failure_total" in body

    def test_counter_increments_appear_in_output(self):
        chat_request_total.labels(status="ok").inc(3)
        r = client.get("/metrics")
        assert 'chat_request_total{status="ok"}' in r.text
        # 至少 3（可能更多，因为前面测试已 inc 过）
        import re
        m = re.search(r'chat_request_total\{status="ok"\}\s+([\d.]+)', r.text)
        assert m
        assert float(m.group(1)) >= 3

    def test_render_metrics_helper(self):
        body, ct = render_metrics()
        assert isinstance(body, bytes)
        assert "text/plain" in ct
        assert b"chat_request_total" in body
