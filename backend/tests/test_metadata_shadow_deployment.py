"""元数据影子队列的 Compose 部署契约。"""
from __future__ import annotations

from pathlib import Path

import yaml


COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _queue_arg(command: list[str]) -> str:
    index = command.index("-Q")
    return command[index + 1]


def _environment(service: dict) -> dict[str, str]:
    return {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in service.get("environment", [])
        if "=" in item
    }


def test_app_and_index_worker_default_to_one_percent_cascade_canary():
    services = _compose()["services"]

    for service_name in ("app", "worker"):
        environment = _environment(services[service_name])
        assert environment["METADATA_CASCADE_ENABLED"] == (
            "${METADATA_CASCADE_ENABLED:-true}"
        )
        assert environment["METADATA_CASCADE_ROLLOUT_PERCENT"] == (
            "${METADATA_CASCADE_ROLLOUT_PERCENT:-1}"
        )
        assert environment["METADATA_CLASSIFIER_ENABLED"] == (
            "${METADATA_CLASSIFIER_ENABLED:-false}"
        )


def test_primary_worker_does_not_consume_shadow_queue():
    worker = _compose()["services"]["worker"]

    assert _queue_arg(worker["command"]) == "agent,rag_index"
    assert "rag_metadata_shadow" not in worker["command"]


def test_shadow_worker_isolated_on_shadow_queue():
    shadow_worker = _compose()["services"]["metadata-shadow-worker"]
    command = shadow_worker["command"]

    assert _queue_arg(command) == "${CELERY_METADATA_SHADOW_QUEUE:-rag_metadata_shadow}"
    assert "agent" not in command
    assert "rag_index" not in command
    assert "--concurrency=${METADATA_SHADOW_WORKER_CONCURRENCY:-2}" in command
    assert "postgres" in shadow_worker["depends_on"]
    assert "redis" in shadow_worker["depends_on"]
    assert "app_cache:/app/.cache" in shadow_worker["volumes"]
