"""APISIX 客服 WebSocket 路由配置契约。"""
from __future__ import annotations

from pathlib import Path

import yaml


def test_cs_ws_route_explicitly_enables_websocket():
    config_path = Path(__file__).resolve().parents[3] / "apisix" / "apisix.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    route = next(item for item in config["routes"] if item["id"] == "cs-ws")

    assert route["uri"] == "/ws/cs/*"
    assert route["enable_websocket"] is True
    assert "gateway-auth" not in route.get("plugins", {})
