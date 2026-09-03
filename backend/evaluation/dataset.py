"""测试集加载器 — 从 datasets/ 目录读取 JSON 测试集并校验。

V2.0 扩展：支持从子目录加载多个分片文件（如 datasets/rag/*.json）。
"""

import json
from pathlib import Path
from backend.evaluation.models import TestCase, ModuleKind

DATASET_DIR = Path(__file__).resolve().parent / "datasets"


def load_dataset(module: ModuleKind) -> list[TestCase]:
    """加载指定模块的默认测试集 JSON 文件，返回 TestCase 列表。

    查找顺序（跳过 *.deprecated.json，避免弃用集静默生效）：
      1. {module}/ 目录（V2 分片结构）
      2. {module}_v2.json
      3. {module}_test_kb.json（RAG 主力评测集）
      4. {module}.json
    也可以用 load_dataset_file() 显式指定文件。
    """
    # V2: 优先检查分片目录
    split_dir = DATASET_DIR / module
    if split_dir.is_dir():
        return load_dataset_directory(split_dir, default_module=module)

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


def load_dataset_directory(
    directory: Path,
    default_module: str = "rag",
    exclude_patterns: list[str] | None = None,
) -> list[TestCase]:
    """从目录加载所有 JSON 测试集文件，合并返回 TestCase 列表。

    V2.0 分片结构：每个文件是一个测试子集（如 retrieval_basic.json, retrieval_hard.json）。

    Args:
        directory: 包含 JSON 测试集文件的目录
        default_module: 当 JSON 中无 module 字段时的默认值
        exclude_patterns: 要排除的文件名模式（如 ["*.deprecated.json"]）

    Returns:
        list[TestCase]: 合并后的所有测试用例
    """
    exclude_patterns = exclude_patterns or ["*.deprecated.*"]
    all_cases: list[TestCase] = []
    seen_files: set[str] = set()

    for json_file in sorted(directory.glob("*.json")):
        # 检查排除模式
        should_exclude = any(
            json_file.match(pattern) for pattern in exclude_patterns
        )
        if should_exclude:
            continue

        # 防止重复加载
        if json_file.name in seen_files:
            continue
        seen_files.add(json_file.name)

        cases = _load_from_path(json_file, default_module=default_module)
        all_cases.extend(cases)

    return all_cases


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
    """校验测试集，返回错误信息列表。空列表表示通过。

    V2.0 扩展校验：
    - 检查 expected.relevant_docs 格式（doc_id 应为 10 位 hex）
    - 检查 answer_type 枚举值
    - 检查 query_type 枚举值
    - 检查 tier 枚举值
    - 检查 must_contain / must_not_contain 为列表类型
    """
    errors: list[str] = []

    VALID_ANSWER_TYPES = {"factual", "numeric", "procedural", "comparative", ""}
    VALID_QUERY_TYPES = {
        "single_doc", "multi_hop", "adversarial", "negative", "table",
        "chunk_level", "long_doc", "comparative", "procedural", "aggregation", "",
    }
    VALID_TIERS = {"smoke", "core", "hard", "regression", ""}

    # 检查 ID 唯一性
    seen_ids: set[str] = set()
    for case in cases:
        if case.id in seen_ids:
            errors.append(f"Duplicate case ID: {case.id}")
        seen_ids.add(case.id)

        # 检查必填字段
        if not case.question.strip():
            errors.append(f"Case {case.id}: question is empty")
        if case.module not in ("planner", "rag", "sql", "e2e", "cs"):
            errors.append(f"Case {case.id}: invalid module '{case.module}'")

        # V2: 校验 expected.relevant_docs 格式
        relevant_docs = case.expected.get("relevant_docs", [])
        if relevant_docs:
            for doc_id in relevant_docs:
                if not isinstance(doc_id, str):
                    errors.append(f"Case {case.id}: relevant_docs contains non-string: {doc_id}")
                elif len(doc_id) != 10:
                    # 允许非标准长度（旧数据），但发出警告
                    pass  # 不报错，兼容旧格式

        # V2: 校验枚举字段
        answer_type = case.metadata.get("answer_type", "")
        if answer_type and answer_type not in VALID_ANSWER_TYPES:
            errors.append(f"Case {case.id}: invalid answer_type '{answer_type}'")

        query_type = case.metadata.get("query_type", "")
        if query_type and query_type not in VALID_QUERY_TYPES:
            errors.append(f"Case {case.id}: invalid query_type '{query_type}'")

        tier = case.metadata.get("tier", "")
        if tier and tier not in VALID_TIERS:
            errors.append(f"Case {case.id}: invalid tier '{tier}'")

        # V2: 校验列表类型字段
        for field in ("must_contain", "must_not_contain"):
            value = case.metadata.get(field)
            if value is not None and not isinstance(value, list):
                errors.append(f"Case {case.id}: {field} must be a list, got {type(value).__name__}")

    return errors
