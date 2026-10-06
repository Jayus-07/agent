"""evaluation/run_records.py — 评测 run 的 DB 台账（M7 / 台账 D7）

为什么存在：评测结果此前只在 ``data/eval_runs/`` 文件目录（不可 SQL 查询、
无版本关联、无触发者）。本模块在 ``persist_report`` 落文件后同步 upsert
一行 run 级摘要到 ``ai.eval_run_records``（agent_memory 库）。

设计原则：
- **表是索引不是替代**：文件目录仍是明细权威（report.json / per_case），
  表只存摘要 + 版本指纹——管理端列表查询与发布门禁走表，深挖走文件。
- 软失败：DB 不可用时只记 warning 返回 False，评测主流程不受影响
  （评测可以在任何环境跑，包括无 PG 的离线机器）。
- prompt_snapshot 用 PG 权威口径（snapshot_prompt_versions），与
  meta.json 的 yaml 扫描口径并存——meta.json 保留旧口径做对照。
"""
from __future__ import annotations

import json
import os
from typing import Any

from backend.evaluation.models import EvalReport
from backend.shared.logger import logger

# C3-2：样本级 DB 镜像开关（默认关——量大防拖慢评测主流程；
# 需要样本级 SQL 对账/管理端样本查询时打开）
SAMPLE_DB_SINK_ENABLED = os.getenv("EVAL_SAMPLE_DB_SINK_ENABLED", "false").strip().lower() in (
    "1", "true", "yes",
)


def collect_prompt_snapshot() -> dict[str, str]:
    """PG 权威的 prompt 版本快照；DB 不可达时回退 yaml 文件扫描口径。"""
    try:
        from backend.prompts.service import snapshot_prompt_versions

        snapshot = snapshot_prompt_versions()
        if snapshot:
            return {k: str(v) for k, v in snapshot.items()}
    except Exception as e:  # noqa: BLE001 — 口径采集软失败
        logger.warning(f"[run_records] PG prompt 快照不可达，回退 yaml 扫描: {e}")
    from backend.evaluation.storage import collect_prompt_versions

    return collect_prompt_versions()


def collect_model_binding_fingerprint() -> str:
    """当前生效的模型角色绑定集指纹（llm_model_role_bindings 行集 hash）。"""
    try:
        import hashlib

        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT role, model_name FROM llm_model_role_bindings "
                "ORDER BY role, model_name"
            )
            blob = "\n".join(f"{r}:{m}" for r, m in cur.fetchall())
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""  # 无 DB / 服务不可达：不阻塞评测，指纹留空


def _summarize(report: EvalReport) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    case_count = 0
    pass_count = 0
    for s in report.summaries:
        metrics[s.module] = {
            "total": s.total, "passed": s.passed, "failed": s.failed,
            "errors": s.errors, "skipped": s.skipped,
            "pass_rate": s.pass_rate,
            "metrics": {k: v for k, v in (s.metrics or {}).items()
                        if isinstance(v, (int, float, str, bool))},
        }
        case_count += s.total
        pass_count += s.passed
    return {
        "metrics": metrics,
        "case_count": case_count,
        "pass_count": pass_count,
        "pass_rate": round(pass_count / case_count, 4) if case_count else None,
    }


