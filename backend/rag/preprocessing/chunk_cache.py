"""chunk 指纹缓存 — 文档未变时跳过 解析 → 分类 → 分块 全流水线。

背景：预热/重建索引时 loader 对每个文件无条件跑 parse_and_chunk，
其中分类胶着时的 LLM 仲裁是网络调用（冷启动可达数十秒），而增量
索引最终常判定 skipped —— 解析全部白做还烧 token。

本模块以 (mtime_ns, size, sha256, PIPELINE_VERSION) 为指纹把 chunks
缓存到磁盘（pickle，langchain Document 可序列化），指纹命中直接复用，
含 doc_type 在内的 chunk metadata 一并保留，LLM 仲裁结果随之复用。

注意：仅 (mtime_ns, size) 不够 —— cp -p / git checkout / rsync -a 会
保留 mtime 与 size 却改了内容；内容 SHA256 保证缓存与磁盘一致。
"""
from __future__ import annotations

import os
import pickle
import tempfile
from pathlib import Path
from typing import List, Optional

from langchain_core.documents import Document

from backend.config.database import RAG_DATA_DIR
from backend.shared.logger import logger

# 流水线行为版本：解析器/清洗/分类/分块策略有语义变化时 +1，旧缓存整体失效
PIPELINE_VERSION = 2

_CACHE_DIR = Path(RAG_DATA_DIR) / "chunk_cache"


def _stat_fingerprint(file_path: str) -> Optional[tuple]:
    """快速文件指纹：(mtime_ns, size)。取不到 stat 返回 None（视为不可缓存）。"""
    try:
        st = os.stat(file_path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _content_hash(file_path: str) -> Optional[str]:
    """文件内容 SHA256（分块读取，控制内存峰值）。"""
    import hashlib
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def _fingerprint(file_path: str) -> Optional[tuple]:
    """文件指纹：(mtime_ns, size, sha256)。stat 或内容读取失败返回 None。"""
    stat_fp = _stat_fingerprint(file_path)
    if stat_fp is None:
        return None
    content = _content_hash(file_path)
    if content is None:
        return None
    return stat_fp + (content,)


def _cache_path(file_path: str) -> Path:
    """缓存文件路径：绝对路径 sha1 + PIPELINE_VERSION 命名，避开路径非法字符。"""
    import hashlib
    digest = hashlib.sha1(os.path.abspath(file_path).encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"v{PIPELINE_VERSION}_{digest}.pkl"


def load_cached_chunks(file_path: str) -> Optional[List[Document]]:
    """指纹命中返回缓存的 chunks，否则返回 None（损坏缓存按 miss 处理并清理）。"""
    fp = _fingerprint(file_path)
    if fp is None:
        return None
    path = _cache_path(file_path)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
        if payload.get("fingerprint") != fp or payload.get("version") != PIPELINE_VERSION:
            return None
        chunks = payload.get("chunks")
        if not isinstance(chunks, list):
            return None
        return chunks
    except Exception:
        # 损坏/不兼容的缓存：删除后按 miss 处理
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def save_cached_chunks(file_path: str, chunks: List[Document]) -> None:
    """原子写入缓存（tmp + os.replace），并发上传场景下不会读到半截文件。"""
    fp = _fingerprint(file_path)
    if fp is None or not chunks:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=_CACHE_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                pickle.dump(
                    {"version": PIPELINE_VERSION, "fingerprint": fp, "chunks": chunks},
                    f,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            os.replace(tmp_name, _cache_path(file_path))
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
    except Exception as e:
        # 缓存写入失败不影响主流程，仅损失下次启动的加速
        logger.debug(f"[ChunkCache] 写入失败（忽略）: {file_path} - {e}")


def clear_cache() -> int:
    """清空全部 chunk 缓存，返回删除的文件数（维护/排查用）。"""
    if not _CACHE_DIR.exists():
        return 0
    n = 0
    for p in _CACHE_DIR.glob("*.pkl"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
