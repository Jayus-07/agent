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



def append_case(
    case: TestCase,
    module: ModuleKind | None = None,
    dataset_dir: Path | None = None,
) -> dict:
    """将单条 TestCase 原子追加到 cases.jsonl（JSONL 格式）。

    去重逻辑：若已有 case 的 metadata.trace_id 与当前 case 相同则跳过。

    Returns:
        {"appended": True/False, "reason": str, "path": str}
    """
    target_module = module or case.module
    base_dir = dataset_dir or DATASET_DIR
    target_path = base_dir / target_module / "cases.jsonl"

    with _write_lock:
        existing_cases: list[TestCase] = []

        if target_path.exists():
            try:
                existing_cases = _load_jsonl_cases(target_path, target_module)
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

        errors = validate_dataset(existing_cases + [case])
        if errors:
            logger.warning(f"[curator] 追加后校验警告: {errors[:3]}")

        case_line = json.dumps({
            "id": case.id,
            "question": case.question,
            "module": case.module,
            "expected": case.expected,
            "metadata": case.metadata,
        }, ensure_ascii=False)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(".jsonl.tmp")
        try:
            if target_path.exists():
                tmp_path.write_text(
                    target_path.read_text(encoding="utf-8") + case_line + "\n",
                    encoding="utf-8",
                )
            else:
                tmp_path.write_text(case_line + "\n", encoding="utf-8")
            tmp_path.replace(target_path)
        except Exception as exc:
            try:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            except OSError:
                pass  # 清理失败不掩盖真正的写入错误
            logger.error(f"[curator] 写入评测集失败: {target_path}: {exc}")
            return {"appended": False, "reason": f"写入失败: {exc}", "path": str(target_path)}

    logger.info(f"[curator] 追加 TestCase {case.id} → {target_path}")
    return {"appended": True, "reason": "ok", "path": str(target_path)}


def _load_jsonl_cases(file_path: Path, default_module: str) -> list[TestCase]:
    """从 JSONL 文件加载 TestCase 列表（供 curator 内部使用）。"""
    from backend.evaluation.dataset.validator import _normalize_ground_truth_context

    cases = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            expected = item.pop("expected", {})
            metadata = item.pop("metadata", {})
            _normalize_ground_truth_context(expected)
            cases.append(TestCase(
                id=item["id"],
                question=item["question"],
                module=item.get("module", default_module),
                expected=expected,
                metadata=metadata,
            ))
    return cases


def list_cases(module: ModuleKind) -> list[TestCase]:
    """加载指定模块的评测用例列表。"""
    try:
        return load_dataset(module)
    except FileNotFoundError:
        return []
