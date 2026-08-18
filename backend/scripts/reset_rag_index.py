"""清空 RAG 索引数据,便于重新跑端到端上传测试。

清空范围(只清索引数据,不动 PostgreSQL / 用户文件):
  - doc_registry.db           文档注册表
  - chunk_store.db            chunk 原文
  - chroma/chroma.sqlite3     chunk 级向量
  - doc_db/chroma.sqlite3     doc 级向量
  - bm25/                     BM25 倒排索引(整个目录)

不动:
  - data/docs/                 物理文档(测试还会重新扫描)
  - data/uploads/             上传临时区
  - trace_store.db            trace 持久化(留给评测后看)
  - PostgreSQL databases

幂等:目录不存在或为空时跳过,不抛错。
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, '.')

from backend.config.database import (
    DOCS_DIRECTORY,
    CHUNK_STORE_PATH,
    DOC_REGISTRY_PATH,
    CHROMA_PATH,
    DOC_DB_PATH,
    BM25_INDEX_DIR,
)
from backend.shared.logger import logger


def _wipe_sqlite(path: Path, label: str) -> int:
    """清空 SQLite 表(不删文件,只 DELETE FROM)。

    比 truncate file 安全:保留 schema,后续启动可继续用。
    Returns:
        删除的记录数(估算)。
    """
    if not path.exists():
        logger.info(f"[reset] {label} 文件不存在,跳过: {path}")
        return 0
    try:
        conn = sqlite3.connect(str(path))
        cur = conn.cursor()
        # 通用:找所有表名,逐一清空
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]
        total = 0
        for tbl in tables:
            try:
                cur.execute(f"DELETE FROM {tbl}")
                total += cur.rowcount
            except Exception as e:
                logger.warning(f"[reset] {label} 清表 {tbl} 失败: {e}")
        conn.commit()
        conn.close()
        logger.info(f"[reset] {label} 清空: {total} 行 ({len(tables)} 表)")
        return total
    except Exception as e:
        logger.error(f"[reset] {label} 失败: {e}", exc_info=True)
        return 0


def _wipe_chroma(path: Path, label: str) -> int:
    """清空 ChromaDB 目录(保留目录结构)。

    ChromaDB 是 SQLite-backed,最稳的清空方式是删整个目录,
    启动时会自动重建空库。
    """
    if not path.exists():
        logger.info(f"[reset] {label} 目录不存在,跳过: {path}")
        return 0
    try:
        # 删目录下所有文件(包括 chroma.sqlite3 + uuid 子目录)
        file_count = 0
        for child in path.iterdir():
            if child.is_file():
                child.unlink()
                file_count += 1
            elif child.is_dir():
                shutil.rmtree(child)
                file_count += 1
        logger.info(f"[reset] {label} 已清空: {file_count} 个文件/目录")
        return file_count
    except Exception as e:
        logger.error(f"[reset] {label} 失败: {e}", exc_info=True)
        return 0


def _wipe_bm25(path: Path) -> int:
    """清空 BM25 索引目录(整目录删除)。"""
    if not path.exists():
        logger.info(f"[reset] BM25 目录不存在,跳过: {path}")
        return 0
    try:
        file_count = 0
        for child in path.iterdir():
            if child.is_file():
                child.unlink()
                file_count += 1
            elif child.is_dir():
                shutil.rmtree(child)
                file_count += 1
        logger.info(f"[reset] BM25 已清空: {file_count} 个文件/目录")
        return file_count
    except Exception as e:
        logger.error(f"[reset] BM25 失败: {e}", exc_info=True)
        return 0


def main() -> int:
    logger.info("=" * 60)
    logger.info("[reset] 开始清空 RAG 索引数据")
    logger.info(f"  DOCS_DIRECTORY = {DOCS_DIRECTORY} (保留,不动)")
    logger.info("=" * 60)

    total = 0
    total += _wipe_sqlite(Path(DOC_REGISTRY_PATH), "doc_registry")
    total += _wipe_sqlite(Path(CHUNK_STORE_PATH), "chunk_store")
    total += _wipe_chroma(Path(CHROMA_PATH), "chroma/chunks")
    total += _wipe_chroma(Path(DOC_DB_PATH), "doc_db/docs")
    total += _wipe_bm25(Path(BM25_INDEX_DIR))

    logger.info("=" * 60)
    logger.info(f"[reset] 完成: 总清理条目数 = {total}")
    logger.info("[reset] 下一步:重启后端 → /upload 测试")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())