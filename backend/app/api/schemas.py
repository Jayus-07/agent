"""API 层 — Pydantic 请求/响应模型"""
from pydantic import BaseModel, Field
from typing import Optional


# ── 对话（Multi-Agent）───────────────────────────

class ChatRequest(BaseModel):
    question: str = Field(..., description="用户问题", min_length=1, max_length=2000)
    session_id: str = Field("default", description="会话ID，同一会话内记忆持久化")
    kb_id: Optional[str] = Field(None, description="知识库ID（policy/tech/finance/hr 等，默认 default）")
    request_id: Optional[str] = Field("default", description="请求ID，用于中止信号路由")

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
