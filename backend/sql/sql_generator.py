"""
sql_generator.py — 调用 LLM 生成 SQL

只接收有限表名的 schema 描述，生成纯 SELECT 语句。
表名采用 `<schema>.<table>` 全限定形式（业务数据仓库多域架构）。
"""
from backend.infra.llm import llm
from backend.prompts.service import prompt_service
from backend.sql.query_examples import select_query_examples
from backend.sql.schema_loader import schema_loader
from backend.shared.logger import logger


SQL_QUERY_SEMANTIC_CONTRACT = """## 查询口径约束（必须遵守）
- 用户原问题中的时间范围、筛选条件、分组维度、排序方向和返回数量都是硬条件，不能遗漏、扩大、缩小或自行补充。
- 时间范围按原意处理：最近 30 天是滚动 30 天，不等于自然月；本月、上月按自然月边界。用户未给时间范围时，不自行加最近 N 天/月。
- 区分指标：订单量统计订单数，销量统计商品件数，退款次数统计退款记录数，退款金额汇总退款金额，退款率必须有明确分子和分母；不能拿次数代替金额或比率。
- 项目统一口径（评测 SC-109、SC-119、SC-124、SC-125）：销量或销售额排名/高低比较都排除 `status = 'cancelled'` 的订单。使用 `order.order_items` 统计时必须关联 `order.orders`；分类选择、子查询或多层排名中的每一层独立统计都要应用过滤。只有用户明确要求包含已取消订单时才覆盖此口径。
- 本项目商品级退款率口径与 SQL 评测集 SC-117 一致：退款金额汇总 `order.refunds.refund_amount`，除以商品销售额 `SUM(order.order_items.quantity * order.order_items.price)`；先分别按商品聚合再关联，避免一对多 JOIN 重复计数，分母为零时返回 NULL。
- 退款率排名必须排除销售额分母为 NULL 或小于等于零的商品，并显式使用 `NULLS LAST`；不能让未定义的退款率排在有效退款率之前。
- 比较本月与上月时只生成这两个自然月；明确要求完整月份时使用月初（含）到下月月初（不含）的完整边界，不得把未来月份混进比较；应保留缺少明细的月份，并把无正销售额月份的退款率标为 NULL。
- 当前自然月尚未结束且用户要求比较本月与上月时，默认比较本月累计（含今天）与上月相同天数；上月天数较少时截止上月月末。明确要求完整自然月时再比较完整月份。
- 用户问“最近 N 个自然日每天/按日”时，结果必须包含连续的 N 个日期；用日历序列左连接事件数据，把确认无事件的日期计为 0，窗口为今天及之前 N-1 天，明天零点为不含上界。
- 比率与百分数使用明确列名和单位：`*_rate` / `*_ratio` 返回 0 到 1 的比例值；`*_percent` 返回 0 到 100 的百分数值。不得把 0.42 和 42 混作同一单位。
- 空结果上的 `SUM` 是 NULL；只有业务含义明确为“没有记录就是 0”时才用 `COALESCE` 转成 0。比率分母为零或非正时保留 NULL，不把未知伪装成 0。
- 按商品、客户或分类聚合/去重时，分组键包含稳定主键和展示名称。不能只按可能重复的名称分组，避免把不同实体合并。
- 除上述商品级退款率定义外，销售额优先使用表结构中明确的业务金额字段；只有用户明确要求或项目口径指定时才用明细数量×单价，不要把订单总额、明细金额和扣退款后的净额混为一谈。
- “最多/最高”按降序，“最少/最低”按升序；“前 N / Top N”必须保留 N。
- 不得用相近字段或指标替代问题要求的口径；列不在提供的 schema 中时，不得编造列名。
"""


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
    query_examples = select_query_examples(question, table_names)
    if query_examples:
        example_blocks = [
            f"示例问题：{example.question}\n"
            f"适用表：{', '.join(sorted(example.tables))}\n"
            f"示例 SQL：\n{example.sql}"
            for example in query_examples
        ]
        table_info = "\n\n".join(part for part in (
            table_info,
            "## 与当前问题相关的 SQL 查询示例（仅供参考）\n"
            "示例只能帮助理解字段、关联和聚合写法。最终查询以用户问题为准，"
            "必须保留问题中明确提出的时间范围、筛选条件、分组维度、排序和返回数量；"
            "不得因为示例省略这些条件，也不得把含义不同的业务口径当成同义词。\n"
            + "\n\n".join(example_blocks),
        ) if part)

    generation_question = question
    if feedback:
        generation_question += f"\n\n## 上次生成失败，请修正\n{feedback}"
    generation_question += f"\n\n{SQL_QUERY_SEMANTIC_CONTRACT}"

    r = prompt_service.render_sync(
        "sql.generator", table_info=table_info, question=generation_question,
    )

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
