"""评测运行操作审计（C2-6/REL-09）。

取消、强制重跑、熔断触发等运行期操作统一落 ``ai.task_operation_audits``
（migration 060 / M10 建表，agent_memory 库），与 admin 任务审计同表——
「谁在什么时候对哪个对象做过什么」一张表可查。

口径对齐（表有 CHECK 约束，operation 只允许六值）：
- 取消           → operation='cancel'（原生支持）
- 强制全量重跑   → operation='reexecute'
- provider 熔断  → operation='pause'（冷却=暂停轰炸）
- 目标对象       → task_id 列携带 eval run_id（对象=run，与任务 ID 同列不冲突）
- 细节           → reason 列携带 ``eval_run:<细节>``（500 字上限）

设计：
- 不建表不迁移：表结构权威在 migration 060（init_db 已登记），本模块
  只读写；表缺失 = 环境未迁移，如实记 warning 软失败。
- 软失败：审计写不进去只记 warning——审计是旁路，不得反噬评测主流程；
  但取消 API 会校验审计写结果（见 routes/evaluation.py），保证
  「取消必有痕」的验收口径。
"""
from __future__ import annotations

from typing import Any

from backend.shared.logger import logger

# 表结构权威 = backend/sql/migrations/060_task_operation_audits.sql
# （含 ck_task_op_operation CHECK：retry/revoke/reexecute/pause/resume/cancel）


def record_operation(
    operation: str,
    target_id: str,
    *,
    actor: str = "",
    detail: str = "",
    before_status: str = "",
    after_status: str = "",
) -> bool:
    """记录一条运行操作审计；返回是否落库成功（软失败不抛）。"""
    mapped = {
        "cancel": "cancel",
        "eval_run.cancel": "cancel",
        "force_rerun": "reexecute",
        "eval_run.force_rerun": "reexecute",
        "provider_breaker": "pause",
        "eval_run.provider_breaker_open": "pause",
        # deadline/token 熔断同属「运行中止」语义，真实原因进 reason
        "eval_run.deadline_exceeded": "pause",
        "eval_run.evaluator_token_limit": "pause",
        "eval_run.token_circuit_break": "pause",
    }.get(operation)
    if mapped is None:
        logger.warning(f"[eval-audit] 未映射的操作类型 {operation!r}，拒绝写入（CHECK 会拒）")
        return False
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            conn.cursor().execute(
                """
                INSERT INTO ai.task_operation_audits
                    (task_id, operation, actor, reason, before_status, after_status)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    target_id,
                    mapped,
                    actor,
                    f"eval_run:{detail}"[:500],
                    before_status,
                    after_status,
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001 — 审计旁路软失败
        logger.warning(f"[eval-audit] 操作审计写入失败（operation={mapped}）: {e}")
        return False


def list_operations(target_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """读取某 eval run 的操作审计（软失败返回空列表，面板容错）。"""
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT operation, actor, reason, before_status, after_status, created_at
                FROM ai.task_operation_audits
                WHERE task_id = %s
                ORDER BY created_at DESC LIMIT %s
                """,
                (target_id, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "operation": r[0],
                "target_id": target_id,
                "actor": r[1],
                "detail": str(r[2] or ""),
                "before_status": r[3],
                "after_status": r[4],
                "created_at": str(r[5]),
            }
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[eval-audit] 操作审计查询失败: {e}")
        return []


__all__ = ["record_operation", "list_operations"]
