"""selection_funnel/import_pool.py — 海选候选池：批量导入通道

数据源背景（2026-09-17 用户拍板 A 方案）：
  竞品监控 watchlist 受反爬预算硬约束（anti_ban.GLOBAL_DAILY_BUDGET=40 次/天），
  只能稳定养 1-2 个对象的持续快照，撑不起漏斗「海选」语义——监控池是盯盘，
  漏斗建池要的是类目大盘候选，量级差两个数量级。

本模块承接运营真实工作流：从生意参谋 / 竞品分析工具导出表格
（CSV / Excel(.xlsx) / Excel 复制出的 TSV 文本）→ 中文表头宽松映射 →
归一化候选池（PostgreSQL，agent_business 库）→ pool_builder 的主源。
watchlist 降级为兜底补充源（顺序由 SELECTION_FUNNEL_POOL_SOURCES 配置，
默认 import 在前）。

存储演进（2026-09-18 用户拍板「SQLite 不要了」）：SQLite 轨整体退场，
生产唯一后端为 import_pool_pg.PostgresImportPoolStore（高并发连接池）；
单测经 conftest 注入内存替身（DB 属外部依赖，按测试纪律 mock），
PG 真实行为由 test_import_pool_pg.py 集成测试锁定。

原则：
  - 宁缺毋造：销量列不冒充评价数；缺失字段保留 None 由下游披露
  - 未识别列收进 extra，不丢信息
  - 解析/存储失败一律降级为空列表 + note，不炸漏斗
"""
from __future__ import annotations

import io
import os
import re
from typing import Any

from backend.shared.logger import logger

# 单批导入行数上限（防手滑拖入超大文件拖垮建库）
MAX_IMPORT_ROWS = int(os.getenv("SELECTION_IMPORT_MAX_ROWS", "2000"))

# ── 中文表头宽松映射（strip + 去空白后精确匹配；同名列取第一个命中）──────
_COLUMN_ALIASES: dict[str, set[str]] = {
    "title": {"标题", "商品标题", "商品名称", "商品名", "宝贝名称", "宝贝标题", "名称", "title", "product name"},
    "price": {"价格", "售价", "到手价", "单价", "活动价", "售价(元)", "price"},
    "original_price": {"原价", "划线价", "日常价", "原价(元)", "original price"},
    "rating": {"评分", "宝贝评分", "商品评分", "店铺评分", "星级", "rating", "score"},
    "review_count": {"评价数", "评论数", "评价人数", "评论数量", "reviews"},
    "sales": {"销量", "月销量", "月销", "付款人数", "30天销量", "近30天销量", "sales"},
    "platform": {"平台", "渠道", "平台渠道", "platform"},
    "category": {"类目", "品类", "一级类目", "叶子类目", "category"},
    "url": {"链接", "商品链接", "链接地址", "商品地址", "url", "link"},
    "unit_cost": {"成本", "进货价", "进价", "成本价", "采购价", "成本(元)", "unit_cost", "cost"},
    "promo_text": {"促销", "优惠", "促销信息", "活动信息", "优惠活动", "promo"},
    "highlights": {"卖点", "商品卖点", "亮点", "亮点描述", "核心卖点", "highlights"},
}


def _norm_header(h: Any) -> str:
    return re.sub(r"\s+", "", str(h or "")).lower()


def map_columns(headers: list[str]) -> tuple[dict[int, str], dict[str, str]]:
    """表头行 → {列下标: 归一字段}；返回 (mapping, 未知列名表)。

    未知列保留原名（后续进 extra），不丢弃信息。
    """
    mapping: dict[int, str] = {}
    unknown: dict[str, str] = {}
    lowered = [_norm_header(h) for h in headers]
    for idx, h in enumerate(lowered):
        if not h:
            continue
        hit: str | None = None
        for field, aliases in _COLUMN_ALIASES.items():
            if h in aliases:
                hit = field
                break
        if hit and hit not in mapping.values():
            mapping[idx] = hit
        else:
            # 未知列或已被占用的重复语义列 → 按 header 原文进 extra
            unknown[str(idx)] = str(headers[idx]).strip()
    return mapping, unknown


