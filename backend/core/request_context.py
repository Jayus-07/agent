"""core/request_context.py — 权威请求上下文（身份字段唯一定义点）

统一上下文重构（2026-09-14）前，请求身份分裂在三套载体：
  - orchestration.request_context.RequestContext（随图状态显式传递）
  - rag.context.RequestContext（contextvars，与上者同名！）
  - tools.session 裸 contextvars
身份字段被迫多处定义（2026-09-14 加 department 时两处 dataclass 各加
一遍 + 写转换管道 + checkpoint 契约测试同步）。重构后：

  - 本模块的 RequestContext 是身份/会话/检索授权字段的唯一定义点；
  - orchestration 经图状态显式传递本类实例（执行链跨多层线程，ContextVar
    不跨线程继承——显式传递是有意设计，见 orchestration.request_context
    模块说明；节点入口 bind_from_state 还原后重新 bind）；
  - bind() 是 orchestration → RAG / tools / proxy 的唯一转换点：除绑定
    tools/proxy/tracer 的 ContextVar 外，把自身注入 RAG contextvars 层
    （rag.context.attach_identity，组合借读非复制）；
  - RAG 运行态（检索过滤/决策中间态）仍在 rag.context.RagRequestState，
    叶子层读取 API 形状不变。

bind 幂等 / stream_sink=None 显式清除陈旧 sink / bind_sink=False 不覆盖
等语义保持不变。
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable


# ── 工具层会话 ContextVar（原 tools.session 定义，收编至此统一由 bind() 写入）──
# 当前会话 ID，由 LangGraph workflow 在每次请求时设置
_current_session_id: ContextVar[str] = ContextVar("session_id", default="multi-agent-default")
# 当前请求用户身份：RequestContext.bind() 时设置，工具层读它做权限校验/审计归属。
# 与 proxy 的 _user_id_var（限流用）分离：工具层不依赖 infra 细节。
_current_user_id: ContextVar[str] = ContextVar("tool_user_id", default="")
# 当前请求租户身份：只接受入口解析后的可信值；空值表示未声明，
# 下游不得将其静默替换成共享租户。
_current_tenant_id: ContextVar[str] = ContextVar("tool_tenant_id", default="")
# 客户端幂等键（Idempotency-Key）；未提供时由业务工具从请求体指纹派生。
_current_idempotency_key: ContextVar[str] = ContextVar(
    "tool_idempotency_key", default=""
)
# 当前请求用户部门：检索侧授权（subject_type=employee 时决定可见知识库范围）
_current_department: ContextVar[str] = ContextVar("tool_department", default="")
# 当前请求持有的文档级权限；None 表示可信权限未声明，受限文档拒绝
_current_permissions: ContextVar[tuple[str, ...] | None] = ContextVar(
    "tool_permissions", default=None
)


def set_session_id(sid: str) -> None:
    _current_session_id.set(sid)


def _get_session_id() -> str:
    return _current_session_id.get()


def set_tool_user_id(user_id: str) -> None:
    _current_user_id.set(user_id or "")


def get_tool_user_id() -> str:
    """当前请求用户 ID；无上下文（MCP 直调/脚本）返回空串。"""
    return _current_user_id.get()


def set_tool_tenant_id(tenant_id: str) -> None:
    _current_tenant_id.set(tenant_id or "")


def get_tool_tenant_id() -> str:
    """当前请求租户 ID；无可信上下文返回空串。"""
    return _current_tenant_id.get()


def set_tool_idempotency_key(idempotency_key: str) -> None:
    _current_idempotency_key.set(idempotency_key or "")


def get_tool_idempotency_key() -> str:
    """当前请求客户端幂等键；无上下文返回空串。"""
    return _current_idempotency_key.get()


def set_tool_department(department: str) -> None:
    _current_department.set(department or "")


def get_tool_department() -> str:
    """当前请求用户部门；无上下文/未声明返回空串。"""
    return _current_department.get()


def set_tool_permissions(permissions: tuple[str, ...] | None) -> None:
    _current_permissions.set(
        None if permissions is None else tuple(sorted(set(permissions)))
    )


def get_tool_permissions() -> tuple[str, ...] | None:
    """当前请求持有的文档级权限；无可信上下文时返回 None。"""
    return _current_permissions.get()


@dataclass
class RequestContext:
    """一次用户请求的权威执行上下文（身份/会话/检索授权字段唯一定义点）。"""

    session_id: str = "default"
    user_id: str = "default"
    tenant_id: str = ""
    idempotency_key: str = ""
    kb_id: str = "default"
    # 员工部门（检索侧授权用）：请求体/网关注头带入；空 = 未声明，
    # RAG 工具按 fail-safe 以 customer 主体检索（对客最严格集合）
    department: str = ""
    # 主体类型（customer/employee）：检索授权第一属性，入口解析一次下游只读；
    # "" = 未声明主体 → 授权未启用（旧行为）。图路径由 RAG 工具层按
    # fail-safe 规则推导后经 pipeline.ask 声明（见 tools/rag.py）。
    subject_type: str = ""
    # 请求者持有的**文档级**权限集合（§4 权限范围，2026-09-17）：与 KB 级
    # authorized_kbs 互补的第二层。None = 未声明 → 受限文档 fail-safe 拒绝
    # （general 文档不受影响，存量语料无受限标记则行为完全不变）。
    # 见 backend/rag/permissions.py。
    permissions: tuple[str, ...] | None = None
    # TraceRecord 引用（不注具体类型：避免 observability ← core 导入环）
    trace: Any = None
    # 流式增量回调 sink(text: str) -> None；None = 非流式请求
    stream_sink: Callable[[str], None] | None = None
    # 按请求模型覆盖（空 = 用全局 LLM_MODEL；非法模型名在 bind 时被忽略）
    model: str = ""
    # dict 还原形态为 False：不覆盖当前线程已绑定的 sink/trace（防清掉 worker 主上下文）
    bind_sink: bool = True

    def bind(self) -> None:
        """把上下文绑定到当前线程的 ContextVar（节点入口 / worker 入口调用）。

        幂等：重复绑定同值无害；stream_sink=None 会显式清除陈旧 sink，
        防止线程池复用导致的跨请求串味（bind_sink=False 时不覆盖）。
        末尾把自身注入 RAG contextvars 层——orchestration → RAG 身份
        借读的唯一转换点。
        """
        from backend.infra.llm.proxy import (
            set_current_user_id, set_request_model, set_stream_sink,
        )
        from backend.infra.llm.budget import (
            bind_request_budget, clear_request_budget,
        )
        from backend.observability.tracer import trace_collector
        from backend.shared.logger import set_log_context
        from backend.rag.context import attach_identity

        if self.trace is not None:
            trace_collector.bind(self.trace)
            trace_id = str(getattr(self.trace, "id", "") or "")
            if trace_id:
                bind_request_budget(trace_id)
            else:
                clear_request_budget()
        elif self.bind_sink:
            clear_request_budget()
        set_session_id(self.session_id)
        set_current_user_id(self.user_id)
        set_tool_user_id(self.user_id)
        set_tool_tenant_id(self.tenant_id)
        set_tool_idempotency_key(self.idempotency_key)
        set_tool_department(self.department)
        set_tool_permissions(self.permissions)
        set_log_context(user_id=self.user_id)
        set_request_model(self.model)
        if self.bind_sink:
            set_stream_sink(self.stream_sink)
        attach_identity(self)

    def checkpoint_safe(self) -> dict:
        """checkpointer 序列化安全形态：剔除 trace/sink 等不可序列化对象。"""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "idempotency_key": self.idempotency_key,
            "kb_id": self.kb_id,
            "department": self.department,
            "subject_type": self.subject_type,
            "permissions": self.permissions,
            "model": self.model,
        }
