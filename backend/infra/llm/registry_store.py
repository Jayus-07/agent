"""registry_store.py — LLM 注册表 DB 覆盖层的读取与注入

把 DB 里的**厂商实例 / 凭据 / 用户自建模型**（迁移 0017 三张表）读成内存快照，
注入 `models.set_dynamic_models()` 与 `credentials.set_db_credentials()`，
使 `get_available_models()` / `resolve_credentials()` 自动带上 DB 覆盖。

设计要点（见 docs/model-config-governance-design.md B.3）：

1. **热路径零 IO**：调用方只在后台刷新循环里调 `refresh_registry()`
   （同 `sys_config.refresh_loop` 模式），问答链路只读进程内字典。
2. **fail-open**（与 `sys_config` 的 fail-closed 方向相反）：
   DB 不可用 / 表不存在 → 返回空快照并保留上一轮内存快照。
   绝不能因为 DB 抖动把系统运行时凭据替换成旧 env。
3. **凭据解密 fail-loud**：单条解密失败只跳过该 provider 并记 error，不影响
   其它 provider。绝不把密文或旧 env 当 Key 用（见 shared/crypto 的说明）。

⚠️ 本模块顶层 import SQLAlchemy —— **不得被热路径模块（factory / proxy /
providers）顶层引用**，否则会把 SQLAlchemy 拖进 LLM 导入链。

当前状态：注册表刷新循环已由 `app/server.py` 挂载；数据库不可用时仍保持
fail-open，沿用上一轮已知覆盖层。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from sqlalchemy import text

from backend.infra.llm import credentials as _credentials
from backend.infra.llm import models as _models
from backend.infra.llm import specialized as _specialized
from backend.infra.llm.credentials import ProviderCredentials
from backend.config import model_roles as _model_roles
from backend.memory.database import get_session
from backend.services import specialized_model_adapters as _specialized_adapters
from backend.shared.crypto import DEFAULT_KEY_ENV, SecretCryptoError, decrypt_secret
from backend.shared.logger import logger


@dataclass(frozen=True)
class RegistrySnapshot:
    """一次 DB 读取的结果。`loaded=False` 表示 DB 不可用（勿据此清空动态层）。"""

    providers: list[dict] = field(default_factory=list)
    models: list[dict] = field(default_factory=list)
    credentials: dict[str, ProviderCredentials] = field(default_factory=dict)
    # provider_id → {fingerprint, last4, rotatedAt, rotatedBy}
    # 只给管理端展示用（脱敏），**与「能否解密」解耦**：解密失败时仍需让管理员
    # 看到「已落库但运行时不可用」，否则真因被埋。刻意不塞进 ProviderCredentials
    # —— 那是出站调用热路径的数据类，展示元数据混进去会让每个构造点背上无关字段。
    credential_meta: dict[str, dict] = field(default_factory=dict)
    # role → DB 显式模型名；role_meta 与管理端来源展示共用
    roles: dict[str, str] = field(default_factory=dict)
    role_meta: dict[str, dict] = field(default_factory=dict)
    # role → embedding/rerank provider、适配器与非敏感运行参数
    specialized: dict[str, dict] = field(default_factory=dict)
    loaded: bool = False


_SELECT_PROVIDERS = """
    SELECT id, display_name, driver, base_url, network_scope,
           extra_headers, billing, is_builtin, enabled,
           last_probe_at, last_probe_ok, last_probe_worst_grade
    FROM llm_providers
    WHERE enabled = true
    ORDER BY id
"""

_SELECT_MODELS = """
    SELECT name, provider_id, display_name, description, model_kind, capabilities,
           context_length, pricing
    FROM llm_models
    WHERE enabled = true
    ORDER BY name
"""

_SELECT_CREDENTIALS = """
    SELECT provider_id, key_cipher, key_fingerprint, key_last4, key_version,
           updated_by, updated_at
    FROM llm_provider_credentials
"""

_SELECT_ROLES = """
    SELECT role, model_name, updated_by, updated_at
    FROM llm_model_role_bindings
    ORDER BY role
"""

_SELECT_SPECIALIZED = """
    SELECT role, provider_id, adapter, model_name, base_url, options, enabled,
           last_probe_at, last_probe_ok, last_probe_summary,
           last_probe_elapsed_ms, updated_by, updated_at
    FROM llm_specialized_model_bindings
    ORDER BY role
