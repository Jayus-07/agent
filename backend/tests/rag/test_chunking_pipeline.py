# backend/tests/rag/test_chunking_pipeline.py
from backend.rag.preprocessing.pipeline import parse_and_chunk

MD = """# 售后制度
## 退货流程
### 审核
客服审核退货原因。
## 差评处理
48小时内给出方案。
"""


def test_pipeline_end_to_end(tmp_path):
    p = tmp_path / "a.md"
    p.write_text(MD, encoding="utf-8")
    chunks = parse_and_chunk(str(p))
    assert chunks
    assert {"leaf", "parent"} <= {c.metadata["granularity"] for c in chunks}
    leaf = next(c for c in chunks if c.metadata["granularity"] == "leaf")
    assert leaf.metadata["parent_chunk_id"]
    assert leaf.metadata["section_path"]


def test_pipeline_unstructured_falls_back(tmp_path):
    t = tmp_path / "b.txt"
    t.write_text("客户提交申请后，客服核对订单信息。" * 100, encoding="utf-8")
    chunks = parse_and_chunk(str(t))
    assert chunks
    assert all(c.metadata.get("chunk_tokens", 0) > 0 for c in chunks)
