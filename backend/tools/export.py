"""导出工具 — CSV 导出（UTF-8 BOM，Excel 兼容；写文件操作，需人工审批）。"""
from langchain_core.tools import tool
from backend.shared.logger import logger


def _get_sql_agent():
    from backend.sql.sql_agent import get_sql_agent

    return get_sql_agent()


@tool
def export_csv_tool(question: str, filename: str = "",
                    idempotency_key: str = "") -> str:
    """
    查询数据库并导出结果为 CSV 文件（UTF-8 BOM，Excel 兼容打开）。
    question: 自然语言查询问题（如 "上周各渠道销售额"）
    filename: 导出文件名（不含扩展名），默认自动生成
    返回: 导出文件路径和行数
    """
    from backend.security.tool_approval import ensure_approved
    from backend.tools.session import (
        _get_session_id, get_tool_idempotency_key, get_tool_tenant_id,
        get_tool_user_id,
    )

    # 写操作审批门：指纹只用 question（filename 自动生成，不含时间戳则稳定，
    # 含时间戳的默认名在批准后才生成，不参与指纹）
    pending = ensure_approved(
        "export_csv", "export",
        user_id=get_tool_user_id(),
        detail={"question": question, "filename": filename, "session_id": _get_session_id()},
    )
    if pending is not None:
        return pending

    payload = {"question": question, "filename": filename}
    if get_tool_tenant_id():
        from backend.shared.idempotency import run_idempotent_operation

        result = run_idempotent_operation(
            "data.export",
            payload,
            lambda: {"message": _export_csv_after_approval(question, filename)},
            client_key=idempotency_key or get_tool_idempotency_key(),
        )
        return str(result["message"])

    # 兼容尚未经网关注入租户的旧直调/本地开发路径；有可信租户时不降级。
    return _export_csv_after_approval(question, filename)


def _safe_export_path(filename: str):
    """把 LLM 可控的 filename 约束到 exports 目录内（2026-09-23 D1-2）。

    此前 filename 直接拼 `export_dir / f"{filename}.csv"`，传 `../../x`
    即可写出目录树外任意 .csv（路径穿越写文件）。现四道防线：
      ① 显式拒绝路径分隔符 `/` `\\` 与 `..`；
      ② 字符白名单：中文/字母/数字/下划线/连字符（尾部 .csv 先剥除，
         扩展名永远由服务端追加）；
      ③ resolve 后 is_relative_to 二次确认仍在 exports 内；
      ④ 空名回落时间戳默认名。
    返回安全绝对路径；不合法抛 ValueError。
    """
    import re
    from pathlib import Path
    from datetime import datetime
    from backend.config import STORAGE_DOCS_DIR

    name = (filename or "").strip()
    if not name:
        name = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if name.lower().endswith(".csv"):
        name = name[:-4]
    if not name or "/" in name or "\\" in name or ".." in name \
            or not re.fullmatch(r"[\w-]+", name, re.UNICODE):
        raise ValueError(
            "filename 只允许中文/字母/数字/下划线/连字符，"
            "不含路径分隔符与扩展名")
    export_dir = (Path(STORAGE_DOCS_DIR) / "exports").resolve()
    filepath = (export_dir / f"{name}.csv").resolve()
    if not filepath.is_relative_to(export_dir):
        raise ValueError("filename 越界")
    return filepath


def _export_csv_after_approval(question: str, filename: str = "") -> str:
    """审批通过后的导出执行；全局幂等 claim 在调用此函数之前完成。"""
    import csv
    from pathlib import Path

    # STOP C：导出与 sql_query_tool 走同一策略链（权限门/表域/scope 注入/
    # 审计），不再经 agent.ask 旧链旁路；身份来自 contextvars 可信上下文
    from backend.tools.sql import tool_policy_context
    agent = _get_sql_agent()
    result = agent.ask_struct(question, policy=tool_policy_context())

    if result.status not in ("success", "no_data"):
        logger.warning(
            f"[Tool:export_csv] 查询失败 status={result.status}: "
            f"{(result.error or '')[:120]}")
        return f"[EXPORT FAILED] 查询未成功: {result.error or result.status}"

    rows, columns = result.rows or [], result.columns or []
    if not rows:
        return f"[EXPORT FAILED] 查询无结果: {question[:80]}"

    try:
        filepath = _safe_export_path(filename)
    except ValueError as e:
        logger.warning(f"[Tool:export_csv] 拒绝非法 filename: {filename!r}")
        return f"[EXPORT FAILED] 文件名不合法: {e}"

    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(str(filepath), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    logger.info(f"[Tool:export_csv] {len(rows)} 行 → {filepath}")
    return f"已导出 {len(rows)} 行数据到 {filepath}"


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(export_csv_tool, __file__)

