"""
proxy.py — _LLMProxy 代理对象 + 模块级 llm 单例

核心设计:
  - 懒加载：首次调用时才初始化 LLM，根据 LLM_MODEL 自动选择 Provider
  - `llm` 是 _LLMProxy 代理；每次 .invoke()/.stream() 都委派给当前活跃模型
  - LLMFactory.set_current("deepseek-chat") 后，所有 `llm.invoke(...)` 自动走新模型
  - 所有调用方 `from backend.infra.llm import llm` 无需修改
  - 每次调用记录 token + finish_reason + cost_usd 到模块级 _last_call_meta（供 tracer 读取）
"""
import inspect
import threading

from langchain_core.language_models.chat_models import BaseChatModel

from backend.config import LLM_MODEL
from backend.infra.llm.factory import get_llm_factory
from backend.infra.llm.models import AVAILABLE_MODELS, PROVIDERS, compute_cost_usd
from backend.shared.logger import logger


# =====================================================
# 懒加载默认 LLM
# =====================================================

_default_llm = None
_default_lock = threading.Lock()

# PR-0.4: 当前请求的 user_id（限流用）— 调用方可通过 set_current_user_id() 设置
_user_lock = threading.Lock()
_current_user_id: str | None = None


def set_current_user_id(user_id: str | None) -> None:
    """设置当前线程/请求的 user_id（限流用）。FastAPI 路由层在每次请求开始时调用。"""
    global _current_user_id
    with _user_lock:
        _current_user_id = user_id


def _thread_local_user_id() -> str | None:
    """读取当前 user_id（限流用）。"""
    with _user_lock:
        return _current_user_id


def _get_provider_for(model_name: str) -> str:
    """根据模型名查找所属 provider"""
    for m in AVAILABLE_MODELS:
        if m["name"] == model_name:
            return m["provider"]
    return "ollama"  # 兜底


def _build_default_llm():
    """根据 LLM_MODEL 构建正确的 Provider 实例"""
    global _default_llm
    if _default_llm is not None:
        return _default_llm

    with _default_lock:
        if _default_llm is not None:
            return _default_llm

        logger.info(f"正在初始化默认 LLM: {LLM_MODEL}")
        provider = _get_provider_for(LLM_MODEL)

        if provider == "ollama":
            from langchain_ollama import ChatOllama
            from backend.config import LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT
            _default_llm = ChatOllama(
                model=LLM_MODEL,
                temperature=LLM_TEMPERATURE,
                num_ctx=LLM_CONTEXT_LENGTH,
                request_timeout=LLM_REQUEST_TIMEOUT,
            )
        elif provider == "deepseek":
            from backend.infra.llm.providers.deepseek import build_deepseek
            _default_llm = build_deepseek(LLM_MODEL)
        elif provider == "minimax":
            from backend.infra.llm.providers.minimax import build_minimax
            _default_llm = build_minimax(LLM_MODEL)
        else:
            # 兜底：Ollama
            from langchain_ollama import ChatOllama
            from backend.config import LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT
            _default_llm = ChatOllama(
                model=LLM_MODEL,
                temperature=LLM_TEMPERATURE,
                num_ctx=LLM_CONTEXT_LENGTH,
                request_timeout=LLM_REQUEST_TIMEOUT,
            )

        logger.info(f"LLM init OK: {LLM_MODEL} (provider={provider})")
        return _default_llm


def _resolve_active_llm() -> BaseChatModel:
    """返回当前生效的 LLM 实例：factory 缓存 > 懒加载默认。

    每次调用都实时获取工厂实例，确保模型切换后立即生效。
    """
    factory = get_llm_factory()
    if factory is not None:
        cached = factory._instance_cache.get(factory._current_model)
        if cached is not None:
            return cached
    return _build_default_llm()


# =====================================================
# <think> 剥离（防御性，正规方案是 Provider 传 reasoning_split）
# =====================================================

def _strip_think(text: str) -> str:
    """剥离模型输出的 <think>...</think> 推理块"""
    import re
    text = re.sub(r'<think>[\s\S]*?</think>\s*', '', text)
    text = re.sub(r'<think>[\s\S]*', '', text)
    return text.strip()


# =====================================================
# 最近一次 LLM 调用元数据（token / finish_reason / cost）
# =====================================================
# P1 并发隔离：模块级 dict 会被并发请求互相覆盖（A 的 token 被 B 覆盖，
# tracer 读到 B 的）。改用 ContextVar 按调用上下文隔离；
# 读写必须整体 set()/get()（不可变替换），禁止对 .get() 返回的 dict
# 做 clear()/update()（会改到共享对象）。
import contextvars as _contextvars

_last_tokens_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_last_tokens", default={},
)
_last_call_meta_var: _contextvars.ContextVar = _contextvars.ContextVar(
    "llm_last_call_meta", default={},
)


