"""
sql_generator.py — 调用 LLM 生成 SQL

只接收有限表名的 schema 描述，生成纯 SELECT 语句。
表名采用 `<schema>.<table>` 全限定形式（业务数据仓库多域架构）。
"""
from backend.infra.llm import llm
from backend.sql.schema_loader import schema_loader
from backend.shared.logger import logger

GENERATE_PROMPT = """你是 SQL 查询生成助手。根据用户问题和提供的表结构，生成一条 PostgreSQL 语法的 SELECT 语句。

规则:
1. 只生成 SELECT 语句
2. **严格使用提供的全限定表名（格式 `<schema>.<table>`）和列名**，绝对不要编造不存在的列（如 department, dept_name, salary 等）
3. **跨域 JOIN**：当问题涉及多个业务域（如"商品销量"需要 product + order），使用各自的全限定名（如 `product.products JOIN order.order_items ON ...`）
4. 使用标准 PostgreSQL 语法
5. 字符串比较使用 LIKE 或 =，ILIKE 用于不区分大小写的匹配
6. 如果是对敏感列查询，不要生成
7. 只输出 SQL 语句本身，不要加 markdown 代码块标记
8. 不要在 SQL 末尾加分号

## 表连接示例（重点！）

查询"最近一个月内价格最高的商品信息"：
```sql
SELECT p.product_name, p.sale_price, p.brand
FROM product.products p
ORDER BY p.sale_price DESC
LIMIT 5
```

查询"高退款率商品 TOP10"（跨 product + order）：
```sql
SELECT p.product_name,
       COUNT(r.id) AS refund_count
FROM product.products p
JOIN order.refunds r ON r.product_id = p.id
GROUP BY p.product_name
ORDER BY refund_count DESC
LIMIT 10
```

## 数据库表结构（schema 全限定名）
{table_info}

用户问题: {question}

直接输出 SQL:"""


def generate_sql(question: str, table_names: list) -> str:
    """
    生成 SQL 语句。

    参数:
        question: 用户自然语言问题
        table_names: 相关 schema-qualified 表名（如 ['product.products', 'order.orders']）

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
