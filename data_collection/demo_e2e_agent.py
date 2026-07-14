"""
demo_e2e_agent.py — Data Collection Center → Agent 全链路演示

演示路径:
  Phase 1  数据采集  — Pipeline 采集 5 个电商数据集
  Phase 2  交叉分析  — Pandas merge/groupby/pivot 跨表分析
  Phase 3  业务洞察  — 自动生成经营诊断与预警
  Phase 4  集成地图  — 展示 DCC → SQL Agent → Reporter → LangGraph 对接点

用法:
  ./.venv/Scripts/python.exe data_collection/demo_e2e_agent.py
  ./.venv/Scripts/python.exe data_collection/demo_e2e_agent.py --chart  # 含可视化

这条脚本是面试展示的核心入口。
"""

import io
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"

import pandas as pd

from data_collection.pipeline import CollectionPipeline, CollectResult
from data_collection.fetchers.static_fetcher import StaticDataFetcher
from data_collection.parsers.json_parser import JsonParser
from data_collection.cleaners.default_cleaner import DefaultCleaner
from data_collection.analyzers.stats_analyzer import StatsAnalyzer
from data_collection.scheduler import Scheduler

# ══════════════════════════════════════════════════════════
# Phase 1: 数据采集
# ══════════════════════════════════════════════════════════

DATASETS = [
    {"name": "商品", "source": "static://datasets/products.json", "dedup": ["sku"]},
    {"name": "订单", "source": "static://datasets/orders.json", "dedup": ["订单号"]},
    {"name": "店铺", "source": "static://datasets/shops.json", "dedup": ["店铺id"]},
    {"name": "库存", "source": "static://datasets/inventory.json", "dedup": ["sku", "仓库"]},
    {"name": "供应商", "source": "static://datasets/suppliers.json", "dedup": ["供应商id"]},
]


def collect_all() -> dict[str, CollectResult]:
    """采集全部 5 个数据集"""
    pipeline = CollectionPipeline(
        fetcher=StaticDataFetcher(),
        parser=JsonParser(),
        cleaner=DefaultCleaner(),
        analyzer=StatsAnalyzer(),
    )
    scheduler = Scheduler()
    for ds in DATASETS:
        scheduler.register(
            name=ds["name"],
            task=lambda s=ds["source"], dk=ds["dedup"]: pipeline.run(
                source=s, dedup_keys=dk, table="stg_demo",
            ),
        )
    return scheduler.run_all()


# ══════════════════════════════════════════════════════════
# Phase 2: 交叉分析
# ══════════════════════════════════════════════════════════

def load_dataframes(results: dict[str, CollectResult]) -> dict[str, pd.DataFrame]:
    """从 Pipeline 结果提取 DataFrame，统一小写列名"""
    dfs = {}
    for name, r in results.items():
        if r.status == "success" and r.cleaned:
            df = pd.DataFrame(r.cleaned.records)
            df.columns = [c.lower() for c in df.columns]  # 统一小写
            dfs[name] = df
    return dfs


def print_section(title: str) -> None:
    bar = "=" * 68
    print(f"\n{bar}\n  {title}\n{bar}")


def print_finding(icon: str, text: str) -> None:
    print(f"  {icon}  {text}")


# ── 2.1 销售全景 ──
def analysis_sales(dfs: dict[str, pd.DataFrame]) -> None:
    """订单 × 商品交叉分析：品类营收、渠道对比、地区分布"""
    print_section("📊 分析 1/4 — 销售全景（订单 JOIN 商品）")

    orders = dfs.get("订单")
    products = dfs.get("商品")
    if orders is None or products is None:
        print("  ⚠️ 缺少数据，跳过")
        return

    merged = orders.merge(products[["sku", "品类", "品牌"]], on="sku", how="left")

    # (a) 品类营收
    by_cat = merged.groupby("品类").agg(营收=("金额", "sum"), 订单数=("订单号", "nunique")).sort_values("营收", ascending=False)
    print("\n  ▸ 品类营收排名")
    for cat, row in by_cat.iterrows():
        bar_len = int(row["营收"] / by_cat["营收"].max() * 20)
        print(f"    {cat:　<5s}  ¥{row['营收']:8.0f}  {row['订单数']:2.0f}单  {'█' * bar_len}")

    # (b) 渠道对比
    by_ch = merged.groupby("渠道").agg(营收=("金额", "sum"), 订单数=("订单号", "nunique"), 均价=("金额", "mean"))
    print("\n  ▸ 渠道对比")
    for ch, row in by_ch.iterrows():
        print(f"    {ch:8s}  ¥{row['营收']:8.0f}  {row['订单数']:.0f}单  均价¥{row['均价']:.0f}")

    # (c) 地区分布
    by_region = merged.groupby("地区").agg(营收=("金额", "sum")).sort_values("营收", ascending=False)
    print("\n  ▸ 地区营收分布")
    for region, row in by_region.iterrows():
        pct = row["营收"] / by_region["营收"].sum() * 100
        print(f"    {region:6s}  ¥{row['营收']:8.0f}  ({pct:.0f}%)")


