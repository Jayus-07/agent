"""专项模型绑定的进程内缓存。

该模块只保存 embedding/rerank 的非敏感绑定信息；API Key 仍由统一的
``credentials.resolve_credentials`` 按 provider_id 解析，避免在角色缓存中
复制密钥。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


SPECIALIZED_ROLES = frozenset({"embedding", "rerank"})


@dataclass(frozen=True)
class SpecializedBinding:
    """一次专项模型调用所需的非敏感配置。"""

    role: str
    provider_id: str
    adapter: str
    model_name: str
    base_url: str
    options: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    last_probe_ok: bool | None = None
    last_probe_summary: str | None = None
    last_probe_elapsed_ms: int | None = None


_bindings: dict[str, SpecializedBinding] = {}


def _binding(role: str, value: Mapping[str, Any]) -> SpecializedBinding | None:
    role = str(role or "").strip()
    if role not in SPECIALIZED_ROLES:
        return None
    model_name = str(value.get("model_name") or "").strip()
    provider_id = str(value.get("provider_id") or "").strip()
    adapter = str(value.get("adapter") or "").strip()
    base_url = str(value.get("base_url") or "").strip().rstrip("/")
    if not model_name or not provider_id or not adapter or not base_url:
        return None
    raw_options = value.get("options") or {}
    options = dict(raw_options) if isinstance(raw_options, Mapping) else {}
    return SpecializedBinding(
        role=role,
        provider_id=provider_id,
        adapter=adapter,
        model_name=model_name,
        base_url=base_url,
        options=options,
        enabled=bool(value.get("enabled", True)),
        last_probe_ok=value.get("last_probe_ok"),
        last_probe_summary=(
            str(value.get("last_probe_summary"))
            if value.get("last_probe_summary") is not None
            else None
        ),
        last_probe_elapsed_ms=(
            int(value["last_probe_elapsed_ms"])
            if value.get("last_probe_elapsed_ms") is not None
            else None
        ),
    )


def set_bindings(bindings: Mapping[str, Mapping[str, Any]] | None) -> None:
    """替换专项绑定缓存；非法或不完整条目被忽略。"""
    _bindings.clear()
    for role, value in (bindings or {}).items():
        if not isinstance(value, Mapping):
            continue
        binding = _binding(str(role), value)
        if binding is not None:
            _bindings[binding.role] = binding


def resolve_binding(role: str) -> SpecializedBinding | None:
    """返回启用中的专项绑定；未配置时返回 ``None`` 让调用方走 env 兜底。"""
    binding = _bindings.get(str(role or "").strip())
    if binding is None or not binding.enabled:
        return None
    return binding


def snapshot() -> dict[str, dict[str, Any]]:
    """返回不含密钥的缓存快照，仅供测试和诊断使用。"""
    return {
        role: {
            "role": binding.role,
            "provider_id": binding.provider_id,
            "adapter": binding.adapter,
            "model_name": binding.model_name,
            "base_url": binding.base_url,
            "options": dict(binding.options),
            "enabled": binding.enabled,
            "last_probe_ok": binding.last_probe_ok,
            "last_probe_summary": binding.last_probe_summary,
            "last_probe_elapsed_ms": binding.last_probe_elapsed_ms,
        }
        for role, binding in _bindings.items()
    }


def reset_for_tests() -> None:
    """清理测试态缓存。"""
    _bindings.clear()
