"""
chart_generator.py — matplotlib 图表自动生成

根据 REPORT_REGISTRY 中的 charts 配置，自动生成图表，
转为 base64 编码后嵌入 Markdown。

安全: 使用非交互式后端 'Agg'，不弹窗，纯内存操作。
"""

import io
import base64
import os
import re
import uuid
from typing import Dict, Any, List, Optional
from collections import Counter

# 非交互式后端，必须在 import pyplot 之前设置
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

from backend.shared.logger import logger

# =====================================================
# 中文字体配置
# =====================================================

def _setup_chinese_font():
    """尝试配置中文字体，失败则回退英文"""
    # 按优先级尝试
    candidates = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "WenQuanYi Micro Hei",
        "Arial Unicode MS",
        "sans-serif",
    ]
    for font_name in candidates:
        try:
            matplotlib.font_manager.findfont(font_name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [font_name, "sans-serif"]
            plt.rcParams["axes.unicode_minus"] = False
            logger.info(f"[Chart] 使用字体: {font_name}")
            return
        except Exception:
            continue

    plt.rcParams["font.sans-serif"] = ["sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False
    logger.warning("[Chart] 未找到中文字体，图表中文可能显示为方块")


_setup_chinese_font()

# 配色方案
_COLORS = ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
           "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC"]


# =====================================================
# 图表生成器
# =====================================================

class ChartGenerator:
    """根据配置自动生成 matplotlib 图表，返回 Markdown 嵌入片段

    chart_output_dir（2026-10-25）：配置后图表 PNG 落盘为 artifact 文件，
    Markdown 引用 url_prefix + 文件名；未配置保持 base64 内嵌（旧行为，
    兼容单测与离线脚本）。动机：base64 内嵌让报告 Markdown 动辄数万
    token，被 L1 上下文预算压成预览后用户看到"无法展示"（实测日报
    before_tokens=49713，其中图表 base64 占绝对主体）。
    """

    def __init__(self, figsize=(8, 5), dpi=100,
                 chart_output_dir: Optional[str] = None,
                 url_prefix: str = ""):
        self.figsize = figsize
        self.dpi = dpi
        self.chart_output_dir = chart_output_dir
        # url_prefix：图表 URL 前缀。绝对地址（如 http://gw:9080）时前端
        # <img> 直连网关公开路由（浏览器 img 无 Authorization，BFF/网关
        # 鉴权面不为此开口）；空则用相对 /api/report-files/（需 BFF 放行，
        # 当前未启用）。
        if url_prefix:
            self.url_prefix = url_prefix.rstrip("/") + "/api/report-files/"
        else:
            self.url_prefix = "/api/report-files/"

    # ---------------------------------------------------
    # 主入口
    # ---------------------------------------------------

    def generate(self, chart_configs: List[dict], data: List[dict]) -> str:
        """
        批量生成图表，返回 Markdown 字符串。

        参数:
            chart_configs: 图表配置列表
                           [{"type": "bar", "x": "dept_name", "y": "total_budget",
                             "title": "预算分布", "width": 8, "height": 5}]
            data:          数据列表 [{col: val, ...}, ...]

        返回:
            Markdown 格式的图片嵌入片段
        """
        if not chart_configs or not data:
            return ""

        if self.chart_output_dir:
            try:
                os.makedirs(self.chart_output_dir, exist_ok=True)
            except OSError as e:
                logger.warning(
                    f"[Chart] 图表目录不可写，回退 base64 内嵌: {e}")
                self.chart_output_dir = None

        parts = []
        for i, cfg in enumerate(chart_configs):
            try:
                chart_type = cfg.get("type", "bar")
                title = cfg.get("title", f"Chart {i+1}")
                w = cfg.get("width", self.figsize[0])
                h = cfg.get("height", self.figsize[1])

                fig = self._render_figure(chart_type, data, cfg, title, (w, h))
                if fig is None:
                    continue

                if self.chart_output_dir:
                    # 文件名纯 ASCII（中文标题经 URL 各端编码差异大，实测
                    # urllib ascii 直连失败）：slug 剔除非 ASCII，空则回退
                    # chart；中文可读性由 Markdown alt 文本承载
                    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_")[:40]
                    fname = f"{slug or 'chart'}_{uuid.uuid4().hex[:8]}.png"
                    fpath = os.path.join(self.chart_output_dir, fname)
                    fig.savefig(fpath, format="png", bbox_inches="tight",
                                dpi=self.dpi)
                    fig.clf()
                    plt.close(fig)
                    parts.append(f"### {title}\n\n![{title}]({self.url_prefix}{fname})\n")
                else:
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png", bbox_inches="tight", dpi=self.dpi)
                    fig.clf()
                    plt.close(fig)
                    buf.seek(0)
                    img_base64 = base64.b64encode(buf.read()).decode("ascii")
                    parts.append(f"### {title}\n\n![{title}](data:image/png;base64,{img_base64})\n")
                logger.info(f"[Chart] 生成图表: {title} ({chart_type})")

            except Exception as e:
                logger.error(f"[Chart] 图表生成失败 [{cfg.get('title', '?')}]: {e}")
                parts.append(f"*[图表生成失败: {cfg.get('title', '?')} — {e}]*\n")

        return "\n".join(parts)

    def _render_figure(self, chart_type: str, data: List[dict], cfg: dict,
                       title: str, figsize: tuple):
        """按类型渲染并返回 figure（不负责保存/编码）；未知类型返回 None。"""
        if chart_type == "bar":
            return self._bar_figure(data, cfg, title, figsize)
        if chart_type == "pie":
            return self._pie_figure(data, cfg, title, figsize)
        if chart_type == "line":
            return self._line_figure(data, cfg, title, figsize)
        logger.warning(f"[Chart] 不支持的图表类型: {chart_type}")
        return None

    # ---------------------------------------------------
    # 柱状图
    # ---------------------------------------------------

    def _bar_figure(self, data: List[dict], cfg: dict, title: str,
                    figsize: tuple):
        x_key = cfg["x"]
        y_key = cfg["y"]
        xs = [str(d.get(x_key, "")) for d in data]
        ys = [float(d.get(y_key, 0) or 0) for d in data]

        fig, ax = plt.subplots(figsize=figsize, dpi=self.dpi)
        colors = _COLORS[:len(xs)] if len(xs) <= len(_COLORS) else _COLORS * (len(xs) // len(_COLORS) + 1)
        bars = ax.bar(range(len(xs)), ys, color=colors, edgecolor="white", linewidth=0.8)

        # 数值标签
        for bar_item, val in zip(bars, ys):
            ax.text(bar_item.get_x() + bar_item.get_width() / 2,
                    bar_item.get_height() + max(ys) * 0.01,
                    f"{val:.1f}", ha="center", va="bottom", fontsize=9)

        ax.set_xticks(range(len(xs)))
        ax.set_xticklabels(xs, rotation=30, ha="right", fontsize=9)
        ax.set_title(title, fontsize=13, fontweight="bold")
        ax.set_ylabel(y_key, fontsize=10)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.yaxis.set_major_formatter(ticker.FormatStrFormatter("%.0f"))

        fig.tight_layout()
        return fig

    # ---------------------------------------------------
    # 饼图
    # ---------------------------------------------------

    def _pie_figure(self, data: List[dict], cfg: dict, title: str,
                    figsize: tuple):
        x_key = cfg["x"]
        y_key = cfg["y"]
        labels = [str(d.get(x_key, "")) for d in data]
        values = [float(d.get(y_key, 0) or 0) for d in data]

        # 过滤零值
        filtered = [(l, v) for l, v in zip(labels, values) if v > 0]
        if not filtered:
            logger.warning("[Chart] 饼图数据全为零，跳过")
            return None
        labels, values = zip(*filtered)

        fig, ax = plt.subplots(figsize=figsize, dpi=self.dpi)
        colors = _COLORS[:len(labels)]
        wedges, texts, autotexts = ax.pie(
            values, labels=labels, autopct="%1.1f%%",
            colors=colors, startangle=90,
            textprops={"fontsize": 9},
        )
        for at in autotexts:
            at.set_fontweight("bold")
            at.set_fontsize(10)

        ax.set_title(title, fontsize=13, fontweight="bold")
        fig.tight_layout()
        return fig

    # ---------------------------------------------------
    # 折线图
    # ---------------------------------------------------

    def _line_figure(self, data: List[dict], cfg: dict, title: str,
                     figsize: tuple):
        x_key = cfg["x"]
        y_key = cfg["y"]
        xs = [str(d.get(x_key, "")) for d in data]
        ys = [float(d.get(y_key, 0) or 0) for d in data]

        fig, ax = plt.subplots(figsize=figsize, dpi=self.dpi)
        ax.plot(xs, ys, marker="o", linewidth=2, markersize=6,
                color=_COLORS[0], markerfacecolor="white",
                markeredgewidth=1.5)

        for i, (x, y) in enumerate(zip(xs, ys)):
            ax.annotate(f"{y:.1f}", (x, y),
                        textcoords="offset points", xytext=(0, 10),
                        ha="center", fontsize=9)

        ax.set_xticklabels(xs, rotation=30, ha="right", fontsize=9)
        ax.set_title(title, fontsize=13, fontweight="bold")
        ax.set_ylabel(y_key, fontsize=10)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", alpha=0.3)

        fig.tight_layout()
        return fig

    # ---------------------------------------------------
    # 兼容入口（base64 模式，旧调用方/单测）
    # ---------------------------------------------------

    def _bar_chart(self, data, cfg, title, figsize) -> str:
        fig = self._bar_figure(data, cfg, title, figsize)
        return self._fig_to_base64(fig) if fig is not None else ""

    def _pie_chart(self, data, cfg, title, figsize) -> str:
        fig = self._pie_figure(data, cfg, title, figsize)
        return self._fig_to_base64(fig) if fig is not None else ""

    def _line_chart(self, data, cfg, title, figsize) -> str:
        fig = self._line_figure(data, cfg, title, figsize)
        return self._fig_to_base64(fig) if fig is not None else ""

    # ---------------------------------------------------
    # 工具方法
    # ---------------------------------------------------

    def _fig_to_base64(self, fig) -> str:
        """将 matplotlib figure 转为 base64 PNG 字符串"""
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", dpi=self.dpi)
        fig.clf()
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("ascii")


# 全局单例
chart_generator = ChartGenerator()
