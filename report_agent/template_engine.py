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

from utils.logger import logger


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
        """注册内置兜底模板，即使 templates/ 目录为空也能用"""
        self._builtin_templates = {
            "sales_summary.j2": """## {{ metadata.get("title", "月度销售报告") }}
> 生成时间：{{ metadata.fetched_at }}，共 {{ metadata.row_count }} 条记录

| 部门 | 项目数 | 总预算 | 进行中 | 已完成 |
|------|--------|--------|--------|--------|
{% for row in data %}
| {{ row.dept_name | dash }} | {{ row.project_count }} | {{ row.total_budget | money }} | {{ row.active_count }} | {{ row.completed_count }} |
{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
            "sales_detail.j2": """## {{ metadata.get("title", "月度销售详细报告") }}
> 生成时间：{{ metadata.fetched_at }}

{% for row in data %}
### {{ row.dept_name | dash }}
- 📊 项目总数：**{{ row.project_count }}** 个
- 💰 总预算：**{{ row.total_budget | money }}**
- 🟢 进行中：{{ row.active_count }} 个
- ✅ 已完成：{{ row.completed_count }} 个

{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
            "project_progress.j2": """## {{ metadata.get("title", "项目进度报告") }}
> 生成时间：{{ metadata.fetched_at }}

| 项目名称 | 所属部门 | 状态 | 预算 | 开始日期 | 结束日期 | 成员数 |
|----------|----------|------|------|----------|----------|--------|
{% for row in data %}
| {{ row.project_name | dash }} | {{ row.owner_dept | dash }} | {{ row.status | status }} | {{ row.budget | money }} | {{ row.start_date | date_cn }} | {{ row.end_date | date_cn }} | {{ row.member_count }} |
{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
            "dept_overview.j2": """## {{ metadata.get("title", "部门概览报告") }}
> 生成时间：{{ metadata.fetched_at }}

{% for row in data %}
### {{ row.get("部门", row.get("dept_name", row.get("name", "未命名"))) | dash }}
- **员工数**：{{ row.get("员工数", row.get("employee_count", "—")) }}
- **项目数**：{{ row.get("项目数", row.get("project_count", "—")) }}
- **总预算**：{{ (row.get("总预算", row.get("total_budget", 0)) or 0) | money }}

{% endfor %}

{% if data | length == 0 %}
*(暂无数据)*
{% endif %}
""",
            "budget_usage.j2": """## {{ metadata.get("title", "预算使用分析报告") }}
> 生成时间：{{ metadata.fetched_at }}，共 {{ metadata.row_count }} 个项目

| 项目名称 | 所属部门 | 预算金额 | 状态 | 成员数 | 开始日期 | 结束日期 | 周期(天) |
|---------|---------|---------|------|--------|---------|---------|---------|
{% for row in data %}
| {{ row.get("项目名称", row.get("project_name", "—")) | dash }}
| {{ row.get("所属部门", row.get("dept_name", "—")) | dash }}
| {{ row.get("预算金额", row.get("budget_amount", row.get("budget", 0)) or 0) | money }}
| {{ row.get("状态", row.get("status", "—")) | status }}
| {{ row.get("成员数", row.get("member_count", 0)) }}
| {{ row.get("开始日期", row.get("start_date", None)) | date_cn }}
| {{ row.get("结束日期", row.get("end_date", None)) | date_cn }}
| {{ row.get("周期天数", row.get("duration_days", "—")) }} |
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
        from report_agent.data_fetcher import REPORT_REGISTRY

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
        "sales_summary.j2":   {"dept_name", "project_count", "total_budget",
                               "active_count", "completed_count"},
        "sales_detail.j2":    {"dept_name", "project_count", "total_budget",
                               "active_count", "completed_count"},
        "project_progress.j2": {"project_name", "owner_dept", "status",
                                "budget", "start_date", "end_date", "member_count"},
        "dept_overview.j2":   {"部门", "dept_name", "员工数", "employee_count",
                               "项目数", "project_count", "总预算", "total_budget"},
        "budget_usage.j2":    {"项目名称", "project_name", "所属部门", "dept_name",
                               "预算金额", "budget_amount", "budget", "status", "状态"},
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
                from report_agent.data_fetcher import REPORT_REGISTRY
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
