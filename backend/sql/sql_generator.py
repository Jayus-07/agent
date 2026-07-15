"""
sql_generator.py — 调用 LLM 生成 SQL

只接收有限表名的 schema 描述，生成纯 SELECT 语句。
"""
from backend.infra.llm import llm
from backend.sql.schema_loader import schema_loader
from backend.shared.logger import logger

GENERATE_PROMPT = """你是 SQL 查询生成助手。根据用户问题和提供的表结构，生成一条 PostgreSQL 语法的 SELECT 语句。

规则:
1. 只生成 SELECT 语句
2. **严格使用提供的表名和列名**，绝对不要编造不存在的列（如 department, dept_name, salary 等）
3. 使用标准 PostgreSQL 语法
4. 字符串比较使用 LIKE 或 =，ILIKE 用于不区分大小写的匹配
5. 如果是对敏感列（phone, password）查询，不要生成
6. 只输出 SQL 语句本身，不要加 markdown 代码块标记
7. 不要在 SQL 末尾加分号

## 表连接示例（重点！）

查询"技术部有多少人"的正确写法：
```sql
SELECT COUNT(*) AS count FROM users
JOIN departments ON users.dept_id = departments.id
WHERE departments.name = '技术部'
```

查询"每个部门的项目数量"的正确写法：
```sql
SELECT d.name AS 部门, COUNT(p.id) AS 项目数 FROM departments d
LEFT JOIN users u ON u.dept_id = d.id
LEFT JOIN project_members pm ON pm.user_id = u.id
LEFT JOIN projects p ON p.id = pm.project_id
GROUP BY d.name
```

关键点：
- users 表通过 dept_id 关联 departments 表
- project_members 表通过 user_id 关联 users，通过 project_id 关联 projects
- 不要编造 department、project_name 等不存在的列名

## 数据库表结构
{table_info}

用户问题: {question}

直接输出 SQL:"""


def generate_sql(question: str, table_names: list) -> str:
    """
    生成 SQL 语句。

    参数:
        question: 用户自然语言问题
        table_names: 相关表名列表

    返回:
        SQL 字符串
    """
    table_info = schema_loader.get_table_info(table_names)

    prompt = GENERATE_PROMPT.format(table_info=table_info, question=question)

    try:
        resp = llm.invoke(prompt)
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
