"""
factory.py — LLMFactory: 多 Provider 注册 + 运行时切换

核心职责:
  - 管理 Provider 注册表 + 可用模型清单（统一走 models.get_available_models）
  - 运行时切换当前模型 (set_current)
  - 模型实例缓存 + 惰性构建 + **缓存失效** (invalidate)
  - 余额查询（委托 providers/）

不包含:
  - 模块级 LLM 初始化（那是 proxy.py 的事）
  - _LLMProxy 代理对象（那是 proxy.py 的事）

凭据解析已收敛到 `infra/llm/credentials.py`：本模块**不再持有任何 API Key 常量** ——
否则「管理端改了密钥、工厂读的仍是导入时拷进来的旧值」，UI 会形同虚设
（设计 B.5#1 / #5）。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

# 注：BaseChatModel 仅作类型标注（TYPE_CHECKING 化）—— langchain_core 1.4.x 的
# chat_models 模块在环境装了 transformers 时会连带导入 torch（实测 ~8s），
# 而本模块处在 backend.infra.llm 的高频导入链上。
# providers/* 的延迟导入同理（langchain_openai / langchain_ollama 顶层导入重）。
if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

from backend.config import LLM_MODEL
from backend.infra.llm.credentials import (
    UnknownProviderError,
    check_provider_usable,
    credentials_version,
    resolve_credentials,
)
from backend.infra.llm.models import get_available_models, resolve_provider
from backend.shared.logger import logger


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

    def available_models(self) -> list[dict]:
        """当前生效的可用模型清单（代码层 + DB 覆盖层，见 models.get_available_models）。"""
        return get_available_models()

    def set_current(self, model_name: str) -> dict:
        """切换全局当前模型。切换后所有 `llm.invoke(...)` 自动走新模型。

        返回: {"ok": True, "model": "..."} 或 {"ok": False, "error": "..."}
        """
        models = get_available_models()

        # 校验
        if not any(m["name"] == model_name for m in models):
            return {
                "ok": False,
                "error": f"未知模型: {model_name}",
                "available": [m["name"] for m in models],
            }

        provider = self._get_provider(model_name)

        # 可用性校验：密钥缺失 / cloud 模式禁用本地 Ollama / 未知 provider。
        # 收敛自原先 8 个硬编码 if —— 那种写法会让自建 provider 被**静默跳过**校验，
        # 切过去之后调用时才 401（设计 B.5#5）。
        unavailable = check_provider_usable(provider)
        if unavailable:
            result = {"ok": False, "error": unavailable}
            if provider == "ollama":
                # 保持历史返回形状：本地推理被禁用时附带可选模型清单
                result["available"] = [m["name"] for m in models]
            return result

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
        # 凭据在**这里**解析（调用时），而不是在 provider 模块导入时 —— 见文件头注释
        credentials = resolve_credentials(provider, model_name=model_name)

        # providers/* 延迟导入（见文件顶部注释）
        if provider == "ollama":
            from backend.infra.llm.providers.ollama import build_ollama
            return build_ollama(model_name, credentials)
        elif provider == "deepseek":
            from backend.infra.llm.providers.deepseek import build_deepseek
            return build_deepseek(model_name, credentials)
        elif provider == "minimax":
            from backend.infra.llm.providers.minimax import build_minimax
            return build_minimax(model_name, credentials)
        elif provider == "qwen":
            from backend.infra.llm.providers.qwen import build_qwen
            return build_qwen(model_name, credentials)
        elif provider == "qwen_tp":
            from backend.infra.llm.providers.qwen_tp import build_qwen_tp
            return build_qwen_tp(model_name, credentials)
        elif provider == "vllm":
            from backend.infra.llm.providers.vllm import build_vllm
            return build_vllm(model_name, credentials)
        elif provider == "siliconflow":
            from backend.infra.llm.providers.siliconflow import build_siliconflow
            return build_siliconflow(model_name, credentials)
        else:
            raise ValueError(f"未知 provider: {provider}")

    def _get_provider(self, model_name: str) -> str:
        """根据模型名推断 provider。

        委托 `models.resolve_provider`（统一入口，含 DB 覆盖层）。
        **保持历史语义**：注册表未命中时按名称启发式推断，仍判不出则按本地
        Ollama 处理并记一次 warning —— 详见 `resolve_provider` docstring 中
        「为什么暂不 fail-closed」（本地 Ollama 模型名不可穷举）。
        """
        return resolve_provider(model_name)

    # ---------------------------------------------------
    # 缓存失效 / 凭据版本（设计 B.5#2）
    # ---------------------------------------------------

    def invalidate(self, model_name: str | None = None) -> None:
        """清除模型实例缓存。凭据变更 / 密钥轮换后**必须**调用。

        `model_name=None` 清全部。不清缓存会让用户看到「测试通过了、线上仍用旧
        key，且不报任何错」—— 这是本仓库最难查的一类问题。
        """
        with self._lock:
            if model_name is None:
                self._instance_cache.clear()
            else:
                self._instance_cache.pop(model_name, None)
        logger.info("[LLMFactory] 实例缓存已失效: %s", model_name or "全部")

    def key_version(self, provider: str | None = None) -> int:
        """当前凭据版本（DB 轮换计数）。

        与缓存实例的构建时版本比对即可发现「密钥已换但仍用旧实例」。
        本阶段无 DB 覆盖层 → 恒 0。
        """
        if provider is None:
            provider = self._get_provider(self._current_model)
        return credentials_version(provider)

    # ---------------------------------------------------
    # 余额查询
    # ---------------------------------------------------

    def get_balance(self, provider: str = None) -> dict:
        """查询 provider 余额。

        返回: {"ok": True, "provider": "...", "balance": "...", ...}
        """
        if provider is None:
            provider = self._get_provider(self._current_model)

        try:
            credentials = resolve_credentials(provider)
        except UnknownProviderError:
            return {"ok": False, "error": f"不支持的 provider: {provider}"}

        # providers/* 延迟导入（见文件顶部注释）
        if provider == "deepseek":
            from backend.infra.llm.providers.deepseek import get_deepseek_balance
            return get_deepseek_balance(credentials)
        elif provider == "minimax":
            from backend.infra.llm.providers.minimax import get_minimax_balance
            return get_minimax_balance(credentials)
        elif provider == "qwen":
            from backend.infra.llm.providers.qwen import get_qwen_balance
            return get_qwen_balance(credentials)
        elif provider == "qwen_tp":
            from backend.infra.llm.providers.qwen_tp import get_qwen_tp_balance
            return get_qwen_tp_balance(credentials)
        elif provider == "ollama":
            from backend.infra.llm.providers.ollama import get_ollama_balance
            return get_ollama_balance(credentials)
        elif provider == "vllm":
            from backend.infra.llm.providers.vllm import get_vllm_balance
            return get_vllm_balance(credentials)
        elif provider == "siliconflow":
            from backend.infra.llm.providers.siliconflow import get_siliconflow_balance
            return get_siliconflow_balance(credentials)
        else:
            return {"ok": False, "error": f"不支持的 provider: {provider}"}

    async def get_balance_async(self, provider: str = None) -> dict:
        """get_balance 的异步版（P1-16）— 供 async 路由调用，不阻塞事件循环。

        DeepSeek 走 httpx 原生异步；其余 provider 尚无异步实现，
        丢线程池执行（to_thread）避免阻塞 event loop。
        """
        if provider is None:
            provider = self._get_provider(self._current_model)

        if provider == "deepseek":
            from backend.infra.llm.providers.deepseek import get_deepseek_balance_async
            try:
                credentials = resolve_credentials(provider)
            except UnknownProviderError:
                return {"ok": False, "error": f"不支持的 provider: {provider}"}
            return await get_deepseek_balance_async(credentials)

        import asyncio
        return await asyncio.to_thread(self.get_balance, provider)


# 全局单例
_factory: Optional[LLMFactory] = None


def get_llm_factory() -> LLMFactory:
    """返回 LLMFactory 单例（懒加载，proxy.py 模块加载后设置 _module_default）"""
    global _factory
    if _factory is None:
        _factory = LLMFactory()
    return _factory
