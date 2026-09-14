"""4.3 增强项 — 表格描述 / 实体与时间引用落库。

契约：
- generate_table_descriptions：LLM 批量生成行描述，长度不符/失败 → {}
- _embed_text_for 消费 metadata["table_desc"] 拼【表格】前缀段
- indexer 注入 entities/time_refs（数据可达性）
"""
import json
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document

from backend.rag.preprocessing import table_describe as td
from backend.rag.preprocessing.table_describe import generate_table_descriptions


class TestTableDescribe:

    def test_llm_path_returns_indexed_descriptions(self, monkeypatch):
        def fake_llm(prompt, llm_obj=None):
            msg = MagicMock()
            msg.content = json.dumps({
                "descriptions": ["货币资金在Q3的金额与环比。", "应收账款变动情况。"]
            })
            return msg

        monkeypatch.setattr(td, "_invoke_llm", fake_llm)
        out = generate_table_descriptions(
            ["科目 货币资金\nQ3金额 123", "科目 应收账款\nQ3金额 456"],
            table_summary="资产负债表：共 2 行数据",
        )
        assert out == {0: "货币资金在Q3的金额与环比。", 1: "应收账款变动情况。"}

    def test_length_mismatch_returns_empty(self, monkeypatch):
        def fake_llm(prompt, llm_obj=None):
            msg = MagicMock()
            msg.content = json.dumps({"descriptions": ["只有一条"]})
            return msg

        monkeypatch.setattr(td, "_invoke_llm", fake_llm)
        assert generate_table_descriptions(["行1", "行2"]) == {}

    def test_llm_failure_returns_empty(self, monkeypatch):
        def boom(prompt, llm_obj=None):
            raise RuntimeError("down")

        monkeypatch.setattr(td, "_invoke_llm", boom)
        assert generate_table_descriptions(["行1"]) == {}

    def test_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setattr(td, "ENABLE_TABLE_DESCRIPTIONS", False)
        assert generate_table_descriptions(["行1"]) == {}

    def test_max_rows_guard(self, monkeypatch):
        captured = {}

        def fake_llm(prompt, llm_obj=None):
            captured["prompt"] = prompt
            msg = MagicMock()
            msg.content = json.dumps({"descriptions": ["描述内容超过四字"] * td.TABLE_DESC_MAX_ROWS})
            return msg

        monkeypatch.setattr(td, "_invoke_llm", fake_llm)
        kv = [f"行{i}" for i in range(td.TABLE_DESC_MAX_ROWS + 10)]
        out = generate_table_descriptions(kv)
        # 超限行无描述
        assert all(i not in out for i in range(td.TABLE_DESC_MAX_ROWS, len(kv)))
        assert len(out) == td.TABLE_DESC_MAX_ROWS  # 超限的 10 行不生成


class TestEmbedPrefix:

    def test_table_desc_segment(self):
        from backend.rag.indexing.indexer import IncrementalIndexer
        idx = IncrementalIndexer.__new__(IncrementalIndexer)
        chunk = Document(page_content="科目 货币资金", metadata={
            "table_desc": "货币资金在Q3的金额与环比。",
        })
        text = idx._embed_text_for(chunk, doc_summary="")
        assert text.startswith("【表格】货币资金在Q3的金额与环比。")
        assert text.endswith("科目 货币资金")

    def test_table_desc_with_other_prefixes(self):
        from backend.rag.indexing.indexer import IncrementalIndexer
        idx = IncrementalIndexer.__new__(IncrementalIndexer)
        chunk = Document(page_content="kv行", metadata={
            "section_title": "资金表",
            "table_desc": "描述。",
        })
        text = idx._embed_text_for(chunk, doc_summary="摘要")
        order = [text.index("【文档】"), text.index("【章节】"), text.index("【表格】")]
        assert order == sorted(order), "前缀顺序：文档→章节→表格"
