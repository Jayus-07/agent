"""request_context.py — 请求上下文的图状态管道（显式传递 + 节点入口还原）

权威 RequestContext 定义已收编至 backend.core.request_context（2026-09-14
统一上下文重构：身份字段唯一定义点 + 统一 bind()，bind 同时完成
orchestration → RAG 的身份借读注入）。本模块保留图状态管道，职责不变：

问题背景（显式传递存在的原因，有意设计，勿删）:
  执行链路跨多层线程（chat SSE executor → graph worker → LangGraph Send
  线程池），新线程不继承 ContextVar，漏绑即静默故障：span 落 noop、
  并行 Skill 分支不流式、限流扣错用户。

方案:
  - RequestContext 实例随图状态显式流动（state key: "request_context"）；
  - Supervisor Send 派发时透传该 key → 并行 Skill 分支天然可达；
  - 每个节点入口（trace_middleware 统一收口）经 bind_from_state 重新
    绑定 ContextVar，绑定幂等；
  - ContextVar 仅保留给 FastAPI / proxy / tracer 层的读取方，不再依赖
    跨线程继承。

注意: dataclass 实例直接放入 state——若主图开启 checkpointer
（MAIN_GRAPH_CHECKPOINTER_ENABLED），trace/stream_sink 不可序列化，
此时 runner 会改存 checkpoint_safe() 的纯字段 dict，
get_context_from_state 负责还原（bind_sink=False：不覆盖当前线程
已绑定的 sink/trace，Send 并行分支的流式/trace 降级为已知限制）。
"""
from backend.core.request_context import RequestContext  # noqa: F401


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
            subject_type=ctx.get("subject_type", ""),
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