"""


_last_runtime_signature: tuple | None = None


def _stable_value(value) -> str:
    """将快照字段转成稳定字符串，兼容 datetime / JSONB 等值。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _row_signature(row: dict, *, ignored: set[str] | None = None) -> tuple:
    ignored = ignored or set()
    return tuple(
        (str(key), _stable_value(value))
        for key, value in sorted(row.items(), key=lambda item: str(item[0]))
        if str(key) not in ignored
    )


def _snapshot_signature(snapshot: RegistrySnapshot) -> tuple:
    """只包含会影响运行时客户端/角色解析的配置字段。"""
    provider_rows = tuple(
        _row_signature(
            row,
            # 探测状态只用于管理端展示，不应导致问答客户端重建。
            ignored={"last_probe_at", "last_probe_ok", "last_probe_worst_grade"},
        )
        for row in snapshot.providers
    )
    model_rows = tuple(_row_signature(row) for row in snapshot.models)
    credential_rows = tuple(
        (
            provider_id,
            credential.provider,
            credential.api_key,
            credential.base_url,
            _stable_value(dict(credential.extra_headers or {})),
            _stable_value(dict(credential.extra_body or {})),
            credential.source,
            credential.version,
        )
        for provider_id, credential in sorted(snapshot.credentials.items())
    )
    credential_meta = tuple(
        (
            provider_id,
            meta.get("fingerprint"),
            meta.get("last4"),
            meta.get("rotatedAt"),
        )
        for provider_id, meta in sorted(snapshot.credential_meta.items())
    )
    roles = tuple(sorted(snapshot.roles.items()))
    specialized = tuple(
        _row_signature(row)
        for _, row in sorted(snapshot.specialized.items())
    )
    return (
        provider_rows,
        model_rows,
        credential_rows,
        credential_meta,
        roles,
        specialized,
    )


def _model_entry(row) -> dict:
    """DB 行 → AVAILABLE_MODELS 兼容条目（pricing JSONB 展开为单价字段）。"""
    pricing = row["pricing"] or {}
    return {
        "provider": row["provider_id"],
        "name": row["name"],
        "display": row["display_name"] or row["name"],
        "description": row["description"] or "",
        "model_kind": _models.normalize_model_kind(row.get("model_kind")),
        "input_price_per_1m": float(pricing.get("input_price_per_1m", 0.0) or 0.0),
        "output_price_per_1m": float(pricing.get("output_price_per_1m", 0.0) or 0.0),
        "context_length": row["context_length"],
        "capabilities": row["capabilities"] or {},
        "source": "db",
    }


def _derive_specialized_bindings(snapshot: RegistrySnapshot) -> dict[str, dict]:
    """从当前角色绑定和模型目录派生 embedding/rerank 运行配置。

    ``llm_specialized_model_bindings`` 是旧专项卡留下的兼容数据；供应商页
    登记模型后，真正的 provider、地址和用途必须以统一模型目录为准。这样
    向量与重排可以分别绑定不同 provider，也不会把同一份旧 Key 继续复用。
    """
    bindings = {
        str(role): dict(value)
        for role, value in snapshot.specialized.items()
        if role in {"embedding", "rerank"}
    }
    models_by_name = {
        str(item.get("name") or ""): item
        for item in snapshot.models
        if item.get("name")
    }
    providers_by_id = {
        str(item.get("id") or ""): item
        for item in snapshot.providers
        if item.get("id")
    }

    for role in ("embedding", "rerank"):
        previous = bindings.get(role) or {}
        selected_name = str(
            snapshot.roles.get(role) or previous.get("model_name") or ""
        ).strip()
        entry = models_by_name.get(selected_name)
        if not entry:
            continue
        if _models.model_kind_of(entry) != role:
            # 角色值若已变成另一用途，不能继续沿用旧专项绑定。
            bindings.pop(role, None)
            continue

        provider_id = str(entry.get("provider") or "").strip()
        provider = providers_by_id.get(provider_id) or {}
        credential = snapshot.credentials.get(provider_id)
        same_model = (
            previous.get("model_name") == selected_name
            and previous.get("provider_id") == provider_id
        )
        # 供应商表保存的是通用根地址，专项绑定表可能保存兼容协议的
        # 子路径（例如 DashScope 的 /compatible-mode/v1）。同一模型和
        # provider 仍在用时，必须保留专项地址，否则运行时会请求错误的
        # /embeddings 路径并得到 404。
        specialized_base_url = (
            str(previous.get("base_url") or "").strip().rstrip("/")
            if same_model
            else ""
        )
        base_url = (
            specialized_base_url
            or str(
                provider.get("base_url")
                or (credential.base_url if credential else None)
                or ""
            ).strip().rstrip("/")
        )
        if not provider_id or not base_url:
            continue

        adapter = str(previous.get("adapter") or "").strip() if same_model else ""
        bindings[role] = {
            "role": role,
            "provider_id": provider_id,
            "adapter": adapter or _specialized_adapters.infer_adapter(role, base_url),
            "model_name": selected_name,
            "base_url": base_url,
            "options": previous.get("options") if same_model else {},
            "enabled": True,
            "last_probe_at": previous.get("last_probe_at") if same_model else provider.get("last_probe_at"),
            "last_probe_ok": previous.get("last_probe_ok") if same_model else provider.get("last_probe_ok"),
            "last_probe_summary": previous.get("last_probe_summary") if same_model else None,
            "last_probe_elapsed_ms": previous.get("last_probe_elapsed_ms") if same_model else None,
        }
    return bindings


