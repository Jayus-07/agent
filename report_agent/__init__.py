"""
report_agent — 报告生成模块

流程: SQL/API 取数据 → Jinja2 模板渲染 → LLM 润色 → Markdown 报告

安全原则: LLM 只做语言润色，数字和事实通过硬校验锁定，不依赖 LLM 承诺。

用法:
    from report_agent import generate_report

    report = generate_report("monthly_sales", {"month": "2026-05"}, user_id="user_001")
    print(report)
"""

from report_agent.report_generator import ReportGenerator, generate_report
from report_agent.data_fetcher import REPORT_REGISTRY, register_report_type, SQLFetcher, APIFetcher
from report_agent.snapshot import save_snapshot, load_snapshot, list_snapshots
from report_agent.preference import preference_store

# chart_generator 和 llm_polisher 在各自模块中惰性导入，避免未安装 matplotlib 时报错

__all__ = [
    "ReportGenerator",
    "generate_report",
    "REPORT_REGISTRY",
    "register_report_type",
    "SQLFetcher",
    "APIFetcher",
    "save_snapshot",
    "load_snapshot",
    "list_snapshots",
    "preference_store",
]
