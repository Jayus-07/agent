"""
template_engine.py — Jinja2 模板引擎

功能:
  1. 模板注册表：扫描 templates/ 目录，建立报告类型 → 模板文件映射
  2. 动态选择：根据 report_type + 用户偏好自动选模板
  3. Jinja2 渲染：SandboxedEnvironment，带自定义过滤器
  4. 安全防护：沙箱模式，禁止执行任意 Python 代码
"""

import os
import re
from typing import Dict, Any, Optional
from datetime import datetime

from jinja2 import Environment, BaseLoader, TemplateNotFound
from jinja2.sandbox import SandboxedEnvironment

from backend.shared.logger import logger


# =====================================================
# Jinja2 环境
# =====================================================

def _money(value, decimals=1):
    """格式化金额（万元）"""
    if value is None:
        return "—"
    try:
        return f"{float(value):.{decimals}f} 万元"
    except (ValueError, TypeError):
        return str(value)


def _percent(value, decimals=1):
    """格式化为百分比"""
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.{decimals}f}%"
    except (ValueError, TypeError):
        return str(value)


def _date_cn(value):
    """日期转中文格式（YYYY年MM月DD日）

    支持多种输入格式：
      - datetime / date 对象：直接格式化
      - "2026-05-24"（ISO）  → strptime
      - "2026/05/24"（斜杠） → strptime
      - "2026.05.24"（点）   → strptime
      - "2026年5月24日"（中文） → strptime（兼容旧快照）
    """
    if value is None or value == "":
        return "—"

    # datetime/date 对象
    if isinstance(value, datetime):
        return value.strftime("%Y年%m月%d日")
    if hasattr(value, "strftime"):  # 兼容 date
        return value.strftime("%Y年%m月%d日")

    if isinstance(value, str):
        s = value.strip()
        # 尝试多种格式
        for fmt in (
            "%Y-%m-%d",      # ISO 短横线
            "%Y/%m/%d",      # 斜杠
            "%Y.%m.%d",      # 点
            "%Y年%m月%d日",   # 中文
        ):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.strftime("%Y年%m月%d日")
            except ValueError:
                continue
        # 兜底：返回原值
        return s

    return str(value)


def _default_dash(value):
    """空值显示为 —"""
    if value is None or value == "":
        return "—"
    return value


def _status_cn(value):
    """状态值转中文"""
    mapping = {
        "active": "进行中",
        "completed": "已完成",
        "planning": "规划中",
        "cancelled": "已取消",
    }
    return mapping.get(str(value).lower(), str(value))


def _truncate(value, length=50, suffix="..."):
    """字符串截断"""
    if value is None:
        return "—"
    s = str(value)
    if len(s) <= length:
        return s
    return s[:length] + suffix


# 创建沙箱环境（禁止访问 Python 内置函数、文件系统等）
_jinja_env = SandboxedEnvironment(
    loader=BaseLoader(),
    autoescape=False,  # Markdown 不需要 HTML 转义
    trim_blocks=True,
    lstrip_blocks=True,
)

# 注册自定义过滤器
_jinja_env.filters["money"] = _money
_jinja_env.filters["percent"] = _percent
_jinja_env.filters["date_cn"] = _date_cn
_jinja_env.filters["dash"] = _default_dash
_jinja_env.filters["status"] = _status_cn
_jinja_env.filters["truncate"] = _truncate


# =====================================================
# 模板管理器
# =====================================================

