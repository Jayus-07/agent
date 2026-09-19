"""元数据级联灰度 Prometheus 告警契约。"""
from __future__ import annotations

from pathlib import Path

import yaml


RULES_PATH = Path(__file__).resolve().parents[2] / "docker" / "prometheus-alert-rules.yml"
REQUIRED_ALERTS = {
    "MetadataCascadeFallbackRateHigh",
    "MetadataShadowFailureRateHigh",
    "MetadataShadowBackpressure",
    "MetadataShadowQueueAgeHigh",
    "MetadataResourceWaitTimeout",
    "MetadataRouteError",
    "MetadataClassifierMismatch",
}


def _metadata_group() -> dict:
    document = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    return next(
        group
        for group in document["groups"]
        if group["name"] == "agent-platform-metadata-canary"
    )


def test_metadata_canary_group_has_all_stop_signals():
    group = _metadata_group()
    rules = {rule.get("alert"): rule for rule in group["rules"] if "alert" in rule}

    assert REQUIRED_ALERTS <= rules.keys()
    for name in REQUIRED_ALERTS:
        assert rules[name]["for"]
        assert rules[name]["labels"]["severity"] in {"warning", "critical"}


def test_metadata_canary_group_records_failure_and_fallback_rates():
    group = _metadata_group()
    recording_names = {
        rule.get("record") for rule in group["rules"] if "record" in rule
    }

    assert "metadata:shadow_failure_rate:ratio5m" in recording_names
    assert "metadata:legacy_fallback_rate:ratio5m" in recording_names
