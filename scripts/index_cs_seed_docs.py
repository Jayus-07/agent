"""索引客服知识库种子文档（批次四能力 MVP 补资料，2026-09-22）。

把种子文档按 (kb_id, audience=customer) 索引进 RAG，覆盖：
  - cs_faq（通用服务 FAQ + 高频速查）
  - cs_product（商品使用 FAQ）
  - cs_complaint（投诉处理流程与补偿标准）
  - cs_scripts（标准服务话术）
  - cs_aftersales（换货维修 FAQ）
  - cs_policy（B1 政策底座：退款/价保/优惠券/积分/支付/配送/发票/售后）

文档事实源：git 内 docs/customer-service/kb/（镜像内 /app/docs/...，见
scripts/cs_kb_seed.py）。运行时先同步到
{DOCS_DIRECTORY}/{kb_id}/customer/ 再索引；无 git 源的历史文档沿用数据卷
内已有文件（早期 docker cp 流程的兼容路径）。

用法（容器内）:
    python scripts/index_cs_seed_docs.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.config import DOCS_DIRECTORY, DOC_REGISTRY_PATH
from backend.rag.pipeline import RAGPipeline
from backend.rag.indexing.doc_registry import DocumentRegistry
from backend.rag.indexing.indexer import IncrementalIndexer
from scripts.cs_kb_seed import (
    SEED_FILES,
    repo_root_source_dir,
    sync_kb_seed_files,
)


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    copied = sync_kb_seed_files(
        repo_root_source_dir(repo_root), DOCS_DIRECTORY, SEED_FILES,
    )
    for kb_id, fname in copied:
        print(f"[sync] {kb_id}/customer/{fname}")

    print("[index] 初始化 RAG pipeline（首次约 15s，需加载 embedding 模型）...")
    # 独立脚本进程没有 app 启动流程的注册表注入 —— 先从 DB 加载模型覆盖层
    # （与 celery worker 的 refresh_worker_model_registry 同口径），否则
    # embedding 走不到管理端配置的向量模型供应商。
    try:
        import asyncio

        from backend.infra.llm.registry_store import refresh_registry

        loaded = bool(asyncio.run(refresh_registry()))
        print(f"[index] 模型注册表加载: {'ok' if loaded else '空（回退默认）'}")
    except Exception as exc:
        print(f"[index] 模型注册表加载失败（继续用默认配置）: {exc}")

    pipeline = RAGPipeline()
    registry = DocumentRegistry(DOC_REGISTRY_PATH)

    ok, skipped, failed = 0, 0, 0
    for kb_id, fname in SEED_FILES:
        fpath = os.path.join(DOCS_DIRECTORY, kb_id, "customer", fname)
        if not os.path.isfile(fpath):
            print(f"[index] 跳过（文件不存在）: {fpath}")
            skipped += 1
            continue
        print(f"[index] {kb_id}/customer/{fname}")
        indexer = IncrementalIndexer(
            docs_dir=DOCS_DIRECTORY,
            vectordb=pipeline.vectordb,
            doc_db=pipeline.doc_db,
            embedding=pipeline.embedding,
            registry=registry,
            kb_id=kb_id,
            department=None,
            bm25_store=pipeline.bm25_store,
        )
        try:
            result = indexer.reindex_file(fpath)
            print(f"[index]   -> {result}")
            ok += 1
        except Exception as exc:
            print(f"[index]   !! 失败: {exc}")
            failed += 1

    print(f"[index] 完成: ok={ok} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