class TemplateEngine:
    """
    模板引擎：管理模板注册、选择、渲染。

    用法:
        engine = TemplateEngine(template_dir="report_agent/templates")
        draft = engine.render("monthly_sales", data_dict, template_name="sales_summary.j2")
    """

    def __init__(self, template_dir: str = None):
        """
        参数:
            template_dir: 模板文件目录，默认为 report_agent/templates/
        """
        if template_dir is None:
            template_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "templates"
            )
        self.template_dir = template_dir
        self._template_cache: Dict[str, str] = {}
        self._builtin_templates: Dict[str, str] = {}
        self._load_builtin_templates()
        self._scan_directory()

    # ---------------------------------------------------
    # 内置默认模板（兜底）
    # ---------------------------------------------------

    def _load_builtin_templates(self):
        """注册内置兜底模板 — 跨境电商报告（即使 templates/ 目录为空也能用）"""
        self._builtin_templates = {
            "daily_sales.j2": """## {{ metadata.get("title", "销售日报") }}
> 生成时间：{{ metadata.fetched_at }}，共 {{ metadata.row_count }} 条记录

| 日期 | 渠道 | 订单数 | 销售额 | 下单客户数 | 客单价 |
|------|------|--------|--------|------------|--------|
{% for row in data %}
| {{ row.get("日期", row.get("date", "—")) }} | {{ row.get("渠道", row.get("channel", "—")) }} | {{ row.get("订单数", row.get("order_count", 0)) }} | {{ row.get("销售额", row.get("sales_amount", 0)) | money }} | {{ row.get("下单客户数", row.get("customer_count", 0)) }} | {{ row.get("客单价", row.get("avg_order_value", 0)) | money }} |
{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
            "product_performance.j2": """## {{ metadata.get("title", "商品动销分析报告") }}
> 生成时间：{{ metadata.fetched_at }}，共 {{ metadata.row_count }} 个 SKU

| 产品名称 | 品牌 | 销售订单数 | 销售数量 | 销售额 | 毛利 | 毛利率 |
|----------|------|------------|----------|--------|------|--------|
{% for row in data %}
| {{ row.get("产品名称", row.get("product_name", "—")) }} | {{ row.get("品牌", row.get("brand", "—")) }} | {{ row.get("销售订单数", row.get("order_count", 0)) }} | {{ row.get("销售数量", row.get("qty_sold", 0)) }} | {{ row.get("销售额", row.get("sales_amount", 0)) | money }} | {{ row.get("毛利", row.get("gross_profit", 0)) | money }} | {{ row.get("毛利率", row.get("gross_margin", 0)) }}% |
{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
            "inventory_health.j2": """## {{ metadata.get("title", "库存健康报告") }}
> 生成时间：{{ metadata.fetched_at }}

| 仓库 | 仓库类型 | 产品名称 | SKU编码 | 现有库存 | 已预留 | 在途 | 可用库存 | 库存状态 |
|------|---------|----------|---------|----------|--------|------|----------|----------|
{% for row in data %}
| {{ row.get("仓库", row.get("warehouse", "—")) }} | {{ row.get("仓库类型", row.get("warehouse_type", "—")) }} | {{ row.get("产品名称", row.get("product_name", "—")) }} | {{ row.get("SKU编码", row.get("sku_code", "—")) }} | {{ row.get("现有库存", row.get("qty_on_hand", 0)) }} | {{ row.get("已预留", row.get("qty_reserved", 0)) }} | {{ row.get("在途库存", row.get("qty_in_transit", 0)) }} | {{ row.get("可用库存", row.get("qty_available", 0)) }} | {{ row.get("库存状态", row.get("inventory_status", "—")) }} |
{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
            "ad_performance.j2": """## {{ metadata.get("title", "广告效果分析报告") }}
> 生成时间：{{ metadata.fetched_at }}，近 30 天数据

| 广告平台 | 活动名称 | 类型 | 状态 | 总花费 | 总展示 | 总点击 | CTR | CPC | 总转化 | 广告销售额 | ACoS | ROAS |
|----------|---------|------|------|--------|--------|--------|-----|-----|--------|-----------|------|------|
{% for row in data %}
| {{ row.get("广告平台", row.get("ad_channel", "—")) }} | {{ row.get("活动名称", row.get("campaign_name", "—")) }} | {{ row.get("活动类型", row.get("campaign_type", "—")) }} | {{ row.get("状态", row.get("status", "—")) }} | {{ row.get("总花费", row.get("total_spend", 0)) | money }} | {{ row.get("总展示", row.get("impressions", 0)) }} | {{ row.get("总点击", row.get("clicks", 0)) }} | {{ row.get("CTR", 0) }}% | {{ row.get("CPC", 0) | money }} | {{ row.get("总转化", row.get("conversions", 0)) }} | {{ row.get("广告销售额", row.get("ad_sales", 0)) | money }} | {{ row.get("ACoS", 0) }}% | {{ row.get("ROAS", 0) }} |
{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
            "order_fulfillment.j2": """## {{ metadata.get("title", "订单履约报告") }}
> 生成时间：{{ metadata.fetched_at }}，近 30 天数据

| 渠道 | 订单状态 | 订单数 | 金额合计 | 平均发货耗时(h) | 平均签收耗时(h) | 退款订单数 | 退款率 |
|------|---------|--------|----------|----------------|----------------|-----------|--------|
{% for row in data %}
| {{ row.get("渠道", row.get("channel", "—")) }} | {{ row.get("订单状态", row.get("order_status", "—")) }} | {{ row.get("订单数", row.get("order_count", 0)) }} | {{ row.get("金额合计", row.get("total_amount", 0)) | money }} | {{ row.get("平均发货耗时小时", row.get("avg_pick_hours", "—")) }} | {{ row.get("平均签收耗时小时", row.get("avg_delivery_hours", "—")) }} | {{ row.get("退款订单数", row.get("refund_count", 0)) }} | {{ row.get("退款率", row.get("refund_rate", 0)) }}% |
{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
            "customer_analysis.j2": """## {{ metadata.get("title", "客户分析报告") }}
> 生成时间：{{ metadata.fetched_at }}

| 国家 | 客户分层 | 客户数 | 平均LTV | 平均订单数 | 近30天活跃 | 活跃率 | 累计订单总数 |
|------|---------|--------|---------|-----------|-----------|--------|-------------|
{% for row in data %}
| {{ row.get("国家", row.get("country", "—")) }} | {{ row.get("客户分层", row.get("segment", "—")) }} | {{ row.get("客户数", row.get("customer_count", 0)) }} | {{ row.get("平均LTV", row.get("avg_ltv", 0)) | money }} | {{ row.get("平均订单数", row.get("avg_orders", 0)) }} | {{ row.get("近30天活跃", row.get("active_30d", 0)) }} | {{ row.get("活跃率", row.get("active_rate", 0)) }}% | {{ row.get("累计订单总数", row.get("total_orders", 0)) }} |
{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
        }

    # ---------------------------------------------------
    # 目录扫描
    # ---------------------------------------------------

    def _scan_directory(self):
        """扫描模板目录，加载 .j2 和 .md 文件"""
        if not os.path.isdir(self.template_dir):
            logger.warning(f"[TemplateEngine] 模板目录不存在: {self.template_dir}")
            return

        for fname in os.listdir(self.template_dir):
            if fname.endswith((".j2", ".md", ".jinja2", ".j2md")):
                fpath = os.path.join(self.template_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        self._template_cache[fname] = f.read()
                    logger.info(f"[TemplateEngine] 加载模板: {fname}")
                except Exception as e:
                    logger.warning(f"[TemplateEngine] 读取模板失败 {fname}: {e}")

    # ---------------------------------------------------
    # 模板获取
    # ---------------------------------------------------

    def get_template(self, report_type: str, preferred: str = None) -> str:
        """
        获取模板字符串。

        选择优先级:
          1. preferred 参数显式指定
          2. 目录中的同名文件
          3. 内置默认模板
          4. 通用兜底模板
        """
        from backend.business_report.data_fetcher import REPORT_REGISTRY

        registry = REPORT_REGISTRY.get(report_type, {})
        available = registry.get("templates", [])

        # 确定候选模板名
        candidate = preferred
        if not candidate and available:
            candidate = available[0]

        # 尝试加载
        if candidate:
            # 先查磁盘缓存
            if candidate in self._template_cache:
                return self._template_cache[candidate]
            # 再查内置
            if candidate in self._builtin_templates:
                return self._builtin_templates[candidate]

        # 兜底：用第一个可用的内置模板
        if available:
            for tpl in available:
                if tpl in self._builtin_templates:
                    return self._builtin_templates[tpl]

        # 最后兜底
        logger.warning(f"[TemplateEngine] 未找到模板 for {report_type}，使用通用模板")
        return self._builtin_templates.get("dept_overview.j2", "## 报告\n\n{{ data }}\n")

    # ---------------------------------------------------
    # 渲染
    # ---------------------------------------------------

    # 模板名 → 必需列名集合（render 入口校验，缺列则降级到 fallback）
    # 列名匹配为"包含任一即可"（OR 语义），兼容中英文别名
    _REQUIRED_COLUMNS: Dict[str, set] = {
        "daily_sales.j2":          {"日期", "date", "渠道", "channel", "订单数", "order_count",
                                     "销售额", "sales_amount", "下单客户数", "customer_count", "客单价", "avg_order_value"},
        "product_performance.j2":   {"产品名称", "product_name", "品牌", "brand",
                                     "销售订单数", "order_count", "销售数量", "qty_sold",
                                     "销售额", "sales_amount", "毛利", "gross_profit", "毛利率", "gross_margin"},
        "inventory_health.j2":      {"仓库", "warehouse", "产品名称", "product_name",
                                     "SKU编码", "sku_code", "现有库存", "qty_on_hand",
                                     "可用库存", "qty_available", "库存状态", "inventory_status"},
        "ad_performance.j2":        {"广告平台", "ad_channel", "活动名称", "campaign_name",
                                     "总花费", "total_spend", "ACoS", "ROAS",
                                     "CTR", "CPC", "总转化", "conversions"},
        "order_fulfillment.j2":     {"渠道", "channel", "订单状态", "order_status",
                                     "订单数", "order_count", "金额合计", "total_amount",
                                     "退款率", "refund_rate"},
        "customer_analysis.j2":     {"国家", "country", "客户分层", "segment",
                                     "客户数", "customer_count", "平均LTV", "avg_ltv",
                                     "活跃率", "active_rate"},
    }

    def _check_required_columns(
        self, template_name: str, data: list
    ) -> tuple:
        """检查数据是否包含模板所需的所有列（OR 语义：每组别名列至少有一个存在即可）。

        返回:
            (is_ok, missing_keys) — is_ok=True 表示所有必需列都有；
                                   False 时 missing_keys 为缺失的组别名。
        """
        required = self._REQUIRED_COLUMNS.get(template_name)
        if not required or not data:
            return True, set()

        if not isinstance(data[0], dict):
            return True, set()

        available = set(data[0].keys())

        # 把必需列按"组"切分：相邻的英中别名视为同一组（如 dept_name / 部门）
        # 简化：每两个为一组 [英文, 中文]
        groups = []
        cols = list(required)
        for i in range(0, len(cols), 2):
            groups.append(set(cols[i:i+2]))

        missing = set()
        for group in groups:
            if not (group & available):
                # 这一组一个都没匹配
                missing.add("/".join(sorted(group)))

        return len(missing) == 0, missing

    def render(
        self,
        report_type: str,
        result: Dict[str, Any],
        template_name: str = None,
        title: str = None,
        chart_markdown: str = "",
    ) -> str:
        """
        渲染模板为 Markdown 初稿。

        参数:
            report_type:   报告类型
            result:        data_fetcher 返回的 {"data": [...], "metadata": {...}}
            template_name: 指定模板文件名（可选）
            title:         报告标题（覆盖 metadata 中的默认值）
            chart_markdown: 图表嵌入的 Markdown 片段

        返回:
            Markdown 字符串
        """
        template_str = self.get_template(report_type, preferred=template_name)

        # 推断实际使用的模板名（用于列名校验）
        actual_template_name = template_name
        if not actual_template_name or (
            actual_template_name not in self._template_cache
            and actual_template_name not in self._builtin_templates
        ):
            try:
                from backend.business_report.data_fetcher import REPORT_REGISTRY
                reg = REPORT_REGISTRY.get(report_type, {})
                for tpl in reg.get("templates", []):
                    if tpl in self._builtin_templates or tpl in self._template_cache:
                        actual_template_name = tpl
                        break
            except Exception:
                pass

        # 列名校验：缺列时降级到 fallback_render
        data = result.get("data", [])
        if data and actual_template_name:
            cols_ok, missing = self._check_required_columns(actual_template_name, data)
            if not cols_ok:
                logger.warning(
                    f"[TemplateEngine] {report_type} 模板 {actual_template_name} "
                    f"缺少必需列 {sorted(missing)}，降级到自动表格"
                )
                return self._fallback_render(
                    result,
                    f"数据 schema 不匹配，缺少列: {', '.join(sorted(missing))}"
                )

        # 构建模板上下文
        context = {
            "data": result["data"],
            "metadata": dict(result.get("metadata", {})),
            "chart": chart_markdown,
        }
        if title:
            context["metadata"]["title"] = title

        try:
            # 用 Jinja2 沙箱渲染
            jinja_template = _jinja_env.from_string(template_str)
            rendered = jinja_template.render(**context)

            logger.info(f"[TemplateEngine] 渲染完成: {report_type}, "
                        f"模板={template_name or 'auto'}, "
                        f"长度={len(rendered)} 字符")
            return rendered

        except Exception as e:
            logger.error(f"[TemplateEngine] 模板渲染失败: {e}")
            # 降级：返回原始数据作为 Markdown 表格
            return self._fallback_render(result, str(e))

    def _fallback_render(self, result: Dict[str, Any], error_msg: str) -> str:
        """模板渲染失败时的降级输出"""
        data = result.get("data", [])
        meta = result.get("metadata", {})

        lines = [
            f"## 报告",
            f"> ⚠️ 模板渲染失败: {error_msg}",
            f"> 生成时间: {meta.get('fetched_at', '未知')}",
            "",
        ]

        if data and isinstance(data, list) and len(data) > 0:
            # 自动表格
            columns = list(data[0].keys())
            lines.append("| " + " | ".join(columns) + " |")
            lines.append("| " + " | ".join("---" for _ in columns) + " |")
            for row in data:
                vals = [str(row.get(c, "")) for c in columns]
                lines.append("| " + " | ".join(vals) + " |")

        return "\n".join(lines)

    def reload(self):
        """重新扫描模板目录（热更新）"""
        self._template_cache.clear()
        self._scan_directory()
        logger.info("[TemplateEngine] 模板缓存已刷新")
