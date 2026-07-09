"""
factory.py — LLMFactory: 多 Provider 注册 + 运行时切换

核心职责:
  - 管理 Provider 注册表 + 可用模型清单
  - 运行时切换当前模型 (set_current)
  - 模型实例缓存 + 惰性构建
  - 余额查询（委托 providers/）

不包含:
  - 模块级 LLM 初始化（那是 proxy.py 的事）
  - _LLMProxy 代理对象（那是 proxy.py 的事）
"""

import threading
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from config import LLM_MODEL, DEEPSEEK_API_KEY
from llm.models import AVAILABLE_MODELS
from llm.providers.ollama import build_ollama, get_ollama_balance
from llm.providers.deepseek import build_deepseek, get_deepseek_balance
from utils.logger import logger


class LLMFactory:
    """多 Provider LLM 工厂，支持运行时切换全局当前模型。

    用法:
        factory = LLMFactory()
        factory.set_current("deepseek-chat")   # 切换全局模型
        model = factory.get_current()          # 获取当前实例
        balance = factory.get_balance("deepseek")  # 查余额
    """

    def __init__(self, module_default=None):
        self._lock = threading.Lock()
        self._current_model: str = LLM_MODEL
        self._instance_cache: dict = {}  # model_name → BaseChatModel 实例
        self._module_default = module_default  # 兜底 LLM（由 proxy.py 传入）

    # ---------------------------------------------------
    # 切换 / 获取
    # ---------------------------------------------------

    def get_current_model_name(self) -> str:
        """返回当前生效的模型名（字符串）"""
        return self._current_model

    def set_current(self, model_name: str) -> dict:
        """切换全局当前模型。切换后所有 `llm.invoke(...)` 自动走新模型。

        返回: {"ok": True, "model": "..."} 或 {"ok": False, "error": "..."}
        """
        # 校验
        if not any(m["name"] == model_name for m in AVAILABLE_MODELS):
            return {
                "ok": False,
                "error": f"未知模型: {model_name}",
                "available": [m["name"] for m in AVAILABLE_MODELS],
            }

        provider = self._get_provider(model_name)

        # API Key 校验
        if provider == "deepseek" and not DEEPSEEK_API_KEY:
            return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置，请在 .env 中设置"}

        # 预热实例化
        try:
            instance = self._build_instance(model_name)
        except Exception as e:
            logger.error(f"[LLMFactory] 切换到 {model_name} 失败: {e}")
            return {"ok": False, "error": f"模型实例化失败: {e}"}

        with self._lock:
            self._instance_cache[model_name] = instance
            self._current_model = model_name

        logger.info(f"[LLMFactory] 全局模型已切换: {self._current_model}")
        return {"ok": True, "model": model_name, "provider": provider}

    def get_current(self) -> BaseChatModel:
        """获取当前 LLM 实例（无锁，单读）"""
        with self._lock:
            return self._instance_cache.get(self._current_model) or self._module_default

    # ---------------------------------------------------
    # 实例化
    # ---------------------------------------------------

    def _build_instance(self, model_name: str) -> BaseChatModel:
        """根据模型名构建 LLM 实例（带缓存）"""
        if model_name in self._instance_cache:
            return self._instance_cache[model_name]

        provider = self._get_provider(model_name)

        if provider == "ollama":
            return build_ollama(model_name)
        elif provider == "deepseek":
            return build_deepseek(model_name)
        else:
            raise ValueError(f"未知 provider: {provider}")

    def _get_provider(self, model_name: str) -> str:
        """根据模型名推断 provider"""
        for m in AVAILABLE_MODELS:
            if m["name"] == model_name:
                return m["provider"]
        if "deepseek" in model_name:
            return "deepseek"
        return "ollama"

    # ---------------------------------------------------
    # 余额查询
    # ---------------------------------------------------

    def get_balance(self, provider: str = None) -> dict:
        """查询 provider 余额。

        返回: {"ok": True, "provider": "...", "balance": "...", ...}
        """
        if provider is None:
            provider = self._get_provider(self._current_model)

        if provider == "deepseek":
            return get_deepseek_balance()
        elif provider == "ollama":
            return get_ollama_balance()
        else:
            return {"ok": False, "error": f"不支持的 provider: {provider}"}


# 全局单例
_factory: Optional[LLMFactory] = None


def get_llm_factory() -> LLMFactory:
    """返回 LLMFactory 单例（懒加载，proxy.py 模块加载后设置 _module_default）"""
    global _factory
    if _factory is None:
        _factory = LLMFactory()
    return _factory
