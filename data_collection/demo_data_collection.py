"""
demo_data_collection.py — Data Collection Center 一键演示脚本

串联 5 套电商业务数据集，走完整 Pipeline:
  StaticFetcher → JsonParser → DefaultCleaner → StatsAnalyzer → (可选) SQLAlchemyWriter

演示场景:
  1. 全部 5 个数据集采集分析（仅分析，不写库）
  2. 含 PostgreSQL 写入的完整闭环（--write）
  3. 通过 Scheduler 批量执行

用法:
  ./.venv/Scripts/python.exe data_collection/demo_data_collection.py           # 仅分析
  ./.venv/Scripts/python.exe data_collection/demo_data_collection.py --write   # 含数据库写入
  ./.venv/Scripts/python.exe data_collection/demo_data_collection.py --chart   # 含可视化图表
  ./.venv/Scripts/python.exe data_collection/demo_data_collection.py --http    # HttpFetcher 模式（需先启动 Mock API）

依赖:
  已有依赖即可，无需额外安装。
"""

import io
import os
import sys
import time
from typing import Any

# ── 项目根路径 + UTF-8 ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"

from data_collection.pipeline import CollectionPipeline, CollectResult
from data_collection.fetchers.static_fetcher import StaticDataFetcher
from data_collection.fetchers.http_fetcher import HttpFetcher
from data_collection.parsers.json_parser import JsonParser
from data_collection.cleaners.default_cleaner import DefaultCleaner
from data_collection.analyzers.stats_analyzer import StatsAnalyzer
from data_collection.scheduler import Scheduler
from data_collection.visualizer import DataVisualizer
from data_collection.config import DC_DATABASE_URL
from utils.logger import logger


# ══════════════════════════════════════════════════════════
# 数据集定义
# ══════════════════════════════════════════════════════════

DATASETS = [
    {
        "name": "商品数据",
        "source": "static://datasets/products.json",
        "table": "stg_products",
        "dedup_keys": ["sku"],
        "groupby_keys": ["品类", "平台", "状态"],
        "dataset_name": "products",
    },
    {
        "name": "订单数据",
        "source": "static://datasets/orders.json",
        "table": "stg_orders",
        "dedup_keys": ["订单号"],
        "groupby_keys": ["渠道", "地区", "状态"],
        "dataset_name": "orders",
    },
    {
        "name": "店铺数据",
        "source": "static://datasets/shops.json",
        "table": "stg_shops",
        "dedup_keys": ["店铺ID"],
        "groupby_keys": ["平台", "地区", "状态"],
        "dataset_name": "shops",
    },
    {
        "name": "库存数据",
        "source": "static://datasets/inventory.json",
        "table": "stg_inventory",
        "dedup_keys": ["SKU", "仓库"],
        "groupby_keys": ["仓库", "状态"],
        "dataset_name": "inventory",
    },
    {
        "name": "供应商数据",
        "source": "static://datasets/suppliers.json",
        "table": "stg_suppliers",
        "dedup_keys": ["供应商ID"],
        "groupby_keys": ["品类", "地区", "状态"],
        "dataset_name": "suppliers",
    },
]


# ══════════════════════════════════════════════════════════
# 构建 Pipeline
# ══════════════════════════════════════════════════════════

def build_pipeline(use_http: bool = False, enable_write: bool = False) -> CollectionPipeline:
    """根据参数构建 Pipeline"""
    fetcher = HttpFetcher() if use_http else StaticDataFetcher()
    writer = None
    if enable_write:
        try:
            from data_collection.writers.sqlalchemy_writer import SQLAlchemyWriter
            writer = SQLAlchemyWriter(DC_DATABASE_URL)
        except Exception as e:
            print(f"  ⚠️ 数据库连接失败: {e}\n  → 将跳过写入，仅执行分析\n")

    return CollectionPipeline(
        fetcher=fetcher,
        parser=JsonParser(),
        cleaner=DefaultCleaner(),
        analyzer=StatsAnalyzer(),
        writer=writer,
    )


# ══════════════════════════════════════════════════════════
# 输出格式化
# ══════════════════════════════════════════════════════════

def print_header(title: str) -> None:
    width = 70
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def print_result(
    i: int, total: int, ds: dict, result: CollectResult,
    visualizer: "DataVisualizer | None" = None,
) -> None:
    """单数据集采集结果"""
    status_icon = {"success": "✅", "partial": "⚠️", "failed": "❌"}.get(
        result.status, "❓"
    )

    print(f"\n{'─' * 70}")
    print(f"  [{i}/{total}] {status_icon} {ds['name']} — {result.status}")
    print(f"{'─' * 70}")

    if result.parsed:
        print(f"  解析: {result.parsed.record_count} 条记录")

    if result.cleaned:
        c = result.cleaned
        print(f"  清洗: {c.row_count} 条 (去重移除 {c.dedup_removed} 条)")
        if c.null_filled:
            filled = ", ".join(f"{k}:{v}" for k, v in c.null_filled.items())
            print(f"  填充缺失值: {filled}")

    if result.analyzed:
        a = result.analyzed
        if a.summary:
            for field, stats in a.summary.items():
                mean = stats.get("mean", "N/A")
                print(f"  {field} 均值: {mean}")

        if a.aggregations:
            for agg_name, agg_data in a.aggregations.items():
                if agg_name.endswith("_count"):
                    # 此 key 存储的就是分组计数 {"电子产品": 2, "配件": 1, ...}
                    if isinstance(agg_data, dict):
                        print(f"  {agg_name}: {len(agg_data)} 个分组")

        if a.missing_report:
            print(f"  缺失值诊断: {len(a.missing_report)} 个字段需关注")

        # 生成图表
        if visualizer and result.status == "success":
            chart_md = visualizer.render(result, dataset_name=ds.get("dataset_name", ""))
            if chart_md:
                print(f"  📊 图表: 已生成")

    if result.write:
        w = result.write
        print(f"  写入: insert={w.inserted}, skip={w.skipped}, {w.elapsed_ms:.0f}ms")

    if result.error:
        print(f"  ❌ 错误: {result.error}")


