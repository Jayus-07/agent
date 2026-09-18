"""统一 RAG 评测语料 catalog 的纯解析与校验。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


RAG_EVAL_KB_ID = "rag_eval_kb"
_KNOWN_FIXTURE_SETS = frozenset({"baseline", "expanded_100", "scale_20k"})
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_MANIFEST_PATH = (
    _PROJECT_ROOT / "backend" / "evaluation" / "fixtures" / RAG_EVAL_KB_ID / "manifest.json"
)


@dataclass(frozen=True)
class FixtureDocument:
    """一份评测文档的稳定标识、迁移期源路径及附加标注。"""

    fixture_doc_id: str
    source_file: str
    fixture_set: str
    format: str
    kb_id: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FixtureCatalog:
    """统一测试 KB 的只读语料目录。"""

    kb_id: str
    fixture_version: str | None
    _documents: tuple[FixtureDocument, ...]
    _source_root: Path = field(repr=False, compare=False)

    def documents(self, fixture_set: str | None = None) -> list[FixtureDocument]:
        """返回指定语料集合的文档；未指定时返回全部文档。"""
        if fixture_set is None:
            return list(self._documents)
        return [document for document in self._documents if document.fixture_set == fixture_set]

    def validate(self) -> None:
        """在任何入库动作前验证目录，避免错误测试资产污染评测结果。"""
        if self.kb_id != RAG_EVAL_KB_ID:
            raise ValueError(f"kb_id 必须为 {RAG_EVAL_KB_ID}: {self.kb_id}")

        # 先完成不依赖文件系统的结构校验，避免缺文件掩盖重复 ID 等直接配置错误。
        fixture_doc_ids: set[str] = set()
        for document in self._documents:
            # 稳定 ID 重复会令幂等入库无法区分重试和另一份资产。
            if not document.fixture_doc_id or document.fixture_doc_id in fixture_doc_ids:
                raise ValueError(f"fixture_doc_id 必须唯一且非空: {document.fixture_doc_id}")
            fixture_doc_ids.add(document.fixture_doc_id)

        for document in self._documents:
            # 未知集合会绕过 baseline/expanded_100 的隔离评测口径。
            if document.fixture_set not in _KNOWN_FIXTURE_SETS:
                raise ValueError(f"未知 fixture_set: {document.fixture_set}")

            # 混入旧 KB 会使同一评测报告实际检索到不同物理语料。
            if document.kb_id != self.kb_id:
                raise ValueError(
                    f"文档 {document.fixture_doc_id} 的 kb_id 与 catalog 不一致: {document.kb_id}"
                )

            # 源文件不存在时不应继续入库，避免后续把半成品误标为 active。
            source_path = Path(document.source_file)
            if not source_path.is_absolute():
                source_path = self._source_root / source_path
            if not source_path.is_file():
                raise ValueError(f"source_file 不存在: {document.source_file}")


@dataclass(frozen=True)
class EvalProfile:
    """旧 KB 迁移期写入统一测试 KB 时所需的固定映射。"""

    source_kb_id: str
    target_kb_id: str
    fixture_set: str | None
    deprecated: bool


def catalog_from_dict(data: dict[str, Any]) -> FixtureCatalog:
    """从 JSON 已解析对象构造 catalog；不触碰数据库、向量库或环境变量。"""
    documents_data = data.get("documents", [])
    if not isinstance(documents_data, list):
        raise ValueError("documents 必须是列表")

    documents: list[FixtureDocument] = []
    for raw_document in documents_data:
        if not isinstance(raw_document, dict):
            raise ValueError("documents 条目必须是对象")
        metadata = {
            key: value
            for key, value in raw_document.items()
            if key
            not in {
                "fixture_doc_id",
                "source_file",
                "fixture_set",
                "format",
                "kb_id",
            }
        }
        documents.append(
            FixtureDocument(
                fixture_doc_id=str(raw_document.get("fixture_doc_id", "")),
                source_file=str(raw_document.get("source_file", "")),
                fixture_set=str(raw_document.get("fixture_set", "")),
                format=str(raw_document.get("format", "")),
                kb_id=str(raw_document.get("kb_id", data.get("kb_id", ""))),
                metadata=metadata,
            )
        )

    return FixtureCatalog(
        kb_id=str(data.get("kb_id", "")),
        fixture_version=(
            str(data["fixture_version"]) if data.get("fixture_version") is not None else None
        ),
        _documents=tuple(documents),
        _source_root=_PROJECT_ROOT,
    )


def load_fixture_catalog(path: Path | None = None) -> FixtureCatalog:
    """读取统一 manifest；相对 source_file 始终相对仓库根目录解释。"""
    manifest_path = path or _DEFAULT_MANIFEST_PATH
    with manifest_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("fixture manifest 根节点必须是对象")
    data = dict(data)
    data["documents"] = list(data.get("documents", []))
    for source_manifest in data.get("source_manifests", []):
        if not isinstance(source_manifest, dict):
            raise ValueError("source_manifests 条目必须是对象")
        source_manifest_path = _PROJECT_ROOT / str(source_manifest.get("source_manifest", ""))
        with source_manifest_path.open("r", encoding="utf-8") as file:
            source_data = json.load(file)
        source_prefix = str(source_manifest.get("source_files_prefix", ""))
        fixture_set = str(source_manifest.get("fixture_set", ""))
        for source_document in source_data.get("documents", []):
            source_file = str(source_document.get("file", ""))
            data["documents"].append(
                {
                    "fixture_doc_id": source_document.get("doc_id", ""),
                    "source_file": f"{source_prefix}/{source_file}",
                    # 旧路径在一个发布周期内仍供旧命令读取，删除条件见 manifest。
                    "source_compat_path": f"{source_prefix}/{source_file}",
                    "fixture_set": fixture_set,
                    "format": source_document.get("format", ""),
                    "kb_id": data.get("kb_id", ""),
                    "legacy_metadata": source_document,
                }
            )
    return catalog_from_dict(data)


def resolve_eval_profile(source_kb_id: str) -> EvalProfile:
    """将新旧测试 KB 标识映射为统一写入目标和语料范围。"""
    profiles = {
        RAG_EVAL_KB_ID: EvalProfile(
            source_kb_id=RAG_EVAL_KB_ID,
            target_kb_id=RAG_EVAL_KB_ID,
            fixture_set=None,
            deprecated=False,
        ),
        "rag_test_kb": EvalProfile(
            source_kb_id="rag_test_kb",
            target_kb_id=RAG_EVAL_KB_ID,
            fixture_set="baseline",
            deprecated=True,
        ),
        "rag_100_docs": EvalProfile(
            source_kb_id="rag_100_docs",
            target_kb_id=RAG_EVAL_KB_ID,
            fixture_set="expanded_100",
            deprecated=True,
        ),
    }
    try:
        return profiles[source_kb_id]
    except KeyError as exc:
        raise ValueError(f"不支持的评测 KB: {source_kb_id}") from exc
