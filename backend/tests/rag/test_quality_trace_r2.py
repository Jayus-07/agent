"""R2 质量留痕改造测试（R-P0-3 原文可追溯 + R-P1-3 过滤明细）。

对应 docs/RAG质量专项-01-审计报告.md §三 P0/P1 修复项：
- DocumentNode 新增 raw_text / cleaning_operations 字段（位置构造兼容）
- pipeline.parse_and_chunk 清洗前留存原文 + 清洗操作留痕
- indexer._filter_quality_summary 汇总串格式
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.rag.preprocessing.ast import DocumentNode
from backend.rag.preprocessing.chunking import ChunkStrategyRouter
from backend.rag.preprocessing.pipeline import parse_and_chunk
from backend.rag.indexing.indexer import _filter_quality_summary


# ── R-P0-3: AST 字段 ──

def test_document_node_new_fields_default():
    """新字段默认值：raw_text 为空串、cleaning_operations 为空列表。"""
    node = DocumentNode(type="paragraph", text="hello")
    assert node.raw_text == ""
    assert node.cleaning_operations == []


def test_document_node_positional_construction_compat():
    """既有位置构造（type, text, level, children, rows, source_range）不受新字段影响。"""
    node = DocumentNode("paragraph", "t", 1, [], None, (3, 9))
    assert node.type == "paragraph"
    assert node.source_range == (3, 9)
    assert node.raw_text == ""


# ── R-P0-3: pipeline 清洗留痕 ──

class _CapturingStrategy:
    """捕获传入 AST 的假策略，替代真实切分以便检查清洗前后的节点。"""

    def __init__(self):
        self.captured = None

    def split(self, normalized_ast, file_path):
        self.captured = normalized_ast
        return []


@pytest.fixture()
def md_file_with_dirty_text(tmp_path: Path) -> Path:
    """含控制字符/半角标点/URL 的 md（配合 monkeypatch 打开对应清洗开关）。"""
    p = tmp_path / "dirty.md"
    p.write_text(
        "# 标题\n\n正文A\x08带控制字符: 继续。\n\n正文B 段落。\n",
        encoding="utf-8",
    )
    return p


def test_parse_and_chunk_preserves_raw_text_and_operations(
    md_file_with_dirty_text, monkeypatch
):
    """清洗后 node.text 变化时，raw_text == 清洗前原文且 cleaning_operations 非空；
    未被清洗改动的节点 raw_text == text。"""
    # 本环境部分清洗开关默认关闭，显式打开待测操作
    monkeypatch.setattr("backend.config.CLEAN_REMOVE_CONTROL_CHARS", True)
    captured = _CapturingStrategy()
    monkeypatch.setattr(ChunkStrategyRouter, "route", lambda self, dt, rep: captured)

    parse_and_chunk(str(md_file_with_dirty_text))

    assert captured.captured is not None
    changed, unchanged = [], []
    for node in captured.captured.root.children:
        # 跳过虚拟根/容器标题节点只看叶子
        for n in [node, *node.children]:
            if n.raw_text != n.text:
                changed.append(n)
            else:
                unchanged.append(n)

    # 标题 + 正文A（含 \x08 控制字符）中至少一个节点应发生清洗变化并留痕
    assert any(n.cleaning_operations for n in changed), (
        "清洗改动了节点但 cleaning_operations 为空"
    )
    # raw_text 必须等于清洗前原文：对 changed 节点，raw_text 是旧 text，
    # 应包含控制字符原文片段
    for n in changed:
        assert n.raw_text != "" or n.text != ""
    # 未变化节点的 operations 为空
    for n in unchanged:
        assert n.cleaning_operations == []


def test_parse_and_chunk_clean_text_differs_for_control_chars(
    md_file_with_dirty_text, monkeypatch
):
    """控制字符确实被清洗掉（text != raw_text 的节点，raw_text 含 \x08）。"""
    monkeypatch.setattr("backend.config.CLEAN_REMOVE_CONTROL_CHARS", True)
    captured = _CapturingStrategy()
    monkeypatch.setattr(ChunkStrategyRouter, "route", lambda self, dt, rep: captured)

    parse_and_chunk(str(md_file_with_dirty_text))

    all_nodes = [n for c in captured.captured.root.children for n in [c, *c.children]]
    dirty = [n for n in all_nodes if "\x08" in n.raw_text]
    assert dirty, "含控制字符的节点未捕获到 raw_text 原文"
    for n in dirty:
        assert "\x08" not in n.text
        assert "removed_control_chars" in n.cleaning_operations


# ── R-P1-3: 过滤汇总串 ──

def test_filter_quality_summary_empty():
    assert _filter_quality_summary([]) == ""


def test_filter_quality_summary_format():
    details = [
        {"chunk_index": 0, "reason": "too_short", "preview": "a"},
        {"chunk_index": 2, "reason": "too_short", "preview": "b"},
        {"chunk_index": 5, "reason": "all_symbols", "preview": "c"},
    ]
    s = _filter_quality_summary(details)
    assert s == "filtered_chunks:3(too_short=2,all_symbols=1)"
