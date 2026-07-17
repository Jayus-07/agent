"""
proxy.py — _LLMProxy 代理对象 + 模块级 llm 单例

核心设计:
  - 懒加载：首次调用时才初始化 LLM，根据 LLM_MODEL 自动选择 Provider
  - `llm` 是 _LLMProxy 代理；每次 .invoke()/.stream() 都委派给当前活跃模型
  - LLMFactory.set_current("deepseek-chat") 后，所有 `llm.invoke(...)` 自动走新模型
  - 所有调用方 `from backend.infra.llm import llm` 无需修改
  - 每次调用记录 token + finish_reason + cost_usd 到模块级 _last_call_meta（供 tracer 读取）
"""
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


_last_tokens = {}
_last_call_meta = {}  # token + finish_reason + cost_usd（供 tracer 读取，Phase 4）


def _record_tokens(result):
    """从 LLM 返回值提取 token + finish_reason + cost，存为 dict 供 tracer 读取。

    无 token_usage 时清空 _last_tokens 和 _last_call_meta。
    """
    global _last_tokens, _last_call_meta
    try:
        tu = {}
        if hasattr(result, "response_metadata") and result.response_metadata:
            tu = result.response_metadata.get("token_usage", {})
        if not tu and hasattr(result, "usage_metadata") and result.usage_metadata:
            tu = result.usage_metadata
        p = tu.get("prompt_tokens", 0)
        c = tu.get("completion_tokens", 0)
        t = tu.get("total_tokens", p + c)
        if not t:
            _last_tokens = {}
            _last_call_meta = {}
            return
        _last_tokens = {"prompt_tokens": p, "completion_tokens": c, "total_tokens": t}

        # Phase 4: 提取 finish_reason + cost_usd
        finish_reason = "unknown"
        if hasattr(result, "response_metadata") and result.response_metadata:
            finish_reason = result.response_metadata.get(
                "finish_reason",
                result.response_metadata.get("stop_reason", "unknown"),
            )
        cost = compute_cost_usd(LLM_MODEL, p, c)
        _last_call_meta = {
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": t,
            "finish_reason": finish_reason,
            "cost_usd": cost,
        }
    except Exception:
        _last_tokens = {}
        _last_call_meta = {}

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
            def wrapper(*args, **kwargs):
                result = attr(*args, **kwargs)
                _record_tokens(result)
                return _wrap_result(result)
            return wrapper
        return attr

    def __call__(self, *args, **kwargs):
        result = _resolve_active_llm().invoke(*args, **kwargs)
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
