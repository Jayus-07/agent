"""APISIX 系统直通面 B4 收口契约（审计 P1-4，2026-11-03）。

病史：/metrics、/openapi.json、/ 三条网关直通路由自 B1 起无鉴权暴露——
凡 9080 对外可达的部署，任何人可拉取 Prometheus 指标（含路由清单、错误
分布）与完整 OpenAPI 接口面（攻击侦察）。配置自注释「B4 复核收口」悬置
至今。本文件钉死收口后的网关面契约，防止配置回退：

  1. /metrics、/openapi.json、/ 直通路由不存在（网关侧 404）；
  2. /health 保留直通（内容不含敏感配置，供外部探活；容器 healthcheck
     走容器内 127.0.0.1:8000，不依赖网关）；
  3. 兜底 /api/* 仍挂 gateway-auth（鉴权兜底不被顺路放松）。

消费方事实（移除依据，勿凭记忆改回）：
  - Prometheus 抓 agent-platform-app 走 host.docker.internal:8000（docker/prometheus.yml）；
  - 容器 healthcheck 走容器内 127.0.0.1:8000/health（docker-compose.yml）；
  - openapi.json 仅开发直连调试用，前端 BFF 只代理 /api/*。
"""
from __future__ import annotations

from pathlib import Path

import yaml


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parents[3] / "apisix" / "apisix.yaml"
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def test_removed_direct_routes_are_absent():
    config = _load_config()
    ids = {r["id"] for r in config["routes"]}
    uris = {r["uri"] for r in config["routes"]}

    assert "sys-metrics" not in ids, "/metrics 直通不得回退（侦察面，审计 P1-4）"
    assert "sys-openapi" not in ids, "/openapi.json 直通不得回退（侦察面）"
    assert "sys-index" not in ids, "/ 直通不得回退"
    assert "/metrics" not in uris and "/openapi.json" not in uris and "/" not in uris


def test_health_direct_route_is_retained_for_probes():
    config = _load_config()
    health = [r for r in config["routes"] if r["id"] == "sys-health"]

    assert len(health) == 1, "/health 直通须保留（外部探活，内容不含敏感配置）"
    assert health[0]["uri"] == "/health"
    assert health[0]["upstream_id"] == "app"


def test_api_fallback_still_requires_gateway_auth():
    config = _load_config()
    fallback = next(r for r in config["routes"] if r["id"] == "app-fallback")

    assert fallback["uri"] == "/api/*"
    assert "gateway-auth" in fallback["plugins"], "兜底路由鉴权不得放松"
