"""
analyzers/stats_analyzer.py — 统计分析器

对清洗后数据执行三步分析:
  1. describe(): 数值字段描述统计（均值/标准差/四分位数）
  2. groupby(): 按指定维度分组聚合（计数/求和/均值）
  3. 缺失值诊断: 字段缺失率 + 填补策略建议

结果存入 AnalyzedData，下游可直接喂给 Reporter。
"""

from typing import Any

import pandas as pd

from backend.data_collection.analyzers.base import AbstractAnalyzer, AnalyzedData
from backend.data_collection.cleaners.base import CleanedData
from backend.shared.logger import logger

# 默认数值字段列表（中文）
DEFAULT_NUMERIC_FIELDS = [
    "售价", "成本", "金额", "单价", "数量", "评分",
    "库存量", "安全库存", "预留量", "商品数", "不良率",
    "交期天数", "起订量",
]

# 常用分组维度
DEFAULT_GROUPBY_KEYS = {
    "products": ["品类", "平台", "状态"],
    "orders": ["渠道", "地区", "状态"],
    "shops": ["平台", "地区", "状态"],
    "inventory": ["仓库", "状态"],
    "suppliers": ["品类", "地区", "状态"],
}

# 缺失值填补策略
MISSING_STRATEGIES = {
    "high_null_ratio": 0.5,       # 缺失率 > 50% → 建议删除
    "mode_threshold": 0.2,        # 缺失率 ≤ 20% → 众数填充（分类）/ 中位数（数值）
}


class StatsAnalyzer(AbstractAnalyzer):
    """统计分析器

    用法:
        analyzer = StatsAnalyzer()
        result = analyzer.analyze(cleaned, config={
            "groupby_keys": ["平台", "品类"],
            "numeric_fields": ["售价", "成本"],
            "dataset_name": "products",
        })

        # result.summary:          describe 统计
        # result.aggregations:     groupby 聚合结果
        # result.missing_report:   缺失值诊断
    """

    def analyze(
        self,
        cleaned: CleanedData,
        config: dict[str, Any] | None = None,
    ) -> AnalyzedData:
        config = config or {}
        groupby_keys = config.get("groupby_keys")
        numeric_fields = config.get("numeric_fields", DEFAULT_NUMERIC_FIELDS)
        dataset_name = config.get("dataset_name", "")

        if not cleaned.records:
            logger.warning("[StatsAnalyzer] 无数据可分析")
            return AnalyzedData(source=cleaned.source, records=[])

        df = pd.DataFrame(cleaned.records)
        logger.info(
            f"[StatsAnalyzer] 开始分析: {len(df)} 行 × {len(df.columns)} 列"
        )

        # ── 1. describe() 统计 ──
        numeric_cols = [c for c in numeric_fields if c in df.columns]
        summary: dict[str, Any] = {}
        if numeric_cols:
            desc = df[numeric_cols].describe(percentiles=[0.25, 0.5, 0.75])
            summary = {
                col: {k: v for k, v in desc[col].items() if pd.notna(v)}
                for col in desc.columns
            }
            logger.info(f"[StatsAnalyzer] describe: {len(summary)} 个数值字段")

        # ── 2. groupby 聚合 ──
        aggregations: dict[str, Any] = {}
        if groupby_keys:
            # 如果未指定，自动推断分组键
            if groupby_keys == "auto" and dataset_name:
                groupby_keys = DEFAULT_GROUPBY_KEYS.get(dataset_name, [])
            elif groupby_keys == "auto":
                # 自动取前几个分类字段
                groupby_keys = [c for c in df.columns if c not in numeric_cols][:2]

            if groupby_keys:
                for gk in groupby_keys:
                    if gk not in df.columns:
                        continue
                    group = df.groupby(gk)
                    agg_dict = {}

                    # 对数值字段计算 count + sum
                    for nc in numeric_cols:
                        if nc in df.columns:
                            agg_dict[nc] = ["count", "sum"]

                    if agg_dict:
                        try:
                            result = group.agg(agg_dict)
                            # 展平 MultiIndex 列名
                            result.columns = [
                                f"{col}_{func}" for col, func in result.columns
                            ]
                            aggregations[f"by_{gk}"] = result.to_dict()
                        except Exception as e:
                            logger.warning(f"[StatsAnalyzer] groupby {gk} 失败: {e}")

                    # 同时上传统计数
                    aggregations[f"by_{gk}_count"] = group.size().to_dict()

                logger.info(
                    f"[StatsAnalyzer] groupby: {list(aggregations.keys())}"
                )

        # ── 3. 缺失值诊断 ──
        missing_report: dict[str, Any] = {}
        for col in df.columns:
            null_count = int(df[col].isna().sum())
            if null_count == 0:
                continue
            null_pct = round(null_count / len(df), 4)

            if null_pct > MISSING_STRATEGIES["high_null_ratio"]:
                strategy = "建议删除该字段（缺失率过高）"
            elif pd.api.types.is_numeric_dtype(df[col]):
                strategy = "用中位数填充"
            else:
                strategy = "用「未知」填充（分类字段）"

            missing_report[col] = {
                "缺失数": null_count,
                "缺失率": null_pct,
                "策略": strategy,
            }

        if missing_report:
            logger.info(f"[StatsAnalyzer] 缺失值诊断: {len(missing_report)} 个字段")

        return AnalyzedData(
            source=cleaned.source,
            records=cleaned.records,
            summary=summary,
            aggregations=aggregations,
            missing_report=missing_report,
        )
