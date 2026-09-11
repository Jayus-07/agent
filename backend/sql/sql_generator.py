"""
sql_generator.py — 调用 LLM 生成 SQL

只接收有限表名的 schema 描述，生成纯 SELECT 语句。
表名采用 `<schema>.<table>` 全限定形式（业务数据仓库多域架构）。
"""
from backend.infra.llm import llm
from backend.prompts.service import prompt_service
from backend.sql.schema_loader import schema_loader
from backend.shared.logger import logger


def generate_sql(question: str, table_names: list, feedback: str | None = None) -> str:
    """
    生成 SQL 语句。

    参数:
        question: 用户自然语言问题
        table_names: 相关 schema-qualified 表名（如 ['product.products', 'order.orders']）
        feedback: 上次失败的反馈（错误原因 + 上次 SQL），重试时传入以引导修正

    返回:
        SQL 字符串
    """
    table_info = schema_loader.get_table_info(table_names)

    if feedback:
        question = f"{question}\n\n## 上次生成失败，请修正\n{feedback}"

    r = prompt_service.render_sync("sql.generator", table_info=table_info, question=question)

    try:
        resp = llm.invoke(r.text)
        sql = resp.content.strip()

        # 去除可能的 markdown 标记
        if sql.startswith("```"):
            lines = sql.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            sql = "\n".join(lines).strip()

        # 去掉末尾分号
        sql = sql.rstrip(";").rstrip()

        logger.info(f"[SQLGen] 生成 SQL: {sql[:120]}")
        return sql

    except Exception as e:
        logger.error(f"[SQLGen] LLM 生成 SQL 失败: {e}")
        raise
