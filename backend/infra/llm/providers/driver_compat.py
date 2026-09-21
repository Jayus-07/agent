"""driver_compat.py — 按协议驱动构建 LLM 实例（DB 自建供应商的通用出口）

proxy._build_llm_for 与 LLMFactory._build_instance 的内置分发表只认代码层
登记的厂商（deepseek / qwen / qwen_tp / vllm / siliconflow / minimax / …）。
管理端在供应商页新登记的自建供应商（custom-*）不在表里，此前的归宿是：

- proxy：落 ollama 兜底 —— 拿着 OpenAI 兼容地址去打 /api/chat（Ollama 协议
  路径）→ 上游 404 → 健康页归因「模型不存在」（2026-09-22 实测，
  ``ResponseError: (status code: 404)``）；
- factory：直接 raise「未知 provider」。

即「能测、能标可用、用不了」。这是 qwen_tp（2026-09-17）与 vllm 两例
同型缺陷的整类收口：**协议差异按供应商登记的 driver 分发，不再逐厂商
加 if 分支**。driver 与供应商页「测试连接」（provider_probe.build_probe_client）
同一口径（llm_providers.driver：openai | anthropic | ollama）。

凭据在调用时解析并显式传入（P1a-2 契约，同 providers/* 各构建器）。
"""

from __future__ import annotations

from backend.config import (
    LLM_CONTEXT_LENGTH,
    LLM_REQUEST_TIMEOUT,
    LLM_STREAM_USAGE,
    LLM_TEMPERATURE,
)
from backend.infra.llm.credentials import ProviderCredentials

SUPPORTED_DRIVERS = frozenset({"openai", "anthropic", "ollama"})


def build_by_driver(
    driver: str,
    model_name: str,
    credentials: ProviderCredentials | None,
):
    """按 driver 构建 LangChain 聊天模型实例。

    与 providers/* 各构建器同构：温度 / 超时 / max_tokens 取全局配置，
    api_key 与 base_url 只来自凭据（无 env 回退），extra_headers /
    extra_body 透传供应商登记的附加参数。
    """
    d = (driver or "").strip().lower()

    if d == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:  # pragma: no cover - 依赖由锁定文件保证
            raise ImportError(
                "openai 兼容驱动需要 langchain_openai 包，请 pip install langchain-openai"
            ) from e

        api_key = credentials.api_key if credentials is not None else ""
        base_url = credentials.base_url if credentials is not None else ""
        extra_body = dict(credentials.extra_body) if credentials and credentials.extra_body else None
        headers = (
            credentials.resolved_headers()
            if credentials is not None and credentials.extra_headers
            else None
        )
        return ChatOpenAI(
            model=model_name,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_CONTEXT_LENGTH,
            request_timeout=LLM_REQUEST_TIMEOUT,
            api_key=api_key or "EMPTY",  # 未设 Key 的自托管实例用占位符（同 vllm 约定）
            base_url=base_url,
            stream_usage=LLM_STREAM_USAGE,
            extra_body=extra_body,
            default_headers=headers,
        )

    if d == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "anthropic 兼容驱动需要 langchain_anthropic 包，请 pip install langchain-anthropic"
            ) from e

        api_key = credentials.api_key if credentials is not None else ""
        base_url = credentials.base_url if credentials is not None else ""
        headers = (
            credentials.resolved_headers()
            if credentials is not None and credentials.extra_headers
            else None
        )
        return ChatAnthropic(
            model=model_name,
            anthropic_api_key=api_key,
            anthropic_api_url=base_url or None,
            default_headers=headers,
            max_tokens=LLM_CONTEXT_LENGTH,
            temperature=LLM_TEMPERATURE,
            timeout=LLM_REQUEST_TIMEOUT,
        )

    if d == "ollama":
        from backend.infra.llm.providers.ollama import build_ollama

        return build_ollama(model_name, credentials)

    raise ValueError(
        f"不支持的协议驱动: {driver or '(空)'}（可选 openai / anthropic / ollama）"
    )
