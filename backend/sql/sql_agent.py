"""
sql_agent.py — SQL Agent 主编排器

流程: Router(选表) → Generator(生成SQL) → Validator(硬校验)
      → RowSecurity(行级注入) → Executor(执行+脱敏+格式化)

安全原则: 6 层硬校验，无一依赖 LLM 承诺。

返回契约 (A 段重构)：
  - `ask(question)`     — 旧入口，返回 Markdown 字符串（向后兼容 tools 层）
  - `ask_struct(question)` — 新入口，返回 SQLResult（推荐给 SQLSkill）

策略链（2026-09-23 SQL 收口 STOP B）：
  - `ask_struct(question, policy=SQLPolicyContext)` — 生产链路：权限门 +
    表域判定 + 三维 scope 注入（backend/sql/policy.py），SQLPolicyError
    为终态拒绝；重试仍重走 Guard。
  - policy=None（评测/脚本/未接上下文调用）— 旧行为：仅 6 层校验 +
    旧 row_security 注入（受 SQL_ROW_SECURITY_ENABLED 控制）。
"""
from typing import Optional

from backend.sql.policy import SQLPolicyContext
from backend.sql.router import select_tables
from backend.sql.sql_generator import generate_sql
from backend.sql.sql_validator import sql_validator, ValidationError
from backend.sql.row_security import inject_row_filter, RowSecurityError
from backend.sql.executor import execute_sql_struct
from backend.sql.sql_result import SQLResult
from backend.shared.logger import logger


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
    # 主入口：旧（Markdown 字符串）
    # =================================================

    def ask(
        self,
        question: str,
        current_user_id: Optional[int] = None,
    ) -> str:
        """处理自然语言问题，返回 Markdown 表格或错误字符串（向后兼容）。

        推荐新调用方使用 `ask_struct()` 拿 SQLResult。
        """
        result = self.ask_struct(question, current_user_id=current_user_id)
        return result.to_markdown()

    # =================================================
    # 主入口：新（结构化 SQLResult）
    # =================================================

    def ask_struct(
        self,
        question: str,
        current_user_id: Optional[int] = None,
        policy: Optional["SQLPolicyContext"] = None,
    ) -> SQLResult:
        """处理自然语言问题并返回 SQLResult。

        policy 给定 → 策略链（权限门/表域/scope 注入，见模块 docstring）；
        policy=None → 旧行为（未声明主体 = 授权未启用，评测/脚本路径）。

        错误语义约定：
          - router 抛任何异常 → status="failed", error_type="router_error"
          - router 返回 []     → status="no_table"
          - ValidationError    → status="validation_error"
          - SQLPolicyError     → status="permission_denied"（终态，不重试）
          - RowSecurityError   → status="permission_denied"
          - executor 返回的 status 透传（success / no_data / timeout / syntax_error / permission_denied / failed）
        """
        if policy is not None:
            return self._ask_struct_with_policy(question, policy)

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
            return SQLResult.failed(
                status="failed",
                error=f"内部错误：表路由失败 {e}",
                error_type="router_error",
            )

        if not table_names:
            return SQLResult.failed(
                status="no_table",
                error="未找到相关数据表，请调整问题后重试。",
                error_type="no_table",
            )

        # — Step 2-5: 生成 + 校验循环 —
        feedback: str | None = None  # 重试时携带上次 SQL + 失败原因，引导 LLM 修正
        last_sql: str | None = None
        last_result: SQLResult | None = None
        for attempt in range(self.max_retries + 1):
            sql: str | None = None
            try:
                sql = generate_sql(question, table_names, feedback=feedback)
                last_sql = sql

                safe_sql, _, _ = sql_validator.validate(sql)

                # 行级安全：返回 (sql_with_placeholders, params_dict)
                safe_sql, rs_params = inject_row_filter(safe_sql, user_context)

                # 结构化执行
                result = execute_sql_struct(safe_sql, self.db_config, params=rs_params)
                last_result = result

                # 成功路径 → 直接返回
                if result.status in ("success", "no_data"):
                    return result

                # 失败判断：
                # 1. status ∈ {validation_error, permission_denied} → 不可重试
                # 2. status ∈ {syntax_error} → 不可重试（同一 LLM 再来通常还是同样错）
                # 3. status ∈ {timeout, failed, no_table} → 可重试
                if result.status in ("validation_error", "permission_denied", "syntax_error"):
                    return result

                # 其它失败：尝试重试
                if attempt < self.max_retries:
                    feedback = f"上次生成的 SQL:\n{sql}\n执行报错: {result.error}"
                    continue
                return result

            except ValidationError as e:
                logger.warning(f"[SQLAgent] 校验失败 (第{attempt+1}次): {e}")
                if attempt < self.max_retries:
                    feedback = (
                        f"上次生成的 SQL:\n{last_sql}\n"
                        f"被安全校验拒绝（{e}），请避免同样问题"
                    )
                    continue
                return SQLResult.failed(
                    status="validation_error",
                    error=f"SQL 校验失败: {e}",
                    error_type="validation",
                )

            except RowSecurityError as e:
                logger.error(f"[SQLAgent] 行级安全注入失败: {e}")
                return SQLResult.failed(
                    status="permission_denied",
                    error=f"访问控制错误: {e}",
                    error_type="row_security",
                )

            except Exception as e:
                logger.error(f"[SQLAgent] 执行失败 (第{attempt+1}次): {e}")
                if attempt < self.max_retries:
                    feedback = (
                        f"上次生成的 SQL:\n{last_sql}\n执行报错: {e}"
                        if last_sql else f"上次执行报错: {e}"
                    )
                    continue
                return SQLResult.failed(
                    status="failed",
                    error=f"查询失败: {e}",
                    error_type="unknown",
                )

        # 极端兜底（理论上不可达；防御性返回）
        if last_result is not None:
            return last_result
        return SQLResult.failed(
            status="failed",
            error="查询失败，已达到最大重试次数。",
            error_type="retry_exhausted",
        )

    # =================================================
    # 策略链（policy 链：权限门 + 表域判定 + 三维 scope 注入）
    # =================================================

    def _ask_struct_with_policy(
        self,
        question: str,
        policy: "SQLPolicyContext",
    ) -> SQLResult:
        """生产策略链路。与旧行为的差异：

        - 执行前过 SQLPolicyGuard（权限门/表域/scope 注入）；
        - SQLPolicyError 是终态拒绝（对外只回安全文案，原因进日志），
          不进入「带反馈重试」——安全拒绝不允许模型绕过；
        - ValidationError（解析/别名等语法类）保留既有有限重试，
          且每次重试重新走 Guard；
        - scope 注入取代旧 inject_row_filter（避免 customer_id 双重注入）。
        """
        from backend.sql.policy import SQLPolicyGuard, SQLPolicyError

        logger.info(
            f"[SQLAgent:policy] 收到问题: {question[:80]}... "
            f"(user={policy.user_id}, scope={policy.data_scope}, "
            f"dept={policy.department or '-'}, tenant={policy.tenant_id or '-'})"
        )

        # — Step 1: 路由选表（与旧链路一致）—
        try:
            table_names = select_tables(question)
        except Exception as e:
            logger.error(f"[SQLAgent:policy] 表路由失败: {e}")
            return SQLResult.failed(
                status="failed",
                error=f"内部错误：表路由失败 {e}",
                error_type="router_error",
            )
        if not table_names:
            return SQLResult.failed(
                status="no_table",
                error="未找到相关数据表，请调整问题后重试。",
                error_type="no_table",
            )

        # — Step 2-5: 生成 + Guard 循环 —
        guard = SQLPolicyGuard()
        feedback: str | None = None
        sql: str | None = None
        last_result: SQLResult | None = None
        for attempt in range(self.max_retries + 1):
            try:
                sql = generate_sql(question, table_names, feedback=feedback)

                guarded = guard.validate_and_rewrite(sql, policy)
                result = execute_sql_struct(
                    guarded.executable_sql, self.db_config,
                    params=guarded.params,
                )
                last_result = result

                if result.status in ("success", "no_data"):
                    return result
                # 语法/schema/权限类不可重试；timeout/failed 可重试
                if result.status in ("validation_error", "permission_denied", "syntax_error"):
                    return result
                if attempt < self.max_retries:
                    feedback = f"上次生成的 SQL:\n{sql}\n执行报错: {result.error}"
                    continue
                return result

            except SQLPolicyError as e:
                # 安全拒绝 = 终态（对外安全文案，详细原因只进日志）
                logger.warning(
                    f"[SQLAgent:policy] 策略拒绝 code={e.code}: {e}")
                return SQLResult.failed(
                    status="permission_denied",
                    error=e.user_text,
                    error_type="row_security",
                )

            except ValidationError as e:
                logger.warning(
                    f"[SQLAgent:policy] 校验失败 (第{attempt+1}次): {e}")
                if attempt < self.max_retries:
                    feedback = (
                        f"上次生成的 SQL:\n{sql}\n"
                        f"被安全校验拒绝（{e}），请避免同样问题"
                    )
                    continue
                return SQLResult.failed(
                    status="validation_error",
                    error=f"SQL 校验失败: {e}",
                    error_type="validation",
                )

            except Exception as e:
                logger.error(f"[SQLAgent:policy] 执行失败 (第{attempt+1}次): {e}")
                if attempt < self.max_retries:
                    feedback = f"执行报错: {e}"
                    continue
                return SQLResult.failed(
                    status="failed",
                    error=f"查询失败: {e}",
                    error_type="unknown",
                )

        if last_result is not None:
            return last_result
        return SQLResult.failed(
            status="failed",
            error="查询失败，已达到最大重试次数。",
            error_type="retry_exhausted",
        )


# =================================================
# 工厂（多 Agent/路由懒加载）
# =================================================

def init_sql_agent(db_config: dict, max_retries: int = 1) -> SQLAgent:
    """构造 SQLAgent 实例。"""
    host = db_config.get("host", "?")
    dbname = db_config.get("dbname", "?")
    logger.info(f"SQLAgent 初始化完成: postgresql://{host}/{dbname}")
    return SQLAgent(db_config=db_config, max_retries=max_retries)


# 线程安全单例（供 FastAPI deps + MCP server 共用）
import threading as _threading
_sql_agent_lock = _threading.Lock()
_sql_agent_singleton: SQLAgent | None = None


def get_sql_agent() -> SQLAgent:
    """惰性初始化 SQLAgent 单例（线程安全，连业务库）。"""
    global _sql_agent_singleton
    if _sql_agent_singleton is None:
        with _sql_agent_lock:
            if _sql_agent_singleton is None:
                from backend.config import BUSINESS_DB_CONFIG
                _sql_agent_singleton = init_sql_agent(BUSINESS_DB_CONFIG, max_retries=2)
    return _sql_agent_singleton