def _credential(row, providers_by_id: dict[str, dict]) -> ProviderCredentials | None:
    """DB 行 → ProviderCredentials。解密失败返回 None（调用方跳过该条）。"""
    provider_id = row["provider_id"]
    provider_row = providers_by_id.get(provider_id) or {}
    try:
        api_key = decrypt_secret(row["key_cipher"], key_env=DEFAULT_KEY_ENV)
    except SecretCryptoError as e:
        # fail-loud：绝不回落密文原文（那会变成 401 且真因被埋掉）
        logger.error(
            "[LLMRegistry] provider=%s 的凭据解密失败，已跳过（该实例保持未配置）: %s",
            provider_id, e,
        )
        return None
    return ProviderCredentials(
        provider=provider_id,
        api_key=api_key,
        base_url=provider_row.get("base_url") or None,
        extra_headers=provider_row.get("extra_headers") or {},
        source="db",
        version=int(row["key_version"] or 0),
    )


async def load_registry() -> RegistrySnapshot:
    """读注册表与角色覆盖并组装快照。

    DB 不可用 / 表不存在 → ``loaded=False`` 的空快照；调用方必须保留上一轮
    热缓存，不能把一次数据库抖动解释成「管理员清空了配置」。
    """
    try:
        async for session in get_session():
            prow = (await session.execute(text(_SELECT_PROVIDERS))).mappings().all()
            mrow = (await session.execute(text(_SELECT_MODELS))).mappings().all()
            crow = (await session.execute(text(_SELECT_CREDENTIALS))).mappings().all()
            rrow = (await session.execute(text(_SELECT_ROLES))).mappings().all()
            srow = (await session.execute(text(_SELECT_SPECIALIZED))).mappings().all()
            break
        else:
            raise RuntimeError("get_session 未产出会话")
    except Exception:
        logger.warning(
            "[LLMRegistry] DB 注册表读取失败，模型目录保持代码层，云端凭据保持未配置"
            "（若表不存在，请先执行：alembic -c alembic.ini -n memory upgrade head）",
            exc_info=True,
        )
        return RegistrySnapshot(loaded=False)

    providers_by_id = {r["id"]: dict(r) for r in prow}
    creds: dict[str, ProviderCredentials] = {}
    meta: dict[str, dict] = {}
    for r in crow:
        cred = _credential(r, providers_by_id)
        if cred is not None:
            creds[r["provider_id"]] = cred
        # 与解密结果无关地记录展示元数据（见 RegistrySnapshot.credential_meta）
        updated_at = r["updated_at"]
        meta[r["provider_id"]] = {
            "fingerprint": r["key_fingerprint"] or None,
            "last4": r["key_last4"] or None,
            "rotatedAt": updated_at.isoformat() if updated_at else None,
            "rotatedBy": r["updated_by"] or None,
        }

    # provider 的 base_url / extra_headers 也属于 DB 配置，即使当前没有 API Key
    # 行，也要注入凭据快照，让运行时不要回落到 env 的旧地址。
    for provider_id, provider_row in providers_by_id.items():
        if provider_id in creds:
            continue
        if provider_row.get("base_url") or provider_row.get("extra_headers"):
            creds[provider_id] = ProviderCredentials(
                provider=provider_id,
                base_url=provider_row.get("base_url") or None,
                extra_headers=provider_row.get("extra_headers") or {},
                source="db",
                version=0,
            )

    roles: dict[str, str] = {}
    role_meta: dict[str, dict] = {}
    for row in rrow:
        role = str(row["role"] or "")
        if role not in _model_roles.MODEL_ROLES:
            logger.warning("[LLMRegistry] 忽略未知模型角色覆盖: %s", role)
            continue
        roles[role] = str(row["model_name"] or "")
        updated_at = row["updated_at"]
        role_meta[role] = {
            "updatedBy": row["updated_by"] or None,
            "updatedAt": updated_at.isoformat() if updated_at else None,
        }

    specialized: dict[str, dict] = {}
    for row in srow:
        role = str(row["role"] or "")
        if role not in {"embedding", "rerank"}:
            logger.warning("[LLMRegistry] 忽略未知专项模型角色: %s", role)
            continue
        specialized[role] = {
            "role": role,
            "provider_id": str(row["provider_id"] or ""),
            "adapter": str(row["adapter"] or ""),
            "model_name": str(row["model_name"] or ""),
            "base_url": str(row["base_url"] or ""),
            "options": row["options"] or {},
            "enabled": bool(row["enabled"]),
            "last_probe_at": row["last_probe_at"],
            "last_probe_ok": row["last_probe_ok"],
            "last_probe_summary": row["last_probe_summary"],
            "last_probe_elapsed_ms": row["last_probe_elapsed_ms"],
            "updated_by": row["updated_by"] or None,
            "updated_at": row["updated_at"],
        }

    return RegistrySnapshot(
        providers=[dict(r) for r in prow],
        models=[_model_entry(r) for r in mrow],
        credentials=creds,
        credential_meta=meta,
        roles=roles,
        role_meta=role_meta,
        specialized=specialized,
        loaded=True,
    )


