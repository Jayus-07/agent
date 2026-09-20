"""embedding/rerank 协议适配器注册表。

前端只提交适配器名称；URL、认证头、最小请求体和成功响应判定全部在后端
收敛，避免管理端理解百炼、Jina 或 OpenAI 兼容协议的差异。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class AdapterRequest:
    """一次固定探测请求。"""

    url: str
    headers: dict[str, str]
    body: dict[str, Any]


class SpecializedAdapter:
    """专项适配器最小接口。"""

    name = ""

    def build_probe_request(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        options: Mapping[str, Any] | None = None,
    ) -> AdapterRequest:
        raise NotImplementedError

    def response_is_success(self, payload: Mapping[str, Any]) -> bool:
        raise NotImplementedError


def _bearer_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _join(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def infer_adapter(role: str, base_url: str) -> str:
    """根据模型用途和已登记地址选择专项协议适配器。

    供应商页不要求用户理解专项 HTTP 路径：Embedding 统一走 OpenAI
    兼容接口；Rerank 仅对 SiliconFlow 使用 Jina 协议，其余已支持的
    云端地址按 DashScope 原生重排协议处理。
    """
    normalized_role = str(role or "").strip().lower()
    if normalized_role == "embedding":
        return "openai_embedding"
    if normalized_role == "rerank":
        host = (urlparse(str(base_url or "")).hostname or "").lower()
        if host.endswith("siliconflow.cn"):
            return "jina_rerank"
        return "dashscope_rerank"
    raise ValueError(f"不支持的专项模型角色：{role}")


class OpenAIEmbeddingAdapter(SpecializedAdapter):
    name = "openai_embedding"

    def build_probe_request(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        options: Mapping[str, Any] | None = None,
    ) -> AdapterRequest:
        options = options or {}
        body: dict[str, Any] = {
            "model": model_name,
            "input": ["连接测试"],
            "encoding_format": "float",
        }
        dimensions = options.get("dimensions")
        if dimensions is not None:
            body["dimensions"] = int(dimensions)
        return AdapterRequest(
            url=_join(base_url, "embeddings"),
            headers=_bearer_headers(api_key),
            body=body,
        )

    def response_is_success(self, payload: Mapping[str, Any]) -> bool:
        data = payload.get("data")
        return isinstance(data, list) and bool(data)


class DashScopeEmbeddingAdapter(OpenAIEmbeddingAdapter):
    name = "dashscope_embedding"


class DashScopeRerankAdapter(SpecializedAdapter):
    name = "dashscope_rerank"

    def build_probe_request(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        options: Mapping[str, Any] | None = None,
    ) -> AdapterRequest:
        options = options or {}
        parameters: dict[str, Any] = {
            "top_n": int(options.get("top_n", 2)),
            "instruct": str(
                options.get(
                    "instruct",
                    "Given a web search query, retrieve relevant passages "
                    "that answer the query.",
                )
            ),
        }
        return AdapterRequest(
            url=_join(base_url, "services/rerank/text-rerank/text-rerank"),
            headers=_bearer_headers(api_key),
            body={
                "model": model_name,
                "input": {
                    "query": "连接测试",
                    "documents": ["连接测试相关文档", "无关文档"],
                },
                "parameters": parameters,
            },
        )

    def response_is_success(self, payload: Mapping[str, Any]) -> bool:
        output = payload.get("output")
        return isinstance(output, Mapping) and isinstance(output.get("results"), list)


class JinaRerankAdapter(SpecializedAdapter):
    name = "jina_rerank"

    def build_probe_request(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        options: Mapping[str, Any] | None = None,
    ) -> AdapterRequest:
        options = options or {}
        return AdapterRequest(
            url=_join(base_url, "rerank"),
            headers=_bearer_headers(api_key),
            body={
                "model": model_name,
                "query": "连接测试",
                "documents": ["候选文档一", "候选文档二"],
                "top_n": int(options.get("top_n", 2)),
            },
        )

    def response_is_success(self, payload: Mapping[str, Any]) -> bool:
        return isinstance(payload.get("results"), list)


ADAPTERS: dict[str, SpecializedAdapter] = {
    "dashscope_embedding": DashScopeEmbeddingAdapter(),
    "openai_embedding": OpenAIEmbeddingAdapter(),
    "dashscope_rerank": DashScopeRerankAdapter(),
    "jina_rerank": JinaRerankAdapter(),
}


def get_adapter(name: str) -> SpecializedAdapter:
    """按后端白名单取适配器。"""
    try:
        return ADAPTERS[str(name or "").strip()]
    except KeyError as exc:
        raise ValueError(
            f"未知专项协议适配器：{name}（支持 {', '.join(sorted(ADAPTERS))}）"
        ) from exc