def _record_tokens(result):
    """从 LLM 返回值提取 token + finish_reason + cost，存为 dict 供 tracer 读取。

    无 token_usage 时清空 _last_tokens_var 和 _last_call_meta_var。
    """
    try:
        tu = {}
        if hasattr(result, "response_metadata") and result.response_metadata:
            tu = result.response_metadata.get("token_usage", {})
        if not tu and hasattr(result, "usage_metadata") and result.usage_metadata:
            tu = result.usage_metadata
        # ChatAnthropic 用 input_tokens/output_tokens，ChatOpenAI 用 prompt_tokens/completion_tokens
        p = tu.get("prompt_tokens", tu.get("input_tokens", 0))
        c = tu.get("completion_tokens", tu.get("output_tokens", 0))
        t = tu.get("total_tokens", p + c)
        if not t:
            _last_tokens_var.set({})
            _last_call_meta_var.set({})
            return
        _last_tokens_var.set({
            "prompt_tokens": p, "completion_tokens": c, "total_tokens": t,
        })

        # Prometheus 指标：LLM token 用量
        try:
            from backend.observability.metrics import llm_tokens_total
            model = result.response_metadata.get("model_name", "") if hasattr(result, "response_metadata") and result.response_metadata else ""
            if model and p:
                llm_tokens_total.labels(model=model, direction="prompt").inc(p)
            if model and c:
                llm_tokens_total.labels(model=model, direction="completion").inc(c)
        except Exception:
            pass  # 指标记录失败不影响核心链路

        finish_reason = "unknown"
        if hasattr(result, "response_metadata") and result.response_metadata:
            finish_reason = result.response_metadata.get(
                "finish_reason",
                result.response_metadata.get("stop_reason", "unknown"),
            )
        cost = compute_cost_usd(LLM_MODEL, p, c)
        _last_call_meta_var.set({
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": t,
            "finish_reason": finish_reason,
            "cost_usd": cost,
        })
    except Exception:
        _last_tokens_var.set({})
        _last_call_meta_var.set({})

def _wrap_result(result):
    """递归剥离 LLM 返回值中的 <think> 块，兼容 str / AIMessage / list / dict"""
    if isinstance(result, str):
        return _strip_think(result)
    if hasattr(result, 'content') and isinstance(result.content, str):
        result.content = _strip_think(result.content)
    if isinstance(result, list):
        return [_wrap_result(r) for r in result]
    if isinstance(result, dict):
        return {k: _wrap_result(v) for k, v in result.items()}
    return result


# =====================================================
# 代理对象
# =====================================================

class _LLMProxy:
    """代理对象：每次调用都实时委派给当前活跃 LLM，并全局剥离 <think> 块"""

    __slots__ = ()
    _WRAP_METHODS = {'invoke', 'ainvoke', 'generate', 'agenerate', 'batch', 'stream', 'astream'}

    def __getattr__(self, name: str):
        target = _resolve_active_llm()
        attr = getattr(target, name)
        if name in self._WRAP_METHODS and callable(attr):
            # async generator（astream）：保持透传，不包 token 记录
            # （逐 chunk 流式 token 语义本次不覆盖；await async_gen 会抛 TypeError）
            if inspect.isasyncgenfunction(attr):
                return attr
            # async 方法（ainvoke/agenerate）：coroutine 必须先 await 才能取结果，
            # 否则 _record_tokens 作用在未执行的 coroutine 上会把 token 清空（既有 bug）。
            if inspect.iscoroutinefunction(attr):
                async def async_wrapper(*args, **kwargs):
                    # 限流（同步 acquire）+ 熔断（async）
                    from backend.infra.llm.rate_limiter import get_rate_limiter
                    from backend.infra.circuit_breaker import llm_circuit_breaker
                    user_id = kwargs.get("user_id") or _thread_local_user_id()
                    get_rate_limiter().acquire(user_id=user_id)
                    result = await llm_circuit_breaker.acall(attr, *args, **kwargs)
                    _record_tokens(result)
                    return _wrap_result(result)
                return async_wrapper
            def wrapper(*args, **kwargs):
                # 限流 + 熔断
                from backend.infra.llm.rate_limiter import get_rate_limiter
                from backend.infra.circuit_breaker import llm_circuit_breaker
                user_id = kwargs.get("user_id") or _thread_local_user_id()
                get_rate_limiter().acquire(user_id=user_id)
                result = llm_circuit_breaker.call(attr, *args, **kwargs)
                _record_tokens(result)
                return _wrap_result(result)
            return wrapper
        return attr

    def __call__(self, *args, **kwargs):
        # 限流 + 熔断
        from backend.infra.llm.rate_limiter import get_rate_limiter
        from backend.infra.circuit_breaker import llm_circuit_breaker
        user_id = kwargs.get("user_id") or _thread_local_user_id()
        get_rate_limiter().acquire(user_id=user_id)
        result = llm_circuit_breaker.call(_resolve_active_llm().invoke, *args, **kwargs)
        _record_tokens(result)
        return _wrap_result(result)

    def __repr__(self) -> str:
        try:
            target = _resolve_active_llm()
            return f"<LLMProxy -> {type(target).__name__}({getattr(target, 'model', '?')})>"
        except Exception:
            return "<LLMProxy>"

    def __str__(self) -> str:
        return self.__repr__()


# 公开名
llm = _LLMProxy()


def get_llm() -> BaseChatModel:
    """显式获取当前 LLM 实例"""
    return _resolve_active_llm()
