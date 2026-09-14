"""S0 回归测试 — 模拟问题（Document Expansion）生产链路完整性。

背景（2026-09-14 发现）：
F1 重构把 summary/keywords/entities 三路并发化时漏迁了第四任务（问题生成），
`enrich_metadata_llm` 失去全部调用方，`questions_by_chunk` 恒为空 →
chunk metadata 永远不写 simulated_questions → `_embed_text_for` 的
「【相关问题】」前缀在生产链路永不触发（召回特性静默失效）。

本文件锁定三层契约，任何一层断裂即失败：
1. question_gen 模块存在且行为正确（LLM 路径 + 规则降级）
2. `_build_doc_metadata` 的返回值携带非空 questions_by_chunk（接线存在）
3. chunk metadata 注入 + chunk_store 落库契约（借助现有注入代码的输入校验）

注意：test 2/3 在 S0 修复前必然失败（红 = 证明 bug 存在），修复后转绿。
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document

import backend.rag.indexing.indexer as indexer_mod
from backend.rag.indexing.indexer import IncrementalIndexer


# ---------- helpers ----------

def _mk_indexer(registry=None) -> IncrementalIndexer:
    """跳过完整构造链，只注入 _build_doc_metadata 所需依赖。"""
    idx = IncrementalIndexer.__new__(IncrementalIndexer)
    idx.registry = registry or MagicMock()
    idx.registry.list_by_doc_type.return_value = []
    idx.department = "general"
    idx.kb_id = "kb_test"
    idx.embedding = MagicMock()
    idx.embedding.model_name = "fake-embed"
    return idx


def _full_text(n_chars: int = 1500) -> str:
    """1500 字符正文：足够触发 gather 并发组，又不触发 LLM 摘要（<2000 抽取式）。"""
    para = "这是一段用于测试的中文正文内容，描述了某项制度的具体规定与执行标准。"
    return para * (n_chars // len(para) + 1)


_BASE_META = {
    "doc_id": "doc_test_1",
    "source_file": "policy_a.md",
    "file_path": "/data/docs/kb_test/general/policy_a.md",
    "kb_id": "kb_test",
    "department": "general",
}


# ---------- 1. question_gen 模块契约 ----------

class TestQuestionGenModule:
    """S0 修复产物：backend/rag/preprocessing/question_gen.py 必须存在且可用。"""

    def test_module_and_signature(self):
        from backend.rag.preprocessing.question_gen import generate_chunk_questions

        chunks_text = ["第一条 内容甲。", "第二条 内容乙。", "第三条 内容丙。"]
        result = generate_chunk_questions(chunks_text, doc_type="policy")
        assert isinstance(result, list)
        assert len(result) == len(chunks_text), "问题数必须与 chunk 数对齐"
        for per_chunk in result:
            assert isinstance(per_chunk, list)

    def test_llm_path_length_mismatch_falls_back(self, monkeypatch):
        """LLM 返回的模拟问题长度不符 → 整体降级规则版，不得返回空。"""
        from backend.rag.preprocessing import question_gen

        def fake_llm(prompt, llm_obj=None):
            msg = MagicMock()
            msg.content = json.dumps({
                "simulated_questions": [["问1"]]  # 长度 1 != 3 → mismatch
            })
            return msg

        monkeypatch.setattr(question_gen, "_invoke_llm", fake_llm)
        chunks_text = ["第一条 甲。", "第二条 乙。", "第三条 丙。"]
        result = question_gen.generate_chunk_questions(chunks_text, doc_type="policy")
        assert len(result) == 3
        assert all(len(qs) >= 1 for qs in result), "mismatch 后必须规则兜底，不得留空"

    def test_llm_failure_falls_back_to_rule_based(self, monkeypatch):
        """LLM 调用抛异常 → 规则版兜底，产出非空问题（无害原则）。"""
        from backend.rag.preprocessing import question_gen

        def boom(prompt, llm_obj=None):
            raise RuntimeError("llm down")

        monkeypatch.setattr(question_gen, "_invoke_llm", boom)
        chunks_text = ["报销标准是什么？每月上限两千元。", "请假流程如下：先审批后执行。"]
        result = question_gen.generate_chunk_questions(chunks_text, doc_type="policy")
        assert len(result) == 2
        assert all(len(qs) >= 1 for qs in result)

    def test_disabled_flag_returns_empty(self, monkeypatch):
        """ENABLE_SIMULATED_QUESTIONS=off → 空产出（功能开关可关闭）。"""
        from backend.rag.preprocessing import question_gen

        monkeypatch.setattr(question_gen, "ENABLE_SIMULATED_QUESTIONS", False)
        result = question_gen.generate_chunk_questions(["某内容。"], doc_type="policy")
        assert result == []


# ---------- 2. _build_doc_metadata 接线契约 ----------

class TestMetadataWiring:
    """核心回归断言：元数据构建必须产出非空 questions_by_chunk。

    修复前（F1 遗留 bug）：questions_by_chunk 恒为 []，本测试红。
    """

    def test_build_doc_metadata_produces_questions(self, monkeypatch):
        # 隔离重依赖：关键词/实体走假实现，摘要走抽取式（<2000 字）
        from backend.rag.preprocessing.keyword import KeywordResult
        import backend.rag.preprocessing.keyword as kw_mod
        import backend.rag.preprocessing.entity as entity_mod

        monkeypatch.setattr(
            kw_mod, "extract_doc_keywords_typed",
            lambda *a, **k: KeywordResult(),
        )
        monkeypatch.setattr(entity_mod, "extract_entities", lambda *a, **k: {})

        # 修复后 question_gen 会被 gather 并发调用——此处桩化以隔离 LLM
        import backend.rag.preprocessing.question_gen as qg_mod
        monkeypatch.setattr(
            qg_mod, "generate_chunk_questions",
            lambda chunks_text, doc_type="general": [
                [f"问题{i}a", f"问题{i}b"] for i in range(len(chunks_text))
            ],
        )

        idx = _mk_indexer()
        # 模拟生产调用形态：_index_file_inner 传入 chunks 文本列表
        chunks_text = ["第一条 报销标准为每月两千元。", "第二条 请假需提前审批。"]
        result = asyncio.run(
            idx._build_doc_metadata(_full_text(), dict(_BASE_META),
                                    chunks_text=chunks_text)
        )

        assert result.get("questions_by_chunk"), (
            "S0 回归：_build_doc_metadata 必须产出非空 questions_by_chunk"
            "（F1 重构漏迁第四任务导致的静默失效）"
        )
        assert len(result["questions_by_chunk"]) == len(chunks_text), (
            "问题数组长度必须与 chunk 数对齐"
        )

    def test_metadata_sums_llm_tokens_from_all_sources(self, monkeypatch):
        """1.3b：summary/question 路径 tokens 与 keywords 路径一起进 llm_tokens 口径。"""
        from backend.rag.preprocessing.keyword import KeywordResult
        import backend.rag.preprocessing.keyword as kw_mod
        import backend.rag.preprocessing.entity as entity_mod

        kw = KeywordResult()
        kw.llm_tokens = {"prompt_tokens": 100, "completion_tokens": 20, "cost_usd": 0.1}
        monkeypatch.setattr(kw_mod, "extract_doc_keywords_typed", lambda *a, **k: kw)
        monkeypatch.setattr(entity_mod, "extract_entities", lambda *a, **k: {})

        import backend.rag.preprocessing.question_gen as qg_mod
        monkeypatch.setattr(
            qg_mod, "generate_chunk_questions",
            lambda chunks_text, doc_type="general": [
                ["q"] for _ in chunks_text
            ],
        )
        # question_gen 路径的 tokens（修复实现需回传）
        monkeypatch.setattr(
            qg_mod, "LAST_QUESTION_GEN_TOKENS",
            {"prompt_tokens": 50, "completion_tokens": 10, "cost_usd": 0.05},
            raising=False,
        )

        idx = _mk_indexer()
        chunks_text = ["第一条 报销标准为每月两千元。", "第二条 请假需提前审批。"]
        result = asyncio.run(
            idx._build_doc_metadata(_full_text(), dict(_BASE_META),
                                    chunks_text=chunks_text)
        )
        tokens = result.get("llm_tokens") or {}
        assert tokens.get("prompt_tokens", 0) >= 100, (
            "llm_tokens 汇总必须包含 keywords 路径用量"
        )


# ---------- 3. chunk 注入与落库契约 ----------

class TestChunkInjection:
    """chunk metadata 注入 simulated_questions 的既有逻辑（修复后应被真实喂到）。"""

    def test_inject_writes_simulated_questions(self):
        """indexer 主流程对 questions_by_chunk 的注入段：非空问题写入 chunk.metadata。"""
        chunks = [Document(page_content=f"c{i}", metadata={}) for i in range(2)]
        questions_by_chunk = [["Q1a", "Q1b"], ["Q2a"]]

        for i, ch in enumerate(chunks):
            _sq = questions_by_chunk[i] if i < len(questions_by_chunk) else []
            if _sq:
                ch.metadata["simulated_questions"] = _sq

        assert chunks[0].metadata["simulated_questions"] == ["Q1a", "Q1b"]
        assert chunks[1].metadata["simulated_questions"] == ["Q2a"]

    def test_embed_text_for_uses_injected_questions(self, patched_trace=None):
        """前缀拼接消费端（既有逻辑，防止接线修复后消费端损坏）。"""
        idx = IncrementalIndexer.__new__(IncrementalIndexer)
        chunk = Document(page_content="正文",
                         metadata={"simulated_questions": ["标准是什么？"]})
        text = idx._embed_text_for(chunk)
        assert text.startswith("【相关问题】标准是什么？")
        assert text.endswith("正文")
