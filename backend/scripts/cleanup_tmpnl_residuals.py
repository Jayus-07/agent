"""清理 tmpnl*_测评上传入库_*.md 残留。

背景:两个临时上传文件以 GBK 编码写入磁盘(MIME 校验宽 + Windows multipart
filename 编码问题,P0-2 已记录),但已通过 SHA256 diff 检查入库到 doc_db /
chroma / chunk_store。它们没有对应业务语义、文件名乱码,且无意义地占用
policy_general KB 的检索名额。

清理策略:
  1. 物理删除磁盘文件(让 incremental sync 自然跳过)
  2. 软删 doc_registry 记录(便于审计 + 防复活)
  3. 从 ChromaDB doc_db / vectordb / chunk_store 删向量+chunk
  4. 留 INFO 级日志,可观测

幂等:重复运行不会报错(没记录就跳过)。

实现注意:不走 pipeline 实例化(避免加载 BGE embedding 模型,启动 ~15s)。
直接 chromadb.PersistentClient 操作 SQLite。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, '.')

import chromadb

from backend.rag.indexing.doc_registry import DocumentRegistry
from backend.config.database import DOCS_DIRECTORY, CHROMA_PATH, DOC_DB_PATH
from backend.shared.logger import logger

POLICY_GENERAL = "policy_general"
GENERAL_DEPT = "general"
# 注意：文件名原始字节是 GBK 编码写入磁盘（Windows multipart + 上传链路 P0-2 漏洞），
# 在 PowerShell/某些终端会被误显示，所以用宽匹配前缀 + 后缀作为指纹：
#   - 前缀 "tmp"（覆盖 tmpnl / tmpxdc 等 GBK 编码误读后呈现的多种形式）
#   - 必含上传链路标识 "_测评上传入库_"
TARGET_PREFIX = "tmp"
TARGET_SUFFIX = "_测评上传入库_"


def find_residual_files() -> list[Path]:
    """扫描 policy_general/general 下所有匹配 tmpnl*_测评上传入库_*.md 的文件。"""
    base = Path(DOCS_DIRECTORY) / POLICY_GENERAL / GENERAL_DEPT
    if not base.exists():
        logger.warning(f"[cleanup_tmpnl] 目录不存在: {base}")
        return []
    return [
        f for f in base.iterdir()
        if f.is_file()
        and f.suffix.lower() == ".md"
        and f.name.startswith(TARGET_PREFIX)
        and TARGET_SUFFIX in f.name
    ]


def collect_registry_rows(reg: DocumentRegistry, files: list[Path]) -> list[dict]:
    """为每个文件查 doc_registry,可能返回 raw path / realpath 两份记录(历史符号链接导致)。"""
    rows: list[dict] = []
    for f in files:
        real = os.path.realpath(f)
        for path in (str(f), real):
            row = reg.get_by_path(path)
            if row:
                rows.append(row)
    # 按 doc_id 去重(同一文件可能对应多条历史记录)
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in rows:
        did = r.get("doc_id", "")
        if did in seen:
            continue
        seen.add(did)
        deduped.append(r)
    return deduped


def _delete_doc_id_from_chroma(client: chromadb.PersistentClient, collection_name: str,
                                doc_id: str, persist_dir: str) -> int:
    """从指定 collection 删除某个 doc_id 的所有向量。返回删除数。

    严禁 except Exception: pass — 任何异常向上抛,调用方决定如何处理。
    """
    coll = client.get_or_create_collection(collection_name)
    res = coll.get(where={"doc_id": doc_id})
    ids = res.get("ids", [])
    if ids:
        coll.delete(ids=ids)
        logger.info(f"[cleanup_tmpnl]   {collection_name}/{doc_id[:12]}... 删 {len(ids)} 条")
    return len(ids)


def cleanup_chroma_for_doc_ids(doc_ids: list[str]) -> tuple[int, int]:
    """从 ChromaDB chunk + doc 两库删除指定 doc_id 的向量。
    返回 (chunk_total, doc_total)。失败不影响其他 doc_id。
    """
    chunk_total = doc_total = 0
    if not doc_ids:
        return chunk_total, doc_total
    # chromadb.PersistentClient 不需要 embedding_function,启动快
    chunk_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    doc_client = chromadb.PersistentClient(path=str(DOC_DB_PATH))
    for did in doc_ids:
        try:
            chunk_total += _delete_doc_id_from_chroma(chunk_client, "chunks", did, str(CHROMA_PATH))
        except Exception as e:
            logger.error(f"[cleanup_tmpnl] chunks 删除 {did} 失败: {e}", exc_info=True)
        try:
            doc_total += _delete_doc_id_from_chroma(doc_client, "docs", did, str(DOC_DB_PATH))
        except Exception as e:
            logger.error(f"[cleanup_tmpnl] docs 删除 {did} 失败: {e}", exc_info=True)
    return chunk_total, doc_total


def cleanup_chunk_store_for_doc_ids(doc_ids: list[str]) -> int:
    """从 chunk_store SQLite 删除指定 doc_id 的 chunk 原文。"""
    if not doc_ids:
        return 0
    try:
        from backend.rag.indexing.chunk_store import get_chunk_store
        store = get_chunk_store()
        total = 0
        for did in doc_ids:
            try:
                deleted = store.delete_by_doc_id(did)
                total += deleted
            except Exception as e:
                logger.error(f"[cleanup_tmpnl] chunk_store.delete {did} 失败: {e}", exc_info=True)
        logger.info(f"[cleanup_tmpnl] chunk_store 删除完成: {total} 条")
        return total
    except Exception as e:
        logger.error(f"[cleanup_tmpnl] chunk_store 访问失败: {e}", exc_info=True)
        return 0


def main() -> int:
    reg = DocumentRegistry("data/doc_registry.db")
    files = find_residual_files()
    if not files:
        logger.info("[cleanup_tmpnl] 未发现残留文件,无需清理")
        return 0
    logger.info(f"[cleanup_tmpnl] 发现 {len(files)} 个残留文件:")
    for f in files:
        logger.info(f"  - {f}")

    rows = collect_registry_rows(reg, files)
    doc_ids = sorted({r["doc_id"] for r in rows})
    if not doc_ids:
        logger.warning("[cleanup_tmpnl] 残留文件均未注册,仅删文件")
    else:
        logger.info(f"[cleanup_tmpnl] 涉及 doc_id: {doc_ids}")
        chunk_n, doc_n = cleanup_chroma_for_doc_ids(doc_ids)
        cs_n = cleanup_chunk_store_for_doc_ids(doc_ids)
        logger.info(
            f"[cleanup_tmpnl] Chroma 清理: chunks={chunk_n}, docs={doc_n}; "
            f"chunk_store: {cs_n}"
        )

    # 软删 registry + 物理删文件
    deleted_files = 0
    deleted_rows = 0
    for r in rows:
        try:
            affected = reg.mark_deleted_by_doc_id(r["doc_id"])
            deleted_rows += affected
        except Exception as e:
            logger.error(f"[cleanup_tmpnl] mark_deleted_by_doc_id {r['doc_id']} 失败: {e}", exc_info=True)
    for f in files:
        try:
            f.unlink()
            deleted_files += 1
            logger.info(f"[cleanup_tmpnl] 已删除物理文件: {f.name}")
        except FileNotFoundError:
            pass  # 已被删,跳过
        except OSError as e:
            logger.error(f"[cleanup_tmpnl] 删除文件失败 {f}: {e}", exc_info=True)

    logger.info(
        f"[cleanup_tmpnl] 完成: 物理文件={deleted_files}, "
        f"软删 registry 行={deleted_rows}, doc_id={len(doc_ids)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())