"""把统一评测 fixtures 部署进隔离测试 KB 并索引入库。

踩坑记录（为什么流程长这样）：
  1. pipeline 启动 sync 是 data/docs 全量 delta diff——registry 中路径不在
     data/docs 下的 active 行一律标删。fixtures 必须部署进 data/docs。
  2. sync 的 modified 分支先 _remove_document 再 _index_file，预注册的
     slug 行会被删掉导致 md5 协议重派 doc_id。因此 slug 行必须带真实
     file_hash 让 sync 视为 unchanged，重索引由本脚本在 sync 之后直调。
  3. 扫描件（无文字层 PDF）历史上会让 sync 抛 RuntimeError → 管线回退
     「全量重建」路径 → _sync_registry_after_full_rebuild 执行
     registry.clear() + 全量 md5 重派（near-dup pending 状态全丢）。
     ⚠️ 2026-09-17 起两点已变：cd92801 加固后单文件失败只跳过不再回退
     全量重建；在线 OCR（D6，RAG_OCR_PROVIDER=dashscope）接通后扫描件
     可正常解析。因此扫描件可用 --include-scanned 正式入库（脚本会先
     ocr_available() 预检，OCR 不可用则拒绝执行，仍保持"绝不无 OCR 进库"）。
  4. 版本指纹 .version 覆盖 data/docs 全目录，文件增删后必须刷新，
     否则触发全量重建（同 3 的灾难）。

幂等流程：
  ① 部署指定 fixture_set 的文本 fixtures 到 data/docs/rag_eval_kb/general/{format}/
     （is_scanned 的 2 份排除；若早前已部署则移除）
  ② 记录现存 fixture 行的 hash doc_id（待清理向量）；slug 行以
     「真实 file_hash + active」注册（sync 视为 unchanged，不被动）
  ③ pipeline init（干净增量 sync）
  ④ _remove_document 清掉全部 hash doc_id 的向量/chunk/BM25 残留
  ⑤ slug 行改回 file_hash=''，逐文件 _index_file → 全链路 slug
     （chunk_id 确定性 = f"{slug}_{i}"），结束后 register 写回真实 hash
  ⑥ 刷新 .version；汇总断言
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.config.database import DOC_REGISTRY_PATH, DOCS_DIRECTORY  # noqa: E402
from backend.evaluation.dataset.fixture_catalog import (  # noqa: E402
    RAG_EVAL_KB_ID,
    FixtureDocument,
    load_fixture_catalog,
)
from backend.rag.indexing.doc_registry import DocumentRegistry  # noqa: E402
from backend.shared.logger import logger  # noqa: E402

KB_ID = RAG_EVAL_KB_ID
DEST_DIR = Path(DOCS_DIRECTORY) / KB_ID / "general"


# 弃用别名库（rag-eval-kb-unification 计划：rag_test_kb / rag_100_docs 保留为
# 只读兼容别名一个迁移周期）。迁移副本与这些库内容互为拷贝属预期，不是质量事故。
DEPRECATED_ALIAS_KBS = frozenset({"rag_100_docs", "rag_test_kb"})


def activate_reviewed_migration_copies(
    registry, paths: dict[str, str], deprecated_alias_kbs: frozenset[str] = DEPRECATED_ALIAS_KBS
) -> list[str]:
    """迁移副本知情激活：近重复源在弃用别名库中的 pending_review 行翻为 active。

    全局 MinHash 查重（2026-09-17 事故修复）会把与遗留库同内容的迁移副本
    判为 near_dup → pending_review；本迁移的夹具复用旧 fixture 文件属预期拷贝，
    必须由本函数显式裁决激活并留痕。同 KB 内部重复或查无来源的近重复
    不在豁免之列，保持 pending_review 交人工审核。
    """
    activated: list[str] = []
    for slug, fpath in paths.items():
        row = registry.get_by_path(fpath) or {}
        if row.get("status") != "pending_review" or not row.get("near_dup_id"):
            continue
        source = registry.get_by_doc_id(row["near_dup_id"])
        if not source or source.get("kb_id") not in deprecated_alias_kbs:
            print(
                f"[HOLD-FOR-REVIEW] {slug}: 近重复源 "
                f"{source.get('kb_id') if source else '未知'}/{row['near_dup_id']} 不在弃用别名库，保持 pending_review"
            )
            continue
        registry.update_status(fpath, "active")
        activated.append(slug)
        print(
            f"[ACTIVATE-REVIEWED] {slug} ← 近重复源 {source.get('kb_id')}/{row['near_dup_id']}"
            f"（弃用别名库迁移副本，知情激活）"
        )
    return activated


@dataclass(frozen=True)
class FixtureIngestResult:
    """一次 fixture 入库的可审计结果。"""

    fixture_doc_id: str
    file_path: str
    metadata: dict[str, Any]
    index_result: dict[str, Any]
    skipped: bool = False


def _fixture_source_path(document: FixtureDocument) -> Path:
    """解析 catalog 中的源文件；不允许以当前工作目录猜测路径。"""
    source = Path(document.source_file)
    if source.is_absolute() and source.is_file():
        return source
    candidate = REPO_ROOT / source
    if candidate.is_file():
        return candidate
    if document.fixture_set == "expanded_100":
        candidate = REPO_ROOT / "backend/evaluation/fixtures/rag_100_docs/files" / source
    if not candidate.is_file():
        raise FileNotFoundError(f"fixture 源文件不存在: {document.source_file}")
    return candidate


def _fixture_metadata(document: FixtureDocument) -> dict[str, Any]:
    """构造入库元数据，明确保留统一 KB 与 fixture 范围。"""
    if document.kb_id != RAG_EVAL_KB_ID:
        raise ValueError(f"fixture 必须写入 {RAG_EVAL_KB_ID}: {document.kb_id}")
    metadata = dict(document.metadata)
    legacy = metadata.pop("legacy_metadata", {})
    if isinstance(legacy, dict):
        metadata = {**legacy, **metadata}
    metadata.update(
        {
            "kb_id": RAG_EVAL_KB_ID,
            "fixture_doc_id": document.fixture_doc_id,
            "fixture_set": document.fixture_set,
        }
    )
    return metadata


def fixture_document_from_path(fixture_path: Path, fixture_set: str) -> FixtureDocument:
    """从统一 catalog 反查文档，避免调用方手写稳定 ID。"""
    path = fixture_path.resolve()
    catalog = load_fixture_catalog()
    for document in catalog.documents(fixture_set):
        if _fixture_source_path(document).resolve() == path:
            return document
    raise ValueError(f"catalog 中找不到 fixture: set={fixture_set}, path={fixture_path}")


def ingest_fixture(
    document: FixtureDocument,
    *,
    registry: Any,
    indexer: Any,
) -> FixtureIngestResult:
    """以稳定 fixture ID 幂等入库单份文档，并把范围写入 registry。"""
    source_path = _fixture_source_path(document)
    metadata = _fixture_metadata(document)
    file_hash = _sha256(source_path)
    existing = registry.get_by_path(str(source_path)) or {}
    if (
        existing.get("doc_id") == document.fixture_doc_id
        and existing.get("file_hash") == file_hash
        and existing.get("status") == "active"
    ):
        return FixtureIngestResult(
            fixture_doc_id=document.fixture_doc_id,
            file_path=str(source_path),
            metadata=metadata,
            index_result={"skipped": True, "doc_id": document.fixture_doc_id},
            skipped=True,
        )

    registry.register_in_progress(
        str(source_path),
        doc_id=document.fixture_doc_id,
        file_hash=file_hash,
        kb_id=RAG_EVAL_KB_ID,
        department=str(metadata.get("department") or "general"),
    )
    registry.update_fields(str(source_path), {"fixture_set": document.fixture_set})
    index_result = indexer._index_file(str(source_path), file_hash=file_hash) or {}
    return FixtureIngestResult(
        fixture_doc_id=document.fixture_doc_id,
        file_path=str(source_path),
        metadata=metadata,
        index_result=index_result,
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# 2026-09-18 Chroma→pgvector 收口：原 _refresh_version() 向 CHROMA_PATH/DOC_DB_PATH
# 磁盘目录写 .version 指纹，防 sync 误判触发全量重建。pgvector 轨下向量库在 PG 表、
# 目录恒不存在，_need_rebuild 的 .version 检查两端均已失效（增量由 registry 驱动），
# 该函数为 no-op 死代码，删除。


def main() -> int:
    include_scanned = "--include-scanned" in sys.argv
    requested_set = None
    for index, arg in enumerate(sys.argv):
        if arg == "--fixture-set" and index + 1 < len(sys.argv):
            requested_set = sys.argv[index + 1]
    if requested_set is None:
        requested_set = "expanded_100"
        print("[DEPRECATED] 未指定 --fixture-set，兼容映射到 expanded_100；新命令请显式指定。")

    catalog = load_fixture_catalog()
    catalog.validate()
    docs = catalog.documents(requested_set)
    if not docs:
        print(f"[ABORT] fixture_set={requested_set} 没有可入库文档")
        return 2
    legacy_meta = {
        document.fixture_doc_id: document.metadata.get("legacy_metadata", {})
        for document in docs
    }
    texts = list(docs) if include_scanned else [
        document for document in docs
        if not isinstance(legacy_meta[document.fixture_doc_id], dict)
        or not legacy_meta[document.fixture_doc_id].get("is_scanned")
    ]
    scans = [
        document for document in docs
        if isinstance(legacy_meta[document.fixture_doc_id], dict)
        and legacy_meta[document.fixture_doc_id].get("is_scanned")
    ]
    if include_scanned:
        # OCR 预检：在线 OCR 不可用（供应商 off / 未配 Key）时拒绝把扫描件入库
        from backend.rag.preprocessing.parser import ocr as ocr_mod
        if not ocr_mod.ocr_available():
            print("[ABORT] --include-scanned 需要在线 OCR 可用"
                  "（RAG_OCR_PROVIDER=dashscope 且已配置 API Key）")
            return 2
        print(f"[OCR] 供应商 {ocr_mod.rag_cfg.RAG_OCR_PROVIDER} 就绪，"
              f"扫描件 {len(scans)} 份将走 OCR 入库")
    print(
        f"fixtures: {len(docs)} 份（文本 {len(texts)} + 扫描件 {len(scans)}），"
        f"kb_id={KB_ID}, fixture_set={requested_set}"
    )

    registry = DocumentRegistry(DOC_REGISTRY_PATH)

    # ① 部署文本 fixtures；扫描件反向清理（不进 data/docs）
    paths: dict[str, str] = {}
    for document in texts:
        src = _fixture_source_path(document)
        if not src.is_file():
            print(f"[MISS] {document.fixture_doc_id} <- {document.source_file}")
            continue
        legacy = legacy_meta[document.fixture_doc_id]
        relative_path = (
            Path(legacy["file"])
            if isinstance(legacy, dict) and legacy.get("file")
            else Path(src.name)
        )
        dest = DEST_DIR / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
        paths[document.fixture_doc_id] = str(dest)
    for document in scans:
        if include_scanned:
            continue  # 扫描件随文本一起部署（下方 texts 循环已含）
        legacy = legacy_meta[document.fixture_doc_id]
        relative_path = (
            Path(legacy["file"])
            if isinstance(legacy, dict) and legacy.get("file")
            else Path(document.source_file).name
        )
        stray = DEST_DIR / relative_path
        if stray.exists():
            stray.unlink()
            print(f"[PURGE-FILE] 扫描件移出 data/docs: {document.source_file}")
    print(f"部署: {len(paths)}/{len(texts)} 份文本")

    # ② 记录待清理的 hash doc_id；slug 行带真实 hash 注册（sync 跳过）
    all_rows = registry.list_all()
    hash_ids: set[str] = set()
    for p, r in all_rows.items():
        if r.get("kb_id") == KB_ID:
            hash_ids.add(r.get("doc_id", ""))
            if p not in paths.values() or r.get("status") not in ("active",):
                registry.mark_deleted(p)  # 旧路径/异常行一律标删
    hash_ids.discard("")
    _perm_by_slug = {
        document.fixture_doc_id: (
            legacy_meta[document.fixture_doc_id].get("permission_scope") or "general"
            if isinstance(legacy_meta[document.fixture_doc_id], dict)
            else "general"
        )
        for document in docs
    }
    # §6 版本治理（R4）：fixtures 的 version 元数据（TRAVEL_VERSIONS 链）随
    # slug 行写入 registry —— 索引管线经 doc_row 读出后贯通 chunk metadata
    _ver_by_slug: dict[str, dict] = {
        document.fixture_doc_id: (
            legacy_meta[document.fixture_doc_id].get("version") or {}
            if isinstance(legacy_meta[document.fixture_doc_id], dict)
            else {}
        )
        for document in docs
    }

    def _version_meta(slug: str) -> dict:
        v = _ver_by_slug.get(slug) or {}
        return {
            "version_id": v.get("version_id", ""),
            "effective_from": v.get("effective_from"),
            "effective_to": v.get("effective_to"),
            "supersedes_version_id": v.get("supersedes") or "",
        }

    for slug, fpath in paths.items():
        registry.register(
            fpath, doc_id=slug, file_hash=_sha256(Path(fpath)),
            kb_id=KB_ID, chunk_ids=[], doc_db_id="",
            metadata={"doc_type": "general", "department": "general",
                      "permission_scope": _perm_by_slug.get(slug, "general"),
                      "fixture_set": requested_set,
                      **_version_meta(slug)},
        )
    print(f"slug 行注册: {len(paths)}；待清理 hash doc_id: {len(hash_ids)}")

    # ③ 干净启动（sync：25 slug 行 unchanged、主语料 unchanged、无扫描件）
    from backend.rag.pipeline import get_rag_pipeline
    pipeline = get_rag_pipeline()

    from backend.rag.indexing.indexer import IncrementalIndexer
    indexer = IncrementalIndexer(
        docs_dir=str(Path(DOCS_DIRECTORY)),
        vectordb=pipeline.vectordb,
        doc_db=pipeline.doc_db,
        embedding=pipeline.embedding,
        registry=registry,
        kb_id=KB_ID,
        fixture_set=requested_set,
        bm25_store=pipeline.bm25_store,
    )

    # ④ 清理 hash doc_id 的全部存储残留
    for h in sorted(hash_ids):
        indexer._remove_document(h)
    if hash_ids:
        print(f"已清理 {len(hash_ids)} 个 hash doc_id 的向量/chunk/BM25 残留")

    # ⑤ slug 行 file_hash 置空 → 逐文件直调 _index_file（复用 slug）
    ok, failed = [], []
    for slug, fpath in paths.items():
        registry.register(
            fpath, doc_id=slug, file_hash="",
            kb_id=KB_ID, chunk_ids=[], doc_db_id="",
            metadata={"doc_type": "general", "department": "general",
                      "permission_scope": _perm_by_slug.get(slug, "general"),
                      "fixture_set": requested_set,
                      **_version_meta(slug)},
        )
        try:
            ret = indexer._index_file(fpath) or {}
            n = int(ret.get("chunk_count") or 0)
            ok.append((slug, n))
            print(f"[OK] {slug}: {n} chunks")
        except Exception as e:
            registry.update_status(fpath, "failed")
            failed.append((slug, f"{type(e).__name__}: {e}"))
            print(f"[FAIL] {slug}: {type(e).__name__}: {e}")
            logger.warning(f"[ingest] {slug} 索引失败", exc_info=True)

    # ⑤' 迁移副本知情激活（近重复源在弃用别名库 → active；其余保持人工审核）
    reviewed = activate_reviewed_migration_copies(registry, paths)
    print(f"知情激活: {len(reviewed)}（弃用别名库迁移副本）")

    # ⑥ 校验 + 刷新版本
    rows = registry.list_all()
    bad = []
    for slug, fpath in paths.items():
        r = rows.get(fpath) or {}
        if r.get("doc_id") != slug or r.get("status") != "active":
            bad.append(slug)

    print("\n===== 汇总 =====")
    print(f"索引成功: {len(ok)} | 失败: {len(failed)} | 知情激活: {len(reviewed)} | 行校验异常: {bad or '无'}")
    if failed:
        for slug, err in failed:
            print(f"  FAIL {slug}: {err}")
    main_active = sum(1 for p, r in rows.items()
                      if r.get("kb_id") != KB_ID and r.get("status") == "active")
    main_pending = sum(1 for p, r in rows.items()
                       if r.get("kb_id") != KB_ID and r.get("status") == "pending_review")
    print(f"主语料: active={main_active}, pending_review={main_pending}（基线应为 24+4）")
    return 1 if (failed or bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
