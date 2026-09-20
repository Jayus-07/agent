"""专项 embedding/rerank 连接测试。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Literal

import httpx

from backend.services.specialized_model_adapters import get_adapter
from backend.shared.logger import logger
from backend.tools.url_guard import UrlBlockedError, assert_url_allowed


SpecializedRole = Literal["embedding", "rerank"]
_DETAIL_LIMIT = 300
_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class SpecializedProbeResult:
    role: str
    adapter: str
    ok: bool
    status_code: int | None
    summary: str
    detail: str
    elapsed_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "adapter": self.adapter,
            "ok": self.ok,
            "statusCode": self.status_code,
            "summary": self.summary,
            "detail": self.detail,
            "elapsedMs": self.elapsed_ms,
        }


def _clip(value: Any) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= _DETAIL_LIMIT else text[:_DETAIL_LIMIT] + "…（已截断）"


def _safe_error(payload: Any, api_key: str) -> str:
    if isinstance(payload, Mapping):
        code = payload.get("code") or payload.get("error", {}).get("code")
        message = payload.get("message") or payload.get("error", {}).get("message")
        value = f"{code}: {message}" if code or message else str(dict(payload))
    else:
        value = str(payload)
    return _clip(value.replace(api_key, "<API_KEY>"))


async def probe_specialized(
    *,
    role: SpecializedRole,
    adapter: str,
    base_url: str,
    api_key: str,
    model_name: str,
    options: Mapping[str, Any] | None = None,
    network_scope: str = "public",
) -> SpecializedProbeResult:
    """执行一次不落库的最小专项模型请求。"""
    started = time.monotonic()
    if not model_name.strip():
        return SpecializedProbeResult(
            role=role,
            adapter=adapter,
            ok=False,
            status_code=None,
            summary="模型名不能为空",
            detail="请填写模型名后重试",
            elapsed_ms=0,
        )
    try:
        assert_url_allowed(
            base_url,
            allow_private=(network_scope or "public").lower() == "private",
        )
        protocol_adapter = get_adapter(adapter)
        request = protocol_adapter.build_probe_request(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            options=options,
        )
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                request.url,
                headers=request.headers,
                json=request.body,
            )
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if 200 <= response.status_code < 300 and isinstance(payload, Mapping):
            if protocol_adapter.response_is_success(payload):
                return SpecializedProbeResult(
                    role=role,
                    adapter=adapter,
                    ok=True,
                    status_code=response.status_code,
                    summary="专项模型调用通过",
                    detail="已收到有效响应",
                    elapsed_ms=elapsed_ms,
                )
        detail = _safe_error(payload, api_key)
        return SpecializedProbeResult(
            role=role,
            adapter=adapter,
            ok=False,
            status_code=response.status_code,
            summary=f"模型调用失败（HTTP {response.status_code}）",
            detail=detail,
            elapsed_ms=elapsed_ms,
        )
    except UrlBlockedError as exc:
        detail = str(exc)
        summary = "地址被安全策略拦截"
    except Exception as exc:  # noqa: BLE001 - 探测必须转成可展示结论
        detail = _safe_error(f"{type(exc).__name__}: {exc}", api_key)
        summary = "专项模型请求失败"
    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "[SpecializedProbe] role=%s adapter=%s target=%s ok=false elapsed=%dms",
        role,
        adapter,
        base_url,
        elapsed_ms,
    )
    return SpecializedProbeResult(
        role=role,
        adapter=adapter,
        ok=False,
        status_code=None,
        summary=summary,
        detail=_clip(detail),
        elapsed_ms=elapsed_ms,
    )
