#!/usr/bin/env python
"""校验 RAG 文档处理血缘的完整性。

这是上线前/故障排查用的只读脚本，不读取原文、Prompt 或模型响应；只检查
运行、阶段、模型身份和文档摘要之间是否自洽。

用法：
    python scripts/verify_processing_lineage.py --doc-id <doc_id> --json
    python scripts/verify_processing_lineage.py --doc-id <doc_id> \
        --run-id <run_id> --expect-stage embedding --expect-stage vector_write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


_BACKEND_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _BACKEND_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验 RAG 处理模型血缘")
    parser.add_argument("--doc-id", default="", help="文档 ID")
    parser.add_argument("--run-id", default="", help="运行 ID；需同时提供 --doc-id")
    parser.add_argument(
        "--expect-stage", action="append", default=[], help="要求存在的阶段，可重复"
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def _load_detail(args: argparse.Namespace) -> dict[str, Any] | None:
    from backend.rag.indexing.processing_lineage_pg import (
        get_processing_lineage_repository,
    )

    repository = get_processing_lineage_repository()
    if args.run_id and not args.doc_id:
        raise ValueError("--run-id 必须同时提供 --doc-id")
    if args.run_id:
        return repository.get_run_detail(args.doc_id, args.run_id)
    if args.doc_id:
        result = repository.list_runs(args.doc_id, page=1, page_size=1)
        items = result.get("items") or []
        if not items:
            return None
        return repository.get_run_detail(args.doc_id, str(items[0]["run_id"]))
    # 没有筛选条件时只检查仓储是否可读，避免扫描全量历史运行。
    with repository._conn() as conn:  # noqa: SLF001 - 只读诊断入口
        row = repository._exec(  # noqa: SLF001
            conn,
            f"SELECT run_id, doc_id, status FROM {repository._runs_table} "
            "ORDER BY started_at DESC LIMIT 1",
        ).fetchone()
    return dict(row) if row else None


def _check(detail: dict[str, Any] | None, expected_stages: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if detail is None:
        warnings.append("没有找到可检查的处理运行")
        return {"ok": True, "errors": errors, "warnings": warnings, "detail": None}

    steps = list(detail.get("steps") or [])
    seen_attempts: set[tuple[str, int]] = set()
    seen_step_ids: set[str] = set()
    for step in steps:
        stage = str(step.get("stage") or "")
        attempt = int(step.get("attempt_no") or 1)
        key = (stage, attempt)
        if key in seen_attempts:
            errors.append(f"重复阶段尝试: {stage}#{attempt}")
        seen_attempts.add(key)
        step_id = str(step.get("step_id") or "")
        if step_id in seen_step_ids:
            errors.append(f"重复 step_id: {step_id}")
        seen_step_ids.add(step_id)
        if step.get("status") == "skipped" and any(
            step.get(field)
            for field in ("provider", "model_name", "model_revision", "artifact_fingerprint")
        ):
            errors.append(f"skipped 阶段带模型: {stage}")
        if step.get("status") in {"success", "cached", "fallback"} and step.get(
            "engine_type"
        ) in {"llm", "embedding", "ocr", "classifier"} and not step.get("model_name"):
            errors.append(f"模型阶段缺少 model_name: {stage}")

    stages = {str(step.get("stage") or "") for step in steps}
    missing = [stage for stage in expected_stages if stage not in stages]
    if missing:
        errors.append("缺少期望阶段: " + ",".join(missing))

    summary = detail.get("model_summary") or []
    step_models = {
        (step.get("role"), step.get("provider"), step.get("model_name"))
        for step in steps
        if step.get("model_name")
    }
    for item in summary:
        key = (item.get("role"), item.get("provider"), item.get("model_name"))
        if key not in step_models:
            errors.append(f"model_summary 中模型没有对应阶段: {item.get('model_name')}")

    status = str(detail.get("status") or "")
    running = [step.get("stage") for step in steps if step.get("status") == "running"]
    if status in {"success", "failed", "duplicate", "cancelled"} and running:
        errors.append("终态运行仍有 running 阶段: " + ",".join(map(str, running)))

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "run_id": detail.get("run_id"),
        "doc_id": detail.get("doc_id"),
        "status": status,
        "stage_count": len(steps),
        "stages": sorted(stages),
        "model_count": len(summary),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _check(_load_detail(args), args.expect_stage)
    except Exception as exc:
        result = {"ok": False, "errors": [f"读取血缘失败: {type(exc).__name__}: {exc}"]}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        print("血缘校验通过" if result.get("ok") else "血缘校验失败")
        for message in result.get("errors", []):
            print(f"- {message}")
        for message in result.get("warnings", []):
            print(f"- 警告: {message}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
