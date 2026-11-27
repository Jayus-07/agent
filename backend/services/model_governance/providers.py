"""模型治理中心 - 供应商登记、凭据安全管理与网络范围。"""
from __future__ import annotations

from typing import Any
from collections.abc import Mapping
from sqlalchemy import text

from backend.infra.llm import (
    credentials as _real_credentials_mod,
    registry_store as _real_registry_store,
)
from backend.memory.database import get_session as _real_get_session
from backend.services import provider_probe as _real_provider_probe
from backend.services.model_governance.base import (
    DependencyProxy,
    ModelConfigConflict,
    ModelConfigNotFound,
    _PROBE_SAVE_RETRY_BACKOFF,
    _decode_json_object,
    _iso,
    _json_value,
    _normalize_extra_body,
    _normalize_extra_headers,
    _provider_slug,
    _valid_base_url,
)
from backend.services.model_governance.health import _invalidate_manual_health as _real_invalidate_manual_health
from backend.services.model_governance.models import (
    _apply_model_pricing as _real_apply_model_pricing,
    _has_effective_price as _real_has_effective_price,
    _parse_model_price as _real_parse_model_price,
    _validate_model_pricing as _real_validate_model_pricing,
)
from backend.shared.crypto import (
    decrypt_secret as _real_decrypt_secret,
    encrypt_secret as _real_encrypt_secret,
    fingerprint as _real_fingerprint,
    last4 as _real_last4,
)
from backend.shared.logger import logger

get_session = DependencyProxy("get_session", _real_get_session)
registry_store = DependencyProxy("registry_store", _real_registry_store)
credentials_mod = DependencyProxy("credentials_mod", _real_credentials_mod)
provider_probe = DependencyProxy("provider_probe", _real_provider_probe)
encrypt_secret = DependencyProxy("encrypt_secret", _real_encrypt_secret)
decrypt_secret = DependencyProxy("decrypt_secret", _real_decrypt_secret)
fingerprint = DependencyProxy("fingerprint", _real_fingerprint)
last4 = DependencyProxy("last4", _real_last4)
_invalidate_manual_health = DependencyProxy("_invalidate_manual_health", _real_invalidate_manual_health)
_apply_model_pricing = DependencyProxy("_apply_model_pricing", _real_apply_model_pricing)
_parse_model_price = DependencyProxy("_parse_model_price", _real_parse_model_price)
_validate_model_pricing = DependencyProxy("_validate_model_pricing", _real_validate_model_pricing)
_has_effective_price = DependencyProxy("_has_effective_price", _real_has_effective_price)


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



