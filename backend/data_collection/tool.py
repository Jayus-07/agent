"""
data_collection/tool.py — LangChain Tool 包装

将 CollectionPipeline 包装为标准 @tool，供 DataCollectionSkill 调用。

Skill → Tool → Pipeline → (Fetcher → Parser → Cleaner → Analyzer → Writer)
"""

import json
from typing import Any

from langchain_core.tools import tool

from backend.data_collection.config import (
    DC_DATABASE_URL,
    DC_HTTP_TIMEOUT,
)
from backend.data_collection.fetchers.static_fetcher import StaticDataFetcher
from backend.data_collection.fetchers.http_fetcher import HttpFetcher
from backend.data_collection.parsers.json_parser import JsonParser
from backend.data_collection.cleaners.default_cleaner import DefaultCleaner
from backend.data_collection.analyzers.stats_analyzer import StatsAnalyzer
from backend.data_collection.writers.sqlalchemy_writer import SQLAlchemyWriter
from backend.data_collection.pipeline import CollectionPipeline, CollectResult
from backend.utils.logger import logger


def _build_pipeline(
    fetcher_type: str,
    enable_analysis: bool = True,
    enable_write: bool = True,
) -> CollectionPipeline:
    """根据参数构建 Pipeline 实例"""
    # Fetcher
    if fetcher_type == "http":
        fetcher = HttpFetcher(timeout=DC_HTTP_TIMEOUT)
    else:
        fetcher = StaticDataFetcher()

    # Parser (后续可自动检测)
    parser = JsonParser()

    # Cleaner
    cleaner = DefaultCleaner()

    # Analyzer (可选)
    analyzer = StatsAnalyzer() if enable_analysis else None

    # Writer (可选)
    writer = SQLAlchemyWriter(DC_DATABASE_URL) if enable_write else None

    return CollectionPipeline(
        fetcher=fetcher,
        parser=parser,
        cleaner=cleaner,
        analyzer=analyzer,
        writer=writer,
    )


@tool
def data_collection_tool(
    source: str = "",
    target_table: str = "stg_products",
    fetcher_type: str = "static",
    dedup_keys: str = "",
    groupby_keys: str = "",
    write_mode: str = "append",
    enable_analysis: bool = True,
    enable_write: bool = False,
) -> str:
    """
    从指定数据源采集数据，经清洗+分析后写入数据库。

    参数:
        source: 数据源
            - "static://datasets/products.json"  本地数据集
            - "http://localhost:8001/mock/products"  Mock API
            - 简写: "products" → static://datasets/products.json
        target_table: 目标数据库表名，默认 stg_products
        fetcher_type: "static" (本地文件) | "http" (HTTP API)
        dedup_keys: 去重键，逗号分隔，如 "SKU" 或 "订单号,SKU"
        groupby_keys: 分析维度，逗号分隔，如 "平台,品类"
        write_mode: "append" | "replace" | "upsert"
        enable_analysis: 是否执行 Pandas 统计分析
        enable_write: 是否写入数据库（仅测试时可设为 false）

    返回: Markdown 格式的采集报告，含统计摘要。
    """
    # 前置处理
    if not source:
        return "❌ 错误: 请提供 source 参数（数据源标识）"

    # 简写 → 完整路径
    if not source.startswith(("static://", "http://", "https://")):
        source = f"static://datasets/{source}.json"

    # 构建 Pipeline
    pipeline = _build_pipeline(
        fetcher_type=fetcher_type,
        enable_analysis=enable_analysis,
        enable_write=enable_write,
    )

    # 清洗规则
    clean_rules: dict[str, Any] = {}
    if dedup_keys:
        clean_rules["dedup_keys"] = [k.strip() for k in dedup_keys.split(",")]

    # 分析配置
    analysis_config: dict[str, Any] | None = None
    if groupby_keys:
        gk = [k.strip() for k in groupby_keys.split(",")]
        # 推断数据集名称用于默认分组键
        ds_name = ""
        for ds in ("products", "orders", "shops", "inventory", "suppliers"):
            if ds in source:
                ds_name = ds
                break
        analysis_config = {
            "groupby_keys": gk,
            "dataset_name": ds_name,
        }
    elif "products" in source:
        analysis_config = {"groupby_keys": "auto", "dataset_name": "products"}
    elif "orders" in source:
        analysis_config = {"groupby_keys": "auto", "dataset_name": "orders"}

    # 执行
    result = pipeline.run(
        source=source,
        table=target_table,
        clean_rules=clean_rules,
        analysis_config=analysis_config,
        write_mode=write_mode,
    )

    # 输出 Markdown 报告
    return _format_result(result)


def _format_result(result: CollectResult) -> str:
    """将 CollectResult 格式化为可读的 Markdown 报告"""
    lines = result.to_markdown().split("\n")

    # 追加统计分析（如果有）
    if result.analyzed:
        analyzed = result.analyzed

        if analyzed.summary:
            lines.append("")
            lines.append("### 📊 数值字段统计")
            lines.append("")
            for field, stats in analyzed.summary.items():
                lines.append(f"**{field}**: 均值={stats.get('mean', 'N/A')}, "
                           f"中位数={stats.get('50%', 'N/A')}, "
                           f"最小={stats.get('min', 'N/A')}, "
                           f"最大={stats.get('max', 'N/A')}")

        if analyzed.aggregations:
            lines.append("")
            lines.append("### 📈 分组聚合结果")
            lines.append("```json")
            lines.append(json.dumps(analyzed.aggregations, ensure_ascii=False, indent=2))
            lines.append("```")

        if analyzed.missing_report:
            lines.append("")
            lines.append("### ⚠️ 缺失值诊断")
            lines.append("")
            for field, info in analyzed.missing_report.items():
                lines.append(f"- **{field}**: {info['缺失数']} 条缺失 "
                           f"({info['缺失率']:.1%}), {info['策略']}")

    return "\n".join(lines)
