"""API 层 — Pydantic 请求/响应模型"""
from pydantic import BaseModel, Field
from typing import Optional

from backend.config.chat_input import CHAT_INPUT_MAX_CHARS


# ── 对话（Multi-Agent）───────────────────────────

class ChatRequest(BaseModel):
    question: str = Field(..., description="用户问题", min_length=1,
                          max_length=CHAT_INPUT_MAX_CHARS)
    session_id: str = Field("default", description="会话ID，同一会话内记忆持久化")
    kb_id: Optional[str] = Field(None, description="知识库ID（policy/tech/finance/hr 等，默认 default）")
    request_id: Optional[str] = Field("default", description="请求ID，用于中止信号路由")
    user_id: Optional[str] = Field(None, description="用户ID（优先从请求体获取，其次从信任网关注头获取）")
    department: Optional[str] = Field("", description="员工部门ID（检索授权用；空=未声明，按对客最严格集合处理）")
    model: Optional[str] = Field(None, description="按请求模型覆盖（须为已注册模型名，空 = 全局默认）")
    domain_hint: Optional[str] = Field("", description="入口域提示：customer_service=客服窗口锁域（直接进客服管线，不重新判域/不受灰度影响）；空=全局入口按需路由")

class ChatResponse(BaseModel):
    answer: str
    session_id: str
    sources: list = Field(default_factory=list, description="来源文档列表")


class AbortRequest(BaseModel):
    session_id: str = Field("default", description="会话ID")
    request_id: str = Field("default", description="请求ID")


# ── SQL 查询 ─────────────────────────────────────

class SQLAskRequest(BaseModel):
    question: str = Field(..., description="自然语言数据查询", min_length=1, max_length=2000)
    # P1-11: 已废弃 — 该字段可被客户端伪造，服务端不再采用。
    # 行级安全的用户上下文改由可信网关头（X-User-Id，需 TRUST_USER_HEADER=true）推导。
    current_user_id: Optional[int] = Field(
        None, description="[已废弃] 用户身份由服务端从可信头推导，此字段被忽略", deprecated=True
    )


class SQLQueryResponse(BaseModel):
    """SQL 结构化查询结果（POST /sql/query）"""
    status: str = Field(..., description="success/no_data/failed/timeout/syntax_error/permission_denied/validation_error/no_table")
    answer: str = Field("", description="Markdown 表格（成功时）或错误信息（失败时）")
    columns: list = Field(default_factory=list, description="结果列名")
    rows: list = Field(default_factory=list, description="结果行（已脱敏）")
    row_count: int = Field(0, description="结果行数")
    elapsed_sec: float = Field(0.0, description="执行耗时（秒）")
    error: Optional[str] = Field(None, description="失败原因")
    error_type: Optional[str] = Field(None, description="错误子分类")
    sql: Optional[str] = Field(None, description="实际执行的 SQL（管理端核对 NL2SQL 结果用）")


# ── 管理端表浏览（GET /sql/tables*，2026-10-01）──
# 与 NL2SQL 同一安全栈（sql.read 预检 + SQLPolicyGuard + 只读连接池 +
# 列级脱敏），SQL 由服务端按白名单元数据拼装（无 LLM），供核对问答结果。

class BrowseColumn(BaseModel):
    name: str = Field(..., description="列名（敏感列不出现在目录中）")
    comment: str = Field("", description="列业务注释")


class BrowseTable(BaseModel):
    schema_name: str = Field(..., description="所属 schema（product/order/...）")
    name: str = Field(..., description="表名（不含 schema 前缀）")
    qualified_name: str = Field(..., description="全限定名，如 product.products")
    description: str = Field("", description="表业务说明")
    columns: list = Field(default_factory=list, description="可见列（BrowseColumn）")


class TableCatalogResponse(BaseModel):
    tables: list = Field(default_factory=list, description="白名单内全部业务表（BrowseTable）")


class TableBrowseResponse(BaseModel):
    """表分页浏览结果（GET /sql/tables/{schema}/{table}）"""
    qualified_name: str = Field(..., description="全限定表名")
    status: str = Field("success", description="success/no_data/failed/timeout/...")
    page: int = Field(1, description="页码（1 起）")
    page_size: int = Field(20, description="每页行数")
    total: int = Field(0, description="过滤后总行数")
    columns: list = Field(default_factory=list, description="可见列名（按声明序）")
    rows: list = Field(default_factory=list, description="结果行（已脱敏）")
    elapsed_sec: float = Field(0.0, description="执行耗时（秒）")
    error: Optional[str] = Field(None, description="失败原因")
    error_type: Optional[str] = Field(None, description="错误子分类")


# ── RAG 检索 ─────────────────────────────────────

class RAGAskRequest(BaseModel):
    question: str = Field(..., description="知识库检索问题", min_length=1, max_length=2000)
    session_id: str = Field("default", description="会话ID")
    kb_id: Optional[str] = Field(None, description="知识库ID（不传则默认 default）")


# ── 报告生成 ─────────────────────────────────────

class ReportRequest(BaseModel):
    report_type: str = Field(..., description="报告类型，如 monthly_sales / project_progress")
    filters: dict = Field(default_factory=dict, description="筛选条件")
    user_id: str = Field("default", description="用户标识（用于偏好学习）")
    polish: bool = Field(True, description="是否启用LLM语言润色")


# ── SSE 流式事件 ─────────────────────────────────

class SSEEvent(BaseModel):
    """SSE 流式输出的单个事件"""
    stage: str = Field(..., description="阶段: planning/supervising/executing/reporting/done/error")
    label: str = Field("", description="中文阶段名")
    message: str = Field("", description="详细描述")
    node: str = Field("", description="LangGraph 节点名")
    data: dict = Field(default_factory=dict, description="附带数据")


# ── 通用 ─────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
