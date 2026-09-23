"""元数据影子队列的 Compose 部署契约。"""
from __future__ import annotations

from pathlib import Path

import yaml


COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"
INIT_DBS_PATH = Path(__file__).resolve().parents[2] / "docker" / "init-dbs.sh"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _queue_arg(command: list[str]) -> str:
    index = command.index("-Q")
    return command[index + 1]


def _environment(service: dict) -> dict[str, str]:
    # Phase2 Step5：worker environment 改为 mapping 形式（支撑 YAML anchor
    # 合并），兼容旧的 list（KEY=VALUE）形式。
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    return {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in env
        if "=" in item
    }


def test_fresh_database_init_includes_metadata_migrations():
    """全新部署能建出 metadata 三表。

    2026-09-21 起 docker/init-dbs.sh 手抄清单退役，迁移事实源唯一化为
    scripts/init_db.py（compose 的 db-migrate one-shot 服务执行）——守护
    对象从 init-dbs.sh 文本改为 init_db.py 的 MIGRATION_TARGETS 登记。
    """
    init_db_path = INIT_DBS_PATH.parent.parent / "scripts" / "init_db.py"
    source = init_db_path.read_text(encoding="utf-8")

    for migration in (
        "025_metadata_rule_governance.sql",
        "026_metadata_shadow_jobs.sql",
        "027_rag_processing_lineage.sql",
    ):
        assert f'"{migration}": "memory"' in source, f"{migration} 未登记到 memory 库"
    # 数字顺序保证依赖次序（025 → 026 → 027）
    assert source.index("025_metadata_rule_governance.sql") < source.index(
        "026_metadata_shadow_jobs.sql"
    )
    assert source.index("026_metadata_shadow_jobs.sql") < source.index(
        "027_rag_processing_lineage.sql"
    )


def test_app_and_index_worker_default_to_full_dev_cascade_without_shadow():
    services = _compose()["services"]

    # Phase2 Step5：索引执行体拆至 rag-index-worker（agent-worker 保留
    # cascade 指针供 agent 图内 RAG 检索使用）。
    for service_name in ("app", "agent-worker", "rag-index-worker"):
        environment = _environment(services[service_name])
        assert environment["METADATA_CASCADE_ENABLED"] == (
            "${METADATA_CASCADE_ENABLED:-true}"
        )
        assert environment["METADATA_CASCADE_ROLLOUT_PERCENT"] == (
            "${METADATA_CASCADE_ROLLOUT_PERCENT:-100}"
        )
        assert environment["METADATA_CLASSIFIER_ENABLED"] == (
            "${METADATA_CLASSIFIER_ENABLED:-false}"
        )
        assert environment["METADATA_CLASSIFIER_MODEL_PATH"] == (
            "${METADATA_CLASSIFIER_MODEL_PATH:-/app/data/models/metadata_lr/lr_model_dryrun.joblib}"
        )
        assert environment["METADATA_CASCADE_SHADOW_ENABLED"] == (
            "${METADATA_CASCADE_SHADOW_ENABLED:-false}"
        )
        assert environment["METADATA_SHADOW_QUEUE_ENABLED"] == (
            "${METADATA_SHADOW_QUEUE_ENABLED:-false}"
        )


def test_primary_worker_does_not_consume_shadow_queue():
    # Phase2 Step5：主 worker 更名 agent-worker 且收窄为仅消费 agent；
    # rag_index 也已拆独立池，均不消费 shadow 队列。
    worker = _compose()["services"]["agent-worker"]

    assert _queue_arg(worker["command"]) == "${CELERY_AGENT_QUEUE:-agent}"
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
