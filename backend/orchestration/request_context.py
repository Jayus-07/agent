"""request_context.py — 请求级执行上下文（显式传递 + 节点入口绑定）

问题背景:
  请求上下文（trace / session_id / user_id / 流式 sink）此前散落在多个
  独立 ContextVar 中，依赖"线程环境自动继承"。但执行链路跨多层线程
  （chat SSE executor → graph worker → LangGraph Send 线程池），新线程
  不继承 ContextVar，漏绑即静默故障：span 落 noop、并行 Skill 分支不
  流式、限流扣错用户。

方案（显式上下文）:
  - RequestContext dataclass 集中持有上下文值，随图状态显式流动
    （state key: "request_context"）；
  - Supervisor Send 派发时透传该 key → 并行 Skill 分支天然可达；
  - 每个节点入口（trace_middleware 统一收口）从 state 重新绑定
    ContextVar，绑定幂等：proxy/tracer 的 ContextVar set 本身覆盖语义；
  - ContextVar 仅保留给 FastAPI / proxy / tracer 层的读取方，不再依赖
    跨线程继承。

注意: dataclass 实例直接放入 state——若主图开启 checkpointer
（MAIN_GRAPH_CHECKPOINTER_ENABLED），trace/stream_sink 不可序列化，
此时 runner 会改存 checkpoint_safe() 的纯字段 dict，
get_context_from_state 负责还原（bind_sink=False：不覆盖当前线程
已绑定的 sink/trace，Send 并行分支的流式/trace 降级为已知限制）。
"""
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class RequestContext:
    """一次用户请求的执行上下文，随图状态显式传递。"""

    session_id: str = "default"
    user_id: str = "default"
    kb_id: str = "default"
    # 员工部门（检索侧授权用）：请求体/网关注头带入；空 = 未声明，
    # RAG 工具按 fail-safe 以 customer 主体检索（对客最严格集合）
    department: str = ""
    # TraceRecord 引用（不注具体类型：避免 observability ← orchestration 导入环）
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
        """
        from backend.infra.llm.proxy import (
            set_current_user_id, set_request_model, set_stream_sink,
        )
        from backend.observability.tracer import trace_collector
        from backend.shared.logger import set_log_context
        from backend.tools import set_session_id, set_tool_user_id, set_tool_department

        if self.trace is not None:
            trace_collector.bind(self.trace)
        set_session_id(self.session_id)
        set_current_user_id(self.user_id)
        set_tool_user_id(self.user_id)
        set_tool_department(self.department)
        set_log_context(user_id=self.user_id)
        set_request_model(self.model)
        if self.bind_sink:
            set_stream_sink(self.stream_sink)

    def checkpoint_safe(self) -> dict:
        """checkpointer 序列化安全形态：剔除 trace/sink 等不可序列化对象。"""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "kb_id": self.kb_id,
            "department": self.department,
            "model": self.model,
        }


def put_context(state: dict, ctx: RequestContext) -> None:
    """把 RequestContext 写入图初始状态（make_initial_state 后调用）。"""
    state["request_context"] = ctx


def get_context_from_state(state: dict | None) -> RequestContext | None:
    """从节点输入状态取 RequestContext；无（旧路径/子图/测试假 state）返回 None。

    兼容 checkpoint 安全 dict 形态（主图开 checkpointer 时 runner 存的是
    checkpoint_safe() 的纯字段 dict）——还原时 trace/sink 为空且不覆盖绑定。
    """
    if not state:
        return None
    ctx = state.get("request_context")
    if isinstance(ctx, RequestContext):
        return ctx
    if isinstance(ctx, dict):
        return RequestContext(
            session_id=ctx.get("session_id", "default"),
            user_id=ctx.get("user_id", "default"),
            kb_id=ctx.get("kb_id", "default"),
            department=ctx.get("department", ""),
            model=ctx.get("model", ""),
            trace=None,
            stream_sink=None,
            bind_sink=False,
        )
    return None


def bind_from_state(state: dict | None) -> None:
    """节点入口快捷方式：state 携带上下文则绑定，否则不动环境。

    "否则不动"很重要：ask()/CS 子图等无 state 上下文的路径依赖
    调用方（system.py worker / CS 适配器线程）的环境绑定，清除反而破坏。
    """
    ctx = get_context_from_state(state)
    if ctx is not None:
        ctx.bind()
