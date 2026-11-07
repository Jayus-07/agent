"""客服 full_path 测试库 fixture 与事实来源契约。"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.customer_service.knowledge.evaluation_scope import (
    CS_EVAL_FIXTURE_SET,
    CS_EVAL_KB_ID,
    CS_EVAL_VERSION_ID,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CS_EVAL_SOURCE_ROOT = "docs/customer-service/demo-kb"
_MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / CS_EVAL_KB_ID
    / "manifest.json"
)
_FACT_ID_RE = re.compile(r"^kb:[a-z0-9-]+#[\w\u3400-\u9fff-]+$")


@dataclass(frozen=True)
class CSFixtureDocument:
    """客服评测文档的稳定索引 ID 与仓库来源。"""

    fixture_doc_id: str
    source_file: str
    sha256: str


@dataclass(frozen=True)
class CSFactSource:
    """一个稳定事实 ID 对应的文档和精确章节标题集合。"""

    fact_id: str
    fixture_doc_id: str
    sections: tuple[str, ...]


@dataclass(frozen=True)
class CSFixtureCatalog:
    """只读的客服测试语料与事实来源映射。"""

    kb_id: str
    fixture_set: str
    fixture_version: str
    version_id: str
    source_root: str
    documents: tuple[CSFixtureDocument, ...]
    fact_sources: tuple[CSFactSource, ...]

    def source_path(self, document: CSFixtureDocument) -> Path:
        root = (_REPO_ROOT / self.source_root).resolve()
        path = (root / document.source_file).resolve()
        if root not in path.parents:
            raise ValueError(f"fixture 路径越界: {document.source_file}")
        return path

    def validate(self) -> None:
        """确认语料版本、来源内容 hash、ID 和章节映射均完整。"""
        if self.kb_id != CS_EVAL_KB_ID:
            raise ValueError(f"客服 fixture 只能写入 {CS_EVAL_KB_ID}")
        if self.fixture_set != CS_EVAL_FIXTURE_SET:
            raise ValueError(f"不支持的客服 fixture_set: {self.fixture_set}")
        if self.source_root != _CS_EVAL_SOURCE_ROOT:
            raise ValueError("客服评测语料只能来自锁定的 demo-kb 源目录")
        if not self.fixture_version or self.version_id != CS_EVAL_VERSION_ID:
            raise ValueError("客服 fixture 版本 ID 无效")
        if not self.documents:
            raise ValueError("客服 fixture manifest 没有文档")

        doc_ids: set[str] = set()
        source_files: set[str] = set()
        headings_by_doc_id: dict[str, set[str]] = {}
        for document in self.documents:
            if (not re.fullmatch(r"cs-eval-[a-z0-9-]+", document.fixture_doc_id)
                    or document.fixture_doc_id in doc_ids):
                raise ValueError(f"客服 fixture_doc_id 非法或重复: {document.fixture_doc_id}")
            if document.source_file in source_files:
                raise ValueError(f"客服 fixture source_file 重复: {document.source_file}")
            if not re.fullmatch(r"[0-9a-f]{64}", document.sha256):
                raise ValueError(f"客服 fixture SHA-256 格式非法: {document.source_file}")
            source_path = self.source_path(document)
            if not source_path.is_file():
                raise ValueError(f"客服 fixture 源文件不存在: {source_path}")
            actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if actual_hash != document.sha256:
                raise ValueError(
                    f"客服 fixture 内容与锁定版本不符: {document.source_file}"
                )
            doc_ids.add(document.fixture_doc_id)
            source_files.add(document.source_file)
            headings_by_doc_id[document.fixture_doc_id] = {
                match.group(1).strip()
                for match in re.finditer(
                    r"^#{1,6}\s+(.+?)\s*$",
                    source_path.read_text(encoding="utf-8"),
                    flags=re.MULTILINE,
                )
            }

        for fact_source in self.fact_sources:
            if not _FACT_ID_RE.fullmatch(fact_source.fact_id):
                raise ValueError(f"客服事实 ID 格式非法: {fact_source.fact_id}")
            if fact_source.fixture_doc_id not in doc_ids:
                raise ValueError(
                    f"客服事实 ID 引用未知 fixture: {fact_source.fact_id}"
                )
            if not fact_source.sections or any(
                not isinstance(section, str) or not section.strip()
                for section in fact_source.sections
            ):
                raise ValueError(f"客服事实 ID 缺少章节来源: {fact_source.fact_id}")
            missing_sections = sorted(
                set(fact_source.sections)
                - headings_by_doc_id[fact_source.fixture_doc_id]
            )
            if missing_sections:
                raise ValueError(
                    f"客服事实 ID 引用不存在的章节 {fact_source.fact_id}: "
                    f"{missing_sections}"
                )

    def document_by_id(self) -> dict[str, CSFixtureDocument]:
        return {document.fixture_doc_id: document for document in self.documents}

    def fact_ids_for(self, fixture_doc_id: str, section: str) -> list[str]:
        normalized = _normalize_section(section)
        return sorted({
            fact.fact_id
            for fact in self.fact_sources
            if fact.fixture_doc_id == fixture_doc_id
            and normalized in {_normalize_section(item) for item in fact.sections}
        })

    def supports_fact_id(self, fact_id: str) -> bool:
        """校验锁版客服 suite 中的静态 KB 与 demo-order ID。"""
        if any(item.fact_id == fact_id for item in self.fact_sources):
            return True
        return bool(
            re.fullmatch(r"db:demo_order#DEMO-10\d{2}\.(status|amount|date)", fact_id)
            or fact_id == "db:demo_order#列表语义"
        )


def load_cs_fixture_catalog(path: Path | None = None) -> CSFixtureCatalog:
    """读取并校验客服测试语料；不访问数据库或模型服务。"""
    manifest_path = path or _MANIFEST_PATH
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("客服 fixture manifest schema_version 无效")
    raw_documents = data.get("documents")
    raw_facts = data.get("fact_sources")
    if not isinstance(raw_documents, list) or not isinstance(raw_facts, list):
        raise ValueError("客服 fixture manifest documents/fact_sources 必须为列表")

    documents = tuple(
        CSFixtureDocument(
            fixture_doc_id=str(item.get("fixture_doc_id", "")),
            source_file=str(item.get("source_file", "")),
            sha256=str(item.get("sha256", "")),
        )
        for item in raw_documents if isinstance(item, dict)
    )
    fact_sources = tuple(
        CSFactSource(
            fact_id=str(item.get("fact_id", "")),
            fixture_doc_id=str(item.get("fixture_doc_id", "")),
            sections=tuple(str(section) for section in item.get("sections", [])),
        )
        for item in raw_facts if isinstance(item, dict)
    )
    catalog = CSFixtureCatalog(
        kb_id=str(data.get("kb_id", "")),
        fixture_set=str(data.get("fixture_set", "")),
        fixture_version=str(data.get("fixture_version", "")),
        version_id=str(data.get("version_id", "")),
        source_root=str(data.get("source_root", "")),
        documents=documents,
        fact_sources=fact_sources,
    )
    catalog.validate()
    return catalog


def fact_ids_for_rag_source(source: dict[str, Any], *, kb_id: str) -> list[str]:
    """仅从本版本测试库的稳定文档 ID、文件名和精确章节导出事实 ID。"""
    if kb_id != CS_EVAL_KB_ID:
        return []
    catalog = load_cs_fixture_catalog()
    if source.get("version_id") != catalog.version_id:
        return []
    doc_id = str(source.get("doc_id") or "")
    document = catalog.document_by_id().get(doc_id)
    if document is None:
        return []
    if Path(str(source.get("filename") or "")).name != Path(document.source_file).name:
        return []
    return catalog.fact_ids_for(doc_id, str(source.get("section") or ""))


def _normalize_section(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).casefold()
