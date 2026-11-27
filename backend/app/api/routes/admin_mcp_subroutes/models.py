"""admin_mcp_subroutes/models.py — MCP 管理控制台数据模型与常量。"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

# ── AI 语义契约约束常量（决策 #19）─────────────────────────────
SEMANTIC_SUMMARY_MAX = 300
SEMANTIC_ITEM_MAX = 200
SEMANTIC_LIST_MAX = 10
SEMANTIC_HINT_MAX = 128

# ── 敏感 query 参数黑名单（防 URL 内嵌凭据）────────────────────
_SENSITIVE_QUERY_KEYS = frozenset({
    "api_key", "api-key", "apikey", "access_key", "access_token",
    "auth_key", "auth_token", "authorization", "credential", "credentials",
    "id_token", "key", "passwd", "password", "pwd", "refresh_token",
    "secret", "secret_key", "session_token", "sig", "signature", "token",
})


class _MutationConflict(RuntimeError):
    """幂等回调内的业务冲突（携带可读原因，转 409）。"""


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=512)
    transport: Literal["streamable_http", "http_api"] = "streamable_http"
    auth_type: Literal["none", "static_bearer"] = "none"
    secret: str = Field(default="", max_length=4096)  # 空串=不配置凭据
    network_scope: Literal["public", "private"] = "public"
    enabled: bool = True


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    auth_type: Literal["none", "static_bearer"] | None = None
    # None=保持现有凭据；显式空串=清除
    secret: str | None = Field(default=None, max_length=4096)
    network_scope: Literal["public", "private"] | None = None
    enabled: bool | None = None


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    # 平台可信描述（默认沿用上游描述，审核者可改写；进入 Planner/FC prompt）
    description: str = Field(default="", max_length=500)
    governance: dict[str, Any] = Field(default_factory=dict)
    examples: list[str] = Field(default_factory=list, max_length=10)
    notes: str = Field(default="", max_length=2000)


class GrantRequest(BaseModel):
    product_key: Literal["agent", "customer_service"]
    revoke: bool = False


class TestInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    product_key: Literal["agent", "customer_service", "internal"] = "internal"


class HttpToolRegisterRequest(BaseModel):
    """HTTP API 工具手工登记（http_api 连接专用；MCP 连接走发现）。

    tool_name/description/input_schema/invocation 全部是不可信输入：
    content_hash 绑定后由审核放行，capability_slug 平台归一。
    """
    tool_name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    invocation: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class HttpToolInvocationUpdateRequest(BaseModel):
    """HTTP API 工具调用定义编辑：产生新快照版本；approved 工具即漂移。"""
    description: str = Field(default="", max_length=500)
    invocation: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class SemanticDraftRequest(BaseModel):
    """语义契约草稿编辑（决策 #19）：管理员手工保存/修正 AI 生成的候选。

    长度红线在此只做兜底（防超大 payload），面向用户的 400 校验在端点内
    按语义契约限制（summary≤300 / 单条≤200 / 条数≤10）执行。
    """
    summary: str = Field(default="", max_length=2000)
    scenarios: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    capability_hint: str | None = Field(default=None, max_length=256)