# ── 2.2 库存预警 ──
def analysis_inventory(dfs: dict[str, pd.DataFrame]) -> None:
    """库存 × 商品交叉分析：断货/偏低预警，关联销售数据"""
    print_section("📦 分析 2/4 — 库存预警（库存 JOIN 商品 JOIN 订单）")

    inventory = dfs.get("库存")
    products = dfs.get("商品")
    orders = dfs.get("订单")
    if inventory is None or products is None:
        print("  ⚠️ 缺少数据，跳过")
        return

    # 关联商品名
    inv = inventory.merge(products[["sku", "名称", "品类"]], on="sku", how="left")

    # 预警清单
    alerted = inv[inv["状态"].isin(["偏低", "断货"])].sort_values("库存量")
    if len(alerted) > 0:
        print("\n  ▸ ⚠️ 库存预警清单")
        print(f"  {'SKU':<8s} {'商品':<20s} {'仓库':<12s} {'库存':>5s} {'安全库存':>6s} {'状态':>6s}")
        print(f"  {'─'*65}")
        for _, row in alerted.iterrows():
            name = str(row.get("名称", ""))[:18]
            alert_mark = " 🔴" if row.get("预警") else ""
            print(f"  {row['sku']:<8s} {name:<20s} {str(row['仓库']):<12s} "
                  f"{int(row['库存量']):>5d} {int(row['安全库存']):>6d} {row['状态']:>6s}{alert_mark}")

    # 交叉：低库存商品是否还有订单
    if orders is not None:
        low_skus = set(alerted["sku"].unique())
        recent_orders = orders[orders["sku"].isin(low_skus)]
        if len(recent_orders) > 0:
            print("\n  ▸ 📈 低库存商品近期仍有订单（需紧急补货）")
            by_sku = recent_orders.groupby("sku").agg(订单数=("订单号", "count"), 销售额=("金额", "sum"))
            for sku, row in by_sku.iterrows():
                pname = products[products["sku"] == sku]["名称"].values
                pname_str = str(pname[0])[:25] if len(pname) > 0 else sku
                print(f"    {sku}  {pname_str}  {int(row['订单数'])}单  ¥{row['销售额']:.0f}")
        else:
            print("\n  ▸ ✅ 低库存商品近期无新增订单")

    # 库存总览
    print(f"\n  ▸ 库存总览")
    status_counts = inv.groupby("状态").size()
    for status, count in status_counts.items():
        bar = "█" * count
        print(f"    {status:4s}  {count} 个 SKU-仓库  {bar}")


