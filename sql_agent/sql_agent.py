"""
sql_agent.py — SQL Agent 主编排器

流程: Router(选表) → Generator(生成SQL) → Validator(硬校验)
      → RowSecurity(行级注入) → Executor(执行+脱敏+格式化)

安全原则: 6 层硬校验，无一依赖 LLM 承诺。
"""
from typing import Optional

from sql_agent.schema_loader import schema_loader
from sql_agent.router import select_tables
from sql_agent.sql_generator import generate_sql
from sql_agent.sql_validator import sql_validator, ValidationError
from sql_agent.row_security import inject_row_filter, RowSecurityError
from sql_agent.executor import execute_sql
from utils.logger import logger


class SQLAgent:
    """生产级 SQL Agent 入口"""

    def __init__(self, db_config: dict, max_retries: int = 1):
        """
        参数:
            db_config: PostgreSQL 连接配置
                       {"host": "localhost", "port": 5432, "dbname": "demo",
                        "user": "readonly", "password": "..."}
            max_retries: 校验失败后重新生成 SQL 的次数
        """
        self.db_config = db_config
        self.max_retries = max_retries

    # =================================================
    # 主入口
    # =================================================

    def ask(
        self,
        question: str,
        current_user_id: Optional[int] = None,
    ) -> str:
        """
        处理自然语言问题，返回 Markdown 表格结果。

        参数:
            question: 自然语言问题（中文/英文）
            current_user_id: 当前会话用户 ID（用于行级安全）

        返回:
            Markdown 格式的查询结果
        """
        logger.info(f"[SQLAgent] 收到问题: {question[:80]}... (user={current_user_id})")

        user_context = {}
        if current_user_id is not None:
            user_context["current_user_id"] = current_user_id

        # — Step 1: 路由选表 —
        try:
            table_names = select_tables(question)
            logger.info(f"[SQLAgent] 选中表: {table_names}")
        except Exception as e:
            logger.error(f"[SQLAgent] 表路由失败: {e}")
            return "内部错误：表路由失败，请稍后重试。"

        if not table_names:
            return "未找到相关数据表，请调整问题后重试。"

        # — Step 2-5: 生成 + 校验循环 —
        for attempt in range(self.max_retries + 1):
            try:
                sql = generate_sql(question, table_names)

                safe_sql, _, _ = sql_validator.validate(sql)

                # 行级安全：返回 (sql_with_placeholders, params_dict)
                safe_sql, rs_params = inject_row_filter(safe_sql, user_context)

                # 参数化执行（user_id 等敏感值通过 params 通道，不进 SQL 文本）
                result = execute_sql(safe_sql, self.db_config, params=rs_params)
                return result

            except ValidationError as e:
                logger.warning(f"[SQLAgent] 校验失败 (第{attempt+1}次): {e}")
                if attempt < self.max_retries:
                    question = question + f"\n(之前生成的 SQL 因为 {e} 被拒绝，请避免同样问题)"
                    continue
                return f"SQL 校验失败: {e}"

            except RowSecurityError as e:
                logger.error(f"[SQLAgent] 行级安全注入失败: {e}")
                return f"访问控制错误: {e}"

            except Exception as e:
                logger.error(f"[SQLAgent] 执行失败 (第{attempt+1}次): {e}")
                if attempt < self.max_retries:
                    question = question + f"\n(之前生成的 SQL 执行报错: {e}，请修正)"
                    continue
                return f"查询失败: {e}"

        return "查询失败，已达到最大重试次数。"


# =================================================
# 工厂（多 Agent/路由懒加载）
# =================================================

def init_sql_agent(db_config: dict, max_retries: int = 1) -> SQLAgent:
    """构造 SQLAgent 实例（multimodal 入口统一用 deps.get_sql_agent，工厂保留作 demo/工具调用）"""
    host = db_config.get("host", "?")
    dbname = db_config.get("dbname", "?")
    logger.info(f"SQLAgent 初始化完成: postgresql://{host}/{dbname}")
    return SQLAgent(db_config=db_config, max_retries=max_retries)
