"""
skills/sql/models.py — SQL 查询结果数据协议

SQLResult 是 SQLSkill 与 BusinessAnalyzer 之间的数据协议。
与 backend/sql/sql_result.py（dataclass，含 status/error 执行状态）分层共存：
  - backend/sql/sql_result.py → SQLAgent 内部返回类型
  - backend/skills/sql/models.py → Skill 层对外数据协议
"""
from typing import Any

from pydantic import BaseModel, Field


class SQLResult(BaseModel):
    """SQL 查询结构化结果 — Skill 层数据协议

    只包含纯数据字段，不含执行状态（status/error）。
    由 SQLSkill 从 SQLAgent dataclass 转换后写入 step_results。
    """

    sql: str = Field(description="实际执行的 SQL 语句")
    tables: list[str] = Field(description="涉及的表名（schema.table 全限定格式）")
    columns: list[str] = Field(description="结果列名列表")
    rows: list[dict[str, Any]] = Field(description="数据行列表")
    row_count: int = Field(description="结果行数")
    execution_time: float = Field(description="SQL 执行耗时（秒）")
