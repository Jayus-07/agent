"""索引部门隔离评测用的种子文档。

按 (kb_id, department) 把 data/docs 下新增的 hr / finance 文档索引进 RAG，
使 backend/evaluation/datasets/rag_test_kb.json 里的 DEPT-00x 用例可被检索。

用法:
    python scripts/index_dept_seed_docs.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.config import DOCS_DIRECTORY, DOC_REGISTRY_PATH
from backend.rag.pipeline import RAGPipeline
from backend.rag.indexing.doc_registry import DocumentRegistry
from backend.rag.indexing.indexer import IncrementalIndexer


SEED_FILES = [
    ("policy_general", "hr", "leave_policy_hr.md"),
    ("policy_general", "hr", "payroll_hr.md"),
    ("policy_general", "finance", "reimbursement_fin.md"),
    ("policy_general", "finance", "budget_fin.md"),
]


def main():
    print("[index] 初始化 RAG pipeline（首次约 15s，需加载 embedding 模型）...")
    pipeline = RAGPipeline()
    registry = DocumentRegistry(DOC_REGISTRY_PATH)

    for kb_id, department, fname in SEED_FILES:
        fpath = os.path.join(DOCS_DIRECTORY, kb_id, department, fname)
        if not os.path.isfile(fpath):
            print(f"[index] 跳过（文件不存在）: {fpath}")
            continue
        print(f"[index] {kb_id}/{department}/{fname}")
        indexer = IncrementalIndexer(
            docs_dir=DOCS_DIRECTORY,
            vectordb=pipeline.vectordb,
            doc_db=pipeline.doc_db,
            embedding=pipeline.embedding,
            registry=registry,
            kb_id=kb_id,
            department=department,
            bm25_store=pipeline.bm25_store,
        )
        result = indexer.reindex_file(fpath)
        print(f"        -> {result.get('terminal', result.get('status', '?'))} "
              f"chunks={result.get('chunk_count', '?')}")

    print("[index] 完成。可运行评测验证部门隔离:" )
    print("        python -m backend.evaluation.cli --module rag --live")


if __name__ == "__main__":
    main()
