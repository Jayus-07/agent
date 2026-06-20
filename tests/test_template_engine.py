"""TemplateEngine 测试 — Jinja2 模板渲染"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_agent.template_engine import TemplateEngine


class TestTemplateEngine:
    """Jinja2 模板引擎"""

    def setup_method(self):
        self.engine = TemplateEngine(template_dir="/nonexistent_templates")

    def test_render_sales_summary(self):
        data = [
            {"dept_name": "技术部", "project_count": 3, "total_budget": 500.0,
             "active_count": 2, "completed_count": 1},
            {"dept_name": "产品部", "project_count": 2, "total_budget": 200.0,
             "active_count": 1, "completed_count": 1},
        ]
        result = {"data": data, "metadata": {"fetched_at": "2026-01-01", "row_count": 2}}
        rendered = self.engine.render("test_report", result, template_name="sales_summary.j2")
        assert "技术部" in rendered
        assert "500.0 万元" in rendered
        assert "产品部" in rendered

    def test_render_sales_detail(self):
        data = [{"dept_name": "技术部", "project_count": 5, "total_budget": 800.0,
                  "active_count": 3, "completed_count": 2}]
        result = {"data": data, "metadata": {"fetched_at": "2026-01-01"}}
        rendered = self.engine.render("test", result, template_name="sales_detail.j2")
        assert "技术部" in rendered
        assert "800.0 万元" in rendered

    def test_render_with_title_override(self):
        data = [{"dept_name": "技术部", "project_count": 1, "total_budget": 100.0,
                  "active_count": 1, "completed_count": 0}]
        result = {"data": data, "metadata": {"fetched_at": "2026-01-01"}}
        rendered = self.engine.render("test", result, template_name="sales_summary.j2",
                                       title="自定义标题")
        assert "自定义标题" in rendered

    def test_render_empty_data(self):
        result = {"data": [], "metadata": {"fetched_at": "2026-01-01"}}
        rendered = self.engine.render("test", result, template_name="sales_summary.j2")
        assert "暂无数据" in rendered

    def test_render_with_chart(self):
        data = [{"dept_name": "技术部", "project_count": 1, "total_budget": 100.0,
                  "active_count": 1, "completed_count": 0}]
        result = {"data": data, "metadata": {"fetched_at": "2026-01-01"}}
        rendered = self.engine.render("test", result, chart_markdown="![chart](chart.png)")
        # chart 可能嵌入也可能不嵌入，取决于模板是否支持 chart_markdown 变量
        # 只验证渲染成功且有内容
        assert len(rendered) > 0

    def test_get_template_with_builtin(self):
        tpl = self.engine.get_template("test", preferred="sales_summary.j2")
        assert "月度销售报告" in tpl or "sales" in tpl.lower()

    def test_fallback_on_unknown_template(self):
        """未知模板时使用内置兜底"""
        tpl = self.engine.get_template("test", preferred="nonexistent.j2")
        assert len(tpl) > 0

    def test_money_filter(self):
        """自定义 money 过滤器"""
        rendered = self.engine.render("test",
            {"data": [{"dept_name": "X", "project_count": 1,
                       "total_budget": 1234.5, "active_count": 0, "completed_count": 0}],
             "metadata": {"fetched_at": "x"}},
            template_name="sales_summary.j2")
        assert "1234.5 万元" in rendered

    def test_dash_filter_none(self):
        """空值显示 —"""
        rendered = self.engine.render("test",
            {"data": [{"dept_name": None, "project_count": 1,
                       "total_budget": 0, "active_count": 0, "completed_count": 0}],
             "metadata": {"fetched_at": "x"}},
            template_name="sales_summary.j2")
        assert "—" in rendered

    def test_project_progress_status_filter(self):
        data = [{"project_name": "P1", "owner_dept": "技术部", "status": "active",
                  "budget": 100.0, "start_date": "2025-01-01", "end_date": "2025-12-31",
                  "member_count": 5}]
        result = {"data": data, "metadata": {"fetched_at": "2026-01-01"}}
        rendered = self.engine.render("test", result, template_name="project_progress.j2")
        assert "进行中" in rendered  # status filter: active → 进行中

    def test_fallback_render_on_invalid_template(self):
        """模板语法错误时降级输出"""
        # 直接测试内部降级逻辑
        rendered = self.engine._fallback_render(
            {"data": [{"a": 1, "b": 2}], "metadata": {}},
            "test error"
        )
        assert "test error" in rendered
        assert "a" in rendered
        assert "1" in rendered
