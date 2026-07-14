"""
proxy.py — _LLMProxy 代理对象 + 模块级 llm 单例

核心设计:
  - 模块加载时初始化默认 Ollama LLM (_module_default_llm)
  - `llm` 是 _LLMProxy 代理；每次 .invoke()/.stream() 都委派给当前活跃模型
  - LLMFactory.set_current("deepseek-chat") 后，所有 `llm.invoke(...)` 自动走新模型
  - 12 个调用方 `from llm import llm` 无需修改
"""

from langchain_ollama import ChatOllama
from langchain_core.language_models.chat_models import BaseChatModel

from config import (
    LLM_MODEL, LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT,
)
from llm.factory import get_llm_factory
from utils.logger import logger


# =====================================================
# 模块级默认 LLM（启动时初始化一次）
# =====================================================

logger.info(f"正在初始化默认 LLM: {LLM_MODEL}")
try:
    _module_default_llm = ChatOllama(
        model=LLM_MODEL,
        temperature=LLM_TEMPERATURE,
        num_ctx=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
    )
    logger.info(
        f"LLM init OK: {LLM_MODEL} "
        f"(temperature={LLM_TEMPERATURE}, context={LLM_CONTEXT_LENGTH}, "
        f"timeout={LLM_REQUEST_TIMEOUT}s)"
    )
except Exception as e:
    logger.error(f"LLM 初始化失败: {e}")
    raise


def _resolve_active_llm() -> BaseChatModel:
    """返回当前生效的 LLM 实例：factory cache > module default。

    这是 _LLMProxy 内部委派的核心。每次调用都实时获取工厂实例，
    确保模型切换后立即生效。切换由 LLMFactory.set_current 内部的 self._lock 保护。
    """
    factory = get_llm_factory()  # 实时获取，不缓存引用
    if factory is not None:
        cached = factory._instance_cache.get(factory._current_model)
        if cached is not None:
            return cached
    return _module_default_llm


class _LLMProxy:
    """代理对象：将方法/属性访问委派给当前生效的 LLM 实例。

    设计目的：让 `from llm import llm` 在切换后仍生效。
    调用方写法完全不变：`llm.invoke(...)` / `llm.stream(...)` / `llm.bind_tools(...)`。
    每次访问属性都从 _resolve_active_llm() 拿最新模型。
    """
    __slots__ = ()

    def __getattr__(self, name: str):
        return getattr(_resolve_active_llm(), name)

    def __call__(self, *args, **kwargs):
        return _resolve_active_llm().invoke(*args, **kwargs)

    def __repr__(self) -> str:
        try:
            target = _resolve_active_llm()
            return f"<LLMProxy -> {type(target).__name__}({getattr(target, 'model', '?')})>"
        except Exception:
            return "<LLMProxy>"

    def __str__(self) -> str:
        return self.__repr__()


# 公开名：`llm` 现在是代理，调用方代码无需修改
llm = _LLMProxy()


def get_llm() -> BaseChatModel:
    """显式获取当前 LLM 实例（用于 evaluation/judge 等需要明确获取的代码路径）"""
    return _resolve_active_llm()


# =====================================================
# 追加工厂单例的兜底引用
# =====================================================
# factory.py 中的 LLMFactory.get_current() 返回 None 时兜底到 _module_default_llm。
# 在 proxy.py 加载完成后，给全局单例注入兜底引用。

def _patch_factory_default():
    """给工厂单例注入模块级默认 LLM 作为兜底"""
    factory = get_llm_factory()
    factory._module_default = _module_default_llm


_patch_factory_default()
