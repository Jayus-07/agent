# -*- coding: utf-8 -*-
"""查询点名来源文件的候选收口回归测试（2026-10-29，UFQ-016~021）。

背景：语料含三个 CSV 的同名 SKU 不同价。查询点名 sku.csv 时，其他文件的
同 SKU 行留在上下文会让生成端在冲突值之间摇摆（枚举/拒答跨轮抖动，judge
对枚举裁决不稳），发布门禁因此抖动。收口后指定文件之外的同标识符行不再
进入上下文，枚举在物理上不可能。
"""
from langchain_core.documents import Document

from backend.rag.retrieval.hybrid import scope_by_named_files


def _docs():
    return [
        Document(page_content="[sku.csv] sku 00012345 price 886", metadata={"chunk_id": "a", "source_file": "sku.csv"}),
        Document(page_content="[sku_bom.csv] sku 00012345 price 207", metadata={"chunk_id": "b", "source_file": "sku_bom.csv"}),
        Document(page_content="[sku_gbk.csv] sku 00012345 price 380", metadata={"chunk_id": "c", "source_file": "sku_gbk.csv"}),
    ]


def test_named_file_keeps_only_named_candidates():
    kept = scope_by_named_files(_docs(), "sku.csv 中 SKU 为 00012345 的商品，价格和库存是多少？")
    assert [d.metadata["chunk_id"] for d in kept] == ["a"]


def test_no_file_mention_keeps_all():
    kept = scope_by_named_files(_docs(), "SKU 00012345 的商品，价格和库存是多少？")
    assert len(kept) == 3


def test_named_file_without_hits_keeps_all():
    """点名文件零命中（写错/未索引）不收口，交由既有拒答规则处理。"""
    kept = scope_by_named_files(_docs(), "not_indexed.csv 中 SKU 00012345 的价格是多少？")
    assert len(kept) == 3


def test_match_is_case_insensitive_and_uses_canonical_id_fallback():
    doc = Document(page_content="row", metadata={"chunk_id": "x", "canonical_id": "SKU.CSV"})
    kept = scope_by_named_files([doc], "请查 sku.csv 中型号 00012345 的库存")
    assert [d.metadata["chunk_id"] for d in kept] == ["x"]


def test_basename_of_pathlike_source_matches():
    doc = Document(page_content="row", metadata={"chunk_id": "p", "source_file": "/data/uploads/sku_bom.csv"})
    kept = scope_by_named_files([doc], "sku_bom.csv 中 SKU 00012345 的库存")
    assert [d.metadata["chunk_id"] for d in kept] == ["p"]


def test_non_file_words_do_not_trigger():
    """「表格」「工作表」等无扩展名词不触发收口（011~015/022~024 场景）。"""
    kept = scope_by_named_files(_docs(), "合同表格中培训的单价、数量和小计分别是多少？")
    assert len(kept) == 3
