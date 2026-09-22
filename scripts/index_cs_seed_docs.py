"""索引客服知识库种子文档（批次四能力 MVP 补资料，2026-09-22）。

把 docs/customer-service/demo-kb 下的客服域文档按
(kb_id, audience=customer) 索引进 RAG，补齐：
  - cs_faq（通用服务 FAQ，原仅 1 篇）
  - cs_product（商品使用 FAQ）
  - cs_complaint（投诉处理流程与补偿标准，原为空）
  - cs_scripts（标准服务话术，原为空）
  - cs_aftersales（换货维修 FAQ）

前置：文档已复制到 {DOCS_DIRECTORY}/{kb_id}/customer/ 下
     （容器内 /app/data/docs/...，宿主机经 docker cp 送入）。

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


SEED_FILES = [
    ("cs_faq", "faq-service-general.md"),
    ("cs_faq", "faq-after-sales-process.md"),
    ("cs_product", "faq-product-usage.md"),
    ("cs_product", "product-catalog.md"),
    ("cs_complaint", "process-complaint-handling.md"),
    ("cs_scripts", "scripts-standard.md"),
    ("cs_aftersales", "faq-exchange-repair.md"),
    ("cs_aftersales", "faq-after-sales-process.md"),
]


def main():
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
