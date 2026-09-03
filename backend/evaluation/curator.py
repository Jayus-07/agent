"""评测集管理 — 原子写入 + 去重。

提供将 TestCase 追加到 datasets/ 目录 JSON 文件的能力，
线程安全，写入失败不会损坏已有数据。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.evaluation.dataset import DATASET_DIR, load_dataset, validate_dataset
from backend.evaluation.models import TestCase, ModuleKind
from backend.shared.logger import logger

_write_lock = threading.Lock()


def _dataset_path(module: ModuleKind) -> Path:
    """返回模块对应的评测集文件路径。

    优先使用已有文件；若不存在则创建 {module}.json。
    """
    candidates = [
        DATASET_DIR / f"{module}.json",
    ]
    for p in candidates:
        if p.exists():
            return p

    split_dir = DATASET_DIR / module
    if split_dir.is_dir():
        return split_dir / "curated.json"

    return DATASET_DIR / f"{module}.json"


def append_case(
    case: TestCase,
    module: ModuleKind | None = None,
    dataset_dir: Path | None = None,
) -> dict:
    """将单条 TestCase 原子追加到评测集 JSON。

    去重逻辑：若已有 case 的 metadata.trace_id 与当前 case 相同则跳过。

    Returns:
        {"appended": True/False, "reason": str, "path": str}
    """
    target_module = module or case.module
    base_dir = dataset_dir or DATASET_DIR
    target_path = base_dir / f"{target_module}.json"

    if not target_path.exists():
        split_dir = base_dir / target_module
        if split_dir.is_dir():
            target_path = split_dir / "curated.json"

    with _write_lock:
        existing_cases: list[TestCase] = []
        dataset_version = "1.0"

        if target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                dataset_version = data.get("version", "1.0")
                for item in data.get("test_cases", []):
                    meta = item.get("metadata", {})
                    existing_cases.append(TestCase(
                        id=item["id"],
                        question=item["question"],
                        module=item.get("module", target_module),
                        expected=item.get("expected", {}),
                        metadata=meta,
                    ))
            except Exception as exc:
                logger.error(f"[curator] 读取评测集失败: {target_path}: {exc}")
                return {"appended": False, "reason": f"读取失败: {exc}", "path": str(target_path)}

        new_trace_id = case.metadata.get("trace_id")
        if new_trace_id:
            for existing in existing_cases:
                if existing.metadata.get("trace_id") == new_trace_id:
                    return {
                        "appended": False,
                        "reason": f"trace_id={new_trace_id} 已存在 (case_id={existing.id})",
                        "path": str(target_path),
                    }

        if case.id in {c.id for c in existing_cases}:
            return {
                "appended": False,
                "reason": f"case_id={case.id} 已存在",
                "path": str(target_path),
            }

        existing_cases.append(case)

        errors = validate_dataset(existing_cases)
        if errors:
            logger.warning(f"[curator] 追加后校验警告: {errors[:3]}")

        payload = {
            "version": dataset_version,
            "test_cases": [
                {
                    "id": c.id,
                    "question": c.question,
                    "module": c.module,
                    "expected": c.expected,
                    "metadata": c.metadata,
                }
                for c in existing_cases
            ],
        }

        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp_path.replace(target_path)
        except Exception as exc:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            logger.error(f"[curator] 写入评测集失败: {target_path}: {exc}")
            return {"appended": False, "reason": f"写入失败: {exc}", "path": str(target_path)}

    logger.info(f"[curator] 追加 TestCase {case.id} → {target_path}")
    return {"appended": True, "reason": "ok", "path": str(target_path)}


def list_cases(module: ModuleKind) -> list[TestCase]:
    """加载指定模块的评测用例列表。"""
    try:
        return load_dataset(module)
    except FileNotFoundError:
        return []