def _to_float(v: Any) -> float | None:
    """宽松数值清洗：¥1,299 / 4.8分 / 1.2万 / 3亿 → float；解析失败 → None。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    s = re.sub(r"[,，¥￥元分条个件\s]", "", s)
    mult = 1.0
    if s.endswith("万"):
        mult, s = 10000.0, s[:-1]
    elif s.endswith("亿"):
        mult, s = 100000000.0, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


# 反查表：原始表头（归一化后）→ 归一字段，供 normalize_row 兼容两种 key
_HEADER_TO_FIELD: dict[str, str] = {}
for _field, _aliases in _COLUMN_ALIASES.items():
    _HEADER_TO_FIELD[_field] = _field
    for _a in _aliases:
        _HEADER_TO_FIELD[_a] = _field


def normalize_row(raw: dict[str, Any]) -> dict[str, Any]:
    """原始行 → 归一化候选 dict。title 缺失视为无效行。

    key 兼容两种：归一字段名（parse_table 产物）或原始中文表头。
    """
    out: dict[str, Any] = {
        "title": "", "platform": "", "price": None, "original_price": None,
        "rating": None, "review_count": None, "sales": None,
        "unit_cost": None,
        "category": "", "url": "", "promo_text": "", "highlights": "",
    }
    extra: dict[str, Any] = {}
    for k, v in raw.items():
        field = _HEADER_TO_FIELD.get(_norm_header(k))
        if field:
            if field in ("title", "platform", "category", "url", "promo_text", "highlights"):
                out[field] = str(v).strip() if v is not None else ""
            else:
                out[field] = _to_float(v)
        else:
            extra[k] = v
    if out["review_count"] is not None:
        out["review_count"] = int(round(out["review_count"]))
    if out["sales"] is not None:
        out["sales"] = int(round(out["sales"]))
    out["extra"] = extra or None
    return out


# ── 表格解析（CSV / TSV / XLSX 统一入口）───────────────────────────────
def _sniff_delimiter(text: str) -> str:
    """首行嗅探：制表符多于逗号 → TSV（Excel 复制粘贴产物），否则 CSV。"""
    first = text.splitlines()[0] if text.splitlines() else ""
    return "\t" if first.count("\t") > first.count(",") else ","


def _rows_from_text(text: str) -> list[list[str]]:
    import csv as _csv
    delim = _sniff_delimiter(text)
    return [r for r in _csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]


def _rows_from_xlsx(data: bytes) -> list[list[Any]]:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    return [list(row) for row in ws.iter_rows(values_only=True) if any(c is not None for c in row)]


def parse_table(data: bytes | str, filename: str = "") -> list[dict[str, Any]]:
    """解析表格为归一化候选列表。

    支持格式：CSV 文本/bytes、TSV 文本（Excel 复制粘贴）、XLSX bytes。
    首行必须是表头。无效行（缺标题）剔除；行数超 MAX_IMPORT_ROWS 截断。
    Returns: [{"title":..., "price":..., ...}, ...]（normalize_row 产物）
    Raises: ValueError（空表/无表头/未知格式）
    """
    if isinstance(data, bytes) and data[:2] == b"PK":
        rows = _rows_from_xlsx(data)
    elif isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
        rows = _rows_from_text(data)  # type: ignore[arg-type]
    elif isinstance(data, str):
        rows = _rows_from_text(data)
    else:
        raise ValueError("不支持的导入格式，请提供 CSV/TSV 文本或 XLSX 文件")
    if not rows:
        raise ValueError("导入表格为空")

    headers = [str(h or "").strip() for h in rows[0]]
    mapping, _unknown = map_columns(headers)
    if "title" not in mapping.values():
        raise ValueError("表头中未识别到「标题」列（支持：标题/商品标题/商品名/title 等）")

    invalid = 0
    out: list[dict[str, Any]] = []
    for row in rows[1:]:
        raw: dict[str, Any] = {}
        extra_headers = {i: h for i, h in enumerate(headers) if i not in mapping}
        for idx, cell in enumerate(row):
            field = mapping.get(idx)
            if field:
                raw[field] = cell
            else:
                name = extra_headers.get(idx)
                if name:
                    raw[name] = cell
        norm = normalize_row(raw)
        if not norm["title"]:
            invalid += 1
            continue
        out.append(norm)
        if len(out) >= MAX_IMPORT_ROWS:
            break
    if invalid:
        logger.info("[ImportPool] 剔除无效行（缺标题）%d 条", invalid)
    if not out:
        raise ValueError("导入表格中没有有效数据行（每行至少要有标题）")
    return out


# ── 同款去重键（漏斗唯一口径，pool_builder / verifier / 存储层共用）────
def dedup_key(url: str, title: str, platform: str) -> str:
    """同款判定键：url 优先（有链接以链接为准），无链接退 (title|platform)。

    单一来源定义：pool_builder 的源内/跨源去重与 verifier 的同款历史分组
    都必须走本函数，避免两处口径漂移。
    """
    url = (url or "").strip()
    if url:
        return f"url:{url}"
    return f"title:{(title or '').strip()}|{platform or ''}"


# ── 存储工厂（PG-only，2026-09-18 SQLite 轨退场）───────────────────────
_default_store: Any = None


def _new_default_store() -> Any:
    """生产唯一后端：PostgresImportPoolStore（agent_business 库，高并发连接池）。

    纯分发函数保留供测试直测（conftest 以内存替身 monkeypatch
    get_import_store，DB 属外部依赖按纪律 mock）。
    """
    from backend.selection_funnel.import_pool_pg import PostgresImportPoolStore
    return PostgresImportPoolStore()


def get_import_store() -> Any:
    """惰性单例（测试 monkeypatch get_import_store 注入内存替身）。"""
    global _default_store
    if _default_store is None:
        _default_store = _new_default_store()
    return _default_store


def import_table(data: bytes | str, category: str = "", platform: str = "",
                 filename: str = "") -> tuple[str, int, list[str]]:
    """一条龙：解析 → 入库。Returns: (batch_id, 行数, notes)。

    解析失败不炸调用方——返回 ("", 0, [原因])。
    """
    notes: list[str] = []
    try:
        rows = parse_table(data, filename=filename)
    except Exception as e:
        logger.warning("[ImportPool] 表格解析失败: %s", e)
        return "", 0, [f"导入失败: {e}"]
    truncated = len(rows) >= MAX_IMPORT_ROWS
    batch_id, n = get_import_store().add_batch(rows, category=category, platform=platform)
    notes.append(f"导入批次 {batch_id}：{n} 条候选已入库"
                 + (f"（达到单批上限 {MAX_IMPORT_ROWS}，超出部分未纳入）" if truncated else ""))
    return batch_id, n, notes