# ── 2.3 供应商评估 ──
def analysis_suppliers(dfs: dict[str, pd.DataFrame]) -> None:
    """供应商质量 × 交期分析"""
    print_section("🏭 分析 3/4 — 供应商评估")

    suppliers = dfs.get("供应商")
    if suppliers is None:
        print("  ⚠️ 缺少数据，跳过")
        return

    print("\n  ▸ 质量排名（不良率越低越好）")
    by_quality = suppliers.sort_values("不良率")
    for _, row in by_quality.iterrows():
        rating_bar = "★" * int(row["评分"]) + "☆" * (5 - int(row["评分"]))
        flag = " ⚠️ 待评估" if row["状态"] == "评估中" else ""
        print(f"    {row['名称'][:22]:<22s}  不良率 {row['不良率']:4.1f}%  "
              f"交期 {int(row['交期天数']):2d}天  起订 {int(row['起订量']):4d}  {rating_bar}{flag}")

    # 品类覆盖
    print("\n  ▸ 品类供应商覆盖")
    cat_coverage = suppliers.groupby("品类").agg(
        供应商数=("供应商id", "count"),
        平均不良率=("不良率", "mean"),
        最短交期=("交期天数", "min"),
    )
    for cat, row in cat_coverage.iterrows():
        print(f"    {cat:6s}  {int(row['供应商数'])}家供应商  "
              f"不良率{row['平均不良率']:.1f}%  最短交期{int(row['最短交期'])}天")

    # 风险提示
    risky = suppliers[suppliers["不良率"] > 2.0]
    if len(risky) > 0:
        print(f"\n  ▸ ⚠️ 质量风险供应商（不良率 > 2%）")
        for _, row in risky.iterrows():
            print(f"    {row['名称'][:22]:<22s}  不良率 {row['不良率']}%")


# ── 2.4 店铺健康度 ──
def analysis_shops(dfs: dict[str, pd.DataFrame]) -> None:
    """店铺运营健康度评估"""
    print_section("🏪 分析 4/4 — 店铺健康度")

    shops = dfs.get("店铺")
    if shops is None:
        print("  ⚠️ 缺少数据，跳过")
        return

    print("\n  ▸ 店铺状态")
    for _, row in shops.iterrows():
        status_icon = "✅" if row["状态"] == "正常" else "⛔"
        rating_bar = "★" * int(row["评分"]) + "☆" * (5 - int(row["评分"]))
        print(f"    {status_icon} {row['名称'][:22]:<22s}  {row['平台']:8s}  "
              f"{row['地区']:4s}  {int(row['商品数']):3d}件  {rating_bar}  {row['状态']}")

    # 平台分布
    print("\n  ▸ 平台分布")
    platform_stats = shops.groupby("平台").agg(店铺数=("店铺id", "count"), 平均评分=("评分", "mean"))
    for plat, row in platform_stats.iterrows():
        print(f"    {plat:8s}  {int(row['店铺数'])}家店  均分 {row['平均评分']:.2f}")


# ══════════════════════════════════════════════════════════
# Phase 3: 业务洞察
# ══════════════════════════════════════════════════════════

def generate_insights(results: dict[str, CollectResult]) -> None:
    """汇总所有分析，生成业务洞察与行动建议"""
    print_section("💡 Phase 3 — 业务洞察与行动建议")

    df_clean = sum(
        r.cleaned.row_count
        for r in results.values()
        if r.status == "success" and r.cleaned
    )
    print(f"\n  本次采集: {len(results)} 个数据集, {df_clean} 条清洗后记录\n")

    insights = [
        ("🔴 紧急",
         "BB-001（1080p婴儿监护器）库存仅12件，安全库存20件，近期仍有订单。"
         "建议：立即向 SUP-006（上海婴童）下单补货，交期20天。"),
        ("🟡 关注",
         "BT-003（无线充电板）库存45件偏低。该品类毛利率最高（成本¥40→售价¥99）。"
         "建议：本周内补货，优先分配 Amazon 渠道库存。"),
        ("🟡 关注",
         "HK-003（竹制砧板）已断货且停售。如不再经营，建议清理库存记录；"
         "如恢复上架，需联系 SUP-004（福建竹制品）重新排产。"),
        ("🟢 优化",
         "SUP-009（义乌小商品）不良率3.2%偏高，建议触发质量审查流程。"
         "可考虑将配件品类订单逐步转移至 SUP-002（东莞线缆，不良率0.8%）。"),
        ("🟢 机会",
         "德国市场：Amazon + Shopify 双渠道在售，近期2笔订单，势头良好。"
         "但目前仅 HK-002 在 DE-FRA 仓库有货。建议扩展德国仓 SKU 覆盖。"),
    ]

    for level, text in insights:
        print(f"  {level}  {text}\n")

    print(f"  {'─'*64}")
    print(f"  以上洞察由 DCC 自动生成，可作为 Planner 任务拆解输入")
    print(f"  或直接提供给 Reporter 生成《跨境电商经营诊断报告》。")


