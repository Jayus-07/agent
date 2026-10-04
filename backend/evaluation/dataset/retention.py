"""数据集版本保留守卫（C6-2/DATA-06）。

口径：被历史 run 引用的数据集版本**不能物理删除**——删除工具/流程在
执行删除前必须调用 ``ensure_dataset_version_deletable``；命中引用时
拒绝并提示改走 deprecated（弃用不删，保留可复现性）。

引用判定（ai.eval_run_records.dataset_version JSONB 两种形态）：
1. 旧口径  {"rag": "5.0.0-unified"}            → dataset_version->>module
2. 新口径  {"dataset_version": "5.0.0-unified", ...}（eval_provenance）
"""
from __future__ import annotations

from backend.shared.logger import logger


def count_run_references(module: str, version: str) -> int:
    """统计引用了 (module, version) 的 run 行数；DB 不可达返回 -1
    （调用方对 -1 必须按「无法证明无引用」处理 → 拒绝删除，fail-closed）。"""
    if not version:
        return 0
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT count(*) FROM ai.eval_run_records
                WHERE dataset_version->>%s = %s
                   OR dataset_version->>'dataset_version' = %s
                """,
                (module, version, version),
            )
            return int(cur.fetchone()[0])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dataset-retention] 引用查询失败（按有引用处理）: {e}")
        return -1


def ensure_dataset_version_deletable(module: str, version: str) -> None:
    """删除前置守卫：命中 run 引用（或无法证明无引用）→ 拒绝物理删除。"""
    refs = count_run_references(module, version)
    if refs > 0:
        raise ValueError(
            f"数据集 {module}@{version} 已被 {refs} 个历史评测 run 引用，"
            f"禁止物理删除（DATA-06）；请改用 deprecated 标记保留可复现性"
        )
    if refs < 0:
        raise ValueError(
            f"数据集 {module}@{version} 的引用核查失败（台账不可达），"
            f"按 fail-closed 拒绝删除；恢复台账后重试"
        )


__all__ = ["count_run_references", "ensure_dataset_version_deletable"]
