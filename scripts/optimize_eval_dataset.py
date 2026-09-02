#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""优化 RAG 评测集 rag_test_kb.json（非破坏式，输出 v1.4）。
使用 D:\Python (含项目依赖) 运行：D:\Python\python.exe scripts/optimize_eval_dataset.py

优化项：
  1) 36 条标准领域 QA 用例补 probe_type="domain_recall"（此前无该键，报告里全是 "?"）。
  2) 基于真实文档解析出的真实 chunk_id 生成 chunk 级用例，修复 "Chunk 级召回率=0"。
  3) 统一 doc_id 约定 md5(文件名)[:10]。
"""
import json
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.rag.preprocessing.pipeline import parse_and_chunk

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "backend" / "evaluation" / "datasets" / "rag_test_kb.json"
DOC_DIR = ROOT / "data" / "docs" / "rag_test_kb" / "general"


def doc_id_of(filename: str) -> str:
    return hashlib.md5(filename.encode("utf-8")).hexdigest()[:10]


def find_chunk_ids(doc_path: str, keywords: list[str], max_n: int = 2) -> list[str]:
    """返回同时包含全部关键词的 chunk_id（最多 max_n 个）。"""
    chunks = parse_and_chunk(str(doc_path))
    hits = []
    for c in chunks:
        text = c.page_content
        if all(kw in text for kw in keywords):
            cid = c.metadata.get("chunk_id")
            if cid and cid not in hits:
                hits.append(cid)
        if len(hits) >= max_n:
            break
    return hits


def main():
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    tc = data["test_cases"]
    n_labeled = 0

    # ---- 优化 1：36 条标准 QA 补 domain_recall ----
    for c in tc:
        md = c.setdefault("metadata", {})
        if "probe_type" not in md:
            md["probe_type"] = "domain_recall"
            md["probe_note"] = "2026-08-26: 标准领域 QA 统一归类，原缺失 probe_type"
            n_labeled += 1

    # ---- 优化 2：生成 chunk 级用例（用真实 chunk_id）----
    chunk_specs = [
        {
            "query": "采购合同签订后需要支付多少预付款？",
            "doc_file": "11_采购合同.md",
            "keywords": ["30%", "预付款"],
            "difficulty": "easy", "domain": "legal",
        },
        {
            "query": "申请退款后多久能完成审核？",
            "doc_file": "01_FAQ.md",
            "keywords": ["退款", "审核"],
            "difficulty": "easy", "domain": "after_sales",
        },
        {
            "query": "供应商绩效季度考核包含哪些指标？",
            "doc_file": "12_供应商管理规范.md",
            "keywords": ["绩效考核", "准时交货率"],
            "difficulty": "medium", "domain": "procurement",
        },
        {
            "query": "用户注销账户后个人信息保留多久？",
            "doc_file": "17_数据安全与隐私政策.md",
            "keywords": ["注销", "30 日"],
            "difficulty": "easy", "domain": "compliance",
        },
    ]

    new_cases = []
    existing_ids = {c["id"] for c in tc}
    seq = 1
    for spec in chunk_specs:
        dp = DOC_DIR / spec["doc_file"]
        chunk_ids = find_chunk_ids(dp, spec["keywords"], max_n=2)
        if not chunk_ids:
            print(f"  ⚠️  [{spec['query']}] 未找到含 {spec['keywords']} 的 chunk，跳过")
            continue
        case_id = f"RT-CHUNK-{seq:03d}"
        seq += 1
        case = {
            "id": case_id,
            "question": spec["query"],
            "module": "rag",
            "kb_id": "rag_test_kb",
            "expected": {
                "relevant_docs": [doc_id_of(spec["doc_file"])],
                "relevant_chunks": chunk_ids,
                "min_relevant_chunks": 1,
                "match_type": "chunk_id",
            },
            "metadata": {
                "difficulty": spec["difficulty"],
                "domain": spec["domain"],
                "probe_type": "chunk_level_recall",
                "probe_note": "2026-08-26: 基于真实文档 chunk_id 生成",
                "source_file": str(dp),
            },
        }
        if case_id not in existing_ids:
            new_cases.append(case)
            print(f"  ✅ {case_id}: {spec['query']} -> chunks={chunk_ids}")

    tc.extend(new_cases)
    data["version"] = "1.4"
    out = DATASET.parent / "rag_test_kb_v1.4.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 56)
    print(f"✅ 优化完成")
    print(f"  - 补 domain_recall 标签: {n_labeled} 条")
    print(f"  - 新增 chunk 级用例: {len(new_cases)} 条")
    print(f"  - 用例总数: {len(tc)} (原 54 -> {len(tc)})")
    print(f"  - 保存至: {out}")
    print("=" * 56)


if __name__ == "__main__":
    main()