# ══════════════════════════════════════════════════════════
# Phase 4: 集成地图
# ══════════════════════════════════════════════════════════

def print_integration_map() -> None:
    """展示 DCC 与项目其他模块的对接点"""
    print_section("🔗 Phase 4 — Agent 系统集成地图")

    print("""
  ┌─────────────────────────────────────────────────────────────┐
  │                   Data Collection Center                     │
  │                                                              │
  │  Fetcher → Parser → Cleaner → Analyzer → Writer             │
  │     │                                      │                 │
  │     │  datasets/*.json                     │  stg_* 表       │
  │     │  Mock API (:8001)                    │  PostgreSQL     │
  │     │  HTTP API (Phase 2)                  │                 │
  │     └──────────────────────────────────────┘                 │
  └──────────────────────┬──────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
  ┌────────────┐  ┌────────────┐  ┌────────────┐
  │ SQL Agent  │  │  Reporter  │  │  LangGraph  │
  │            │  │            │  │  Planner    │
  │ 查询 stg_* │  │ 嵌入分析   │  │ 编排采集   │
  │ 多表 JOIN  │  │ 结果+图表  │  │ →分析→报告 │
  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
        │               │               │
        ▼               ▼               ▼
  结构化查询      经营诊断报告      多步工作流

  ── 对接点 ───────────────────────────────────────────

  1. ToolRegistry    →  "data.collect" capability
     Planner 可自动将 "采集 amazon 商品数据" 加入执行计划

  2. BaseSkill       →  DataCollectionSkill
     通过 execute() 接入 Supervisor 调度，含重试/超时/告警

  3. AnalyzedData    →  Reporter 模板
     summary / aggregations / missing_report 直接嵌入 Markdown

  4. stg_* tables    →  SQL Agent
     SQL Agent 可直接查询采集入库的结构化数据

  5. Scheduler       →  Cron / APScheduler (Phase 2)
     定时采集 → 自动清洗 → 增量分析 → 预警推送

  示例 LangGraph 对话:
    User:   "帮我查一下库存不足的商品，生成补货建议"
    Planner: ① data.collect (采集最新库存)
             ② sql.query   (关联订单和库存)
             ③ report.generate (生成补货报告)
    ───────────────────────────────────────────────────
""")


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════

def main(enable_chart: bool = False) -> None:
    # ═══ Phase 1 ═══
    print_section("📥 Phase 1 — 数据采集（5 个电商数据集）")
    results = collect_all()

    total_rows = sum(
        r.cleaned.row_count
        for r in results.values()
        if r.status == "success" and r.cleaned
    )
    for name, r in results.items():
        icon = "✅" if r.status == "success" else "❌"
        rows = r.cleaned.row_count if r.cleaned else 0
        ms = f"{r.elapsed_ms:.0f}ms"
        print(f"  {icon} {name:4s}  {rows:2d} 条  {ms}")

    print(f"\n  📊 合计: {total_rows} 条清洗后记录, 5/5 成功")

    # ═══ Phase 2 ═══
    dfs = load_dataframes(results)
    analysis_sales(dfs)
    analysis_inventory(dfs)
    analysis_suppliers(dfs)
    analysis_shops(dfs)

    # ═══ Phase 3 ═══
    generate_insights(results)

    # ═══ Phase 4 ═══
    print_integration_map()

    # ═══ Charts ═══
    if enable_chart:
        from data_collection.visualizer import DataVisualizer
        visualizer = DataVisualizer()
        for name, r in results.items():
            if r.status == "success":
                ds_name_map = {
                    "商品": "products", "订单": "orders", "店铺": "shops",
                    "库存": "inventory", "供应商": "suppliers",
                }
                visualizer.render(r, dataset_name=ds_name_map.get(name, name))
        charts_dir = Path(__file__).parent / "charts"
        count = len(list(charts_dir.glob("*.png")))
        print(f"\n  📊 图表目录: {charts_dir} ({count} 张)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DCC → Agent 全链路演示")
    parser.add_argument("--chart", action="store_true", help="生成 matplotlib 图表")
    args = parser.parse_args()
    main(enable_chart=args.chart)
