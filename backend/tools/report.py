"""报告工具 — 生成 Markdown 报告（含图表）。"""
from langchain_core.tools import tool
from backend.shared.logger import logger

# 报告生成（Tool + 公共函数）
# =====================================================

def run_report(report_type: str, filters: dict = None, *,
               user_id: str = "default", polish: bool = True) -> str:
    """生成业务报告的统一入口（API route 和 Agent tool 共用）。

    Args:
        report_type: 报告类型，如 monthly_sales / inventory_health
        filters: 筛选条件
        user_id: 用户标识（用于偏好学习）
        polish: 是否启用 LLM 语言润色

    Returns:
        Markdown 格式报告
    """
    from backend.business_report.report_generator import generate_report
    return generate_report(report_type, filters or {}, user_id=user_id, polish=polish)


@tool
def generate_report_tool(report_type: str, filters: dict = None) -> str:
    """
    生成结构化 Markdown 报告（含图表）。
    报告类型需是已注册的类型。
    适用场景：需要输出的正式报告、数据分析汇总。
    """
    filters = filters or {}
    logger.info(f"[Tool:generate_report] 类型={report_type}, 筛选={filters}")
    return run_report(report_type, filters, user_id="multi-agent", polish=False)


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(generate_report_tool, __file__)

