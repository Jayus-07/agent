"""周自动评测入口（P1-2，2026-08-21 从 backend.eval 迁移）。

用统一框架 backend.evaluation 跑 rag_test_kb.json（v1.3，54 条）离线检索评测：
- 确定性：不调 LLM，无 API 成本，可在调度线程内同步执行
- 覆盖面：54 条（旧 golden_v1.json 仅 5 条），含 Top-1/拒答/MRR 等 v2 指标
- 留痕：报告持久化到 data/eval_runs（persist_report）

调用方：
- backend/app/server.py     每周日 02:00 定时任务（weekly_eval）
- backend/app/api/routes/schedules.py  手动触发 API
"""
from __future__ import annotations

import importlib
from typing import Any

from backend.shared.logger import logger

DEFAULT_DATASET = "rag_test_kb.json"


def run_weekly_rag_eval(dataset_file: str = DEFAULT_DATASET) -> dict[str, Any]:
    """运行离线 RAG 检索评测，返回摘要 dict。

    Returns:
        {
          "ok": bool,
          "total": int, "passed": int, "failed": int,
          "pass_rate": float,
          "top1_accuracy": float,
          "reject_accuracy": float,
          "recall_at_5": float,
          "timestamp": str,
        }
    """
    # runner 注册与 CLI 的 _bootstrap_runners 同机制（import backend.evaluation 本身不注册）
    importlib.import_module("backend.evaluation.runners_config")
    from backend.evaluation import persist_report, run_all

    report = run_all(module="rag", live=False, dataset_file=dataset_file)

    # 持久化失败不阻断（CLI 同款策略）
    try:
        persist_report(report)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[WeeklyEval] persist_report 失败（不阻断）: {e}")

    rag = next((s for s in report.summaries if s.module == "rag"), None)
    if rag is None:
        return {"ok": False, "error": "rag module summary not found"}

    return {
        "ok": True,
        "total": rag.total,
        "passed": rag.passed,
        "failed": rag.failed + rag.errors,
        "pass_rate": rag.pass_rate,
        "top1_accuracy": rag.metrics.get("top1_accuracy", 0.0),
        "reject_accuracy": rag.metrics.get("reject_accuracy", 0.0),
        "recall_at_5": rag.metrics.get("recall@5", 0.0),
        "timestamp": report.timestamp,
    }


__all__ = ["run_weekly_rag_eval", "DEFAULT_DATASET"]
