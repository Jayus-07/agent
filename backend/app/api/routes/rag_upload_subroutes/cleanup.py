"""RAG Upload 临时文件清理、垃圾回收与回收站。"""
from __future__ import annotations

import asyncio
import os
import shutil
import time

from backend.app.api.routes.rag_upload_subroutes.base import *
from backend.rag.indexing.publish import cleanup_candidate_by_generation

def _cleanup_candidate_artifacts(upload_id: str, staging_path: str = "") -> None:
    """按运行记录清理候选代次产物（候选 collection 行 + BM25 staging 目录）。

    只在失败终态调用；全部动作幂等且不触碰主 collection / 正式 BM25。
    """
    try:
        from backend.config.database import (
            BM25_INDEX_DIR,
            CHROMA_PATH,
            DOC_DB_PATH,
        )
        from backend.rag.indexing.index_run_store_pg import get_index_run_store

        run = get_index_run_store().get_run(upload_id)
        generation = (run or {}).get("generation", "")
        if not generation:
            return
        cleanup_candidate_by_generation(
            generation,
            chroma_path=str(CHROMA_PATH),
            doc_db_path=str(DOC_DB_PATH),
            bm25_index_dir=str(BM25_INDEX_DIR),
        )
    except Exception as e:  # noqa: BLE001 — 清理失败留痕，残留交由 Sweeper
        logger.warning(f"[RAG] 候选产物清理失败 (upload={upload_id}): {e}")


def cleanup_stale_upload_artifacts(docs_dir: str, tmp_dir: str,
                                   max_age_seconds: float = LOCK_STALE_SECONDS) -> dict:
    """启动清理:回收 docs 目录下超龄 .lock 与中断上传遗留的孤儿 tmp 文件。

    两类残留都来自进程崩溃/强杀,不清理会持续积累:
      - .lock 残留 → 对应文件永久被 FileLockedByOtherError 拒绝
      - tmp 残留 → 磁盘占用泄漏
    只删 mtime 超过 max_age_seconds 的文件,不误伤进行中的上传/索引。

    Returns: {"locks_removed": int, "tmp_removed": int}
    """
    counts = {"locks_removed": 0, "tmp_removed": 0}
    now = time.time()

    for root, _dirs, files in os.walk(docs_dir):
        for fname in files:
            if not fname.endswith(".lock"):
                continue
            p = os.path.join(root, fname)
            try:
                if now - os.path.getmtime(p) > max_age_seconds:
                    os.unlink(p)
                    counts["locks_removed"] += 1
            except OSError as e:
                logger.warning(f"[RAG] stale 锁清理失败 {p}: {e}")

    if os.path.isdir(tmp_dir):
        # 非终态运行持有的暂存文件豁免（恢复 sweeper 可能随时重投，
        # 删了会让重跑任务断源）；其余孤儿按超龄清理
        protected: set[str] = set()
        try:
            from backend.rag.indexing.index_run_store_pg import get_index_run_store
            protected = get_index_run_store().list_non_terminal_staging_paths()
        except Exception as e:
            logger.warning(f"[RAG] 暂存豁免名单读取失败（按无豁免处理）: {e}")
        for root, _dirs, files in os.walk(tmp_dir):
            for fname in files:
                p = os.path.join(root, fname)
                if p in protected:
                    continue
                try:
                    if now - os.path.getmtime(p) > max_age_seconds:
                        os.unlink(p)
                        counts["tmp_removed"] += 1
                except OSError as e:
                    logger.warning(f"[RAG] 孤儿 tmp 清理失败 {p}: {e}")

    if counts["locks_removed"] or counts["tmp_removed"]:
        logger.info(f"[RAG] 启动清理: {counts['locks_removed']} 个 stale 锁, "
                    f"{counts['tmp_removed']} 个孤儿 tmp")
    return counts


