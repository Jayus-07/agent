"""test_chunkers_modular_architecture.py — 针对 chunkers 模块化重构的架构与契约守卫测试。

验证：
1. chunking.py 瘦身成功，行数严格 < 200 行；
2. 模块拓扑完整，无循环导入；
3. 对外导出符号 100% 等价兼容；
4. monkeypatch 动态感知机制可靠。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from langchain_core.documents import Document

import backend.rag.preprocessing.chunking as chunking_facade
import backend.rag.preprocessing.chunkers as chunkers_pkg
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunkers import (
    FinancialTableChunkStrategy,
    FixedSizeChunkStrategy,
    LegalChunkStrategy,
    QAChunkStrategy,
    RecursiveChunkStrategy,
    SemanticChunkStrategy,
    StepChunkStrategy,
    StructureChunkStrategy,
)


class TestChunkingArchitectureGuard:
    def test_chunking_facade_line_count_within_budget(self):
        """确保 chunking.py 单文件行数维持在 200 行以内（Facade 门面契约）。"""
        chunking_py = Path(chunking_facade.__file__)
        lines = chunking_py.read_text(encoding="utf-8").splitlines()
        assert len(lines) < 200, f"chunking.py 行数超限: {len(lines)} >= 200"

    def test_chunking_facade_exports_all_required_symbols(self):
        """确保门面层 100% 导出所有存量公共接口。"""
        required_symbols = [
            "FixedSizeChunkStrategy",
            "RecursiveChunkStrategy",
            "StructureChunkStrategy",
            "StepChunkStrategy",
            "LegalChunkStrategy",
            "QAChunkStrategy",
            "SemanticChunkStrategy",
            "FinancialTableChunkStrategy",
            "ContentUnitDispatchStrategy",
            "ChunkStrategyRouter",
            "ChunkRouteDecision",
            "STRUCTURE_STRATEGIES",
            "coverage_audit",
            "stamp_chunk_pages",
            "stamp_chunk_structure",
            "ensure_unique_chunk_ids",
            "is_table_chunk",
            "CHUNK_TYPE_TABLE_ROW",
            "CHUNK_TYPE_TABLE_SUMMARY",
            "CHUNK_TYPE_TABLE_FALLBACK",
            "CHUNK_TYPE_TABLE_HEADER",
            "TABLE_CHUNK_TYPES",
            "TABLE_CHUNK_PREFIX",
            "LEAF_CHUNK_TOKENS",
            "PARENT_CHUNK_TOKENS",
            "CHUNK_OVERLAP",
            "_split_unstructured",
            "_split_table_node",
            "_attach_virtual_parents",
            "_hard_split_by_budget",
            "_split_to_budget",
            "_chunk_id",
            "_enrich",
        ]
        for name in required_symbols:
            assert hasattr(chunking_facade, name), f"chunking.py 缺少导出符号: {name}"

    def test_strategy_classes_instantiable(self):
        """验证所有拆分后的策略类均可正常实例化并实现 split 契约。"""
        strategies = [
            FixedSizeChunkStrategy(),
            RecursiveChunkStrategy(),
            StructureChunkStrategy(),
            StepChunkStrategy(),
            LegalChunkStrategy(),
            QAChunkStrategy(),
            SemanticChunkStrategy(),
            FinancialTableChunkStrategy(),
        ]
        for s in strategies:
            assert hasattr(s, "split"), f"{s.__class__.__name__} 缺少 split 方法"
            sig = inspect.signature(s.split)
            params = list(sig.parameters.keys())
            assert "ast" in params and "file_path" in params

    def test_monkeypatch_leaf_chunk_tokens_propagation(self, monkeypatch):
        """测试对 chunking 模块的 monkeypatch 能被底层切分策略动态感知。"""
        from backend.rag.preprocessing.chunkers.base import get_leaf_chunk_tokens

        original_val = get_leaf_chunk_tokens()
        monkeypatch.setattr(chunking_facade, "LEAF_CHUNK_TOKENS", 9999)
        assert get_leaf_chunk_tokens() == 9999

        monkeypatch.setattr(chunking_facade, "LEAF_CHUNK_TOKENS", original_val)
        assert get_leaf_chunk_tokens() == original_val
