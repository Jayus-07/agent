"""registry_store.py — LLM 注册表 DB 覆盖层的读取与注入

把 DB 里的**厂商实例 / 凭据 / 用户自建模型**（迁移 0017 三张表）读成内存快照，
注入 `models.set_dynamic_models()` 与 `credentials.set_db_credentials()`，
使 `get_available_models()` / `resolve_credentials()` 自动带上 DB 覆盖。

设计要点（见 docs/model-config-governance-design.md B.3）：

1. **热路径零 IO**：调用方只在后台刷新循环里调 `refresh_registry()`
   （同 `sys_config.refresh_loop` 模式），问答链路只读进程内字典。
2. **fail-open**（与 `sys_config` 的 fail-closed 方向相反）：
   DB 不可用 / 表不存在 → 返回空快照并**保留代码层**。
   绝不能因为 DB 抖动让系统「没有任何可用模型」。
   注意与「守卫开关」的区别：守卫宁可误拦，模型清单宁可回退 —— 回退目标是
   代码层常量，是已知安全的缺省。
3. **凭据解密 fail-loud**：单条解密失败只跳过该 provider（它回落 env）并记
   error，不影响其它 provider。绝不把密文当 Key 用（见 shared/crypto 的说明）。

⚠️ 本模块顶层 import SQLAlchemy —— **不得被热路径模块（factory / proxy /
providers）顶层引用**，否则会把 SQLAlchemy 拖进 LLM 导入链。

P1a 阶段状态：文件与表已就绪，**刷新循环尚未挂载**（P1b 接线）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from backend.infra.llm import credentials as _credentials
from backend.infra.llm import models as _models
from backend.infra.llm.credentials import ProviderCredentials
from backend.memory.database import get_session
from backend.shared.crypto import DEFAULT_KEY_ENV, SecretCryptoError, decrypt_secret
from backend.shared.logger import logger


@dataclass(frozen=True)
class RegistrySnapshot:
    """一次 DB 读取的结果。`loaded=False` 表示 DB 不可用（勿据此清空动态层）。"""

    providers: list[dict] = field(default_factory=list)
    models: list[dict] = field(default_factory=list)
    credentials: dict[str, ProviderCredentials] = field(default_factory=dict)
    loaded: bool = False


_SELECT_PROVIDERS = """
    SELECT id, display_name, driver, base_url, network_scope,
           extra_headers, billing, is_builtin, enabled
    FROM llm_providers
    WHERE enabled = true
    ORDER BY id
"""

_SELECT_MODELS = """
    SELECT name, provider_id, display_name, description, capabilities,
           context_length, pricing
    FROM llm_models
    WHERE enabled = true
    ORDER BY name
"""

_SELECT_CREDENTIALS = """
    SELECT provider_id, key_cipher, key_fingerprint, key_last4, key_version
    FROM llm_provider_credentials
"""


def _model_entry(row) -> dict:
    """DB 行 → AVAILABLE_MODELS 兼容条目（pricing JSONB 展开为单价字段）。"""
    pricing = row["pricing"] or {}
    return {
        "provider": row["provider_id"],
        "name": row["name"],
        "display": row["display_name"] or row["name"],
        "description": row["description"] or "",
        "input_price_per_1m": float(pricing.get("input_price_per_1m", 0.0) or 0.0),
        "output_price_per_1m": float(pricing.get("output_price_per_1m", 0.0) or 0.0),
        "context_length": row["context_length"],
        "capabilities": row["capabilities"] or {},
        "source": "db",
    }


def _credential(row, providers_by_id: dict[str, dict]) -> ProviderCredentials | None:
    """DB 行 → ProviderCredentials。解密失败返回 None（调用方跳过该条）。"""
    provider_id = row["provider_id"]
    provider_row = providers_by_id.get(provider_id) or {}
    try:
        api_key = decrypt_secret(row["key_cipher"], key_env=DEFAULT_KEY_ENV)
    except SecretCryptoError as e:
        # fail-loud：绝不回落密文原文（那会变成 401 且真因被埋掉）
        logger.error(
            "[LLMRegistry] provider=%s 的凭据解密失败，已跳过（该实例回落 .env）: %s",
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
    """读三表并组装快照。DB 不可用 / 表不存在 → `loaded=False` 的空快照。"""
    try:
        async for session in get_session():
            prow = (await session.execute(text(_SELECT_PROVIDERS))).mappings().all()
            mrow = (await session.execute(text(_SELECT_MODELS))).mappings().all()
            crow = (await session.execute(text(_SELECT_CREDENTIALS))).mappings().all()
            break
        else:
            raise RuntimeError("get_session 未产出会话")
    except Exception:
        logger.warning(
            "[LLMRegistry] DB 覆盖层读取失败，模型与凭据保持代码层/ env 兜底"
            "（若表不存在，请先执行：alembic -c alembic.ini -n memory upgrade head）",
            exc_info=True,
        )
        return RegistrySnapshot(loaded=False)

    providers_by_id = {r["id"]: dict(r) for r in prow}
    creds: dict[str, ProviderCredentials] = {}
    for r in crow:
        cred = _credential(r, providers_by_id)
        if cred is not None:
            creds[r["provider_id"]] = cred

    return RegistrySnapshot(
        providers=[dict(r) for r in prow],
        models=[_model_entry(r) for r in mrow],
        credentials=creds,
        loaded=True,
    )


async def refresh_registry() -> bool:
    """拉取 DB 覆盖层并注入进程内缓存。返回是否成功。

    失败时**不清空**已有动态层（保留上次已知值 → 代码层），与 sys_config 一致。
    """
    snap = await load_registry()
    if not snap.loaded:
        return False
    _models.set_dynamic_models(snap.models)
    _credentials.set_db_credentials(snap.credentials)
    logger.info(
        "[LLMRegistry] DB 覆盖层已注入: %d 个厂商实例 / %d 个自建模型 / %d 份凭据",
        len(snap.providers), len(snap.models), len(snap.credentials),
    )
    return True


def reset_for_tests() -> None:
    """测试态注入点：清空动态层与 DB 凭据覆盖。"""
    _models.reset_dynamic_models_for_tests()
    _credentials.reset_credentials_for_tests()
