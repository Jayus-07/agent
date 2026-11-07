"""显式导入客服 full_path baseline fixtures 到 test-only cs_eval_kb。

此入口不会被生产客服 KB seeder 调用。命令必须同时指定 fixture_set 和
--apply；索引会调用当前配置的 embedding Provider。
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path
from typing import Any

from backend.evaluation.cs_fixture_catalog import (
    CS_EVAL_FIXTURE_SET,
    CS_EVAL_KB_ID,
    CS_EVAL_VERSION_ID,
    CSFixtureDocument,
    load_cs_fixture_catalog,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_test_kb_path(path: Path | str, test_kb_root: Path) -> Path:
    """只接受专用 test KB 根目录下的真实路径，拒绝越界/符号链接。"""
    root = test_kb_root.resolve()
    resolved = Path(path).resolve()
    if root not in resolved.parents:
        raise RuntimeError(f"拒绝访问客服 test KB 根目录之外的路径: {path}")
    return resolved


def _require_test_kb() -> None:
    from backend.config.knowledge_base import get_knowledge_bases

    info = get_knowledge_bases().get(CS_EVAL_KB_ID)
    if not info or info.get("audience") != "test":
        raise RuntimeError(
            f"拒绝导入：{CS_EVAL_KB_ID} 必须存在且 audience=test"
        )


def _fixture_metadata(document: CSFixtureDocument, fixture_version: str) -> dict[str, Any]:
    return {
        "doc_type": "general",
        "department": "general",
        "permission_scope": "general",
        "fixture_set": CS_EVAL_FIXTURE_SET,
        "fixture_doc_id": document.fixture_doc_id,
        "fixture_version": fixture_version,
        "version_id": CS_EVAL_VERSION_ID,
    }


def ingest_cs_eval_fixtures(*, fixture_set: str, apply: bool) -> dict[str, Any]:
    """以稳定 doc ID 幂等索引客服评测语料；不触碰其它 KB。"""
    if fixture_set != CS_EVAL_FIXTURE_SET:
        raise ValueError(f"只支持 --fixture-set {CS_EVAL_FIXTURE_SET}")
    if not apply:
        raise ValueError("索引会调用 embedding Provider；必须显式传 --apply")

    catalog = load_cs_fixture_catalog()
    _require_test_kb()

    from backend.config.database import DOC_REGISTRY_PATH, DOCS_DIRECTORY
    from backend.rag.indexing.doc_registry import DocumentRegistry
    from backend.rag.indexing.indexer import IncrementalIndexer
    from backend.rag.indexing.processing_lineage_pg import (
        get_processing_lineage_repository,
    )
    from backend.rag.pipeline import RAGPipeline

    docs_root = Path(DOCS_DIRECTORY).resolve()
    test_kb_path = Path(DOCS_DIRECTORY) / CS_EVAL_KB_ID
    if test_kb_path.is_symlink():
        raise RuntimeError(f"拒绝将客服 test KB 配置为符号链接: {test_kb_path}")
    test_kb_root = test_kb_path.resolve()
    if docs_root not in test_kb_root.parents:
        raise RuntimeError(
            f"客服 test KB 路径不在文档根目录内: {test_kb_path}"
        )
    destination = _resolve_test_kb_path(
        test_kb_root / "general",
        test_kb_root,
    )
    destination.mkdir(parents=True, exist_ok=True)
    registry = DocumentRegistry(DOC_REGISTRY_PATH)
    pipeline = RAGPipeline(mode="index", auto_sync=False)
    indexer = IncrementalIndexer(
        docs_dir=str(Path(DOCS_DIRECTORY)),
        vectordb=pipeline.vectordb,
        doc_db=pipeline.doc_db,
        embedding=pipeline.embedding,
        registry=registry,
        kb_id=CS_EVAL_KB_ID,
        fixture_set=CS_EVAL_FIXTURE_SET,
        bm25_store=pipeline.bm25_store,
        processing_lineage_repository=get_processing_lineage_repository(),
        processing_task_id="cs_eval_fixture_ingest",
        processing_batch_id=CS_EVAL_FIXTURE_SET,
    )

    expected_ids = {document.fixture_doc_id for document in catalog.documents}
    rows = registry.list_all()
    # cs_eval_kb 专用于测试。清理只针对本 KB 中不属于当前锁版 manifest 的
    # 行；生产 KB、其它评测 KB 和文件目录均不进入清理范围。
    removed: list[str] = []
    for registered_path, row in rows.items():
        if row.get("kb_id") != CS_EVAL_KB_ID:
            continue
        stale_path = _resolve_test_kb_path(registered_path, test_kb_root)
        doc_id = str(row.get("doc_id") or "")
        if not doc_id:
            raise RuntimeError(
                f"拒绝处理缺少 doc_id 的客服 test KB 注册路径: {registered_path}"
            )
        if doc_id not in expected_ids:
            indexer._remove_document(doc_id, str(stale_path))
            registry.mark_deleted(str(stale_path))
            if stale_path.is_file():
                stale_path.unlink()
            removed.append(doc_id)

    indexed: list[dict[str, Any]] = []
    for document in catalog.documents:
        source_path = catalog.source_path(document)
        if _sha256(source_path) != document.sha256:
            raise ValueError(f"客服 fixture 内容已变化: {document.source_file}")
        target_path = _resolve_test_kb_path(
            destination / document.source_file,
            test_kb_root,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not target_path.is_file() or _sha256(target_path) != document.sha256:
            shutil.copy2(source_path, target_path)

        metadata = _fixture_metadata(document, catalog.fixture_version)
        registry.register(
            str(target_path),
            doc_id=document.fixture_doc_id,
            file_hash="",
            kb_id=CS_EVAL_KB_ID,
            chunk_ids=[],
            doc_db_id="",
            metadata=metadata,
        )
        result = indexer._index_file(str(target_path)) or {}
        indexed.append({
            "fixture_doc_id": document.fixture_doc_id,
            "chunks": int(result.get("chunk_count") or 0),
        })

    rows = registry.list_all()
    by_id = {
        str(row.get("doc_id") or ""): row
        for row in rows.values()
        if row.get("kb_id") == CS_EVAL_KB_ID
    }
    invalid = sorted(
        document.fixture_doc_id
        for document in catalog.documents
        if (
            document.fixture_doc_id not in by_id
            or by_id[document.fixture_doc_id].get("status") != "active"
        )
    )
    if invalid:
        raise RuntimeError(
            "客服 fixture 未全部进入 active 状态；请检查重复审核或索引失败："
            + ", ".join(invalid)
        )

    return {
        "kb_id": CS_EVAL_KB_ID,
        "audience": "test",
        "fixture_set": CS_EVAL_FIXTURE_SET,
        "version_id": CS_EVAL_VERSION_ID,
        "indexed": indexed,
        "removed_stale_test_doc_ids": sorted(removed),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-set", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    result = ingest_cs_eval_fixtures(
        fixture_set=args.fixture_set,
        apply=args.apply,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
