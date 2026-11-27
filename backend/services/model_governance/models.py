"""模型治理中心 - 模型目录、单价校验落库与能力标签管理。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from collections.abc import Mapping
from sqlalchemy import text

from backend.config import model_roles as _real_model_roles
from backend.config.llm import OLLAMA_ENABLED as _real_OLLAMA_ENABLED
from backend.infra.llm import (
    credentials as _real_credentials_mod,
    models as _real_models_mod,
    registry_store as _real_registry_store,
)
from backend.memory.database import get_session as _real_get_session
from backend.services.model_governance.base import (
    DependencyProxy,
    ModelConfigConflict,
    ModelConfigNotFound,
    _COMPONENT_BY_MODEL_KIND,
    _SPECIALIZED_ROLES,
    _decode_json_object,
    _iso,
    _json_value,
    _normalize_extra_body,
    _normalize_extra_headers,
    _valid_base_url,
)
from backend.services.model_governance.health import _invalidate_manual_health as _real_invalidate_manual_health
from backend.shared.crypto import decrypt_secret as _real_decrypt_secret
from backend.shared.logger import logger

get_session = DependencyProxy("get_session", _real_get_session)
registry_store = DependencyProxy("registry_store", _real_registry_store)
model_roles = DependencyProxy("model_roles", _real_model_roles)
models_mod = DependencyProxy("models_mod", _real_models_mod)
credentials_mod = DependencyProxy("credentials_mod", _real_credentials_mod)
decrypt_secret = DependencyProxy("decrypt_secret", _real_decrypt_secret)
OLLAMA_ENABLED = DependencyProxy("OLLAMA_ENABLED", _real_OLLAMA_ENABLED)
_invalidate_manual_health = DependencyProxy("_invalidate_manual_health", _real_invalidate_manual_health)


def _parse_model_price(
    payload: Mapping[str, Any],
) -> tuple[Decimal | None, Decimal | None, Decimal | None, str]:
    """提取并校验表单单价（每 1M tokens）；未填返回 None。

    返回 (input, output, cached_input, currency)。cached_input 为空表示
    「未配置缓存命中价」（区别于 0 = 明确免费），落库为 NULL，运行时据此
    判 cost_status=estimated。currency 支持 CNY/USD，缺省 USD。
    """
    def _one(*keys: str) -> Decimal | None:
        for key in keys:
            raw = payload.get(key)
            if raw is None or raw == "":
                continue
            try:
                price = Decimal(str(raw)).quantize(Decimal("0.000001"))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"模型单价「{raw}」不是合法数字") from exc
            if price < 0:
                raise ValueError("模型单价不能为负数")
            if price > Decimal("100000"):
                raise ValueError("模型单价超出合理范围（每 1M tokens）")
            return price
        return None

    currency = str(
        payload.get("priceCurrency") or payload.get("price_currency") or "CNY"
    ).strip().upper()
    if currency not in {"CNY", "USD"}:
        raise ValueError(f"不支持的货币单位：{currency}（仅支持 CNY / USD）")
    return (
        _one("inputPricePer1m", "input_price_per_1m"),
        _one("outputPricePer1m", "output_price_per_1m"),
        _one("cachedInputPricePer1m", "cached_input_price_per_1m"),
        currency,
    )



def _validate_model_pricing(
    *,
    provider_row: Mapping[str, Any],
    model_name: str,
    model_kind: str,
    input_price: Decimal | None,
    output_price: Decimal | None,
    cached_input_price: Decimal | None = None,
) -> str:
    """按量计费模型的价格条件必填校验（2026-11-10 待对账治理 1.2）。

    旧逻辑在 `_apply_model_pricing` 里"输入/输出双空直接 return"，无价
    metered 模型得以保存；运行时预占链只能按 price_unknown/needs_review
    挂账，是待对账堆积的直接入口。本校验在 `_upsert_model` 之前与
    `_apply_model_pricing` 内都执行：先算 component 再判必填，避免事务
    回滚不彻底时残留无价模型。返回 component 供调用方复用。
    """
    component = _COMPONENT_BY_MODEL_KIND.get(model_kind, "llm")
    billing = str(provider_row.get("billing") or "")
    if billing != "metered":
        if input_price is not None or output_price is not None:
            raise ValueError("该供应商不是按量计费（metered），无需填写模型单价")
        return component
    if component == "llm":
        if input_price is None or output_price is None:
            raise ValueError(
                f"按量计费文本模型「{model_name}」需同时填写输入与输出单价"
            )
    else:
        # embedding/rerank 只有 input 维度计费；只填 output 等价于无价，
        # 旧逻辑会写出展示价 0 且零计费行，一并拒绝。
        if input_price is None:
            raise ValueError(
                f"按量计费 embedding/rerank 模型「{model_name}」需填写输入单价"
            )
    return component



async def _has_effective_price(session: Any, model_name: str, component: str) -> bool:
    """该模型组件当前是否存在可见生效价（在线计费同一可见性谓词）。

    与 pricing.current_price_eligibility_sql 对齐：active 治理版本行，或
    provider-page 直登价（且该模型组件无 active 治理版本）。
    """
    from backend.infra.llm.pricing import current_price_eligibility_sql

    row = (
        await session.execute(
            text(
                "SELECT 1 FROM model_price p "
                "LEFT JOIN model_price_versions v "
                "ON v.version = p.price_table_version AND v.source = p.source "
                "WHERE p.model_name = :model_name AND p.component = :component "
                "AND p.approval_status = 'approved' AND p.effective_to IS NULL "
                "AND " + current_price_eligibility_sql("model_price", "model_price_versions") +
                " LIMIT 1"
            ),
            {"model_name": model_name, "component": component},
        )
    ).first()
    return row is not None



async def _real_apply_model_pricing(
    session: Any,
    *,
    provider_row: Mapping[str, Any],
    model_name: str,
    model_kind: str,
    input_price: Decimal | None,
    output_price: Decimal | None,
    operator: str,
    cached_input_price: Decimal | None = None,
    price_currency: str = "CNY",
) -> None:
    """把供应商页录入的单价落库（2026-09-22 拍板：登记即生效）。

    两处写入，各司其职：

    1. `llm_models.pricing` JSONB —— 目录展示价（供应商页模型行、
       registry `_model_entry` 读取）；
    2. `model_price` —— 计费事实源（proxy / token_tracker / 预算硬门读取），
       直通写入 `approval_status='approved'` 生效条目。不走去模型价格页的
       导入+双人审核流程：单管理员平台过不了 `reviewer_1 <> reviewer_2`
       的 CHECK，直通条目记 `reviewer_1=操作人、reviewer_2='system'`、
       `source='provider-page'`，model_price 本身 append-only，留痕可追溯。

    只落计费必需维度（pricing._REQUIRED_DIMENSIONS）：llm=input+output，
    embedding/rerank=input；其余维度计费时按 0 跳过。缓存命中价（成本计量
    第一阶段 2026-09-22）为可选第三维度 `cache_read`：仅 llm 且用户填写时
    才落行，**不填不落行**（NULL = 未配置，运行时据此判 estimated）。
    同一维度已有同价生效条目则跳过（避免重复保存产生无谓版本行）；有变化
    则先关闭全部未关闭条目（含 pending 旧草稿）再插入新条目 —— append-only
    的「改价 = 关旧开新」。
    """
    component = _validate_model_pricing(
        provider_row=provider_row,
        model_name=model_name,
        model_kind=model_kind,
        input_price=input_price,
        output_price=output_price,
        cached_input_price=cached_input_price,
    )
    from backend.infra.llm.pricing import current_price_eligibility_sql

    if input_price is None and output_price is None:
        # 校验已保证 metered 双空必然抛错；走到这里的双空只可能是
        # 非 metered 未填价，无事可做。
        return
    if component == "llm":
        dimensions = ("input", "output", "cache_read")
    else:
        dimensions = ("input",)
    prices = {"input": input_price, "output": output_price}
    if component == "llm" and cached_input_price is not None:
        prices["cache_read"] = cached_input_price
    elif component == "llm":
        prices["cache_read"] = None

    # ① 目录展示价
    pricing_json = _json_value(
        {
            "input_price_per_1m": float(prices["input"] or 0),
            "output_price_per_1m": float(prices["output"] or 0),
            "cached_input_price_per_1m": (
                float(prices["cache_read"]) if prices.get("cache_read") is not None else None
            ),
            "price_currency": price_currency,
        }
    )
    await session.execute(
        text(
            "UPDATE llm_models SET pricing = CAST(:pricing AS JSONB), updated_at = now() "
            "WHERE name = :model_name"
        ),
        {"pricing": pricing_json, "model_name": model_name},
    )

    # ② 计费生效价：同价跳过，变价关旧开新
    version = f"provider-page-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    for dimension in dimensions:
        price = prices[dimension]
        if price is None:
            if dimension == "cache_read":
                current_cache_price = (
                    await session.execute(
                        text(
                            "SELECT p.price_per_unit, p.currency, p.price_table_version, p.source "
                            "FROM model_price AS p "
                            "LEFT JOIN model_price_versions AS v "
                            "ON v.version = p.price_table_version AND v.source = p.source "
                            "WHERE p.model_name = :model_name AND p.component = :component "
                            "AND p.dimension = :dimension AND p.approval_status = 'approved' "
                            "AND p.effective_from <= now() "
                            "AND (p.effective_to IS NULL OR p.effective_to > now()) "
                            f"AND {current_price_eligibility_sql('model_price', 'model_price_versions')} "
                            "ORDER BY p.effective_from DESC, p.id DESC LIMIT 1"
                        ),
                        {
                            "model_name": model_name,
                            "component": component,
                            "dimension": dimension,
                        },
                    )
                ).mappings().first()
                if current_cache_price is not None:
                    await session.execute(
                        text(
                            "UPDATE model_price SET effective_to = now() "
                            "WHERE model_name = :model_name AND component = :component "
                            "AND dimension = 'cache_read' AND effective_to IS NULL"
                        ),
                        {"model_name": model_name, "component": component},
                    )
            # 缓存价可选；非 LLM 组件也没有 output 维度。
            continue
        current = (
            await session.execute(
                text(
                    "SELECT p.price_per_unit, p.currency, p.price_table_version, p.source "
                    "FROM model_price AS p "
                    "LEFT JOIN model_price_versions AS v "
                    "ON v.version = p.price_table_version AND v.source = p.source "
                    "WHERE p.model_name = :model_name AND p.component = :component "
                    "AND p.dimension = :dimension AND p.approval_status = 'approved' "
                    "AND p.effective_from <= now() "
                    "AND (p.effective_to IS NULL OR p.effective_to > now()) "
                    f"AND {current_price_eligibility_sql('model_price', 'model_price_versions')} "
                    "ORDER BY p.effective_from DESC, p.id DESC LIMIT 1"
                ),
                {
                    "model_name": model_name,
                    "component": component,
                    "dimension": dimension,
                },
            )
        ).mappings().first()
        if (
            current is not None
            and Decimal(str(current["price_per_unit"])) == price
            and str(current.get("currency") or "").upper() == price_currency
        ):
            continue
        await session.execute(
            text(
                "UPDATE model_price SET effective_to = now() "
                "WHERE model_name = :model_name AND component = :component "
                "AND dimension = :dimension AND effective_to IS NULL"
            ),
            {
                "model_name": model_name,
                "component": component,
                "dimension": dimension,
            },
        )
        await session.execute(
            text(
                "INSERT INTO model_price "
                "(model_name, component, dimension, price_per_unit, unit, currency, "
                " price_table_version, source, effective_from, approval_status, "
                " reviewer_1, reviewer_2) "
                "VALUES (:model_name, :component, :dimension, :price, "
                " 'per_1m_tokens', :currency, :version, 'provider-page', now(), "
                " 'approved', :operator, 'system')"
            ),
            {
                "model_name": model_name,
                "component": component,
                "dimension": dimension,
                "price": price,
                "currency": price_currency,
                "version": version,
                "operator": operator,
            },
        )


_apply_model_pricing = DependencyProxy("_apply_model_pricing", _real_apply_model_pricing)


def _real_model_validation_issue(model_name: str, *, role: str | None = None) -> str | None:
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
    # STOP D（D7）：依赖工具调用的角色，绑定显式声明不支持 tools 的模型时拒绝
    if role == "tool_selector" and isinstance(
        entry.get("capabilities"), dict
    ) and "tools" in entry["capabilities"] \
            and entry["capabilities"].get("tools") is not True:
        return (
            "角色 tool_selector 依赖工具调用（function calling），"
            "当前模型已登记 capabilities.tools=false"
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


_model_validation_issue = DependencyProxy("_model_validation_issue", _real_model_validation_issue)


class ModelCatalogMixin:
    """模型目录维护、单价落库与能力标签管理。"""

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
        allow_soft_deleted_takeover: bool = False,
        upstream_model_name: str = "",
    ) -> str:
        """登记模型；允许历史专项实例与软删行被显式迁移/复活到新供应商。"""
        existing = (
            await session.execute(
                text(
                    "SELECT provider_id, model_kind, enabled, upstream_model_name "
                    "FROM llm_models "
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
        existing_enabled = bool(existing.get("enabled")) if existing else False
        existing_upstream = str(existing.get("upstream_model_name") or "") if existing else ""
        requested_upstream = str(upstream_model_name or "")
        current_embedding = model_roles.resolve_effective("embedding").get("value")
        if existing and model_name == current_embedding and (
            existing_provider_id != provider_id or effective_kind != "embedding"
            or requested_upstream != existing_upstream
        ):
            raise ModelConfigConflict(
                "正在使用的嵌入模型不能原名换供应商或修改上游模型；请登记新名称后经预检切换"
            )
        # 复活过户：占用行已软删（enabled=false）→ 允许迁移到新供应商。
        # 场景：删除供应商后其模型名成为幽灵占用，用户在新供应商下重加同名模型。
        takeover_soft_deleted = bool(
            allow_soft_deleted_takeover
            and existing is not None
            and not bool(existing.get("enabled"))
            and existing_provider_id != provider_id
        )
        migrating_legacy = False
        if existing is not None and existing_provider_id != provider_id:
            if takeover_soft_deleted:
                migrating_legacy = True
            else:
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
                            "upstream_model_name = :upstream_name, "
                            "enabled = true, updated_at = now() "
                            "WHERE name = :model_name"
                        ),
                        {
                            "provider_id": provider_id,
                            "model_name": model_name,
                            "display_name": model_name,
                            "model_kind": effective_kind,
                            "upstream_name": upstream_model_name,
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
                            "model_kind = :model_kind, upstream_model_name = :upstream_name, "
                            "enabled = true, updated_at = now() "
                            "WHERE name = :model_name"
                        ),
                        {
                            "model_name": model_name,
                            "display_name": model_name,
                            "model_kind": effective_kind,
                            "upstream_name": upstream_model_name,
                        },
                    )
            elif models_mod.model_kind_of(code_entry) != effective_kind:
                raise ModelConfigConflict(
                    f"模型 {model_name} 已登记为{models_mod.MODEL_KIND_LABELS[models_mod.model_kind_of(code_entry)]}，"
                    f"不能改为{models_mod.MODEL_KIND_LABELS[effective_kind]}"
                )
            if (
                existing_provider_id != provider_id
                or existing_upstream != requested_upstream
                or not existing_enabled
            ):
                reason = (
                    "模型已重新启用，请重新执行手动验证"
                    if not existing_enabled
                    else "模型所属供应商或上游模型名已变更，请重新执行手动验证"
                )
                await _invalidate_manual_health(
                    session, model_name=model_name, reason=reason,
                )
            return effective_kind

        await session.execute(
            text(
                "INSERT INTO llm_models "
                "(name, provider_id, display_name, model_kind, upstream_model_name, source, created_by, updated_at) "
                "VALUES (:name, :provider_id, :display_name, :model_kind, :upstream_name, 'user', :operator, now())"
            ),
            {
                "name": model_name,
                "provider_id": provider_id,
                "display_name": model_name,
                "model_kind": effective_kind,
                "upstream_name": upstream_model_name,
                "operator": operator,
            },
        )
        await _invalidate_manual_health(
            session,
            model_name=model_name,
            reason="模型已重新登记，请重新执行手动验证",
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
        """测试通过后向供应商登记模型条目（2026-09-22 起兼作改价入口）。

        同名模型已属于**同一供应商**时不再报冲突，而是走更新（含价格变更）；
        属于其他供应商仍拒绝（模型名全局唯一）。表单单价（按量计费）经
        `_apply_model_pricing` 同步落目录展示价与计费生效价。
        """
        provider_id = (provider_id or "").strip()
        model_name = str(payload.get("modelName") or payload.get("model_name") or "").strip()
        if not provider_id:
            raise ModelConfigNotFound("供应商 ID 不能为空")
        if not model_name:
            raise ValueError("模型名不能为空")
        model_kind = models_mod.normalize_model_kind(
            payload.get("modelKind") or payload.get("model_kind")
        )
        input_price, output_price, cached_input_price, price_currency = (
            _parse_model_price(payload)
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
            # 模型名全局唯一（llm_models 主键，价格/角色/账目按名引用）。
            # 但**软删的行不再占名**（2026-09-22 拍板）：占用行 enabled=false 时
            # 允许复活并过户到当前供应商 —— 否则删掉供应商后其模型名成为
            # 看不见也解不开的幽灵占用（实测事故：qwen3.7-plus 卡死）。
            duplicate = (
                await session.execute(
                    text(
                        "SELECT provider_id, enabled FROM llm_models "
                        "WHERE name = :model_name"
                    ),
                    {"model_name": model_name},
                )
            ).mappings().first()
            takeover_soft_deleted = False
            if duplicate is not None and str(duplicate.get("provider_id")) != provider_id:
                if not bool(duplicate.get("enabled")):
                    takeover_soft_deleted = True
                else:
                    raise ModelConfigConflict(
                        f"模型 {model_name} 已登记到供应商 {duplicate.get('provider_id')}；"
                        "模型名全局唯一（价格与角色按名引用），"
                        "请到原供应商下编辑或更换模型名"
                    )
            # 同供应商重复登记 = 更新（含改价）；软删占用 = 复活过户。
            # 继续走探测 + upsert
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
        # 登记名/上游名拆分（2026-09-22）：上游名缺省 = 登记名
        upstream_model_name = str(
            payload.get("upstreamModelName") or payload.get("upstream_model_name")
            or model_name
        ).strip() or model_name
        # 2026-09-22 拍板：测试与保存独立 —— 保存不再强制探测（「测试连接」
        # 是独立入口）。原因：中转站 ~1 次/分钟限流，保存探测与测试连接
        # 背靠背必撞，用户永远保存不了。未验证模型照常登记，探测状态由
        # 卡片「测试连接」随时补齐（last_probe 空则前端灰显"未验证"）。
        probe_result: provider_probe.ProbeResult | None = None
        if payload.get("probe"):
            probe_result = await provider_probe.probe_provider(
                driver=driver,
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
                model_kind=model_kind,
                network_scope=str(provider.get("network_scope") or "public"),
                extra_headers=extra_headers or None,
                upstream_model_name=upstream_model_name,
                retry_l2_backoff_seconds=_PROBE_SAVE_RETRY_BACKOFF,
            )
            await self.record_probe(provider_id, probe_result.to_dict())
            if not probe_result.ok:
                # 失败详情（含上游原始报文）必须留痕 —— 否则同参数「草稿探测过、
                # 保存探测挂」的排障永远拿不到第一现场（2026-09-22 实测）。
                _fail_detail = "; ".join(
                    f"{s.level}:{s.summary}" + (f" | {s.detail}" if s.detail else "")
                    for s in probe_result.steps if s.status == "fail"
                )
                logger.warning(
                    "[ModelConfig] 保存前探测失败（raw）：model=%s upstream=%s steps=%s",
                    model_name, upstream_model_name, _fail_detail,
                )
                raise ValueError(f"模型测试未通过：{probe_result.summary}")

        # 1.2（2026-11-10）：价格必填校验前移——在 _upsert_model 之前拒绝，
        # 避免异常路径残留无价模型污染目录与预算链路。
        _validate_model_pricing(
            provider_row=provider,
            model_name=model_name,
            model_kind=model_kind,
            input_price=input_price,
            output_price=output_price,
            cached_input_price=cached_input_price,
        )

        async for session in get_session():
            await self._upsert_model(
                session,
                provider_id=provider_id,
                model_name=model_name,
                model_kind=model_kind,
                operator=operator,
                allow_legacy_specialized_migration=True,
                migration_base_url=base_url,
                allow_soft_deleted_takeover=takeover_soft_deleted,
                upstream_model_name=upstream_model_name,
            )
            await _apply_model_pricing(
                session,
                provider_row=provider,
                model_name=model_name,
                model_kind=model_kind,
                input_price=input_price,
                output_price=output_price,
                operator=operator,
                cached_input_price=cached_input_price,
                price_currency=price_currency,
            )
            await session.commit()
            break
        await registry_store.refresh_registry()
        return {
            "providerId": provider_id,
            "name": model_name,
            "display": model_name,
            "modelKind": model_kind,
            "inputPricePer1m": float(input_price) if input_price is not None else None,
            "outputPricePer1m": float(output_price) if output_price is not None else None,
            "cachedInputPricePer1m": (
                float(cached_input_price) if cached_input_price is not None else None
            ),
            "priceCurrency": price_currency,
            "probe": (
                {
                    "ok": True,
                    "summary": probe_result.summary,
                    "elapsedMs": sum(item.elapsed_ms for item in probe_result.steps),
                }
                if probe_result is not None
                else None
            ),
        }


    async def remove_provider_model(
        self,
        provider_id: str,
        model_name: str,
        operator: str,
    ) -> dict[str, Any]:
        """从供应商下移除一个自建模型条目（2026-09-22 拍板：软删 + 关联一并停用）。

        守卫与处置：

        1. 仅存在于代码层 `AVAILABLE_MODELS` 的模型不可移除：DB 本来就没有行，删了
           下次 `_merged_models` 合并还会原样出现，属于「假装成功」。
        2. 被 `llm_model_role_bindings` 占用的不可移除（唯一保留的硬红线）：角色
           缺模型会让 main / embedding / rerank 能力直接不可用，要删先去
           「角色绑定」改绑。
        3. 其余引用一律**随软删一并处置**，不再要求先手工清理：
           - `llm_specialized_model_bindings`（专项通道）→ `enabled=false` 停用；
           - `model_price` 未关闭的价格条目 → `effective_to=now()` 关闭
             （价格表是治理账目，append-only 设计，只关不删）。
        4. 模型本体不物理删：`llm_models.enabled=false`。运行时注册表与
           管理端清单都按 `enabled=true` 过滤，软删后即刻从目录消失；重新
           添加同名模型会经 upsert 复活，数据可恢复。

        历史记录复用 `object_type='provider'`（`llm_config_history` 的 CHECK 只有
        role / provider / provider_credential / provider_network_scope，加一个 `model`
        要动 schema），并显式置 `rollbackable=false`：软删虽可手工恢复，但不是一次
        UPDATE 能回放的完整回放，不能让历史回放假装完成。
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

            # 关联处置 ①：专项通道软停用（运行时按 enabled=false 跳过，可恢复）
            await session.execute(
                text(
                    "UPDATE llm_specialized_model_bindings "
                    "SET enabled = false, updated_by = :operator, updated_at = now() "
                    "WHERE model_name = :model_name AND enabled = true"
                ),
                {"model_name": model_name, "operator": operator},
            )
            # 关联处置 ②：未关闭的价格条目按治理口径关闭（append-only，只关不删）
            await session.execute(
                text(
                    "UPDATE model_price SET effective_to = now() "
                    "WHERE model_name = :model_name AND effective_to IS NULL"
                ),
                {"model_name": model_name},
            )

            model_kind = models_mod.normalize_model_kind(row.get("model_kind"))
            # 软删本体：enabled=false 后运行时注册表与管理端清单即刻不可见
            await session.execute(
                text(
                    "UPDATE llm_models SET enabled = false, updated_at = now() "
                    "WHERE name = :model_name"
                ),
                {"model_name": model_name},
            )
            await _invalidate_manual_health(
                session,
                model_name=model_name,
                reason="模型已停用，请重新启用并执行手动验证后再使用",
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
                            "softDeleted": True,
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
                "softDeleted": True,
            }
            break

        await registry_store.refresh_registry()
        assert removed is not None
        return removed

