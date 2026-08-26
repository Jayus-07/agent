# backend/tests/rag/test_chunking_pipeline.py
from types import SimpleNamespace

import pytest

from backend.rag.preprocessing import metadata as _meta
from backend.rag.preprocessing.pipeline import parse_and_chunk

MD = """# 售后制度
## 退货流程
### 审核
客服审核退货原因。
## 差评处理
48小时内给出方案。
"""


@pytest.fixture
def deterministic_classify(monkeypatch):
    """fix f20：隔离 LLM 仲裁，避免单测依赖真实模型的非确定分类。

    售后制度 样本 policy(25)/sop(20) 胶着会触发仲裁：qwen 判 policy
    （Structure 策略，leaf+parent 双层），MiniMax 判 sop（Step 策略，
    仅 leaf）—— 两者语义都合理，单测必须锁定分类才可稳定断言。
    """
    monkeypatch.setattr(
        _meta, "llm",
        SimpleNamespace(invoke=lambda prompt: SimpleNamespace(content="policy")),
    )


def test_pipeline_end_to_end(tmp_path, deterministic_classify):
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


def test_numbered_txt_paragraphs_not_lost(tmp_path):
    """编号 TXT 路由到 Structure 后段落不得被丢弃（TxtParser 段落需嵌套进 section）。"""
    t = tmp_path / "c.txt"
    t.write_text("一、退货流程\n提交申请。\n\n二、审核\n客服审核。\n", encoding="utf-8")
    chunks = parse_and_chunk(str(t))
    assert chunks
    all_text = "\n".join(c.page_content for c in chunks)
    assert "提交申请。" in all_text
    assert "客服审核。" in all_text
