"""F1 冒烟验证：直接调用 _build_doc_metadata，确认 ≥1000 字符文档不再整体失败。

修复前预期：asyncio.gather 处 NameError → 上层 except → 返回 {"doc_type": "general"}。
修复后预期：完整返回 doc_type/confidence/summary/minhash_sig 等字段
（LLM 不可用时摘要走 _extract_first_sentences 兜底，仍不应整体失败）。
"""
import asyncio
import sys
import types

sys.path.insert(0, ".")

from backend.rag.indexing.indexer import IncrementalIndexer


class FakeRegistry:
    def list_by_doc_type(self, doc_type):
        return []


fake_self = types.SimpleNamespace(registry=FakeRegistry(), embedding=None, department="warehouse")

text = (
    "第一章 总则\n"
    "为规范公司库存管理流程，明确仓储部的职责边界，特制定本制度。\n"
    * 3
    + "本制度适用于仓储部、供应链部全体员工，涵盖入库验收、在库盘点、出库复核三大环节。\n" * 5
    + "第二章 入库管理\n供应商到货后，仓储部须在两个工作日内完成验收，核对采购订单、送货单与实物数量。"
    "验收不合格的货物应隔离存放并于当日反馈供应链部。\n" * 8
    + "第三章 盘点管理\n每月末进行一次全面盘点，盘盈盘亏须查明原因并报财务部备案。\n" * 10
)
print(f"sample length: {len(text)} chars")

meta = asyncio.run(
    IncrementalIndexer._build_doc_metadata(fake_self, text, {"source_file": "库存管理制度.txt"})
)

keys = ["doc_type", "confidence", "summary", "minhash_sig", "business_domain", "quality_score"]
print({k: (str(meta.get(k))[:60] if meta.get(k) is not None else None) for k in keys})
assert "doc_type" in meta, "missing doc_type"
assert meta.get("doc_type"), "doc_type empty"
assert meta.get("summary"), "summary empty"
assert meta.get("minhash_sig"), "minhash_sig empty"
print("F1 SMOKE OK: metadata pipeline returns complete dict, no NameError swallowed")
