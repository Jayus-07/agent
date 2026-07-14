"""
data_collection/visualizer.py — 采集数据可视化

从 Pipeline 产出的 AnalyzedData 中提取 groupby 聚合数据，
自动生成 matplotlib 图表并保存为 PNG。

用法:
    visualizer = DataVisualizer()
    charts = visualizer.render(result, dataset_name="products")
    # → 保存 charts/products_品类分布.png 等文件
    # → 返回 Markdown 格式的图表引用
"""

import io
import os
import base64
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

from data_collection.pipeline import CollectResult
from utils.logger import logger

# ── 中文字体配置 ──
def _setup_chinese_font():
    candidates = [
        "Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
        "WenQuanYi Micro Hei", "Arial Unicode MS", "sans-serif",
    ]
    for font_name in candidates:
        try:
            matplotlib.font_manager.findfont(font_name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [font_name, "sans-serif"]
            plt.rcParams["axes.unicode_minus"] = False
            logger.info(f"[Visualizer] 使用字体: {font_name}")
            return
        except Exception:
            continue
    plt.rcParams["font.sans-serif"] = ["sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False

_setup_chinese_font()

# ── 配色 ──
COLORS = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
]


class DataVisualizer:
    """数据采集结果可视化

    从 AnalyzedData.aggregations 提取数据，自动选择图表类型渲染。
    图表保存到 charts_dir，返回 Markdown 引用文本。
    """

    def __init__(self, charts_dir: str | None = None, dpi: int = 120):
        if charts_dir is None:
            charts_dir = os.path.join(os.path.dirname(__file__), "charts")
        self._charts_dir = Path(charts_dir)
        self._charts_dir.mkdir(parents=True, exist_ok=True)
        self._dpi = dpi

    def render(
        self,
        result: CollectResult,
        dataset_name: str = "",
    ) -> str:
        """从一次采集结果渲染所有可用图表，返回 Markdown 文本"""
        if not result.analyzed or not result.analyzed.aggregations:
            return ""

        agg = result.analyzed.aggregations
        parts: list[str] = []
        prefix = dataset_name or "data"

        # ── 从 _count 聚合提取数据 ──
        count_keys = [k for k in agg if k.endswith("_count")]
        for ck in count_keys:
            dim = ck.replace("_count", "").replace("by_", "")
            data = agg[ck]
            if not isinstance(data, dict) or len(data) < 1:
                continue

            # 按值降序排列
            sorted_items = sorted(data.items(), key=lambda x: x[1], reverse=True)
            labels = [item[0] for item in sorted_items]
            values = [item[1] for item in sorted_items]

            # 饼图：类别 ≤ 6 个
            if len(labels) <= 6:
                chart_path = self._pie(
                    labels, values,
                    title=f"{prefix} — {dim}分布",
                    filename=f"{prefix}_{dim}_pie.png",
                )
            else:
                chart_path = self._bar(
                    labels, values,
                    title=f"{prefix} — {dim}分布",
                    xlabel=dim,
                    ylabel="数量",
                    filename=f"{prefix}_{dim}_bar.png",
                )

            if chart_path:
                parts.append(f"![{dim}分布]({chart_path})")

        # ── 数值字段统计（水平柱状图） ──
        if result.analyzed.summary:
            # 选 2-4 个关键数值字段做均值对比
            fields = list(result.analyzed.summary.keys())[:5]
            if len(fields) >= 2:
                means = {
                    f: result.analyzed.summary[f].get("mean", 0)
                    for f in fields
                    if result.analyzed.summary[f].get("mean", 0) > 0
                }
                if means:
                    chart_path = self._hbar(
                        list(means.keys()), list(means.values()),
                        title=f"{prefix} — 关键指标均值",
                        xlabel="均值",
                        filename=f"{prefix}_means_hbar.png",
                    )
                    if chart_path:
                        parts.append(f"![指标均值]({chart_path})")

        return "\n".join(parts)

    # ── 柱状图 ──
    def _bar(
        self, labels: list[str], values: list[int | float],
        title: str, xlabel: str, ylabel: str, filename: str,
    ) -> str | None:
        try:
            fig, ax = plt.subplots(figsize=(10, 5), dpi=self._dpi)
            colors = COLORS[:len(labels)]
            x = range(len(labels))
            bars = ax.bar(x, values, color=colors, edgecolor="white", linewidth=0.8)

            for bar_item, val in zip(bars, values):
                ax.text(bar_item.get_x() + bar_item.get_width() / 2,
                        bar_item.get_height() + max(values) * 0.015,
                        f"{val}", ha="center", fontsize=10, fontweight="bold")

            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=10)
            ax.set_title(title, fontsize=14, fontweight="bold")
            ax.set_xlabel(xlabel, fontsize=11)
            ax.set_ylabel(ylabel, fontsize=11)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))

            fig.tight_layout()
            return self._save(fig, filename)
        except Exception as e:
            logger.warning(f"[Visualizer] bar 图表失败: {e}")
            return None

    # ── 水平柱状图 ──
    def _hbar(
        self, labels: list[str], values: list[int | float],
        title: str, xlabel: str, filename: str,
    ) -> str | None:
        try:
            fig, ax = plt.subplots(figsize=(9, max(4, len(labels) * 0.5)), dpi=self._dpi)
            colors = COLORS[:len(labels)]
            y_pos = range(len(labels))
            bars = ax.barh(y_pos, values, color=colors, edgecolor="white", height=0.6)

            for bar_item, val in zip(bars, values):
                ax.text(bar_item.get_width() + max(values) * 0.01,
                        bar_item.get_y() + bar_item.get_height() / 2,
                        f"{val:.1f}", va="center", fontsize=10)

            ax.set_yticks(y_pos)
            ax.set_yticklabels(labels, fontsize=10)
            ax.set_title(title, fontsize=14, fontweight="bold")
            ax.set_xlabel(xlabel, fontsize=11)
            ax.invert_yaxis()
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)

            fig.tight_layout()
            return self._save(fig, filename)
        except Exception as e:
            logger.warning(f"[Visualizer] hbar 图表失败: {e}")
            return None

    # ── 饼图 ──
    def _pie(
        self, labels: list[str], values: list[int | float],
        title: str, filename: str,
    ) -> str | None:
        try:
            total = sum(values)
            if total == 0:
                return None

            fig, ax = plt.subplots(figsize=(7, 7), dpi=self._dpi)
            colors = COLORS[:len(labels)]
            wedges, texts, autotexts = ax.pie(
                values, labels=None, autopct="%1.1f%%",
                colors=colors, startangle=90,
                pctdistance=0.6,
            )
            for at in autotexts:
                at.set_fontweight("bold")
                at.set_fontsize(11)

            # 图例放右侧
            legend_labels = [f"{l} ({v})" for l, v in zip(labels, values)]
            ax.legend(wedges, legend_labels, title="", loc="center left",
                      bbox_to_anchor=(1, 0.5), fontsize=9)
            ax.set_title(title, fontsize=14, fontweight="bold")

            fig.tight_layout()
            return self._save(fig, filename)
        except Exception as e:
            logger.warning(f"[Visualizer] pie 图表失败: {e}")
            return None

    # ── 保存 ──
    def _save(self, fig, filename: str) -> str | None:
        filepath = self._charts_dir / filename
        fig.savefig(filepath, bbox_inches="tight", dpi=self._dpi)
        plt.close(fig)
        logger.info(f"[Visualizer] 保存图表: {filepath.name}")
        return str(filepath)