def print_summary(results: dict[str, CollectResult], total_elapsed: float) -> None:
    """汇总报告"""
    print_header("采集汇总")

    success = sum(1 for r in results.values() if r.status == "success")
    partial = sum(1 for r in results.values() if r.status == "partial")
    failed = sum(1 for r in results.values() if r.status == "failed")

    total_parsed = sum(
        r.parsed.record_count
        for r in results.values()
        if r.parsed and r.status != "failed"
    )
    total_cleaned = sum(
        r.cleaned.row_count
        for r in results.values()
        if r.cleaned and r.status != "failed"
    )

    print(f"""
  执行结果:
    成功:  {success}/{len(results)}
    部分:  {partial}/{len(results)}
    失败:  {failed}/{len(results)}

  数据统计:
    总解析:  {total_parsed} 条
    总清洗:  {total_cleaned} 条
    去重移除: {sum(r.cleaned.dedup_removed for r in results.values() if r.cleaned)} 条

  总耗时:  {total_elapsed:.0f}ms

  数据集明细:
""")

    for name, r in results.items():
        status = r.status
        parsed_n = r.parsed.record_count if r.parsed else 0
        cleaned_n = r.cleaned.row_count if r.cleaned else 0
        print(f"    {name:　<6s}  {status:8s}  解析 {parsed_n:3d} → 清洗 {cleaned_n:3d}")


# ══════════════════════════════════════════════════════════
# 主演示
# ══════════════════════════════════════════════════════════

def demo_all(
    use_http: bool = False,
    enable_write: bool = False,
    enable_chart: bool = False,
) -> None:
    """一键演示：批量采集全部 5 个数据集"""
    fetcher_mode = "HTTP" if use_http else "StaticFile"
    print_header(f"Data Collection Center 演示 — Fetcher: {fetcher_mode}")

    pipeline = build_pipeline(use_http=use_http, enable_write=enable_write)
    visualizer = DataVisualizer() if enable_chart else None

    # ── 注册全部任务到 Scheduler ──
    scheduler = Scheduler()
    for ds in DATASETS:
        source = ds["source"]
        # HttpFetcher 模式：将 static:// 转换为 http:// URL
        if use_http:
            ds_name = source.rsplit("/", 1)[-1].replace(".json", "")
            source = f"http://localhost:8001/mock/{ds_name}"

        table = ds["table"]
        dedup_keys = ds["dedup_keys"]
        analysis_config = {
            "groupby_keys": ds["groupby_keys"],
            "dataset_name": ds.get("dataset_name", ""),
        }

        # 闭包捕获当前变量
        def make_task(s=source, t=table, dk=dedup_keys, ac=analysis_config):
            return lambda: pipeline.run(
                source=s,
                table=t,
                dedup_keys=dk,
                analysis_config=ac,
                write_mode="append",
            )

        scheduler.register(
            name=ds["name"],
            task=make_task(),
            description=f"采集 {ds['name']} → {ds['table']}",
        )

    # ── 逐个执行并实时输出 ──
    total = len(DATASETS)
    results: dict[str, CollectResult] = {}
    overall_start = time.perf_counter()

    for i, ds in enumerate(DATASETS, 1):
        name = ds["name"]
        result = scheduler.run_now(name)
        results[name] = result
        print_result(i, total, ds, result, visualizer=visualizer)

    overall_elapsed = (time.perf_counter() - overall_start) * 1000

    # ── 汇总 ──
    print_summary(results, overall_elapsed)

    # ── 后续建议 ──
    chart_info = ""
    if enable_chart:
        charts_dir = os.path.join(os.path.dirname(__file__), "charts")
        chart_info = f"  图表目录:    {charts_dir} ({len(os.listdir(charts_dir))} 张)\n"

    print(f"""
{'=' * 70}
  后续可执行:
    SQL Agent:   "查询库存不足的 SKU"
    SQL Agent:   "分析各渠道销售额排名"
    Reporter:    "生成库存健康报告"
    LangGraph:   "采集 amazon 商品数据并生成销售日报"
{chart_info}{'=' * 70}
""")


# ══════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Data Collection Center 一键演示"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="写入 PostgreSQL 数据库（默认仅分析不写入）",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="使用 HttpFetcher 从 Mock API 获取数据（需先启动 Mock API Server）",
    )
    parser.add_argument(
        "--chart",
        action="store_true",
        help="生成 matplotlib 可视化图表（保存到 data_collection/charts/）",
    )
    args = parser.parse_args()

    if args.http:
        print("ℹ️  HttpFetcher 模式 — 请确保 Mock API 已启动:")
        print("    python -m data_collection.mock_api.server")
        print()

    demo_all(use_http=args.http, enable_write=args.write, enable_chart=args.chart)
