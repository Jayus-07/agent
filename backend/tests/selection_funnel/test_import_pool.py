"""tests/selection_funnel/test_import_pool.py — 批量导入通道单测

覆盖：中文表头宽松映射 / 宽松数值清洗 / TSV(Ctrl+C粘贴)·CSV·XLSX 解析 /
销量不冒充评价数 / 无效行剔除 / 存储往返 / 批次级类目补齐 / 一条龙失败降级。
"""
from __future__ import annotations

import io

import pytest

from backend.selection_funnel.import_pool import (
    ImportPoolStore,
    import_table,
    map_columns,
    normalize_row,
    parse_table,
)


# ── 表头映射 ──────────────────────────────────────────────────────────
def test_map_columns_cn_headers():
    mapping, _ = map_columns(["商品标题", "售价", "宝贝评分", "评价人数", "平台渠道", "类目", "商品链接"])
    assert set(mapping.values()) == {"title", "price", "rating", "review_count", "platform", "category", "url"}


def test_map_columns_unknown_kept():
    mapping, unknown = map_columns(["商品标题", "月销量", "店铺优惠标签"])
    assert mapping[0] == "title"
    assert mapping[1] == "sales"          # 月销量是已知别名
    assert "2" in unknown                  # 未知列按下标保留原名


def test_map_columns_duplicate_semantics_first_wins():
    mapping, unknown = map_columns(["价格", "到手价"])  # 两个都映射 price → 首个占位
    assert mapping[0] == "price"
    assert 1 not in mapping and "1" in unknown  # 重复语义列 → unknown，不覆盖首列


# ── 数值清洗 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expect", [
    ("¥1,299", 1299.0), ("4.8分", 4.8), ("1.2万", 12000.0), ("3亿", 300000000.0),
    ("59元", 59.0), (59, 59.0), ("", None), ("暂无", None), (None, None),
])
def test_normalize_price_loose(raw, expect):
    assert normalize_row({"价格": raw})["price"] == expect


def test_sales_not_pretend_review_count():
    """销量列语义≠评价数：分别落位，绝不拿销量冒充 review_count（热度线失真）。"""
    row = normalize_row({"商品标题": "冻干鸡肉", "月销量": "1.2万", "评价数": "500"})
    assert row["sales"] == 12000
    assert row["review_count"] == 500


def test_missing_review_count_kept_none():
    row = normalize_row({"商品标题": "冻干鸡肉", "月销量": "800"})
    assert row["review_count"] is None   # 缺评价数 → None，下游披露不误杀
    assert row["sales"] == 800


# ── 表格解析 ──────────────────────────────────────────────────────────
TSV_PASTE = (
    "商品标题\t售价\t宝贝评分\t评价人数\t平台\t类目\n"
    "冻干鸡肉 500g\t¥59\t4.8\t1.2万\t淘宝\t宠物零食\n"
    "冻干鸭肉 500g\t63\t4.6分\t8000\t拼多多\t宠物零食\n"
    "\t59\t4.5\t300\t淘宝\t宠物零食\n"          # 缺标题 → 无效行
)


def test_parse_tsv_paste_from_excel():
    rows = parse_table(TSV_PASTE)
    assert len(rows) == 2  # 无效行剔除
    first = rows[0]
    assert first["title"] == "冻干鸡肉 500g"
    assert first["price"] == 59.0
    assert first["review_count"] == 12000  # 1.2万
    assert first["platform"] == "淘宝"
    assert first["category"] == "宠物零食"


def test_parse_csv_with_bom():
    csv_text = "商品标题,价格,评分\n冻干鸡肉,59,4.8\n"
    rows = parse_table(csv_text.encode("utf-8-sig"))
    assert len(rows) == 1 and rows[0]["title"] == "冻干鸡肉" and rows[0]["rating"] == 4.8


def test_parse_xlsx_bytes():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["商品标题", "价格", "评分", "评价人数"])
    ws.append(["冻干鸡肉 500g", 59, 4.8, "1.5万"])
    ws.append(["冻干鸭肉 500g", 63, 4.6, 8000])
    buf = io.BytesIO()
    wb.save(buf)
    rows = parse_table(buf.getvalue())
    assert len(rows) == 2
    assert rows[0]["review_count"] == 15000
    assert rows[1]["price"] == 63.0


def test_parse_no_title_header_raises():
    with pytest.raises(ValueError, match="标题"):
        parse_table("价格,评分\n59,4.8\n")


def test_parse_empty_raises():
    with pytest.raises(ValueError, match="空"):
        parse_table("")


# ── 存储与一条龙 ──────────────────────────────────────────────────────
def test_store_roundtrip(tmp_path):
    store = ImportPoolStore(str(tmp_path / "t.db"))
    batch, n = store.add_batch(
        [{"title": "A", "price": 59.0, "rating": 4.8, "review_count": 100,
          "category": "宠物零食", "url": "u1"}], platform="淘宝")
    assert n == 1 and batch.startswith("imp-")
    items = store.list_candidates()
    assert len(items) == 1
    assert items[0]["platform"] == "淘宝"   # 行内缺失 → 批次级补齐
    assert items[0]["title"] == "A"
    assert store.count() == 1
    assert store.clear_batch(batch) == 1
    assert store.count() == 0


def test_import_table_pipeline(tmp_path):
    store = ImportPoolStore(str(tmp_path / "t.db"))
    import backend.selection_funnel.import_pool as ip
    orig = ip.get_import_store
    ip.get_import_store = lambda: store   # 一条龙走临时库
    try:
        batch, n, notes = import_table(TSV_PASTE, category="宠物零食")
        assert n == 2 and batch and "导入批次" in notes[0]
        assert store.count() == 2
    finally:
        ip.get_import_store = orig


def test_import_table_failure_degrades(tmp_path):
    """解析失败不炸调用方：("", 0, [原因])。"""
    batch, n, notes = import_table("这不是一个表格", category="x")
    assert batch == "" and n == 0 and notes and "导入失败" in notes[0]