async def refresh_registry() -> bool:
    """拉取 DB 覆盖层并注入进程内缓存。返回是否成功。

    失败时**不清空**已有动态层（保留上次已知 DB 值），与 sys_config 一致。
    """
    global _last_runtime_signature

    snap = await load_registry()
    if not snap.loaded:
        return False
    signature = _snapshot_signature(snap)
    changed = signature != _last_runtime_signature
    _models.set_dynamic_providers(snap.providers)
    _models.set_dynamic_models(snap.models)
    _credentials.set_db_credentials(snap.credentials)
    _model_roles.inject_overrides(snap.roles, snap.role_meta)
    _specialized.set_bindings(_derive_specialized_bindings(snap))
    if changed:
        # DB 配置变化后必须丢弃已构建的客户端，否则旧 API Key/base_url 会继续被复用。
        # 延迟导入避免 registry_store 与 proxy 的模块级循环依赖。
        try:
            from backend.infra.llm import proxy as _proxy

            _proxy.invalidate_runtime_caches()
        except Exception:
            logger.warning("[LLMRegistry] 清理 LLM 实例缓存失败", exc_info=True)
        _last_runtime_signature = signature
    logger.info(
        "[LLMRegistry] DB 覆盖层已注入: %d 个厂商实例 / %d 个自建模型 / "
        "%d 份凭据 / %d 个角色覆盖",
        len(snap.providers), len(snap.models), len(snap.credentials), len(snap.roles),
    )
    return True


_REFRESH_INTERVAL_S = 15.0


async def refresh_loop(interval: float = _REFRESH_INTERVAL_S) -> None:
    """后台轮询循环（server startup 挂载；首轮立即拉取，异常不退出）。

    与 `sys_config.refresh_loop` 同构。区别在于失败方向：sys_config 是
    fail-closed（宁可用默认值），这里是 **fail-open**（保留上次已知 DB 值），
    绝不能因一次 DB 抖动把模型清单/凭据替换成旧 env。
    """
    while True:
        try:
            await refresh_registry()
        except Exception:
            logger.warning(
                "[LLMRegistry] 刷新循环异常，保留上次已知覆盖层", exc_info=True
            )
        await asyncio.sleep(interval)


def reset_for_tests() -> None:
    """测试态注入点：清空动态层与 DB 凭据覆盖。"""
    global _last_runtime_signature
    _models.reset_dynamic_models_for_tests()
    _model_roles.reset_overrides()
    _credentials.reset_credentials_for_tests()
    _specialized.reset_for_tests()
    _last_runtime_signature = None
