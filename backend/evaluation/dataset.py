"""测试集加载器 — 从 datasets/ 目录读取 JSON 测试集并校验。"""

import json
from pathlib import Path
from backend.evaluation.models import TestCase, ModuleKind

DATASET_DIR = Path(__file__).resolve().parent / "datasets"


def load_dataset(module: ModuleKind) -> list[TestCase]:
    """加载指定模块的默认测试集 JSON 文件，返回 TestCase 列表。

    查找顺序（跳过 *.deprecated.json，避免弃用集静默生效）：
      1. {module}_v2.json
      2. {module}_test_kb.json（RAG 主力评测集）
      3. {module}.json
    也可以用 load_dataset_file() 显式指定文件。
    """
    candidates = [
        f"{module}_v2.json",
        f"{module}_test_kb.json",
        f"{module}.json",
    ]
    for filename in candidates:
        path = DATASET_DIR / filename
        if path.exists() and ".deprecated." not in filename:
            return _load_from_path(path, default_module=module)
    available = sorted(
        p.name for p in DATASET_DIR.glob("*.json")
        if ".deprecated." not in p.name and p.name != "__init__.py"
    )
    raise FileNotFoundError(
        f"模块 '{module}' 无默认可用的测试集（已尝试: {candidates}）。"
        f"当前可用评测集: {available}，可用 --dataset 显式指定。"
    )


def load_dataset_file(filename: str, default_module: str = "rag") -> list[TestCase]:
    """加载指定文件名的测试集（相对于 datasets/ 目录）。"""
    if ".deprecated." in filename:
        raise FileNotFoundError(
            f"评测集 '{filename}' 已弃用，拒绝加载。请改用现行评测集（如 rag_test_kb.json）。"
        )
    file_path = DATASET_DIR / filename
    if not file_path.exists():
        raise FileNotFoundError(f"测试集文件不存在: {file_path}")
    return _load_from_path(file_path, default_module=default_module)


def _load_from_path(file_path: Path, default_module: str = "rag") -> list[TestCase]:
    """从完整路径加载 JSON 测试集。

    Args:
        file_path: JSON 测试集文件路径
        default_module: 当 JSON 中无 module 字段时的默认值

    Dataset v1 schema（version 字段 + expected/metadata 预留字段）：
        {
          "version": "1.0",
          "test_cases": [{
            "id": "RT-001",
            "question": "...",
            "module": "rag",
            "kb_id": "rag_test_kb",
            "expected": {
              "relevant_docs": [...],        # 检索层：应召回的 doc_id
              "relevant_chunks": [...],      # 预留：应召回的 chunk_id（更细粒度）
              "expected_answer": "...",      # 预留：生成层 Faithfulness/Answer Relevance
              "min_relevant_chunks": 1
            },
            "metadata": {"difficulty": "easy", "domain": "...", "doc_type": "..."}
          }]
        }
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 评测集版本化：记录 version，供报告追溯
    dataset_version = data.get("version", "1.0")

    cases = []
    for item in data["test_cases"]:
        # 从 JSON 提取预期字段，其余作为 metadata
        expected = item.pop("expected", {})
        metadata = item.pop("metadata", {})
        # 保留 JSON 中的其他字段（如 kb_id）放入 metadata
        extra = {k: v for k, v in item.items() if k not in ("id", "question", "module")}
        metadata.update(extra)
        # 版本化：每个 case 记录评测集版本
        metadata["dataset_version"] = dataset_version

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
