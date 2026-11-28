"""架构守卫测试：验证 RAGChain 门面瘦身及 chain_modules 子包解耦架构契约与向后兼容性。"""
from __future__ import annotations

import inspect
from pathlib import Path

import backend.rag.chain as chain
import backend.rag.chain_modules as chain_modules
from backend.rag.chain import (
    _HISTORY_REWRITE_CUES,
    DEFAULT_CONTEXTUALIZE_SYSTEM,
    DEFAULT_DOCUMENT_TEMPLATE,
    DEFAULT_QA_SYSTEM,
    RAGChain,
    _build_contextualize_prompt,
    _build_document_prompt,
    _build_qa_prompt,
    _build_retrieval_span_metrics,
    _llm_stream,
    _mark_gate_degraded,
    _needs_history_rewrite,
)


def test_rag_chain_facade_line_count():
    """验证 chain.py 门面物理行数不超过 380 行（原 1924 行，瘦身逾 80%）。"""
    facade_file = Path(chain.__file__)
    lines = facade_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 380, f"chain.py 门面行数为 {len(lines)}，超过 380 行上限"


def test_rag_chain_modules_exist_and_bounded():
    """验证 chain_modules 专职子模块完整存在且无超大单体（每个均 <= 600 行）。"""
    modules_dir = Path(chain_modules.__file__).parent
    expected_modules = {
        "prompts.py",
        "streaming.py",
        "metrics_helper.py",
        "builder.py",
        "gates.py",
        "verification.py",
        "lifecycle.py",
        "__init__.py",
    }
    actual_modules = {p.name for p in modules_dir.glob("*.py")}
    assert expected_modules.issubset(actual_modules), f"缺失专职子模块: {expected_modules - actual_modules}"

    for mod_path in modules_dir.glob("*.py"):
        line_count = len(mod_path.read_text(encoding="utf-8").splitlines())
        assert line_count <= 600, f"子模块 {mod_path.name} 行数为 {line_count}，超过 600 行阈值"


def test_rag_chain_exported_symbols_contract():
    """验证 chain.py 导出的顶级常量与辅助函数契约完整性。"""
    assert isinstance(DEFAULT_CONTEXTUALIZE_SYSTEM, str)
    assert isinstance(DEFAULT_QA_SYSTEM, str)
    assert isinstance(DEFAULT_DOCUMENT_TEMPLATE, str)
    assert isinstance(_HISTORY_REWRITE_CUES, tuple)

    assert callable(_build_contextualize_prompt)
    assert callable(_build_document_prompt)
    assert callable(_build_qa_prompt)
    assert callable(_build_retrieval_span_metrics)
    assert callable(_llm_stream)
    assert callable(_mark_gate_degraded)
    assert callable(_needs_history_rewrite)
    assert hasattr(chain, "llm")
    assert hasattr(chain, "get_context")
    assert hasattr(chain, "ENABLE_HISTORY_AWARE_RETRIEVAL")
    assert hasattr(chain, "ENABLE_TOKEN_STREAMING")
    assert inspect.isclass(RAGChain)


def test_rag_chain_class_methods_contract():
    """验证 RAGChain 上的方法与属性访问器完整保留，支持外部调用与 monkeypatch。"""
    expected_methods = [
        "ask",
        "_build_retrievers",
        "_build_chains",
        "_register_prompt_reload_hooks",
        "_mark_chains_dirty",
        "_gate_wrap_retrieve",
        "_decision_from_injected",
        "_run_evidence_gates",
        "_record_retrieval_events",
        "_verify",
        "_verify_claims",
        "_evaluate",
        "_start",
        "_record_runtime_prompt_usage",
        "_remember_turn",
        "_emit_embedding_stats",
        "_record_rag_metric",
        "_handle_llm_reject",
        "_reject",
        "_finish",
        "_finish_error",
        "_end_root_span",
        "_try_self_correct",
        "_prepare",
        "_execute",
        "_respond",
    ]
    for method_name in expected_methods:
        assert hasattr(RAGChain, method_name), f"RAGChain 缺失核心方法: {method_name}"
        assert callable(getattr(RAGChain, method_name)), f"RAGChain.{method_name} 不可调用"

    expected_properties = [
        "_last_meta",
        "_last_faithfulness",
        "_last_query",
        "_last_sources",
        "_last_citation_evidence",
        "gate",
        "corrector",
    ]
    for prop_name in expected_properties:
        assert hasattr(RAGChain, prop_name), f"RAGChain 缺失属性: {prop_name}"
        assert isinstance(getattr(RAGChain, prop_name), property), f"RAGChain.{prop_name} 不是 property"
