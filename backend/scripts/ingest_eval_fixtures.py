"""R3 后续：把评测 fixtures（rag_100_docs）部署进主语料目录并索引入库（v3）。

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
  ① 部署 25 份文本 fixtures 到 data/docs/rag_100_docs/general/{format}/
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
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.config.database import DOC_REGISTRY_PATH, DOCS_DIRECTORY  # noqa: E402
from backend.rag.indexing.doc_registry import DocumentRegistry  # noqa: E402
from backend.shared.logger import logger  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "backend/evaluation/fixtures/rag_100_docs"
FIXTURE_FILES = FIXTURE_DIR / "files"  # manifest.file 相对此目录
MANIFEST = FIXTURE_DIR / "manifest.json"
KB_ID = "rag_100_docs"
DEST_DIR = Path(DOCS_DIRECTORY) / KB_ID / "general"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _refresh_version():
    from backend.rag.pipeline import RAGPipeline
    from backend.config.database import CHROMA_PATH, DOC_DB_PATH
    for db_path in (CHROMA_PATH, DOC_DB_PATH):
        if Path(db_path).exists():
            (Path(db_path) / ".version").write_text(
                RAGPipeline._compute_db_version(), encoding="utf-8"
            )


def main() -> int:
    include_scanned = "--include-scanned" in sys.argv
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    docs = manifest["documents"]
    texts = docs if include_scanned else [
        d for d in docs if not d.get("is_scanned")]
    scans = [d for d in docs if d.get("is_scanned")]
    if include_scanned:
        # OCR 预检：在线 OCR 不可用（供应商 off / 未配 Key）时拒绝把扫描件入库
        from backend.rag.preprocessing.parser import ocr as ocr_mod
        if not ocr_mod.ocr_available():
            print("[ABORT] --include-scanned 需要在线 OCR 可用"
                  "（RAG_OCR_PROVIDER=dashscope 且已配置 API Key）")
            return 2
        print(f"[OCR] 供应商 {ocr_mod.rag_cfg.RAG_OCR_PROVIDER} 就绪，"
              f"扫描件 {len(scans)} 份将走 OCR 入库")
    print(f"fixtures: {len(docs)} 份（文本 {len(docs) - len(scans)} + 扫描件 {len(scans)}），kb_id={KB_ID}")

    registry = DocumentRegistry(DOC_REGISTRY_PATH)

    # ① 部署文本 fixtures；扫描件反向清理（不进 data/docs）
    paths: dict[str, str] = {}
    for d in texts:
        src = FIXTURE_FILES / d["file"]
        if not src.is_file():
            print(f"[MISS] {d['doc_id']} <- {d['file']}")
            continue
        dest = DEST_DIR / d["file"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
        paths[d["doc_id"]] = str(dest)
    for d in scans:
        if include_scanned:
            continue  # 扫描件随文本一起部署（下方 texts 循环已含）
        stray = DEST_DIR / d["file"]
        if stray.exists():
            stray.unlink()
            print(f"[PURGE-FILE] 扫描件移出 data/docs: {d['file']}")
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
    slugs = set(paths.keys())
    _perm_by_slug = {d["doc_id"]: d.get("permission_scope") or "general" for d in docs}
    # §6 版本治理（R4）：fixtures 的 version 元数据（TRAVEL_VERSIONS 链）随
    # slug 行写入 registry —— 索引管线经 doc_row 读出后贯通 chunk metadata
    _ver_by_slug: dict[str, dict] = {
        d["doc_id"]: (d.get("version") or {}) for d in docs
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
                      **_version_meta(slug)},
        )
    print(f"slug 行注册: {len(paths)}；待清理 hash doc_id: {len(hash_ids)}")

    # ③ 干净启动（sync：25 slug 行 unchanged、主语料 unchanged、无扫描件）
    _refresh_version()
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

    # ⑥ 校验 + 刷新版本
    rows = registry.list_all()
    bad = []
    for slug, fpath in paths.items():
        r = rows.get(fpath) or {}
        if r.get("doc_id") != slug or r.get("status") != "active":
            bad.append(slug)
    _refresh_version()

    print("\n===== 汇总 =====")
    print(f"索引成功: {len(ok)} | 失败: {len(failed)} | 行校验异常: {bad or '无'}")
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