class ProviderMixin:
    """供应商元数据、凭据与生命周期管理能力。"""

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
        upstream_value = payload.get("upstreamModelName")
        upstream_model_name = (
            (str(upstream_value).strip() if upstream_value is not None else "")
            or model_name
            or ""
        )

        api_key = payload.get("apiKey")
        clear_api_key = bool(payload.get("clearApiKey"))
        if api_key is not None and not str(api_key).strip() and not clear_api_key:
            raise ValueError("apiKey 为空时请使用 clearApiKey=true 明确清除")
        # 1.2 同堵（2026-11-10 独立评审 P3-2）：编辑供应商允许顺带提交模型
        # 单价，与 add_provider_model 同口径校验+落价；metered 未带价不改
        # 现状（模型价缺失时由模型编辑入口补齐）。
        input_price, output_price, cached_input_price, price_currency = (
            _parse_model_price(payload)
        )
        extra_headers = _normalize_extra_headers(payload.get("extraHeaders"))
        # None = 未提交该字段（保持原值，见 SQL 的 COALESCE）；{} = 显式清空
        extra_body = (
            _normalize_extra_body(payload.get("extraBody"))
            if payload.get("extraBody") is not None else None
        )

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
            from backend.infra.llm import specialized

            active_embedding = specialized.resolve_binding("embedding")
            if active_embedding and active_embedding.provider_id == provider_id:
                if (base_url != str(old_row.get("base_url") or "")
                        or driver != old_row.get("driver")
                        or (upstream_model_name and upstream_model_name != (
                            active_embedding.upstream_name or active_embedding.model_name))
                        or (extra_body is not None and extra_body != (old_row.get("extra_body") or {}))):
                    raise ModelConfigConflict(
                        "正在使用的嵌入供应商不能直接修改推理地址或模型参数；"
                        "请登记新供应商/模型，再经预检切换和重建"
                    )
            # 2026-09-22 拍板：内置供应商特殊类退役 —— 供应商一律用户自管，
            # driver 不再因「代码目录登记过」而被锁定（ollama / specialized
            # 等特殊驱动的锁定仍在编辑抽屉的前置校验里）。

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
                    # 未提交 extraBody（None）时保持原值：COALESCE 让 NULL 落回列现值
                    "extra_body = COALESCE(CAST(:extra_body AS jsonb), extra_body), "
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
                    # None = 未提交 extraBody（不覆盖，见 SQL 的 COALESCE）
                    "extra_body": (
                        json.dumps(extra_body, ensure_ascii=False)
                        if extra_body is not None else None
                    ),
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
                # 2026-11-20 收口（与 create_provider 同口径）：编辑抽屉里
                # 填一个未登记过的模型名 = 新增登记；metered 且未带价时，
                # 若该模型也不存在生效价则拒绝——避免经「改名」绕过创建
                # 校验产生无价模型（enforce 预算下会全量拒调用）。
                # 已登记模型不带价 = 仅改供应商信息/顺带补价，不做强制。
                component = _COMPONENT_BY_MODEL_KIND.get(model_kind, "llm")
                if billing == "metered" and input_price is None and output_price is None:
                    existing_model = (
                        await session.execute(
                            text(
                                "SELECT 1 FROM llm_models "
                                "WHERE provider_id = :provider_id AND name = :model_name"
                            ),
                            {"provider_id": provider_id, "model_name": model_name},
                        )
                    ).first()
                    if existing_model is None and not await _has_effective_price(
                        session, model_name, component
                    ):
                        raise ValueError(
                            f"按量计费模型「{model_name}」尚未配置生效单价："
                            "请同时填写输入与输出单价（每 1M tokens）后保存"
                        )
                model_kind = await self._upsert_model(
                    session,
                    provider_id=provider_id,
                    model_name=model_name,
                    model_kind=model_kind,
                    operator=operator,
                    allow_legacy_specialized_migration=True,
                    migration_base_url=base_url,
                    upstream_model_name=upstream_model_name,
                )
                if input_price is not None or output_price is not None:
                    # _apply_model_pricing 内含 _validate_model_pricing：
                    # metered 缺必填维度 / 非 metered 填价统一在此拒绝。
                    await _apply_model_pricing(
                        session,
                        provider_row={"billing": billing},
                        model_name=model_name,
                        model_kind=model_kind,
                        input_price=input_price,
                        output_price=output_price,
                        operator=operator,
                        cached_input_price=cached_input_price,
                        price_currency=price_currency,
                    )
            old_headers = _decode_json_object(old_row.get("extra_headers"))
            old_body = _decode_json_object(old_row.get("extra_body"))
            call_config_changed = any((
                str(old_row.get("driver") or "").strip().lower() != driver,
                str(old_row.get("base_url") or "").strip().rstrip("/") != base_url,
                str(old_row.get("network_scope") or "public").strip().lower()
                != network_scope,
                bool(old_row.get("enabled", True)) != bool(payload.get("enabled", True)),
                old_headers != extra_headers,
                extra_body is not None and old_body != extra_body,
                clear_api_key and old_credential is not None,
                api_key is not None
                and fingerprint(str(api_key).strip())
                != str((old_credential or {}).get("key_fingerprint") or ""),
            ))
            if call_config_changed:
                # 仅上游实际调用配置变化后使旧人工结果失效；展示名、计费模式
                # 和重复保存不会改变 Provider 探测结论。
                await _invalidate_manual_health(
                    session,
                    provider_id=provider_id,
                    reason="供应商配置已变更，请重新执行手动验证",
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
        updated = _provider_payload(row, new_credential)
        if (
            billing == "metered"
            and model_name
            and input_price is None
            and output_price is None
        ):
            # 同 create_provider：metered 模型缺价时显式提示补价。
            updated["needs_pricing"] = True
        return updated


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
        # 1.2 同堵（2026-11-10 独立评审 P3-2）：创建供应商允许直接带模型
        # 单价，与 add_provider_model 同口径校验+落价。
        # 2026-11-20 收口：metered 未带价不再放行——缺价模型在 enforce
        # 预算下会被 price_unavailable 全量拒调用（共享栈实测事故：
        # glm-5.3-flash 无生效价格导致所有对话 BUDGET_EXCEEDED），故与
        # add_provider_model 同口径硬校验，「先建后补价」流程退役。
        input_price, output_price, cached_input_price, price_currency = (
            _parse_model_price(payload)
        )
        _validate_model_pricing(
            provider_row={"billing": billing},
            model_name=model_name,
            model_kind=model_kind,
            input_price=input_price,
            output_price=output_price,
            cached_input_price=cached_input_price,
        )
        extra_headers = _normalize_extra_headers(payload.get("extraHeaders"))
        extra_body = _normalize_extra_body(payload.get("extraBody"))

        # 2026-09-22 拍板：测试与保存独立 —— 保存不再强制探测（与
        # add_provider_model 同口径，见彼处注释）。payload.probe=true 时仍
        # 走探测并阻断（保留给需要强校验的调用方）。
        # ⚠️ 必须 or ""：str(None) 会把字符串 'None' 写进库（2026-09-22 实测
        # 事故：qwen3.7-flash 的 upstream 变成 'None'，调用时发 model='None' 404）
        upstream_model_name = str(
            payload.get("upstreamModelName") or payload.get("upstream_model_name") or ""
        ).strip()
        probe: provider_probe.ProbeResult | None = None
        if payload.get("probe"):
            probe = await provider_probe.probe_provider(
                driver=driver,
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
                model_kind=model_kind,
                network_scope=network_scope,
                extra_headers=extra_headers or None,
                upstream_model_name=upstream_model_name or model_name,
                retry_l2_backoff_seconds=_PROBE_SAVE_RETRY_BACKOFF,
            )
            if not probe.ok:
                _fail_detail = "; ".join(
                    f"{s.level}:{s.summary}" + (f" | {s.detail}" if s.detail else "")
                    for s in probe.steps if s.status == "fail"
                )
                logger.warning(
                    "[ModelConfig] 新增供应商探测失败（raw）：model=%s upstream=%s steps=%s",
                    model_name, upstream_model_name, _fail_detail,
                )
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
                    "extra_headers, extra_body, billing, is_builtin, enabled, "
                    "created_by, updated_at) "
                    "VALUES (:id, :display_name, :driver, :base_url, :network_scope, "
                    "CAST(:extra_headers AS jsonb), CAST(:extra_body AS jsonb), "
                    ":billing, false, :enabled, :operator, now())"
                ),
                {
                    "id": provider_id,
                    "display_name": display_name or urlparse(base_url).hostname or provider_id,
                    "driver": driver,
                    "base_url": base_url,
                    "network_scope": network_scope,
                    "extra_headers": json.dumps(extra_headers, ensure_ascii=False),
                    "extra_body": json.dumps(extra_body, ensure_ascii=False),
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
                upstream_model_name=upstream_model_name or model_name,
            )
            if input_price is not None or output_price is not None:
                # _apply_model_pricing 内含 _validate_model_pricing：
                # metered 缺必填维度 / 非 metered 填价统一在此拒绝。
                await _apply_model_pricing(
                    session,
                    provider_row={"billing": billing},
                    model_name=model_name,
                    model_kind=model_kind,
                    input_price=input_price,
                    output_price=output_price,
                    operator=operator,
                    cached_input_price=cached_input_price,
                    price_currency=price_currency,
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
        created = _provider_payload(
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
        if (
            billing == "metered"
            and model_name
            and input_price is None
            and output_price is None
        ):
            # metered 模型无价调用会被预算链拒绝（price_unavailable），
            # 显式提示管理员到模型编辑补价（前端 missingPrice 会禁用保存）。
            created["needs_pricing"] = True
        return created


    async def delete_provider(
        self,
        provider_id: str,
        operator: str,
    ) -> dict[str, Any]:
        """删除供应商（2026-09-22 拍板：软删 + 关联一并停用/关闭）。

        守卫（仅剩一条硬红线，在处置前挡住）：
        1. 名下任一模型被 `llm_model_role_bindings` 占用 → 拒绝，提示先改绑
           （角色缺模型会让能力直接不可用）。

        内置供应商（is_builtin）特殊类已退役（2026-09-22 拍板）：供应商一律
        用户自管，内置行同样可软删。

        通过守卫后不再要求先清理价格/专项引用，全部随软删一并处置：

        - 名下模型：`llm_models.enabled=false`（运行时与管理端清单按
          `enabled=true` 过滤，即刻从目录消失；重新添加可复活）；
        - 专项通道：`llm_specialized_model_bindings.enabled=false` 停用
          （不物理删，避免与 provider 外键 RESTRICT 纠缠，且可恢复）；
        - 价格条目：`model_price.effective_to=now()` 关闭（治理账目
          append-only，只关不删，账目可追溯）；
        - 凭据行保留（软删语义，恢复供应商时密钥仍在）；
        - 供应商本体：`llm_providers.enabled=false`。

        历史记录 `object_type='provider'` 且 `rollbackable=false`：软删虽可
        手工恢复，但不是一次 UPDATE 能回放的完整回放，不能让历史回放假装完成。
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

            # 关联处置 ①：专项通道软停用（不物理删，避免 provider 外键 RESTRICT 纠缠）
            await session.execute(
                text(
                    "UPDATE llm_specialized_model_bindings "
                    "SET enabled = false, updated_by = :operator, updated_at = now() "
                    "WHERE provider_id = :provider_id AND enabled = true"
                ),
                {"provider_id": provider_id, "operator": operator},
            )
            # 关联处置 ②：名下模型未关闭的价格条目按治理口径关闭（append-only，只关不删）
            if model_names:
                await session.execute(
                    text(
                        "UPDATE model_price SET effective_to = now() "
                        "WHERE model_name = ANY(:model_names) AND effective_to IS NULL"
                    ),
                    {"model_names": model_names},
                )
            # 关联处置 ③：名下模型软删（enabled=false 后即刻从目录消失）
            await session.execute(
                text(
                    "UPDATE llm_models SET enabled = false, updated_at = now() "
                    "WHERE provider_id = :provider_id"
                ),
                {"provider_id": provider_id},
            )
            # 凭据行保留：软删语义，恢复供应商时密钥仍在（密文不出管理端）
            await session.execute(
                text(
                    "UPDATE llm_providers SET enabled = false, updated_at = now() "
                    "WHERE id = :provider_id"
                ),
                {"provider_id": provider_id},
            )
            await _invalidate_manual_health(
                session,
                provider_id=provider_id,
                reason="供应商已停用，请重新启用并执行手动验证后再使用",
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
                            "softDeleted": True,
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
                "softDeleted": True,
            }
            break

        await registry_store.refresh_registry()
        assert deleted is not None
        return deleted

