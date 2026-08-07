"""sql_result.py — SQL 执行结果的结构化类型

解决的问题：
  - 旧实现把所有错误都包成 Markdown 字符串返回（如 `return "查询失败: ..."`），
    导致上层 `BaseSkill.execute` 看到 Tool 返回字符串就一律判 success。
  - 错误被埋进 output 字符串，supervisor 看到的 status 永远是 success，
    Reporter 必须用 substring（`"无结果" / "未找到"`）兜底匹配。

新增结构化返回后：
  - 调用方按 `SQLResult.status` 决定下一步：
      success / no_data → 视为成功（no_data 额外触发 supervisor 降级链）
      timeout / syntax_error / permission_denied / validation_error / failed / no_table → failed
  - `error` / `error_type` 与 `StepResult.error_type` 字段对齐（`backend/orchestration/state.py:27`）。
"""
from dataclasses import dataclass
from typing import Literal

# 与 StepResult.status Literal["pending","running","success","failed","skipped"] 配合：
#   success / no_data → success（保留 supervisor 降级触发条件；degradation.py:53）
#   timeout / syntax_error / permission_denied / validation_error / failed / no_table → failed
SQLStatus = Literal[
    "success",          # 查询成功 + 有数据
    "no_data",          # 查询成功 + 空结果集（仍属 success；StepResult.is_empty=True）
    "failed",           # 通用失败（router / executor / 兜底）
    "timeout",          # statement_timeout 触发
    "syntax_error",     # PG 语法/列不存在
    "permission_denied",  # PG 权限拒绝 / read-only transaction
    "validation_error",   # SQL 白名单 / 敏感列 / 函数黑名单 拦截
    "no_table",         # router 找不到匹配表
]


@dataclass
class SQLResult:
    """SQLAgent 结构化执行结果。"""
    status: SQLStatus
    rows: list[dict] | None = None
    columns: list[str] | None = None
    row_count: int = 0
    is_empty: bool = False
    elapsed_sec: float = 0.0
    error: str | None = None
    error_type: str | None = None   # 与 StepResult.error_type 对齐（自由字符串）
    sql_text: str | None = None

    # 便捷构造器
    @classmethod
    def success(cls, rows: list[dict], columns: list[str], sql: str | None,
                elapsed: float = 0.0) -> "SQLResult":
        return cls(
            status="success" if rows else "no_data",
            rows=rows,
            columns=columns,
            row_count=len(rows),
            is_empty=len(rows) == 0,
            elapsed_sec=elapsed,
            sql_text=sql,
        )

    @classmethod
    def failed(cls, status: SQLStatus, error: str, error_type: str | None = None,
               sql: str | None = None, elapsed: float = 0.0) -> "SQLResult":
        return cls(
            status=status,
            error=error,
            error_type=error_type,
            elapsed_sec=elapsed,
            sql_text=sql,
        )

    def to_markdown(self) -> str:
        """成功（含 no_data）时输出 Markdown 表格；失败直接返回 error。"""
        if self.status in ("success", "no_data"):
            if self.is_empty:
                return "(无结果)"
            assert self.rows is not None and self.columns is not None
            lines = [
                "| " + " | ".join(self.columns) + " |",
                "| " + " | ".join(["---"] * len(self.columns)) + " |",
            ]
            for row in self.rows:
                # 全部转字符串防 None 触发 markdown 解析
                lines.append("| " + " | ".join(str(row.get(c, "")) for c in self.columns) + " |")
            return "\n".join(lines)
        return self.error or "(未知错误)"
