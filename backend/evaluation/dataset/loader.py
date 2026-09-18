"""测试集加载器 — 从 datasets/目录读取 JSON/JSONL 测试集并校验。

V2.0 扩展：支持从子目录加载多个分片文件（如 datasets/rag/*.json）。
V3.0 扩展：JSONL 为权威格式（每行一条 TestCase），兼容旧 JSON wrapper。
P0: 支持 EVAL_DATASET_PATH 环境变量动态配置数据集路径
"""

import copy
import json
import os
from pathlib import Path

from backend.config import EVAL_DATASET_PATH
from backend.evaluation.models import ModuleKind, TestCase
from backend.evaluation.dataset.validator import _normalize_ground_truth_context

# P0: 动态数据集路径配置
if EVAL_DATASET_PATH:
    DATASET_DIR = Path(EVAL_DATASET_PATH).expanduser().resolve()
    if not DATASET_DIR.exists():
        raise FileNotFoundError(
            f"EVAL_DATASET_PATH 指定的路径不存在：{DATASET_DIR}"
        )
else:
    DATASET_DIR = Path(__file__).resolve().parent.parent / "datasets"


def load_dataset(
    module: ModuleKind,
    selection: str | None = None,
    limit: int | None = None,
) -> list[TestCase]:
    """加载指定模块的测试集，返回 TestCase 列表。

    查找优先级：
      0. selection 参数 → 加载 Suite（suites/{name}.json，case_id 引用）
         或回退到旧格式 {name}.jsonl（完整 case 拷贝）
      1. {module}/cases.jsonl（V3 权威 canonical 集）
      2. {module}/ 目录（V2 分片结构，跳过 deprecated）
      3. {module}_v2.json / {module}_test_kb.json / {module}.json

    Args:
        module: 模块名（rag / planner）
        selection: 选择集名称（如 "ci_golden"），优先从 suites/ 加载
        limit: 截断返回前 N 条（用于 smoke test）
    """
    split_dir = DATASET_DIR / module

    if selection:
        cases = _load_selection(split_dir, selection, module)
        return cases[:limit] if limit else cases

    canonical = split_dir / "cases.jsonl"
    if canonical.exists():
        cases = _load_jsonl(canonical, default_module=module)
        return cases[:limit] if limit else cases

    if split_dir.is_dir():
        cases = load_dataset_directory(split_dir, default_module=module)
        return cases[:limit] if limit else cases

    candidates = [
        f"{module}_v2.json",
        f"{module}_test_kb.json",
        f"{module}.json",
    ]
    for filename in candidates:
        path = DATASET_DIR / filename
        if path.exists() and ".deprecated." not in filename:
            cases = _load_from_path(path, default_module=module)
            return cases[:limit] if limit else cases
    available = sorted(
        p.name for p in DATASET_DIR.glob("*.json")
        if ".deprecated." not in p.name and p.name != "__init__.py"
    )
    raise FileNotFoundError(
        f"模块 '{module}' 无默认可用的测试集（已尝试: {candidates}）。"
        f"当前可用评测集: {available}，可用 --dataset 显式指定。"
    )


def _load_selection(
    split_dir: Path,
    selection: str,
    module: str,
) -> list[TestCase]:
    """加载选择集：优先 Suite（case_id 引用），回退旧 JSONL 格式。"""
    suite_path = split_dir / "suites" / f"{selection}.json"
    if suite_path.exists():
        return _load_suite(split_dir, suite_path, module)

    sel_path = split_dir / f"{selection}.jsonl"
    if sel_path.exists():
        return _load_jsonl(sel_path, default_module=module)

    raise FileNotFoundError(
        f"选择集不存在: suite '{suite_path}' 和 JSONL '{sel_path}' 均未找到。"
        f"请确认 selection='{selection}' 对应的文件已创建。"
    )


