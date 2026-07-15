"""
cleaners/default_cleaner.py — 默认数据清洗器

组合去重 + 类型转换 + 缺失值填充三个步骤。
基于 Pandas 实现。
"""

from typing import Any

import pandas as pd

from backend.data_collection.cleaners.base import AbstractCleaner, CleanedData
from backend.shared.logger import logger

# 默认类型映射规则（中文字段名 → Python 类型）
DEFAULT_TYPE_MAP = {
    "售价": float,
    "成本": float,
    "金额": float,
    "单价": float,
    "数量": int,
    "评分": float,
    "库存量": int,
    "安全库存": int,
    "预留量": int,
    "商品数": int,
    "不良率": float,
    "交期天数": int,
    "起订量": int,
}


class DefaultCleaner(AbstractCleaner):
    """默认数据清洗器

    三步清洗:
      1. 去重: 基于 dedup_keys 精确去重
      2. 类型转换: 根据 type_map 转换字段类型
      3. 缺失值填充: 数值用中位数，字符串用"未知"

    用法:
        cleaner = DefaultCleaner()
        cleaned = cleaner.clean(records, rules={
            "dedup_keys": ["SKU"],
            "type_map": {"售价": float},
            "fill_values": {"状态": "在售"},
        })
    """

    def clean(
        self,
        records: list[dict[str, Any]],
        rules: dict[str, Any] | None = None,
        source: str = "",
    ) -> CleanedData:
        if not records:
            return CleanedData(
                source=source, records=[], row_count=0, dedup_removed=0,
            )

        rules = rules or {}
        dedup_keys = rules.get("dedup_keys")
        type_map = rules.get("type_map", DEFAULT_TYPE_MAP)
        fill_values = rules.get("fill_values", {})

        df = pd.DataFrame(records)
        original_count = len(df)

        # ── 步1: 去重 ──
        if dedup_keys:
            existing_keys = [k for k in dedup_keys if k in df.columns]
            if existing_keys:
                before = len(df)
                df = df.drop_duplicates(subset=existing_keys, keep="first")
                dedup_removed = before - len(df)
                logger.info(f"[DefaultCleaner] 去重: {before} → {len(df)} ({dedup_removed} 条移除)")
            else:
                dedup_removed = 0
        else:
            # 全字段去重
            before = len(df)
            df = df.drop_duplicates()
            dedup_removed = before - len(df)
            logger.info(f"[DefaultCleaner] 全字段去重: {before} → {len(df)} ({dedup_removed} 条移除)")

        # ── 步2: 类型转换 ──
        type_converted: dict[str, str] = {}
        for col, target_type in type_map.items():
            if col not in df.columns:
                continue
            try:
                old_dtype = str(df[col].dtype)
                if target_type in (int, float):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                elif target_type is str:
                    df[col] = df[col].astype(str)
                type_converted[col] = f"{old_dtype}→{target_type.__name__}"
            except Exception as e:
                logger.warning(f"[DefaultCleaner] 类型转换失败 {col}: {e}")

        # ── 步3: 缺失值填充 ──
        null_filled: dict[str, int] = {}
        for col in df.columns:
            null_count = df[col].isna().sum()
            if null_count == 0:
                continue

            # 用户指定的填充值优先
            if col in fill_values:
                df[col] = df[col].fillna(fill_values[col])
            elif pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].fillna(df[col].median())
            else:
                df[col] = df[col].fillna("未知")

            null_filled[col] = int(null_count)

        # NaN → None (JSON 兼容)
        records_clean = df.astype(object).where(pd.notnull(df), None).to_dict(
            orient="records"
        )

        logger.info(
            f"[DefaultCleaner] 清洗完成: {original_count} → {len(records_clean)} 条, "
            f"去重 {dedup_removed}, 填缺 {sum(null_filled.values())}"
        )

        return CleanedData(
            source=source,
            records=records_clean,
            row_count=len(records_clean),
            dedup_removed=dedup_removed,
            null_filled=null_filled,
            type_converted=type_converted,
        )