def cleanup_expired_progress_queues() -> int:
    """清理超过 PROGRESS_QUEUE_TTL_SECONDS 的进度队列，返回清理条数。

    队列保留期跟随 SSE 总时限，避免后台索引仍可被订阅时本机队列先被清理。
    """
    now = time.time()
    expired = [uid for uid, q in facade._progress_queues.items()
               if getattr(q, "_created_at", 0) < now - PROGRESS_QUEUE_TTL_SECONDS]
    for uid in expired:
        facade._progress_queues.pop(uid, None)
        facade._progress_owners.pop(uid, None)
        facade._celery_routed.discard(uid)
    if expired:
        logger.info(f"[RAG] 清理过期进度队列 {len(expired)} 个")
    return len(expired)


async def progress_queue_gc_loop(
    interval_seconds: float = RAG_UPLOAD_PROGRESS_GC_INTERVAL_SECONDS,
) -> None:
    """后台定时清理过期进度队列（F11），由 server 启动时 create_task。

    异常不退出循环 — GC 任务自身挂掉比队列滞留更糟，单轮失败只告警。
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            facade.cleanup_expired_progress_queues()
        except Exception as e:
            logger.warning(f"[RAG] 进度队列定时清理异常: {e}")


async def _finalize_upload_queue(upload_id: str) -> None:
    """P1-3：向队列放 None 哨兵（让 SSE 流结束）。

    三处分支（失败 / duplicate / 成功）的统一收尾。
    注意：这里【不】主动 pop 队列 —— 后台索引可能先于客户端订阅 SSE 完成
    （小文件场景），若提前 pop，晚到的订阅者会收到“upload_id 不存在或已过期”
    而丢失全部终态事件（done/error/duplicate）。保留队列 + None 哨兵后，
    晚到的订阅者仍可完整消费事件流；队列回收交给两条既有路径：
      1. SSE event_stream 断连时 pop（finally 块）；
      2. cleanup_expired_progress_queues 定时 GC（PROGRESS_QUEUE_TTL_SECONDS）
         兜底客户端从未订阅的残留。
    """
    queue = facade._progress_queues.get(upload_id)
    if queue is not None:
        try:
            await queue.put(None)
        except Exception as put_err:
            logger.warning(f"[RAG] queue.put(None) 失败 ({upload_id}): {put_err}")


def _cleanup_failed_upload_sync(filepath: str, was_overwrite: bool = False) -> None:
    """索引失败后的文件清理（同步核心，Celery Worker 侧复用）。

    B 阶段语义变更：filepath 现在传「暂存文件」——失败清理只删暂存，
    正式路径（data/docs/...）在任何失败分支都不被触碰（旧版可读契约）；
    「删除复活/覆盖丢源」两类历史缺陷在候选模型下结构性地不可能发生。
    was_overwrite 参数保留兼容旧调用方（不再影响行为）。
    """
    if not filepath:
        return
    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
            logger.info(f"[RAG] 已清理索引失败暂存文件: {filepath}")
    except OSError as exc:
        logger.warning(f"[RAG] 索引失败暂存文件清理失败 {filepath}: {exc}")


async def _cleanup_failed_upload(filepath: str, was_overwrite: bool = False) -> None:
    """异步包装（兼容既有调用方）；实现见 _cleanup_failed_upload_sync。"""
    facade._cleanup_failed_upload_sync(filepath, was_overwrite=was_overwrite)


def _remove_bak(filepath: str) -> None:
    """索引成功终态清理覆盖上传的 .bak 备份。

    失败分支不调用 — 索引失败时 .bak 保留,旧版本可人工恢复。
    """
    bak = filepath + ".bak"
    try:
        if os.path.isfile(bak):
            os.remove(bak)
    except OSError as exc:
        logger.warning(f"[RAG] .bak 清理失败 {bak}: {exc}")


for _fn in [
    _cleanup_candidate_artifacts,
    cleanup_stale_upload_artifacts,
    cleanup_expired_progress_queues,
    progress_queue_gc_loop,
    _finalize_upload_queue,
    _cleanup_failed_upload_sync,
    _cleanup_failed_upload,
    _remove_bak
]:
    facade.register(_fn.__name__, _fn)