def _load_suite(
    split_dir: Path,
    suite_path: Path,
    module: str,
) -> list[TestCase]:
    """从 Suite 文件加载：读取 case_ids，从 canonical cases.jsonl 中解析引用。"""
    with open(suite_path, "r", encoding="utf-8") as f:
        suite_data = json.load(f)

    if not isinstance(suite_data, dict):
        raise ValueError(f"Suite 根节点必须是对象: {suite_path}")

    suite_kb_id = str(suite_data.get("kb_id", ""))
    fixture_set = str(suite_data.get("fixture_set", ""))
    dataset_version = str(suite_data.get("dataset_version", ""))
    if not suite_kb_id:
        raise ValueError(f"Suite 缺少 kb_id: {suite_path}")
    if fixture_set not in {"baseline", "expanded_100", "scale_20k"}:
        raise ValueError(f"Suite fixture_set 无效: {fixture_set}")
    if not dataset_version:
        raise ValueError(f"Suite 缺少 dataset_version: {suite_path}")

    case_ids = suite_data.get("case_ids", [])
    if not case_ids:
        raise ValueError(f"Suite 文件 case_ids 为空: {suite_path}")

    canonical_path = split_dir / "cases.jsonl"
    if not canonical_path.exists():
        raise FileNotFoundError(
            f"Suite 引用了 cases.jsonl 但 canonical 文件不存在: {canonical_path}"
        )

    all_cases = _load_jsonl(canonical_path, default_module=module)
    case_map = {c.id: c for c in all_cases}

    missing = [cid for cid in case_ids if cid not in case_map]
    if missing:
        raise ValueError(
            f"Suite '{suite_path.name}' 引用了 canonical 中不存在的 case_id: "
            f"{missing}。请检查 suite 定义或补充 canonical 数据。"
        )

    suite_name = str(suite_data.get("name", suite_path.stem))
    cases: list[TestCase] = []
    for case_id in case_ids:
        case = case_map[case_id]
        metadata = copy.deepcopy(case.metadata)
        case_kb_id = metadata.get("kb_id")
        if case_kb_id and case_kb_id != suite_kb_id:
            raise ValueError(
                f"Suite '{suite_name}' 的 kb_id={suite_kb_id} 与案例 {case_id} "
                f"的 kb_id={case_kb_id} 冲突"
            )

        source_fixture_set = metadata.get("source_fixture_set")
        if source_fixture_set and source_fixture_set != fixture_set:
            # scale_20k 复用 expanded_100 的问题与标注，但运行时必须检索 staging 语料。
            if not (fixture_set == "scale_20k" and source_fixture_set == "expanded_100"):
                raise ValueError(
                    f"Suite '{suite_name}' 的 fixture_set={fixture_set} 与案例 {case_id} "
                    f"的 source_fixture_set={source_fixture_set} 冲突"
                )

        metadata.update(
            {
                "kb_id": suite_kb_id,
                "fixture_set": fixture_set,
                "dataset_version": dataset_version,
                "suite": suite_name,
            }
        )
        cases.append(case.model_copy(update={"metadata": metadata}))
    return cases


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
    """加载指定文件名的测试集（相对于 datasets/ 目录）。支持 .json 和 .jsonl。"""
    if ".deprecated." in filename:
        raise FileNotFoundError(
            f"评测集 '{filename}' 已弃用，拒绝加载。请改用现行评测集（如 cases.jsonl）。"
        )
    file_path = DATASET_DIR / filename
    if not file_path.exists():
        raise FileNotFoundError(f"测试集文件不存在: {file_path}")
    if filename.endswith(".jsonl"):
        return _load_jsonl(file_path, default_module=default_module)
    return _load_from_path(file_path, default_module=default_module)


def _load_from_path(file_path: Path, default_module: str = "rag") -> list[TestCase]:
    """从完整路径加载 JSON 测试集。

    兼容 V1/V2/V3 schema：
    - V1/V2: expected 含 relevant_docs / relevant_snippets
    - V3: expected 含 ground_truth_context（原文片段列表），裸字符串自动规范化为 {text: s}
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    dataset_version = data.get("version", "1.0")

    cases = []
    for item in data["test_cases"]:
        expected = item.pop("expected", {})
        metadata = item.pop("metadata", {})
        extra = {k: v for k, v in item.items() if k not in ("id", "question", "module")}
        metadata.update(extra)
        metadata["dataset_version"] = dataset_version

        _normalize_ground_truth_context(expected)

        cases.append(TestCase(
            id=item["id"],
            question=item["question"],
            module=item.get("module", default_module),
            expected=expected,
            metadata=metadata,
        ))

    return cases


def _load_jsonl(file_path: Path, default_module: str = "rag") -> list[TestCase]:
    """从 JSONL 文件加载测试集（每行一条完整 TestCase JSON）。"""
    # 从 manifest.json 获取版本号
    dataset_version = "1.0"
    manifest_path = file_path.parent / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest = json.load(mf)
                dataset_version = str(manifest.get("version", "1.0"))
        except (json.JSONDecodeError, OSError):
            pass

    cases = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            expected = item.pop("expected", {})
            metadata = item.pop("metadata", {})
            extra = {k: v for k, v in item.items() if k not in ("id", "question", "module")}
            metadata.update(extra)
            metadata["dataset_version"] = dataset_version

            _normalize_ground_truth_context(expected)

            # cs-v2 多轮用例（2026-09-19 合入）：事实源是 turns，question 取
            # 末轮用户输入派生；单轮旧 schema 不受影响。
            turns = item.get("turns") or []
            question = item.get("question") or (
                turns[-1].get("text", "") if turns else "")

            cases.append(TestCase(
                id=item["id"],
                question=question,
                module=item.get("module", default_module),
                expected=expected,
                metadata=metadata,
            ))
    return cases


def select_cases(
    cases: list[TestCase],
    ids: list[str] | None = None,
    tier: str | None = None,
    limit: int | None = None,
) -> list[TestCase]:
    """从用例列表中按条件筛选子集。

    Args:
        cases: 完整用例列表
        ids: 按 ID 过滤（None 表示不过滤）
        tier: 按 metadata.tier 过滤（None 表示不过滤）
        limit: 截断返回前 N 条

    Returns:
        筛选后的用例列表
    """
    result = cases

    if ids is not None:
        id_set = set(ids)
        found_ids = {c.id for c in result}
        missing = id_set - found_ids
        if missing:
            raise ValueError(
                f"select_cases 请求的 case_id 在数据集中不存在: {sorted(missing)}"
            )
        result = [c for c in result if c.id in id_set]

    if tier is not None:
        result = [c for c in result if c.metadata.get("tier") == tier]

    if limit is not None:
        result = result[:limit]

    return result
