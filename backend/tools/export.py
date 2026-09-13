"""导出工具 — CSV 导出（UTF-8 BOM，Excel 兼容；写文件操作，需人工审批）。"""
from langchain_core.tools import tool
from backend.shared.logger import logger

@tool
def export_csv_tool(question: str, filename: str = "") -> str:
    """
    查询数据库并导出结果为 CSV 文件（UTF-8 BOM，Excel 兼容打开）。
    question: 自然语言查询问题（如 "上周各渠道销售额"）
    filename: 导出文件名（不含扩展名），默认自动生成
    返回: 导出文件路径和行数
    """
    import csv
    from pathlib import Path
    from datetime import datetime
    from backend.config import STORAGE_DOCS_DIR
    from backend.security.tool_approval import ensure_approved
    from backend.tools.session import _get_session_id, get_tool_user_id

    # 写操作审批门：指纹只用 question（filename 自动生成，不含时间戳则稳定，
    # 含时间戳的默认名在批准后才生成，不参与指纹）
    pending = ensure_approved(
        "export_csv", "export",
        user_id=get_tool_user_id(),
        detail={"question": question, "filename": filename, "session_id": _get_session_id()},
    )
    if pending is not None:
        return pending

    # 委托 SQL agent 生成并执行 SQL
    agent = _get_sql_agent()
    result = agent.ask(question, current_user_id=get_tool_user_id() or None)

    # 从 SQL agent 结果中提取表格数据
    rows, columns = _extract_table_from_markdown(result)
    if not rows:
        return f"[EXPORT FAILED] 查询无结果或无法解析: {question[:80]}"

    if not filename:
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    export_dir = Path(STORAGE_DOCS_DIR) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    filepath = export_dir / f"{filename}.csv"

    with open(str(filepath), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    logger.info(f"[Tool:export_csv] {len(rows)} 行 → {filepath}")
    return f"已导出 {len(rows)} 行数据到 {filepath}"


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(export_csv_tool, __file__)

