"""tasks/side_effect_probe_tasks.py — Step6 实机验收探针（仅测试环境启用）。

给 T1-T8 提供一个「可从 DB 明确 COUNT 的安全测试动作」（Step6 §三十八）：
ai.side_effect_probe 每次“真实执行”插一行 (probe_key, execution_id)，
天然不幂等——因此行数就是真实副作用计数，能证明
「投递/执行次数 > 1 而真实副作用 = 1」这一 Step6 核心证据。

安全边界：
- SIDE_EFFECT_PROBE_ENABLED 未开启时直接返回 DISABLED，不碰任何表；
- crash 注入（SIDE_EFFECT_TEST_CRASH_AFTER_EFFECT / _BEFORE_EFFECT）与
  瞬时失败注入（SIDE_EFFECT_TEST_FAIL_ONCE）默认全关，仅验收窗口由
  测试脚本设置；
- 不写业务表、不发外部请求、不走任务运行时租约/admission；经
  maintenance 队列显式投递，只在 queue_router 登记路由，不改任何
  既有 workload 拓扑（Step6 §4.1/4.3）。
"""
from __future__ import annotations

import json
import os

from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app

__all__ = ["ProbeTransientError", "execute_side_effect_probe"]

_ENABLED_VALUES = ("1", "true", "yes")


def _flag_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _ENABLED_VALUES


def _memory_dsn() -> str:
    from backend.config.database import MEMORY_DB_CONFIG

    config = MEMORY_DB_CONFIG
    return (
        f"postgresql://{config['user']}:{config['password']}"
        f"@{config['host']}:{config['port']}/{config['dbname']}"
    )


def _probe_row_exists(probe_key: str) -> bool:
    import psycopg

    with psycopg.connect(_memory_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM ai.side_effect_probe WHERE probe_key = %s LIMIT 1",
            (probe_key,),
        )
        exists = cur.fetchone() is not None
        conn.rollback()
    return exists


def _insert_probe_row(probe_key: str, execution_id: str) -> None:
    """真实副作用：每次执行插一行——天然不幂等的可数动作。"""
    import psycopg

    with psycopg.connect(_memory_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai.side_effect_probe (probe_key, execution_id, payload)
            VALUES (%s, %s, %s)
            """,
            (probe_key, execution_id, json.dumps({"probe": True})),
        )


class ProbeTransientError(RuntimeError):
    """T6 用：模拟一次可重试的瞬时失败（副作用确定未发生）。"""


@celery_app.task(
    name="tasks.side_effect_probe",
    bind=True,
    acks_late=True,
    autoretry_for=(ProbeTransientError,),
    retry_kwargs={"max_retries": 3},
    retry_backoff=1,
)
def execute_side_effect_probe(
    self,
    probe_key: str,
    *,
    tenant_id: str = "default",
    actor_id: str = "step6_probe",
) -> dict:
    """探针任务：经 PG durable ledger 保护的可数副作用。

    delivery id（每次投递换发）当 owner execution；logical key =
    (tenant, actor, 'probe.effect', probe_key) 跨 retry/recovery 稳定。
    """
    if not _flag_on("SIDE_EFFECT_PROBE_ENABLED"):
        return {"status": "DISABLED"}

    from backend.shared import idempotency as idem

    execution_id = str(getattr(self.request, "id", "") or "")
    crash_lease = int(os.getenv("SIDE_EFFECT_PROBE_LEASE_SECONDS", "300"))

    def _effect() -> dict:
        if _flag_on("SIDE_EFFECT_TEST_CRASH_BEFORE_EFFECT"):
            # 模拟 claim 之后、副作用之前 crash（T5）
            os._exit(71)
        if (_flag_on("SIDE_EFFECT_TEST_FAIL_ONCE")
                and not _probe_row_exists(probe_key)):
            # 模拟可重试瞬时失败：副作用未发生，ledger 标 FAILED（T6）
            raise ProbeTransientError(f"transient failure for {probe_key}")
        _insert_probe_row(probe_key, execution_id)
        if _flag_on("SIDE_EFFECT_TEST_CRASH_AFTER_EFFECT"):
            # 模拟副作用成功之后、终态写回之前 crash（T4 关键窗口）
            os._exit(70)
        return {"probe_key": probe_key, "delivery": execution_id}

    try:
        result = idem.run_idempotent_side_effect(
            "probe.effect",
            {"probe_key": probe_key},
            _effect,
            tenant_id=tenant_id,
            actor_id=actor_id,
            client_key=probe_key,
            owner_execution_id=execution_id,
            lease_seconds=crash_lease,
        )
    except idem.IdempotencyConflict as exc:
        # 并发重入被拒（RUNNING）：真实副作用未发生，按阻塞返回
        logger.warning("[SideEffectProbe] %s blocked: %s", probe_key, exc)
        return {"status": "BLOCKED", "reason": str(exc)}
    except idem.IdempotencyUnavailable as exc:
        # IN_DOUBT（crash 窗口，副作用是否发生未知）：拒绝重执行，
        # 保持阻塞等待 resolve_stale_side_effect 人工裁决
        logger.error("[SideEffectProbe] %s in-doubt: %s", probe_key, exc)
        return {"status": "IN_DOUBT_BLOCKED", "reason": str(exc)}
    return {"status": "SUCCESS", **result}
