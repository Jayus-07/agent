# llm_factory.py
"""
LLM 工厂：多 Provider 支持 + 运行时切换

设计:
  - 保持向后兼容：模块级 `llm` 仍为默认 ChatOllama 单例
  - 新增 LLMFactory：支持 ollama / deepseek 等多 provider
  - 运行时切换：set_current_model() / get_current_model() / reset_llm()
  - 余额查询：get_provider_balance()（deepseek 实现）
"""

import os
import threading
from typing import Optional

from langchain_ollama import ChatOllama
from langchain_core.language_models.chat_models import BaseChatModel

from config import (
    LLM_MODEL, LLM_TEMPERATURE, LLM_CONTEXT_LENGTH, LLM_REQUEST_TIMEOUT,
    DEEPSEEK_API_KEY, DEEPSEEK_API_BASE,
)
from utils.logger import logger


# =====================================================
# 向后兼容：模块级 llm 单例（默认 ollama）
# =====================================================

logger.info(f"正在初始化默认 LLM: {LLM_MODEL}")
try:
    llm = ChatOllama(
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
    logger.error(f"❌ LLM 初始化失败: {e}")
    raise


def get_llm():
    """返回模块级 LLM 单例。用于需要惰性获取 LLM 的场景（如 evaluation judge）。"""
    return llm


# =====================================================
# LLMFactory: 多 Provider + 运行时切换
# =====================================================

class LLMFactory:
    """多 Provider LLM 工厂，支持运行时切换全局当前模型。

    用法:
        factory = LLMFactory()
        model = factory.get_current()                # 拿当前模型
        factory.set_current("deepseek-chat")         # 切换全局模型
        balance = factory.get_balance("deepseek")    # 查余额
    """

    # Provider 注册表：provider_name → (chat_class, default_model, env_key_for_api_key)
    PROVIDERS = {
        "ollama": {
            "class": ChatOllama,
            "default_model": "qwen2.5:3b",
            "needs_api_key": False,
        },
        "deepseek": {
            "class": None,  # 懒加载（避免引入 langchain_deepseek 依赖）
            "default_model": "deepseek-chat",
            "needs_api_key": True,
        },
    }

    # 可用模型清单（前端展示用）
    AVAILABLE_MODELS = [
        {
            "provider": "ollama",
            "name": "qwen2.5:3b",
            "display": "Qwen 2.5 (3B) - 本地",
            "description": "本地 Ollama，免费，无需 API Key",
        },
        {
            "provider": "ollama",
            "name": "qwen2.5:4b",
            "display": "Qwen 2.5 (4B) - 本地",
            "description": "本地 Ollama，免费",
        },
        {
            "provider": "deepseek",
            "name": "deepseek-chat",
            "display": "DeepSeek Chat - 云端",
            "description": "DeepSeek-V3，需要 API Key（.env 中配置 DEEPSEEK_API_KEY）",
        },
        {
            "provider": "deepseek",
            "name": "deepseek-reasoner",
            "display": "DeepSeek Reasoner - 云端",
            "description": "DeepSeek-R1 推理模型，需要 API Key",
        },
    ]

    def __init__(self):
        self._lock = threading.Lock()
        self._current_model: str = LLM_MODEL
        self._instance_cache: dict = {}  # model_name → BaseChatModel 实例

    # ---------------------------------------------------
    # 切换 / 获取
    # ---------------------------------------------------

    def get_current_model_name(self) -> str:
        """返回当前生效的模型名（字符串，如 'qwen2.5:3b' / 'deepseek-chat'）"""
        return self._current_model

    def set_current(self, model_name: str) -> dict:
        """切换全局当前模型。

        参数:
            model_name: 形如 'qwen2.5:3b' / 'deepseek-chat'

        返回:
            {"ok": True, "model": "..."} 或 {"ok": False, "error": "..."}
        """
        # 校验模型在可用列表里
        if not any(m["name"] == model_name for m in self.AVAILABLE_MODELS):
            return {
                "ok": False,
                "error": f"未知模型: {model_name}",
                "available": [m["name"] for m in self.AVAILABLE_MODELS],
            }

        provider = self._get_provider(model_name)

        # 校验 API Key
        if provider == "deepseek" and not DEEPSEEK_API_KEY:
            return {
                "ok": False,
                "error": "DEEPSEEK_API_KEY 未配置，请在 .env 中设置",
            }

        # 预热：实例化一次，失败立即报错
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
            return self._instance_cache.get(self._current_model) or llm

    # ---------------------------------------------------
    # 实例化
    # ---------------------------------------------------

    def _build_instance(self, model_name: str) -> BaseChatModel:
        """根据模型名构建 LLM 实例（带缓存）"""
        if model_name in self._instance_cache:
            return self._instance_cache[model_name]

        provider = self._get_provider(model_name)

        if provider == "ollama":
            instance = ChatOllama(
                model=model_name,
                temperature=LLM_TEMPERATURE,
                num_ctx=LLM_CONTEXT_LENGTH,
                request_timeout=LLM_REQUEST_TIMEOUT,
            )
        elif provider == "deepseek":
            # 懒加载：避免启动时强制依赖 langchain-deepseek
            try:
                from langchain_openai import ChatOpenAI  # DeepSeek 兼容 OpenAI 协议
            except ImportError as e:
                raise ImportError(
                    "deepseek provider 需要 langchain_openai 包，请 pip install langchain-openai"
                ) from e
            instance = ChatOpenAI(
                model=model_name,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_CONTEXT_LENGTH,
                request_timeout=LLM_REQUEST_TIMEOUT,
                api_key=DEEPSEEK_API_KEY,
                base_url=DEEPSEEK_API_BASE,
            )
        else:
            raise ValueError(f"未知 provider: {provider}")

        return instance

    def _get_provider(self, model_name: str) -> str:
        """根据模型名推断 provider"""
        for m in self.AVAILABLE_MODELS:
            if m["name"] == model_name:
                return m["provider"]
        # 启发式：未在列表中
        if "deepseek" in model_name:
            return "deepseek"
        return "ollama"

    # ---------------------------------------------------
    # 余额查询
    # ---------------------------------------------------

    def get_balance(self, provider: str = None) -> dict:
        """查询 provider 余额。

        返回:
            {"ok": True, "provider": "...", "balance": "0.5", "currency": "CNY"}
            或
            {"ok": False, "error": "..."}
        """
        if provider is None:
            provider = self._get_provider(self._current_model)

        if provider == "deepseek":
            return self._get_deepseek_balance()
        elif provider == "ollama":
            return {
                "ok": True,
                "provider": "ollama",
                "balance": "∞",
                "currency": "本地",
                "note": "Ollama 本地部署，不消耗云端余额",
            }
        else:
            return {"ok": False, "error": f"不支持的 provider: {provider}"}

    def _get_deepseek_balance(self) -> dict:
        """调 DeepSeek 官方余额查询 API"""
        if not DEEPSEEK_API_KEY:
            return {
                "ok": False,
                "error": "DEEPSEEK_API_KEY 未配置",
            }

        try:
            import requests
            resp = requests.get(
                f"{DEEPSEEK_API_BASE.rstrip('/')}/user/balance",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                timeout=10,
            )
            if resp.status_code != 200:
                return {
                    "ok": False,
                    "error": f"DeepSeek API 返回 {resp.status_code}: {resp.text[:200]}",
                }

            body = resp.json()
            # DeepSeek 官方返回结构：{"balance_available": "10.50", ...}
            if not body.get("is_available", True):
                return {
                    "ok": False,
                    "error": "DeepSeek 账户余额不足",
                    "raw": body,
                }

            return {
                "ok": True,
                "provider": "deepseek",
                "balance": body.get("balance_available", "未知"),
                "currency": "CNY",
                "raw": body,
            }
        except Exception as e:
            logger.error(f"[LLMFactory] DeepSeek 余额查询失败: {e}")
            return {"ok": False, "error": f"请求失败: {e}"}


# 全局单例
_factory: Optional[LLMFactory] = None


def get_llm_factory() -> LLMFactory:
    """返回 LLMFactory 单例（懒加载）"""
    global _factory
    if _factory is None:
        _factory = LLMFactory()
    return _factory
