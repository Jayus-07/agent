"""模型治理中心 - 基础类型、常量、通用辅助与依赖代理。"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from collections.abc import Mapping
from urllib.parse import urlparse

HISTORY_OBJECTS = {
    "role",
    "provider",
    "provider_credential",
    "provider_network_scope",
    "role_policy",
}


class ModelConfigNotFound(LookupError):
    """请求的角色、供应商或历史不存在。"""


class ModelConfigConflict(RuntimeError):
    """配置不能按请求执行（例如密钥历史不可回滚）。"""


_SPECIALIZED_ROLES = {"embedding", "rerank"}

# 模型用途 → model_price.component（计价口径）。chat/vision/speech 都按 llm 计。
# 保存路径探测的 L2 失败退避重试（2026-09-22 实测：中转站 ~1 次/分钟限流，
# 「测试连接 + 测试并保存」背靠背必撞）。0 = 不重试。
_PROBE_SAVE_RETRY_BACKOFF = float(
    os.getenv("PROBE_SAVE_RETRY_BACKOFF_SECONDS", "35") or "35"
)

_COMPONENT_BY_MODEL_KIND = {
    "chat": "llm",
    "vision": "llm",
    "speech": "llm",
    "ocr": "llm",
    "embedding": "embedding",
    "rerank": "rerank",
}


class DependencyProxy:
    """透明依赖代理：属性访问和调用优先从 backend.services.model_config 门面获取，
    以无缝感知测试中的 monkeypatch.setattr(model_config, ...)。
    """

    def __init__(self, name: str, fallback: Any):
        super().__setattr__("_name", name)
        super().__setattr__("_fallback", fallback)

    def _target(self) -> Any:
        facade = sys.modules.get("backend.services.model_config")
        if facade is not None and hasattr(facade, self._name):
            val = getattr(facade, self._name)
            if val is not self:
                return val
        return self._fallback

    def __getattr__(self, item: str) -> Any:
        return getattr(self._target(), item)

    def __setattr__(self, key: str, value: Any) -> None:
        setattr(self._target(), key, value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._target()(*args, **kwargs)

    def __bool__(self) -> bool:
        return bool(self._target())

    def __eq__(self, other: Any) -> bool:
        return self._target() == other

    def __str__(self) -> str:
        return str(self._target())

    def __repr__(self) -> str:
        return repr(self._target())

    def __iter__(self) -> Any:
        return iter(self._target())

    def __getitem__(self, item: Any) -> Any:
        return self._target()[item]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    method = getattr(value, "isoformat", None)
    return method() if callable(method) else str(value)


def _json_value(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)



def _valid_base_url(value: str) -> str:
    base_url = (value or "").strip()
    if not base_url:
        return ""
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url 必须是带 host 的 http/https 地址")
    return base_url.rstrip("/")



def _decode_json_object(value: Any) -> Any:
    """把 JSONB/JSON 字段统一成可比较对象，保留无法解析的旧值以触发失效。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value if value is not None else {}



def _normalize_extra_headers(value: Any) -> dict[str, str]:
    """只允许普通扩展请求头，认证信息必须走 API Key 字段。"""
    extra_headers = value or {}
    if not isinstance(extra_headers, Mapping):
        raise ValueError("extraHeaders 必须是对象")
    sensitive_header_names = ("authorization", "api-key", "token", "secret")
    if any(
        any(marker in str(key).lower() for marker in sensitive_header_names)
        for key in extra_headers
    ):
        raise ValueError("敏感请求头不能通过普通配置字段保存，请使用 API Key")
    return {str(key): str(item) for key, item in extra_headers.items()}



_FORBIDDEN_EXTRA_BODY_KEYS = frozenset({
    "model", "messages", "stream", "input", "prompt",
    "tools", "tool_choice", "functions", "function_call",
    "api_key", "authorization", "apikey",
})
# 值深度/长度上限：防止把超大对象塞进每次请求体
_EXTRA_BODY_MAX_KEYS = 20
_EXTRA_BODY_MAX_DEPTH = 3


def _extra_body_depth(value: Any, _level: int = 1) -> int:
    """返回嵌套深度（标量为 1）。"""
    if isinstance(value, Mapping):
        if not value:
            return _level
        return max(_extra_body_depth(v, _level + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        return max((_extra_body_depth(v, _level + 1) for v in value), default=_level)
    return _level


def _normalize_extra_body(value: Any) -> dict[str, Any]:
    """附加请求体（如关闭思考 thinking/enable_thinking）。

    与 extraHeaders 同级的供应商默认值，最终由 driver_compat 透传给
    ChatOpenAI(extra_body=...)。校验三件事：
      1. 必须是对象，键为字符串；
      2. 不得含改变调用语义/越权的保留键（见禁写清单）；
      3. 规模有界（键数、嵌套深度），避免撑大每次请求体。
    空对象 = 不附加任何字段（保持历史行为）。
    """
    extra_body = value or {}
    if not isinstance(extra_body, Mapping):
        raise ValueError("extraBody 必须是对象")
    if len(extra_body) > _EXTRA_BODY_MAX_KEYS:
        raise ValueError(f"extraBody 键数不能超过 {_EXTRA_BODY_MAX_KEYS}")
    normalized: dict[str, Any] = {}
    for key, item in extra_body.items():
        name = str(key).strip()
        if not name:
            raise ValueError("extraBody 存在空键")
        if name.lower() in _FORBIDDEN_EXTRA_BODY_KEYS:
            raise ValueError(
                f"extraBody 不允许设置保留字段 {name!r}"
                "（模型名/流式/工具/凭据请用对应专用配置）"
            )
        if _extra_body_depth(item) > _EXTRA_BODY_MAX_DEPTH:
            raise ValueError(
                f"extraBody[{name}] 嵌套过深（上限 {_EXTRA_BODY_MAX_DEPTH} 层）"
            )
        normalized[name] = item
    return normalized


def _provider_slug(display_name: str, base_url: str) -> str:
    """根据名称或域名生成稳定、可读的自定义供应商 ID。"""
    hostname = urlparse(base_url).hostname or "custom-api"
    source = display_name.strip() or hostname
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", source).strip("-").lower()
    return f"custom-{slug or 'api'}"


