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
    from backend.tools.session import get_tool_user_id

    filters = filters or {}
    logger.info(f"[Tool:generate_report] 类型={report_type}, 筛选={filters}")
    # 偏好学习按用户隔离：优先请求上下文的 user_id，
    # 无上下文（MCP 直调/脚本）时保留历史兜底值
    return run_report(report_type, filters,
                      user_id=get_tool_user_id() or "multi-agent", polish=False)


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(generate_report_tool, __file__)

