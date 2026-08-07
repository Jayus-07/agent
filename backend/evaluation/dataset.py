"""测试集加载器 — 从 datasets/ 目录读取 JSON 测试集并校验。"""

import json
from pathlib import Path
from backend.evaluation.models import TestCase, ModuleKind

DATASET_DIR = Path(__file__).resolve().parent / "datasets"


def load_dataset(module: ModuleKind) -> list[TestCase]:
    """加载指定模块的测试集 JSON 文件，返回 TestCase 列表。

    支持版本化文件名: 先尝试 {module}_v2.json，再退回 {module}.json。
    也可以用 load_dataset_file() 显式指定文件。
    """
    # 优先尝试 v2 版本
    v2_path = DATASET_DIR / f"{module}_v2.json"
    if v2_path.exists():
        return _load_from_path(v2_path, default_module=module)
    return load_dataset_file(f"{module}.json", default_module=module)


def load_dataset_file(filename: str, default_module: str = "rag") -> list[TestCase]:
    """加载指定文件名的测试集（相对于 datasets/ 目录）。"""
    file_path = DATASET_DIR / filename
    if not file_path.exists():
        raise FileNotFoundError(f"测试集文件不存在: {file_path}")
    return _load_from_path(file_path, default_module=default_module)


def _load_from_path(file_path: Path, default_module: str = "rag") -> list[TestCase]:
    """从完整路径加载 JSON 测试集。

    Args:
        file_path: JSON 测试集文件路径
        default_module: 当 JSON 中无 module 字段时的默认值
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = []
    for item in data["test_cases"]:
        # 从 JSON 提取预期字段，其余作为 metadata
        expected = item.pop("expected", {})
        metadata = item.pop("metadata", {})
        # 保留 JSON 中的其他字段（如 kb_id）放入 metadata
        extra = {k: v for k, v in item.items() if k not in ("id", "question", "module")}
        metadata.update(extra)

        cases.append(TestCase(
            id=item["id"],
            question=item["question"],
            module=item.get("module", default_module),
            expected=expected,
            metadata=metadata,
        ))

    return cases


def validate_dataset(cases: list[TestCase]) -> list[str]:
    """校验测试集，返回错误信息列表。空列表表示通过。"""
    errors: list[str] = []

    # 检查 ID 唯一性
    seen_ids: set[str] = set()
    for case in cases:
        if case.id in seen_ids:
            errors.append(f"Duplicate case ID: {case.id}")
        seen_ids.add(case.id)

        # 检查必填字段
        if not case.question.strip():
            errors.append(f"Case {case.id}: question is empty")
        if case.module not in ("planner", "rag", "sql", "e2e"):
            errors.append(f"Case {case.id}: invalid module '{case.module}'")

    return errors