def record_run(report: EvalReport, run_id: str, meta: dict[str, Any] | None = None) -> bool:
    """把 run 摘要 upsert 进 ai.eval_run_records（软失败）。"""
    meta = meta or {}
    summary = _summarize(report)
    # P0-04：有效性裁决（服务侧已随 report.metadata 落盘；历史/旁路路径现算兜底）
    from backend.evaluation.validity import (
        classify_report_validity,
        validity_from_report_metadata,
    )

    validity_verdict = validity_from_report_metadata(report.metadata)
    if validity_verdict is None:
        validity_verdict = classify_report_validity(report)
    row = {
        "run_id": run_id,
        "module": report.module,
        "mode": report.mode,
        "smoke": bool(report.smoke),
        "dataset_version": meta.get("dataset_version", {}),
        "git_sha": meta.get("git_sha", ""),
        "prompt_snapshot": meta.get("prompt_snapshot", collect_prompt_snapshot()),
        "model_binding_fingerprint": meta.get("model_binding_fingerprint",
                                              collect_model_binding_fingerprint()),
        # C3-1/C3-3：生命周期状态与统一快照哈希随台账落库（074 迁移列）
        "status": str((report.metadata or {}).get("lifecycle_status", "")) or "completed",
        "attempt_no": int((report.metadata or {}).get("attempt_no", 1) or 1),
        "evaluation_snapshot_hash": str(
            (report.metadata or {}).get("evaluation_snapshot_hash", "")
        ),
        "validity": validity_verdict.validity.value,
        "invalid_reason": validity_verdict.invalid_reason,
        "trigger": (meta.get("env") or {}).get("trigger", "manual"),
        "triggered_by": (meta.get("env") or {}).get("triggered_by", ""),
        **summary,
    }
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            conn.cursor().execute(
                """
                INSERT INTO ai.eval_run_records (
                    run_id, module, mode, smoke, dataset_version, git_sha,
                    prompt_snapshot, model_binding_fingerprint,
                    status, attempt_no, evaluation_snapshot_hash,
                    validity, invalid_reason,
                    trigger, triggered_by, metrics,
                    case_count, pass_count, pass_rate
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    metrics = EXCLUDED.metrics,
                    case_count = EXCLUDED.case_count,
                    pass_count = EXCLUDED.pass_count,
                    pass_rate = EXCLUDED.pass_rate,
                    status = EXCLUDED.status,
                    attempt_no = EXCLUDED.attempt_no,
                    evaluation_snapshot_hash = EXCLUDED.evaluation_snapshot_hash,
                    validity = EXCLUDED.validity,
                    invalid_reason = EXCLUDED.invalid_reason,
                    updated_at = now()
                """,
                (
                    row["run_id"], row["module"], row["mode"], row["smoke"],
                    json.dumps(row["dataset_version"], ensure_ascii=False),
                    row["git_sha"],
                    json.dumps(row["prompt_snapshot"], ensure_ascii=False),
                    row["model_binding_fingerprint"],
                    row["status"], row["attempt_no"],
                    row["evaluation_snapshot_hash"],
                    row["validity"], row["invalid_reason"],
                    row["trigger"], row["triggered_by"],
                    json.dumps(row["metrics"], ensure_ascii=False),
                    row["case_count"], row["pass_count"], row["pass_rate"],
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001 — 台账软失败
        logger.warning(f"[run_records] eval_run_records 写入失败（不影响评测）: {e}")
        return False


def update_run_terminal_status(run_id: str, status: str, error: str = "") -> bool:
    """C3-1：终态（cancelled/failed）同步进 DB 台账（074 status 列）。

    persist_report 在 mark_run_status 终态收口后调用；软失败。
    """
    if status not in {"completed", "failed", "cancelled"}:
        return False
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            conn.cursor().execute(
                """
                UPDATE ai.eval_run_records
                SET status = %s, updated_at = now()
                WHERE run_id = %s
                """,
                (status, run_id),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[run_records] run 终态同步失败（run_id={run_id}）: {e}")
        return False


def record_run_samples(report: EvalReport, run_id: str) -> int:
    """C3-2/DB-07：样本结果批量 upsert 进 ai.eval_run_samples（075 迁移）。

    (run_id, case_id, evaluator) 唯一——重复消费同键覆盖写（CON-07 幂等）。
    开关 EVAL_SAMPLE_DB_SINK_ENABLED 默认关。返回写入行数（软失败返 -1）。
    """
    if not SAMPLE_DB_SINK_ENABLED:
        return 0
    rows = []
    for r in report.results:
        rows.append((
            run_id, r.case_id, "self", r.status,
            json.dumps(r.metrics or {}, ensure_ascii=False),
            (r.error_stage or "")[:40],
        ))
    if not rows:
        return 0
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.executemany(
                """
                INSERT INTO ai.eval_run_samples
                    (run_id, case_id, evaluator, status, metrics, error_stage)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, case_id, evaluator) DO UPDATE SET
                    status = EXCLUDED.status,
                    metrics = EXCLUDED.metrics,
                    error_stage = EXCLUDED.error_stage,
                    updated_at = now()
                """,
                rows,
            )
            conn.commit()
        return len(rows)
    except Exception as e:  # noqa: BLE001 — 样本镜像是旁路，失败不影响主流程
        logger.warning(f"[run_records] 样本级镜像写入失败: {e}")
        return -1


__all__ = [
    "record_run",
    "record_run_samples",
    "update_run_terminal_status",
    "collect_prompt_snapshot",
    "collect_model_binding_fingerprint",
]
