"""模型与供应商配置治理服务。

本模块是管理端模型配置的唯一写入口：

* 角色绑定、供应商元数据和探测状态使用记忆库事务落库；
* API Key 只在写入瞬间出现，持久化为 Fernet 密文，历史只记指纹与末四位；
* 写成功后立即刷新进程内注册表，聊天热路径不做数据库 IO；
* 所有读接口返回脱敏后的裸 dict，避免把 SQL 行对象直接暴露给路由层。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from sqlalchemy import text

from backend.config import model_roles
from backend.config.llm import OLLAMA_ENABLED
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod
from backend.infra.llm import registry_store
from backend.memory.database import get_session
from backend.services import specialized_model_adapters
from backend.services import specialized_model_probe
from backend.services import provider_probe
from backend.shared.crypto import decrypt_secret, encrypt_secret, fingerprint, last4
from backend.shared.logger import logger


HISTORY_OBJECTS = {
    "role",
    "provider",
    "provider_credential",
    "provider_network_scope",
}


class ModelConfigNotFound(LookupError):
    """请求的角色、供应商或历史不存在。"""


class ModelConfigConflict(RuntimeError):
    """配置不能按请求执行（例如密钥历史不可回滚）。"""


_SPECIALIZED_ROLES = {"embedding", "rerank"}


def _specialized_binding_payload(role: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """校验并归一化一条 embedding/rerank 绑定。"""
    if role not in _SPECIALIZED_ROLES:
        raise ValueError(f"不支持的专项模型角色：{role}")
    model_name = str(value.get("modelName") or value.get("model_name") or "").strip()
    if not model_name:
        raise ValueError(f"{role} 模型名不能为空")
    adapter = str(value.get("adapter") or "").strip()
    specialized_model_adapters.get_adapter(adapter)
    if role == "embedding" and not adapter.endswith("embedding"):
        raise ValueError("embedding 只能使用 embedding 适配器")
    if role == "rerank" and not adapter.endswith("rerank"):
        raise ValueError("rerank 只能使用 rerank 适配器")
    base_url = _valid_base_url(
        str(value.get("baseUrl") or value.get("base_url") or "")
    )
    if not base_url:
        raise ValueError(f"{role} 必须填写 Base URL")
    options = value.get("options") or {}
    if not isinstance(options, Mapping):
        raise ValueError(f"{role} options 必须是对象")
    return {
        "role": role,
        "model_name": model_name,
        "adapter": adapter,
        "base_url": base_url,
        "options": dict(options),
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    method = getattr(value, "isoformat", None)
    return method() if callable(method) else str(value)


def _json_value(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)


def _provider_payload(row: Mapping[str, Any], credential: Mapping[str, Any] | None) -> dict:
    """将 provider 行映射为前端 ProviderRow 的稳定子集。"""
    credential = credential or {}
    return {
        "id": str(row.get("id") or ""),
        "displayName": row.get("display_name") or row.get("id") or "",
        "driver": str(row.get("driver") or ""),
        "baseUrl": str(row.get("base_url") or ""),
        "modelName": str(row.get("model_name") or "") or None,
        "modelKind": models_mod.normalize_model_kind(row.get("model_kind")),
        "networkScope": str(row.get("network_scope") or "public"),
        "billing": str(row.get("billing") or "metered"),
        "isBuiltin": bool(row.get("is_builtin")),
        "enabled": bool(row.get("enabled", True)),
        "credential": {
            "configured": bool(credential.get("configured")),
            "fingerprint": credential.get("fingerprint"),
            "last4": credential.get("last4"),
            "rotatedAt": credential.get("rotatedAt"),
            "rotatedBy": credential.get("rotatedBy"),
        },
        "lastProbe": (
            {
                "at": _iso(row.get("last_probe_at")),
                "ok": bool(row.get("last_probe_ok")),
                "worstGrade": row.get("last_probe_worst_grade"),
            }
            if row.get("last_probe_at") is not None
            else None
        ),
    }


def _valid_base_url(value: str) -> str:
    base_url = (value or "").strip()
    if not base_url:
        return ""
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url 必须是带 host 的 http/https 地址")
    return base_url.rstrip("/")


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


def _provider_slug(display_name: str, base_url: str) -> str:
    """根据名称或域名生成稳定、可读的自定义供应商 ID。"""
    hostname = urlparse(base_url).hostname or "custom-api"
    source = display_name.strip() or hostname
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", source).strip("-").lower()
    return f"custom-{slug or 'api'}"


def _model_validation_issue(model_name: str, *, role: str | None = None) -> str | None:
    """管理端角色写入的本地校验；DB 凭据优先于环境变量。

    所有角色都必须绑定已登记的模型；embedding / rerank 还必须匹配对应用途，
    防止角色绑定页绕过供应商模型目录保存自由文本。
    """
    name = (model_name or "").strip()
    if not name and role == "eval_gen":
        return None
    if not name:
        return "模型名不能为空"
    entry = models_mod.get_model_entry(name)
    if entry is None:
        return f"未知模型：{name}"
    if role and not models_mod.is_model_kind_compatible(
        role, entry.get("model_kind")
    ):
        expected = models_mod.expected_model_kind(role)
        actual = models_mod.model_kind_of(entry)
        return (
            f"角色 {role} 只能绑定{models_mod.MODEL_KIND_LABELS[expected]}，"
            f"当前模型是{models_mod.MODEL_KIND_LABELS[actual]}"
        )
    provider = str(entry.get("provider") or "")
    if provider == "ollama" and not OLLAMA_ENABLED:
        return "Ollama 当前未启用（cloud 模式禁用本地模型）"
    if provider != "ollama":
        try:
            credentials = credentials_mod.resolve_credentials(
                provider,
                model_name=name,
            )
        except credentials_mod.UnknownProviderError:
            credentials = None
        if credentials is not None and not credentials.api_key:
            return f"供应商 {provider} 未配置 API Key，无法使用模型 {name}"
    return None


def _worst_probe_grade(result: Mapping[str, Any]) -> str | None:
    blocked_at = result.get("blocked_at")
    if blocked_at:
        return str(blocked_at)
    for step in result.get("steps") or []:
        if step.get("status") == "fail":
            return str(step.get("level") or "") or None
    return None


class ModelConfigService:
    """模型配置治理的数据库服务，按请求创建并发安全的短事务。"""

    async def update_role(self, role: str, model_name: str, operator: str) -> dict:
        if role not in model_roles.MODEL_ROLES:
            raise ModelConfigNotFound(f"未注册的模型角色：{role}")
        model_name = (model_name or "").strip()
        issue = _model_validation_issue(model_name, role=role)
        if issue:
            raise ValueError(issue)

        old_value: str | None = None
        async for session in get_session():
            row = (
                await session.execute(
                    text(
                        "SELECT model_name FROM llm_model_role_bindings "
                        "WHERE role = :role"
                    ),
                    {"role": role},
                )
            ).first()
            if row is not None:
                old_value = str(row[0])
            await session.execute(
                text(
                    "INSERT INTO llm_model_role_bindings "
                    "(role, model_name, updated_by, updated_at) "
                    "VALUES (:role, :model_name, :operator, now()) "
                    "ON CONFLICT (role) DO UPDATE SET "
                    "model_name = EXCLUDED.model_name, "
                    "updated_by = EXCLUDED.updated_by, "
                    "updated_at = now()"
                ),
                {"role": role, "model_name": model_name, "operator": operator},
            )
            if old_value != model_name:
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, old_value, new_value, operator) "
                        "VALUES ('role', :role, :old_value, :new_value, :operator)"
                    ),
                    {
                        "role": role,
                        "old_value": old_value,
                        "new_value": model_name,
                        "operator": operator,
                    },
                )
            # get_session() 是 async generator；break 会提前结束迭代，无法执行
            # 生成器 yield 后的自动 commit。写入必须在 break 前显式提交。
            await session.commit()
            break

        model_roles.set_override(
            role,
            model_name,
            {"updatedBy": operator, "updatedAt": None},
        )
        await registry_store.refresh_registry()
        return {
            "role": role,
            "oldValue": old_value,
            "newValue": model_name,
            "changedBy": operator,
            "requiresReindex": model_roles.MODEL_ROLES[role].requires_reindex,
        }

    async def _read_provider_secret(self, provider_id: str) -> str:
        """读取已有 provider 密文并在服务端解密，绝不向 API 返回。"""
        async for session in get_session():
            row = (
                await session.execute(
                    text(
                        "SELECT key_cipher FROM llm_provider_credentials "
                        "WHERE provider_id = :provider_id"
                    ),
                    {"provider_id": provider_id},
                )
            ).mappings().first()
            break
        if row is None or not row.get("key_cipher"):
            return ""
        try:
            return decrypt_secret(str(row["key_cipher"]))
        except Exception as exc:  # noqa: BLE001 - 统一转换为可操作错误
            raise ValueError("已有供应商密钥无法解密，请重新输入 API Key") from exc

    async def configure_specialized(
        self,
        payload: Mapping[str, Any],
        operator: str,
    ) -> dict[str, Any]:
        """测试并保存 embedding/rerank 专项配置。

        所有网络测试在事务写入前完成；任一能力失败都只返回测试结果，不落库。
        """
        provider = payload.get("provider") or {}
        if not isinstance(provider, Mapping):
            raise ValueError("provider 必须是对象")
        display_name = str(provider.get("displayName") or provider.get("display_name") or "").strip()
        base_url = _valid_base_url(
            str(provider.get("baseUrl") or provider.get("base_url") or "")
        )
        if not display_name:
            raise ValueError("供应商名称不能为空")
        if not base_url:
            raise ValueError("供应商 Base URL 不能为空")
        provider_id = str(provider.get("providerId") or provider.get("provider_id") or "").strip()
        api_key_input = str(provider.get("apiKey") or provider.get("api_key") or "").strip()
        if not provider_id and not api_key_input:
            raise ValueError("新供应商必须填写 API Key")
        if provider_id and not api_key_input:
            api_key = await self._read_provider_secret(provider_id)
            if not api_key:
                raise ValueError("API Key 不能为空；编辑时可重新输入以轮换密钥")
        else:
            api_key = api_key_input

        raw_bindings = payload.get("bindings") or {}
        if not isinstance(raw_bindings, Mapping):
            raise ValueError("bindings 必须是对象")
        bindings: list[dict[str, Any]] = []
        for role in ("embedding", "rerank"):
            raw = raw_bindings.get(role)
            if raw is None:
                continue
            if not isinstance(raw, Mapping):
                raise ValueError(f"{role} 配置必须是对象")
            bindings.append(_specialized_binding_payload(role, raw))
        if not bindings:
            raise ValueError("至少配置 embedding 或 rerank 之一")

        probe_results: list[dict[str, Any]] = []
        for binding in bindings:
            probe = await specialized_model_probe.probe_specialized(
                role=binding["role"],
                adapter=binding["adapter"],
                base_url=binding["base_url"],
                api_key=api_key,
                model_name=binding["model_name"],
                options=binding["options"],
                network_scope=str(provider.get("networkScope") or "public"),
            )
            probe_results.append(probe.to_dict())
        if not all(bool(item.get("ok")) for item in probe_results):
            return {
                "ok": False,
                "saved": False,
                "tests": probe_results,
                "summary": "专项模型测试未全部通过，配置未保存",
            }

        network_scope = str(provider.get("networkScope") or "public").strip().lower()
        if network_scope not in {"public", "private"}:
            raise ValueError("networkScope 只能是 public 或 private")
        billing = str(provider.get("billing") or "metered").strip().lower()
        if billing not in {"metered", "subscription", "local"}:
            raise ValueError("billing 只能是 metered、subscription 或 local")

        old_credential: Mapping[str, Any] | None = None
        if not provider_id:
            base_id = f"specialized-{_provider_slug(display_name, base_url).removeprefix('custom-')}"
            provider_id = base_id

        async for session in get_session():
            if not provider.get("providerId") and not provider.get("provider_id"):
                suffix = 2
                while (
                    await session.execute(
                        text("SELECT 1 FROM llm_providers WHERE id = :provider_id"),
                        {"provider_id": provider_id},
                    )
                ).first() is not None:
                    provider_id = f"{base_id}-{suffix}"
                    suffix += 1

            existing_provider = (
                await session.execute(
                    text("SELECT * FROM llm_providers WHERE id = :provider_id"),
                    {"provider_id": provider_id},
                )
            ).mappings().first()
            if existing_provider is not None and str(existing_provider.get("driver")) not in {
                "specialized",
            }:
                raise ModelConfigConflict(
                    f"供应商 {provider_id} 已用于通用聊天，不能直接改为专项供应商"
                )

            if existing_provider is None:
                await session.execute(
                    text(
                        "INSERT INTO llm_providers "
                        "(id, display_name, driver, base_url, network_scope, "
                        "extra_headers, billing, is_builtin, enabled, created_by, updated_at) "
                        "VALUES (:id, :display_name, 'specialized', :base_url, :network_scope, "
                        "'{}'::jsonb, :billing, false, true, :operator, now())"
                    ),
                    {
                        "id": provider_id,
                        "display_name": display_name,
                        "base_url": base_url,
                        "network_scope": network_scope,
                        "billing": billing,
                        "operator": operator,
                    },
                )
            else:
                await session.execute(
                    text(
                        "UPDATE llm_providers SET display_name = :display_name, "
                        "base_url = :base_url, network_scope = :network_scope, "
                        "billing = :billing, enabled = true, updated_at = now() "
                        "WHERE id = :provider_id"
                    ),
                    {
                        "provider_id": provider_id,
                        "display_name": display_name,
                        "base_url": base_url,
                        "network_scope": network_scope,
                        "billing": billing,
                    },
                )

            old_credential = (
                await session.execute(
                    text(
                        "SELECT key_fingerprint, key_last4, key_version, updated_at, updated_by "
                        "FROM llm_provider_credentials WHERE provider_id = :provider_id"
                    ),
                    {"provider_id": provider_id},
                )
            ).mappings().first()
            if api_key_input or old_credential is None:
                secret = encrypt_secret(api_key)
                new_fingerprint = fingerprint(api_key)
                new_last4 = last4(api_key)
                version = int((old_credential or {}).get("key_version") or 0) + 1
                await session.execute(
                    text(
                        "INSERT INTO llm_provider_credentials "
                        "(provider_id, key_cipher, key_fingerprint, key_last4, key_version, updated_by, updated_at) "
                        "VALUES (:provider_id, :key_cipher, :fingerprint, :last4, :version, :operator, now()) "
                        "ON CONFLICT (provider_id) DO UPDATE SET key_cipher = EXCLUDED.key_cipher, "
                        "key_fingerprint = EXCLUDED.key_fingerprint, key_last4 = EXCLUDED.key_last4, "
                        "key_version = EXCLUDED.key_version, updated_by = EXCLUDED.updated_by, updated_at = now()"
                    ),
                    {
                        "provider_id": provider_id,
                        "key_cipher": secret,
                        "fingerprint": new_fingerprint,
                        "last4": new_last4,
                        "version": version,
                        "operator": operator,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, secret_fingerprint, operator, rollbackable) "
                        "VALUES ('provider_credential', :key, :fingerprint, :operator, false)"
                    ),
                    {
                        "key": provider_id,
                        "fingerprint": new_fingerprint,
                        "operator": operator,
                    },
                )

            for binding, probe in zip(bindings, probe_results, strict=True):
                # 专项模型也进入统一模型目录，供供应商页展示、角色下拉选择和
                # 后端用途校验；专项适配器的 base_url/options 仍保留在绑定表。
                await self._upsert_model(
                    session,
                    provider_id=provider_id,
                    model_name=binding["model_name"],
                    model_kind=binding["role"],
                    operator=operator,
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_model_role_bindings "
                        "(role, model_name, updated_by, updated_at) "
                        "VALUES (:role, :model_name, :operator, now()) "
                        "ON CONFLICT (role) DO UPDATE SET model_name = EXCLUDED.model_name, "
                        "updated_by = EXCLUDED.updated_by, updated_at = now()"
                    ),
                    {
                        "role": binding["role"],
                        "model_name": binding["model_name"],
                        "operator": operator,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_specialized_model_bindings "
                        "(role, provider_id, adapter, model_name, base_url, options, enabled, "
                        "last_probe_at, last_probe_ok, last_probe_summary, last_probe_elapsed_ms, "
                        "updated_by, updated_at) "
                        "VALUES (:role, :provider_id, :adapter, :model_name, :base_url, "
                        "CAST(:options AS jsonb), true, now(), :probe_ok, :probe_summary, "
                        ":probe_elapsed_ms, :operator, now()) "
                        "ON CONFLICT (role) DO UPDATE SET provider_id = EXCLUDED.provider_id, "
                        "adapter = EXCLUDED.adapter, model_name = EXCLUDED.model_name, "
                        "base_url = EXCLUDED.base_url, options = EXCLUDED.options, enabled = true, "
                        "last_probe_at = now(), last_probe_ok = EXCLUDED.last_probe_ok, "
                        "last_probe_summary = EXCLUDED.last_probe_summary, "
                        "last_probe_elapsed_ms = EXCLUDED.last_probe_elapsed_ms, "
                        "updated_by = EXCLUDED.updated_by, updated_at = now()"
                    ),
                    {
                        "role": binding["role"],
                        "provider_id": provider_id,
                        "adapter": binding["adapter"],
                        "model_name": binding["model_name"],
                        "base_url": binding["base_url"],
                        "options": json.dumps(binding["options"], ensure_ascii=False),
                        "probe_ok": bool(probe["ok"]),
                        "probe_summary": probe["summary"],
                        "probe_elapsed_ms": probe["elapsedMs"],
                        "operator": operator,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, old_value, new_value, operator) "
                        "VALUES ('role', :role, NULL, :new_value, :operator)"
                    ),
                    {
                        "role": binding["role"],
                        "new_value": binding["model_name"],
                        "operator": operator,
                    },
                )
            await session.commit()
            break

        await registry_store.refresh_registry()
        credential = {
            "configured": True,
            "fingerprint": fingerprint(api_key),
            "last4": last4(api_key),
            "rotatedAt": None,
            "rotatedBy": operator,
        }
        return {
            "ok": True,
            "saved": True,
            "provider": {
                "id": provider_id,
                "displayName": display_name,
                "driver": "specialized",
                "baseUrl": base_url,
                "credential": credential,
            },
            "tests": probe_results,
            "bindings": [
                {
                    "role": binding["role"],
                    "modelName": binding["model_name"],
                    "adapter": binding["adapter"],
                    "requiresReindex": binding["role"] == "embedding",
                }
                for binding in bindings
            ],
        }

    async def list_specialized(self) -> dict[str, Any]:
        """返回专项绑定与适配器清单，响应只包含脱敏凭据元数据。"""
        snap = await registry_store.load_registry()
        providers = {
            str(row.get("id")): row
            for row in snap.providers
            if row.get("id")
        }
        items: list[dict[str, Any]] = []
        for role in ("embedding", "rerank"):
            binding = snap.specialized.get(role)
            if not binding:
                continue
            provider_id = str(binding.get("provider_id") or "")
            provider = providers.get(provider_id) or {}
            meta = snap.credential_meta.get(provider_id) or {}
            items.append(
                {
                    "role": role,
                    "modelName": binding.get("model_name") or "",
                    "providerId": provider_id,
                    "providerName": provider.get("display_name") or provider_id,
                    "adapter": binding.get("adapter") or "",
                    "baseUrl": binding.get("base_url") or "",
                    "options": binding.get("options") or {},
                    "enabled": bool(binding.get("enabled", True)),
                    "credential": {
                        "configured": provider_id in snap.credentials and bool(meta),
                        "fingerprint": meta.get("fingerprint"),
                        "last4": meta.get("last4"),
                    },
                    "lastProbe": (
                        {
                            "ok": binding.get("last_probe_ok"),
                            "summary": binding.get("last_probe_summary"),
                            "elapsedMs": binding.get("last_probe_elapsed_ms"),
                            "at": _iso(binding.get("last_probe_at")),
                        }
                        if binding.get("last_probe_at") is not None
                        else None
                    ),
                    "requiresReindex": role == "embedding",
                }
            )
        return {
            "items": items,
            "adapters": [
                "dashscope_embedding",
                "openai_embedding",
                "dashscope_rerank",
                "jina_rerank",
            ],
            "providerBaseUrl": next(
                (
                    str(provider.get("base_url") or "").strip().rstrip("/")
                    for provider in providers.values()
                    if any(
                        item.get("providerId") == provider.get("id")
                        for item in items
                    )
                ),
                "",
            ),
            "source": "db" if snap.loaded else "fallback",
        }

    async def update_provider(
        self,
        provider_id: str,
        payload: Mapping[str, Any],
        operator: str,
    ) -> dict:
        provider_id = (provider_id or "").strip()
        driver = str(payload.get("driver") or "").strip().lower()
        network_scope = str(payload.get("networkScope") or "public").strip().lower()
        billing = str(payload.get("billing") or "metered").strip().lower()
        base_url = _valid_base_url(str(payload.get("baseUrl") or ""))
        if network_scope not in {"public", "private"}:
            raise ValueError("networkScope 只能是 public 或 private")
        if billing not in {"metered", "subscription", "local"}:
            raise ValueError("billing 只能是 metered、subscription 或 local")
        if driver not in {"openai", "anthropic", "ollama", "specialized"}:
            raise ValueError("driver 只能是 openai、anthropic、ollama 或 specialized")

        model_name_value = payload.get("modelName")
        requested_model_kind = payload.get("modelKind")
        model_kind = (
            models_mod.normalize_model_kind(requested_model_kind)
            if requested_model_kind
            else None
        )
        model_name = None
        if model_name_value is not None:
            model_name = str(model_name_value).strip()
            if not model_name:
                raise ValueError("modelName 不能为空")

        api_key = payload.get("apiKey")
        clear_api_key = bool(payload.get("clearApiKey"))
        if api_key is not None and not str(api_key).strip() and not clear_api_key:
            raise ValueError("apiKey 为空时请使用 clearApiKey=true 明确清除")
        extra_headers = _normalize_extra_headers(payload.get("extraHeaders"))

        old_row: Mapping[str, Any] | None = None
        old_credential: Mapping[str, Any] | None = None
        new_credential: dict[str, Any] = {}
        async for session in get_session():
            row = (
                await session.execute(
                    text("SELECT * FROM llm_providers WHERE id = :provider_id"),
                    {"provider_id": provider_id},
                )
            ).mappings().first()
            if row is None:
                raise ModelConfigNotFound(f"未找到供应商：{provider_id}")
            old_row = dict(row)
            expected_driver = models_mod.get_provider_driver(provider_id)
            if provider_id in models_mod.PROVIDERS and expected_driver != driver:
                raise ValueError(
                    f"内置供应商 {provider_id} 的 driver 固定为 {expected_driver}"
                )

            credential_row = (
                await session.execute(
                    text(
                        "SELECT key_fingerprint, key_last4, key_version, "
                        "updated_at, updated_by "
                        "FROM llm_provider_credentials "
                        "WHERE provider_id = :provider_id"
                    ),
                    {"provider_id": provider_id},
                )
            ).mappings().first()
            old_credential = dict(credential_row) if credential_row else None

            await session.execute(
                text(
                    "UPDATE llm_providers SET display_name = :display_name, "
                    "driver = :driver, base_url = :base_url, "
                    "network_scope = :network_scope, extra_headers = CAST(:extra_headers AS jsonb), "
                    "billing = :billing, enabled = :enabled, updated_at = now() "
                    "WHERE id = :provider_id"
                ),
                {
                    "provider_id": provider_id,
                    "display_name": str(payload.get("displayName") or provider_id).strip(),
                    "driver": driver,
                    "base_url": base_url,
                    "network_scope": network_scope,
                    "extra_headers": json.dumps(extra_headers, ensure_ascii=False),
                    "billing": billing,
                    "enabled": bool(payload.get("enabled", True)),
                },
            )

            old_provider_view = {
                "displayName": old_row.get("display_name") or provider_id,
                "driver": old_row.get("driver") or driver,
                "baseUrl": old_row.get("base_url") or "",
                "billing": old_row.get("billing") or "metered",
                "enabled": bool(old_row.get("enabled", True)),
            }
            new_provider_view = {
                "displayName": str(payload.get("displayName") or provider_id).strip(),
                "driver": driver,
                "baseUrl": base_url,
                "billing": billing,
                "enabled": bool(payload.get("enabled", True)),
            }
            if old_provider_view != new_provider_view:
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, old_value, new_value, operator) "
                        "VALUES ('provider', :key, :old_value, :new_value, :operator)"
                    ),
                    {
                        "key": provider_id,
                        "old_value": _json_value(old_provider_view),
                        "new_value": _json_value(new_provider_view),
                        "operator": operator,
                    },
                )
            if (old_row.get("network_scope") or "public") != network_scope:
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, old_value, new_value, operator) "
                        "VALUES ('provider_network_scope', :key, :old_value, :new_value, :operator)"
                    ),
                    {
                        "key": provider_id,
                        "old_value": old_row.get("network_scope") or "public",
                        "new_value": network_scope,
                        "operator": operator,
                    },
                )

            if clear_api_key:
                await session.execute(
                    text(
                        "DELETE FROM llm_provider_credentials "
                        "WHERE provider_id = :provider_id"
                    ),
                    {"provider_id": provider_id},
                )
                if old_credential is not None:
                    await session.execute(
                        text(
                            "INSERT INTO llm_config_history "
                            "(object_type, object_key, secret_fingerprint, operator, rollbackable) "
                            "VALUES ('provider_credential', :key, :fingerprint, :operator, false)"
                        ),
                        {
                            "key": provider_id,
                            "fingerprint": old_credential.get("key_fingerprint") or None,
                            "operator": operator,
                        },
                    )
            elif api_key is not None:
                secret = str(api_key).strip()
                cipher = encrypt_secret(secret)
                new_fingerprint = fingerprint(secret)
                new_last4 = last4(secret)
                version = int((old_credential or {}).get("key_version") or 0) + 1
                await session.execute(
                    text(
                        "INSERT INTO llm_provider_credentials "
                        "(provider_id, key_cipher, key_fingerprint, key_last4, key_version, updated_by, updated_at) "
                        "VALUES (:provider_id, :key_cipher, :fingerprint, :last4, :version, :operator, now()) "
                        "ON CONFLICT (provider_id) DO UPDATE SET "
                        "key_cipher = EXCLUDED.key_cipher, key_fingerprint = EXCLUDED.key_fingerprint, "
                        "key_last4 = EXCLUDED.key_last4, key_version = EXCLUDED.key_version, "
                        "updated_by = EXCLUDED.updated_by, updated_at = now()"
                    ),
                    {
                        "provider_id": provider_id,
                        "key_cipher": cipher,
                        "fingerprint": new_fingerprint,
                        "last4": new_last4,
                        "version": version,
                        "operator": operator,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, secret_fingerprint, operator, rollbackable) "
                        "VALUES ('provider_credential', :key, :fingerprint, :operator, false)"
                    ),
                    {
                        "key": provider_id,
                        "fingerprint": new_fingerprint,
                        "operator": operator,
                    },
                )
                new_credential = {
                    "configured": True,
                    "fingerprint": new_fingerprint,
                    "last4": new_last4,
                    "rotatedAt": None,
                    "rotatedBy": operator,
                }
            if model_name is not None:
                model_kind = await self._upsert_model(
                    session,
                    provider_id=provider_id,
                    model_name=model_name,
                    model_kind=model_kind,
                    operator=operator,
                    allow_legacy_specialized_migration=True,
                    migration_base_url=base_url,
                )
            await session.commit()
            break

        await registry_store.refresh_registry()
        row = dict(old_row or {})
        row.update(
            {
                "display_name": str(payload.get("displayName") or provider_id).strip(),
                "driver": driver,
                "base_url": base_url,
                "network_scope": network_scope,
                "billing": billing,
                "enabled": bool(payload.get("enabled", True)),
            }
        )
        if model_name is not None:
            row["model_name"] = model_name
            row["model_kind"] = model_kind
        if not new_credential and old_credential:
            new_credential = {
                "configured": True,
                "fingerprint": old_credential.get("key_fingerprint") or None,
                "last4": old_credential.get("key_last4") or None,
                "rotatedAt": _iso(old_credential.get("updated_at")),
                "rotatedBy": old_credential.get("updated_by") or None,
            }
        if clear_api_key:
            new_credential = {"configured": False}
        return _provider_payload(row, new_credential)

    async def _upsert_model(
        self,
        session: Any,
        *,
        provider_id: str,
        model_name: str,
        operator: str,
        model_kind: str | None = None,
        allow_legacy_specialized_migration: bool = False,
        migration_base_url: str = "",
    ) -> str:
        """登记模型；仅允许历史专项实例被显式迁移到新供应商。"""
        existing = (
            await session.execute(
                text(
                    "SELECT provider_id, model_kind FROM llm_models "
                    "WHERE name = :model_name"
                ),
                {"model_name": model_name},
            )
        ).mappings().first()
        code_entry = models_mod.get_model_entry(model_name)
        effective_kind = (
            models_mod.normalize_model_kind(model_kind)
            if model_kind
            else (
                models_mod.normalize_model_kind(existing.get("model_kind"))
                if existing is not None
                else models_mod.model_kind_of(code_entry)
                if code_entry is not None
                else "chat"
            )
        )

        existing_provider_id = str(existing.get("provider_id")) if existing else ""
        migrating_legacy = False
        if existing is not None and existing_provider_id != provider_id:
            if allow_legacy_specialized_migration:
                legacy_provider = (
                    await session.execute(
                        text(
                            "SELECT driver FROM llm_providers "
                            "WHERE id = :provider_id"
                        ),
                        {"provider_id": existing_provider_id},
                    )
                ).mappings().first()
                migrating_legacy = bool(
                    legacy_provider
                    and str(legacy_provider.get("driver") or "").lower()
                    == "specialized"
                )
            if not migrating_legacy:
                raise ModelConfigConflict(
                    f"模型 {model_name} 已属于供应商 {existing_provider_id}；"
                    "模型名全局唯一，请编辑原供应商或更换模型名"
                )

        if code_entry is not None and str(code_entry.get("provider")) != provider_id:
            if not migrating_legacy or str(code_entry.get("provider")) != existing_provider_id:
                raise ModelConfigConflict(
                    f"模型 {model_name} 已注册到供应商 {code_entry.get('provider')}；"
                    "请编辑原供应商或更换模型名"
                )

        if existing is not None or code_entry is not None:
            if existing is not None:
                current_kind = models_mod.normalize_model_kind(existing.get("model_kind"))
                if current_kind != effective_kind:
                    raise ModelConfigConflict(
                        f"模型 {model_name} 已登记为{models_mod.MODEL_KIND_LABELS[current_kind]}，"
                        f"不能直接改为{models_mod.MODEL_KIND_LABELS[effective_kind]}，请新增模型条目"
                    )
                if migrating_legacy:
                    migration_base_url = _valid_base_url(migration_base_url)
                    if not migration_base_url:
                        raise ValueError("迁移历史专项模型时必须提供 Base URL")
                    await session.execute(
                        text(
                            "UPDATE llm_models SET provider_id = :provider_id, "
                            "display_name = :display_name, model_kind = :model_kind, "
                            "enabled = true, updated_at = now() "
                            "WHERE name = :model_name"
                        ),
                        {
                            "provider_id": provider_id,
                            "model_name": model_name,
                            "display_name": model_name,
                            "model_kind": effective_kind,
                        },
                    )
                    if effective_kind in _SPECIALIZED_ROLES:
                        adapter = specialized_model_adapters.infer_adapter(
                            effective_kind, migration_base_url
                        )
                        await session.execute(
                            text(
                                "UPDATE llm_specialized_model_bindings "
                                "SET provider_id = :provider_id, adapter = :adapter, "
                                "base_url = :base_url, updated_by = :operator, "
                                "updated_at = now() "
                                "WHERE role = :role AND provider_id = :old_provider_id "
                                "AND model_name = :model_name"
                            ),
                            {
                                "provider_id": provider_id,
                                "adapter": adapter,
                                "base_url": migration_base_url,
                                "operator": operator,
                                "role": effective_kind,
                                "old_provider_id": existing_provider_id,
                                "model_name": model_name,
                            },
                        )
                else:
                    await session.execute(
                        text(
                            "UPDATE llm_models SET display_name = :display_name, "
                            "model_kind = :model_kind, enabled = true, updated_at = now() "
                            "WHERE name = :model_name"
                        ),
                        {
                            "model_name": model_name,
                            "display_name": model_name,
                            "model_kind": effective_kind,
                        },
                    )
            elif models_mod.model_kind_of(code_entry) != effective_kind:
                raise ModelConfigConflict(
                    f"模型 {model_name} 已登记为{models_mod.MODEL_KIND_LABELS[models_mod.model_kind_of(code_entry)]}，"
                    f"不能改为{models_mod.MODEL_KIND_LABELS[effective_kind]}"
                )
            return effective_kind

        await session.execute(
            text(
                "INSERT INTO llm_models "
                "(name, provider_id, display_name, model_kind, source, created_by, updated_at) "
                "VALUES (:name, :provider_id, :display_name, :model_kind, 'user', :operator, now())"
            ),
            {
                "name": model_name,
                "provider_id": provider_id,
                "display_name": model_name,
                "model_kind": effective_kind,
                "operator": operator,
            },
        )
        return effective_kind

    async def list_provider_models(
        self,
        provider_id: str,
        model_kind: str | None = None,
    ) -> dict[str, Any]:
        """返回供应商下按用途过滤的模型目录，不返回任何凭据字段。"""
        provider_id = (provider_id or "").strip()
        if not provider_id:
            raise ModelConfigNotFound("供应商 ID 不能为空")
        normalized_kind = (
            models_mod.normalize_model_kind(model_kind) if model_kind else None
        )
        items = []
        for item in models_mod.get_available_models():
            if str(item.get("provider") or "") != provider_id:
                continue
            kind = models_mod.normalize_model_kind(item.get("model_kind"))
            if normalized_kind and kind != normalized_kind:
                continue
            items.append(
                {
                    "name": item.get("name"),
                    "display": item.get("display") or item.get("name"),
                    "modelKind": kind,
                    "source": item.get("source") or "builtin",
                }
            )
        items.sort(key=lambda item: (item["modelKind"], item["name"]))
        return {"providerId": provider_id, "items": items}

    async def add_provider_model(
        self,
        provider_id: str,
        payload: Mapping[str, Any],
        operator: str,
    ) -> dict[str, Any]:
        """测试通过后向已有供应商追加一个模型目录条目。"""
        provider_id = (provider_id or "").strip()
        model_name = str(payload.get("modelName") or payload.get("model_name") or "").strip()
        if not provider_id:
            raise ModelConfigNotFound("供应商 ID 不能为空")
        if not model_name:
            raise ValueError("模型名不能为空")
        model_kind = models_mod.normalize_model_kind(
            payload.get("modelKind") or payload.get("model_kind")
        )

        provider: Mapping[str, Any] | None = None
        async for session in get_session():
            provider_row = (
                await session.execute(
                    text("SELECT * FROM llm_providers WHERE id = :provider_id"),
                    {"provider_id": provider_id},
                )
            ).mappings().first()
            if provider_row is None:
                raise ModelConfigNotFound(f"未找到供应商：{provider_id}")
            duplicate = (
                await session.execute(
                    text("SELECT provider_id FROM llm_models WHERE name = :model_name"),
                    {"model_name": model_name},
                )
            ).mappings().first()
            if duplicate is not None:
                raise ModelConfigConflict(
                    f"模型 {model_name} 已登记到供应商 {duplicate.get('provider_id')}"
                )
            provider = dict(provider_row)
            break

        assert provider is not None
        driver = str(provider.get("driver") or "").strip().lower()
        credential = None
        try:
            credential = credentials_mod.resolve_credentials(provider_id)
        except credentials_mod.UnknownProviderError:
            credential = None
        api_key = await self._read_provider_secret(provider_id)
        if not api_key and credential is not None:
            api_key = credential.api_key or ""
        base_url = str(provider.get("base_url") or "").strip()
        if not base_url and credential is not None:
            base_url = credential.base_url or ""
        if not base_url:
            raise ValueError("供应商 Base URL 为空，无法测试")
        if not api_key and driver != "ollama":
            raise ValueError("供应商未配置 API Key，无法测试新模型")
        raw_headers = provider.get("extra_headers") or {}
        extra_headers = dict(raw_headers) if isinstance(raw_headers, Mapping) else {}
        probe = await provider_probe.probe_provider(
            driver=driver,
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            model_kind=model_kind,
            network_scope=str(provider.get("network_scope") or "public"),
            extra_headers=extra_headers or None,
        )
        await self.record_probe(provider_id, probe.to_dict())
        if not probe.ok:
            raise ValueError(f"模型测试未通过：{probe.summary}")

        async for session in get_session():
            await self._upsert_model(
                session,
                provider_id=provider_id,
                model_name=model_name,
                model_kind=model_kind,
                operator=operator,
                allow_legacy_specialized_migration=True,
                migration_base_url=base_url,
            )
            await session.commit()
            break
        await registry_store.refresh_registry()
        return {
            "providerId": provider_id,
            "name": model_name,
            "display": model_name,
            "modelKind": model_kind,
            "probe": {
                "ok": True,
                "summary": probe.summary,
                "elapsedMs": sum(item.elapsed_ms for item in probe.steps),
            },
        }

    async def remove_provider_model(
        self,
        provider_id: str,
        model_name: str,
        operator: str,
    ) -> dict[str, Any]:
        """从供应商下移除一个自建模型条目；内置模型与被占用的模型一律拒绝。

        三条红线都在**删除前**挡住 —— 这些引用表都没有外键，删完留下的悬空引用补不回来：

        1. 仅存在于代码层 `AVAILABLE_MODELS` 的模型不可移除：DB 本来就没有行，删了
           下次 `_merged_models` 合并还会原样出现，属于「假装成功」。
        2. 被 `llm_model_role_bindings` 占用的不可移除：该表没有外键，删模型会让角色
           指向不存在的模型，而 main / embedding / rerank 这类角色缺失会直接让能力不可用。
           要删先去「角色绑定」改绑。
        3. 被 `llm_specialized_model_bindings` 或 `model_price` 引用的同样不可移除。

        历史记录复用 `object_type='provider'`（`llm_config_history` 的 CHECK 只有
        role / provider / provider_credential / provider_network_scope，加一个 `model`
        要动 schema），并显式置 `rollbackable=false`：模型删除不是一次 UPDATE 能回放的，
        回滚需要重新探测并重建条目，不能让历史回放假装完成。
        """
        provider_id = (provider_id or "").strip()
        model_name = (model_name or "").strip()
        if not provider_id:
            raise ModelConfigNotFound("供应商 ID 不能为空")
        if not model_name:
            raise ValueError("模型名不能为空")

        removed: dict[str, Any] | None = None
        async for session in get_session():
            row = (
                await session.execute(
                    text(
                        "SELECT provider_id, display_name, model_kind "
                        "FROM llm_models WHERE name = :model_name"
                    ),
                    {"model_name": model_name},
                )
            ).mappings().first()

            if row is None:
                code_entry = models_mod.get_model_entry(model_name)
                if (
                    code_entry is not None
                    and str(code_entry.get("provider") or "") == provider_id
                ):
                    raise ModelConfigConflict(
                        f"模型 {model_name} 是代码层内置模型，不能从管理端移除"
                    )
                raise ModelConfigNotFound(
                    f"供应商 {provider_id} 下未找到模型 {model_name}"
                )

            owner = str(row.get("provider_id") or "")
            if owner != provider_id:
                raise ModelConfigConflict(
                    f"模型 {model_name} 属于供应商 {owner}，请到该供应商下移除"
                )

            role_rows = (
                await session.execute(
                    text(
                        "SELECT role FROM llm_model_role_bindings "
                        "WHERE model_name = :model_name ORDER BY role"
                    ),
                    {"model_name": model_name},
                )
            ).mappings().all()
            if role_rows:
                roles = "、".join(str(item.get("role")) for item in role_rows)
                raise ModelConfigConflict(
                    f"模型 {model_name} 正被角色 {roles} 使用，不能移除；"
                    "请先在「角色绑定」里改绑到其他模型"
                )

            specialized_rows = (
                await session.execute(
                    text(
                        "SELECT role FROM llm_specialized_model_bindings "
                        "WHERE model_name = :model_name ORDER BY role"
                    ),
                    {"model_name": model_name},
                )
            ).mappings().all()
            if specialized_rows:
                roles = "、".join(str(item.get("role")) for item in specialized_rows)
                raise ModelConfigConflict(
                    f"模型 {model_name} 仍绑定在专项通道 {roles} 上，不能移除"
                )

            price_row = (
                await session.execute(
                    text(
                        "SELECT count(*) AS total FROM model_price "
                        "WHERE model_name = :model_name"
                    ),
                    {"model_name": model_name},
                )
            ).mappings().first()
            if price_row and int(price_row.get("total") or 0) > 0:
                raise ModelConfigConflict(
                    f"模型 {model_name} 仍配置了价格表，请先清除价格后再移除"
                )

            model_kind = models_mod.normalize_model_kind(row.get("model_kind"))
            await session.execute(
                text("DELETE FROM llm_models WHERE name = :model_name"),
                {"model_name": model_name},
            )
            await session.execute(
                text(
                    "INSERT INTO llm_config_history "
                    "(object_type, object_key, old_value, new_value, operator, rollbackable) "
                    "VALUES ('provider', :key, :old_value, NULL, :operator, false)"
                ),
                {
                    "key": provider_id,
                    "old_value": _json_value(
                        {
                            "removedModel": model_name,
                            "displayName": row.get("display_name") or model_name,
                            "modelKind": model_kind,
                        }
                    ),
                    "operator": operator,
                },
            )
            await session.commit()
            removed = {
                "providerId": provider_id,
                "name": model_name,
                "display": row.get("display_name") or model_name,
                "modelKind": model_kind,
            }
            break

        await registry_store.refresh_registry()
        assert removed is not None
        return removed

    async def create_provider(
        self,
        payload: Mapping[str, Any],
        operator: str,
    ) -> dict:
        """创建自定义供应商，最小输入为 Base URL、API Key 和模型名。"""
        driver = str(payload.get("driver") or "openai").strip().lower()
        if driver not in {"openai", "anthropic", "ollama"}:
            raise ValueError("driver 只能是 openai、anthropic 或 ollama")
        base_url = _valid_base_url(str(payload.get("baseUrl") or ""))
        if not base_url:
            raise ValueError("自定义供应商必须填写 Base URL")
        model_name = str(payload.get("modelName") or "").strip()
        if not model_name:
            raise ValueError("模型名不能为空")
        model_kind = models_mod.normalize_model_kind(payload.get("modelKind"))
        api_key = str(payload.get("apiKey") or "").strip()
        if not api_key and driver != "ollama":
            raise ValueError("API Key 不能为空")

        network_scope = str(payload.get("networkScope") or "public").strip().lower()
        billing = str(payload.get("billing") or "metered").strip().lower()
        if network_scope not in {"public", "private"}:
            raise ValueError("networkScope 只能是 public 或 private")
        if billing not in {"metered", "subscription", "local"}:
            raise ValueError("billing 只能是 metered、subscription 或 local")
        extra_headers = _normalize_extra_headers(payload.get("extraHeaders"))

        # API 层不能只依赖管理端先测再保存；直接调用写接口也必须通过同一
        # 个按用途分流的探测器，避免把未验证模型写进生效目录。
        probe = await provider_probe.probe_provider(
            driver=driver,
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            model_kind=model_kind,
            network_scope=network_scope,
            extra_headers=extra_headers or None,
        )
        if not probe.ok:
            raise ValueError(f"模型测试未通过：{probe.summary}")
        display_name = str(payload.get("displayName") or "").strip()
        provider_id = ""
        new_fingerprint = fingerprint(api_key) if api_key else ""
        new_last4 = last4(api_key) if api_key else ""

        async for session in get_session():
            base_id = _provider_slug(display_name, base_url)
            provider_id = base_id
            suffix = 2
            while (
                await session.execute(
                    text("SELECT 1 FROM llm_providers WHERE id = :provider_id"),
                    {"provider_id": provider_id},
                )
            ).first() is not None:
                provider_id = f"{base_id}-{suffix}"
                suffix += 1

            await session.execute(
                text(
                    "INSERT INTO llm_providers "
                    "(id, display_name, driver, base_url, network_scope, "
                    "extra_headers, billing, is_builtin, enabled, created_by, updated_at) "
                    "VALUES (:id, :display_name, :driver, :base_url, :network_scope, "
                    "CAST(:extra_headers AS jsonb), :billing, false, :enabled, "
                    ":operator, now())"
                ),
                {
                    "id": provider_id,
                    "display_name": display_name or urlparse(base_url).hostname or provider_id,
                    "driver": driver,
                    "base_url": base_url,
                    "network_scope": network_scope,
                    "extra_headers": json.dumps(extra_headers, ensure_ascii=False),
                    "billing": billing,
                    "enabled": bool(payload.get("enabled", True)),
                    "operator": operator,
                },
            )
            await self._upsert_model(
                session,
                provider_id=provider_id,
                model_name=model_name,
                model_kind=model_kind,
                operator=operator,
                allow_legacy_specialized_migration=True,
                migration_base_url=base_url,
            )
            if api_key:
                await session.execute(
                    text(
                        "INSERT INTO llm_provider_credentials "
                        "(provider_id, key_cipher, key_fingerprint, key_last4, "
                        "key_version, updated_by, updated_at) "
                        "VALUES (:provider_id, :key_cipher, :fingerprint, :last4, "
                        "1, :operator, now())"
                    ),
                    {
                        "provider_id": provider_id,
                        "key_cipher": encrypt_secret(api_key),
                        "fingerprint": new_fingerprint,
                        "last4": new_last4,
                        "operator": operator,
                    },
                )
            provider_view = {
                "displayName": display_name or urlparse(base_url).hostname or provider_id,
                "driver": driver,
                "baseUrl": base_url,
                "modelName": model_name,
                "modelKind": model_kind,
                "billing": billing,
                "enabled": bool(payload.get("enabled", True)),
            }
            await session.execute(
                text(
                    "INSERT INTO llm_config_history "
                    "(object_type, object_key, old_value, new_value, operator) "
                    "VALUES ('provider', :key, NULL, :new_value, :operator)"
                ),
                {
                    "key": provider_id,
                    "new_value": _json_value(provider_view),
                    "operator": operator,
                },
            )
            if api_key:
                await session.execute(
                    text(
                        "INSERT INTO llm_config_history "
                        "(object_type, object_key, secret_fingerprint, operator, rollbackable) "
                        "VALUES ('provider_credential', :key, :fingerprint, :operator, false)"
                    ),
                    {
                        "key": provider_id,
                        "fingerprint": new_fingerprint,
                        "operator": operator,
                    },
                )
            await session.commit()
            break

        await registry_store.refresh_registry()
        return _provider_payload(
            {
                "id": provider_id,
                "display_name": display_name or urlparse(base_url).hostname or provider_id,
                "driver": driver,
                "base_url": base_url,
                "network_scope": network_scope,
                "billing": billing,
                "is_builtin": False,
                "enabled": bool(payload.get("enabled", True)),
                "model_name": model_name,
                "model_kind": model_kind,
            },
            {
                "configured": bool(api_key),
                "fingerprint": new_fingerprint or None,
                "last4": new_last4 or None,
                "rotatedAt": None,
                "rotatedBy": operator,
            },
        )

    async def delete_provider(
        self,
        provider_id: str,
        operator: str,
    ) -> dict[str, Any]:
        """删除自定义供应商（2026-09-21 拍板：名下没有被角色绑定的模型即可删）。

        守卫（全部在删除前挡住 —— 引用表没有外键，删完留悬空引用补不回来）：
        1. 内置供应商（is_builtin）由代码目录管理，不可从管理端删除；
        2. 名下任一模型被 `llm_model_role_bindings` 占用 → 拒绝，提示先改绑
           （角色缺模型会让能力直接不可用）；
        3. 名下任一模型被 `llm_specialized_model_bindings`（按 provider_id 或
           model_name）或 `model_price` 引用 → 拒绝。

        通过守卫后：名下未被绑定的 DB 模型随供应商级联删除（它们只依附于该
        供应商），凭据行一并删除。历史记录 `object_type='provider'` 且
        `rollbackable=false`：删除不是一次 UPDATE 能回放的，重建需要重新探测。
        """
        provider_id = (provider_id or "").strip()
        if not provider_id:
            raise ModelConfigNotFound("供应商 ID 不能为空")

        deleted: dict[str, Any] | None = None
        async for session in get_session():
            row = (
                await session.execute(
                    text(
                        "SELECT id, display_name, driver, base_url, billing, is_builtin "
                        "FROM llm_providers WHERE id = :provider_id"
                    ),
                    {"provider_id": provider_id},
                )
            ).mappings().first()
            if row is None:
                raise ModelConfigNotFound(f"供应商 {provider_id} 不存在")
            if bool(row.get("is_builtin")):
                raise ModelConfigConflict(
                    f"供应商 {provider_id} 是内置供应商，由代码目录管理，不能删除"
                )

            model_rows = (
                await session.execute(
                    text(
                        "SELECT name, display_name, model_kind FROM llm_models "
                        "WHERE provider_id = :provider_id ORDER BY name"
                    ),
                    {"provider_id": provider_id},
                )
            ).mappings().all()
            model_names = [str(item.get("name") or "") for item in model_rows]

            # 守卫 2：角色绑定按 model_name 引用
            for name in model_names:
                role_rows = (
                    await session.execute(
                        text(
                            "SELECT role FROM llm_model_role_bindings "
                            "WHERE model_name = :model_name ORDER BY role"
                        ),
                        {"model_name": name},
                    )
                ).mappings().all()
                if role_rows:
                    roles = "、".join(str(item.get("role")) for item in role_rows)
                    raise ModelConfigConflict(
                        f"供应商 {provider_id} 的模型 {name} 正被角色 {roles} 使用，"
                        "不能删除；请先在「角色绑定」里改绑到其他模型"
                    )

            # 守卫 3a：专项绑定按 provider_id 或 model_name 引用
            specialized_sql = (
                "SELECT role FROM llm_specialized_model_bindings "
                "WHERE provider_id = :provider_id"
            )
            specialized_params: dict[str, Any] = {"provider_id": provider_id}
            if model_names:
                # asyncpg 对空序列的 ANY() 推断不了元素类型，无模型时跳过该分支
                specialized_sql += " OR model_name = ANY(:model_names)"
                specialized_params["model_names"] = model_names
            specialized_rows = (
                await session.execute(text(specialized_sql + " ORDER BY role"), specialized_params)
            ).mappings().all()
            if specialized_rows:
                roles = "、".join(
                    sorted({str(item.get("role")) for item in specialized_rows})
                )
                raise ModelConfigConflict(
                    f"供应商 {provider_id} 仍绑定在专项通道 {roles} 上，不能删除；"
                    "请先在管理端把这些专项角色改绑到其他供应商"
                )

            # 守卫 3b：价格表按 model_name 引用
            if model_names:
                price_rows = (
                    await session.execute(
                        text(
                            "SELECT model_name FROM model_price "
                            "WHERE model_name = ANY(:model_names) LIMIT 5"
                        ),
                        {"model_names": model_names},
                    )
                ).mappings().all()
                if price_rows:
                    names = "、".join(
                        str(item.get("model_name")) for item in price_rows
                    )
                    raise ModelConfigConflict(
                        f"供应商 {provider_id} 的模型 {names} 仍配置了价格表，"
                        "请先清除价格后再删除"
                    )

            await session.execute(
                text("DELETE FROM llm_models WHERE provider_id = :provider_id"),
                {"provider_id": provider_id},
            )
            await session.execute(
                text(
                    "DELETE FROM llm_provider_credentials WHERE provider_id = :provider_id"
                ),
                {"provider_id": provider_id},
            )
            await session.execute(
                text("DELETE FROM llm_providers WHERE id = :provider_id"),
                {"provider_id": provider_id},
            )
            await session.execute(
                text(
                    "INSERT INTO llm_config_history "
                    "(object_type, object_key, old_value, new_value, operator, rollbackable) "
                    "VALUES ('provider', :key, :old_value, NULL, :operator, false)"
                ),
                {
                    "key": provider_id,
                    "old_value": _json_value(
                        {
                            "deleted": True,
                            "displayName": row.get("display_name") or provider_id,
                            "driver": str(row.get("driver") or ""),
                            "baseUrl": str(row.get("base_url") or ""),
                            "billing": str(row.get("billing") or "metered"),
                            "removedModels": model_names,
                        }
                    ),
                    "operator": operator,
                },
            )
            await session.commit()
            deleted = {
                "providerId": provider_id,
                "displayName": row.get("display_name") or provider_id,
                "removedModels": model_names,
            }
            break

        await registry_store.refresh_registry()
        assert deleted is not None
        return deleted

    async def record_probe(self, provider_id: str, result: Mapping[str, Any]) -> None:
        """保存最后一次探测结论；探测接口本身不因审计表故障而失败。"""
        try:
            async for session in get_session():
                await session.execute(
                    text(
                        "UPDATE llm_providers SET last_probe_at = now(), "
                        "last_probe_ok = :ok, last_probe_worst_grade = :worst_grade, "
                        "updated_at = now() WHERE id = :provider_id"
                    ),
                    {
                        "provider_id": provider_id,
                        "ok": bool(result.get("ok")),
                        "worst_grade": _worst_probe_grade(result),
                    },
                )
                await session.commit()
                break
        except Exception:
            logger.warning(
                "[ModelConfig] provider=%s 探测结果落库失败，不影响探测响应",
                provider_id,
                exc_info=True,
            )

    async def list_history(
        self, object_type: str | None = None, limit: int = 200
    ) -> dict:
        if object_type and object_type not in HISTORY_OBJECTS:
            raise ValueError(f"object 只能是 {sorted(HISTORY_OBJECTS)}")
        params: dict[str, Any] = {"limit": limit}
        where = ""
        if object_type:
            where = "WHERE object_type = :object_type"
            params["object_type"] = object_type
        items: list[dict] = []
        async for session in get_session():
            rows = (
                await session.execute(
                    text(
                        "SELECT id, object_type, object_key, old_value, new_value, "
                        "secret_fingerprint, operator, changed_at, rollbackable "
                        f"FROM llm_config_history {where} "
                        "ORDER BY changed_at DESC, id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).mappings().all()
            for row in rows:
                items.append(
                    {
                        "id": str(row["id"]),
                        "object": row["object_type"],
                        "key": row["object_key"],
                        "oldValue": row["old_value"],
                        "newValue": row["new_value"],
                        "secretFingerprint": row["secret_fingerprint"],
                        "operator": row["operator"],
                        "at": _iso(row["changed_at"]),
                        "rollbackable": bool(row["rollbackable"]),
                    }
                )
            break
        return {"items": items}

    async def rollback(self, history_id: int, operator: str) -> dict:
        result: dict[str, Any] = {}
        role_refresh: tuple[str, str] | None = None
        async for session in get_session():
            row = (
                await session.execute(
                    text("SELECT * FROM llm_config_history WHERE id = :history_id"),
                    {"history_id": history_id},
                )
            ).mappings().first()
            if row is None:
                raise ModelConfigNotFound(f"未找到历史记录：{history_id}")
            if row["object_type"] == "provider_credential" or not row["rollbackable"]:
                raise ModelConfigConflict("密钥历史不可回滚，请重新轮换密钥")

            object_type = row["object_type"]
            key = row["object_key"]
            target = row["old_value"]
            if object_type == "role":
                if target is None:
                    await session.execute(
                        text("DELETE FROM llm_model_role_bindings WHERE role = :role"),
                        {"role": key},
                    )
                else:
                    await session.execute(
                        text(
                            "INSERT INTO llm_model_role_bindings "
                            "(role, model_name, updated_by, updated_at) VALUES "
                            "(:role, :model_name, :operator, now()) "
                            "ON CONFLICT (role) DO UPDATE SET model_name = EXCLUDED.model_name, "
                            "updated_by = EXCLUDED.updated_by, updated_at = now()"
                        ),
                        {"role": key, "model_name": target, "operator": operator},
                    )
                    role_refresh = (key, target)
            elif object_type == "provider_network_scope":
                if target not in {"public", "private"}:
                    raise ModelConfigConflict("历史中的 network_scope 非法，拒绝回滚")
                await session.execute(
                    text(
                        "UPDATE llm_providers SET network_scope = :scope, updated_at = now() "
                        "WHERE id = :provider_id"
                    ),
                    {"scope": target, "provider_id": key},
                )
            elif object_type == "provider":
                try:
                    previous = json.loads(target or "{}")
                except json.JSONDecodeError as exc:
                    raise ModelConfigConflict("供应商历史格式损坏，拒绝回滚") from exc
                await session.execute(
                    text(
                        "UPDATE llm_providers SET display_name = :display_name, "
                        "driver = :driver, base_url = :base_url, billing = :billing, "
                        "enabled = :enabled, updated_at = now() WHERE id = :provider_id"
                    ),
                    {
                        "provider_id": key,
                        "display_name": previous.get("displayName") or key,
                        "driver": previous.get("driver") or "openai",
                        "base_url": previous.get("baseUrl") or "",
                        "billing": previous.get("billing") or "metered",
                        "enabled": bool(previous.get("enabled", True)),
                    },
                )
            else:
                raise ModelConfigConflict(f"不支持回滚的历史类型：{object_type}")

            await session.execute(
                text(
                    "INSERT INTO llm_config_history "
                    "(object_type, object_key, old_value, new_value, operator, rollbackable) "
                    "VALUES (:object_type, :key, :old_value, :new_value, :operator, true)"
                ),
                {
                    "object_type": object_type,
                    "key": key,
                    "old_value": row["new_value"],
                    "new_value": target,
                    "operator": operator,
                },
            )
            result = {
                "id": str(history_id),
                "rolledBack": True,
                "changedBy": operator,
                "object": object_type,
                "key": key,
            }
            await session.commit()
            break

        if role_refresh:
            model_roles.set_override(
                role_refresh[0], role_refresh[1], {"updatedBy": operator, "updatedAt": None}
            )
        await registry_store.refresh_registry()
        return result

    async def drift(self) -> dict:
        snap = await registry_store.load_registry()
        if snap.loaded:
            models_mod.set_dynamic_providers(snap.providers)
            models_mod.set_dynamic_models(snap.models)
            credentials_mod.set_db_credentials(snap.credentials)
            model_roles.inject_overrides(snap.roles, snap.role_meta)

        catalog = {
            str(item.get("name")): str(item.get("provider") or "")
            for item in models_mod.get_available_models()
            if item.get("name")
        }
        items: list[dict] = []
        for role, spec in model_roles.MODEL_ROLES.items():
            effective = model_roles.resolve_effective(role)
            name = str(effective.get("value") or "")
            if spec.validator and name and name not in catalog:
                items.append(
                    {
                        "severity": "critical",
                        "kind": "unregistered_model",
                        "subject": role,
                        "message": f"角色 {role} 的模型 {name} 未注册",
                        "hint": "先在模型注册表登记该模型，或改回已注册模型。",
                    }
                )
            provider = catalog.get(name)
            missing_key_reason = (
                credentials_mod.missing_key_message(provider)
                if provider
                else None
            )
            if missing_key_reason:
                items.append(
                    {
                        "severity": "critical",
                        "kind": "missing_key",
                        "subject": role,
                        "message": f"角色 {role} 不可用：{missing_key_reason}",
                        "hint": "在供应商页配置并测试数据库凭据。",
                    }
                )

        if snap.loaded:
            for provider in snap.providers:
                if provider.get("last_probe_at") is None:
                    items.append(
                        {
                            "severity": "warn",
                            "kind": "unverified_provider",
                            "subject": provider.get("id") or "",
                            "message": f"供应商 {provider.get('id')} 尚未完成连通性探测",
                            "hint": "在供应商页点击测试，确认 URL、密钥与模型均可用。",
                        }
                    )
                elif not provider.get("last_probe_ok"):
                    items.append(
                        {
                            "severity": "warn",
                            "kind": "unverified_provider",
                            "subject": provider.get("id") or "",
                            "message": f"供应商 {provider.get('id')} 最近一次探测未通过",
                            "hint": "查看探测分级结果并修正对应的 URL、密钥或模型名。",
                        }
                    )
        else:
            items.append(
                {
                    "severity": "critical",
                    "kind": "unverified_provider",
                    "subject": "registry",
                    "message": "模型注册表数据库不可用，当前仅使用代码层兜底",
                    "hint": "先执行记忆库迁移并检查数据库连接。",
                }
            )

        # 当前仓库没有统一的索引元数据表；明确报告“不可判定”，不伪造全绿。
        items.append(
            {
                "severity": "info",
                "kind": "index_model_mismatch",
                "subject": "embedding",
                "message": "索引元数据尚未接入，暂时无法自动比对向量模型",
                "hint": "切换 embedding 模型后必须人工确认并全量重建索引。",
            }
        )
        return {
            "items": items,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
        }


_service = ModelConfigService()


def get_model_config_service() -> ModelConfigService:
    """路由默认服务；测试可通过 ``app.state.model_config_service`` 替换。"""
    return _service
