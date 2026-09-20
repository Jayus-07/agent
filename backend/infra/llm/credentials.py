"""credentials.py — 出站凭据（API Key / base_url）解析的唯一入口

**为什么需要这一层**（设计 B.5#1 / #5）：
`providers/*.py` 原来用 `from backend.config import QWEN_API_KEY` —— 那是**导入时值拷贝**，
配置只在模块首次导入的瞬间求值一次。于是「免重启生效 / 运行时轮换 / 多实例」全部不成立，
管理端做出来也只是重启才生效。

现在凭据由本模块在**调用时**解析（模块属性访问，而非值拷贝），并通过
`ProviderCredentials` 显式传给 `build_xxx(model, credentials=...)`。
云供应商没有数据库凭据时返回未配置快照，禁止回落旧 env；本地 Ollama
仅保留本机地址这一项基础设施默认值。

DB 覆盖层由 `registry_store.py` 刷新时注入 `_db_overrides`；热路径始终只读
进程内字典（零 IO）。

设计见 docs/model-config-governance-design.md 附录 B.3 / B.5。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from backend.config import llm as _llm
from backend.infra.llm.models import (
    PROVIDER_API_KEY_ENV,
    PROVIDERS,
    get_provider_entry,
    get_provider_ids,
)

# MiniMax 走 Anthropic Messages API（官方推荐路径）。该 URL 原为
# providers/minimax.py 内的硬编码字面量，**不是** config 的 MINIMAX_API_BASE
# （那是另一个 OpenAI 兼容端点，值不同）。搬到这里只为集中，值不变。
MINIMAX_ANTHROPIC_URL = "https://api.minimaxi.com/anthropic"

# 密钥缺失报错的可操作补充（原文案逐字保留，避免前端/运维文档失配）
_KEY_HINTS: dict[str, str] = {
    "qwen_tp": "（sk-sp- 模型包 Key）",
    "vllm": "（deploy.sh 会生成）",
}

_OLLAMA_DISABLED_MESSAGE = (
    "ENV_MODE=cloud 已禁用本地 Ollama，请在 .env 中设置 ENV_MODE=local 后重试"
)


class UnknownProviderError(LookupError):
    """provider 不在注册表内。"""


@dataclass(frozen=True)
class ProviderCredentials:
    """一次调用所需的出站凭据快照。

    字段为 None / 空表示「数据库尚未配置该项」。云供应商不能把它解释为
    旧 env 或 config 常量的回退信号。
    """

    provider: str
    api_key: str | None = None
    base_url: str | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    # db | unconfigured —— 生效快照与管理端展示「这个值从哪来」
    source: str = "unconfigured"
    # DB 轮换计数；与缓存实例比对可发现「密钥已换但仍用旧实例」（B.5#2）
    version: int = 0

    def resolved_headers(self, defaults: Mapping[str, str] | None = None) -> dict[str, str]:
        """合并额外请求头（调用方的默认值在前，凭据覆盖在后）。"""
        out = dict(defaults or {})
        out.update(self.extra_headers or {})
        return out


# ── DB 覆盖层注入点（由 registry_store 后台刷新）───────────────────────
# provider_id → ProviderCredentials；由 registry_store 的刷新循环写入。
_db_overrides: dict[str, ProviderCredentials] = {}


def set_db_credentials(overrides: dict[str, ProviderCredentials] | None) -> None:
    """注入 DB 凭据（registry_store 调用）。None / 空 = 清空 DB 热缓存。"""
    _db_overrides.clear()
    if overrides:
        _db_overrides.update(overrides)


def reset_credentials_for_tests() -> None:
    """测试态注入点：清空 DB 覆盖层。"""
    _db_overrides.clear()


def invalidate_credentials_cache() -> None:
    """凭据变更后调用（清 DB 覆盖层 + 供工厂清实例缓存）。

    P1b 接线后此函数还会通知 `LLMFactory.invalidate()`，避免
    「测试通过、线上仍用旧 key 且不报错」。
    """
    _db_overrides.clear()


def _resolve_unconfigured_credentials(
    provider: str,
    provider_entry: Mapping[str, Any],
) -> ProviderCredentials:
    """构造未在数据库配置的凭据快照，不读取任何模型 env。"""
    # 本地 Ollama 地址属于本机运行时基础设施，不是云供应商密钥；保留代码默认
    # 地址以便明确绑定到 ollama 的本地模型仍可工作。云端 URL 必须来自 DB。
    base_url = None
    if provider == "ollama":
        base_url = provider_entry.get("base_url") or "http://localhost:11434"
    else:
        base_url = provider_entry.get("base_url") or None

    return ProviderCredentials(
        provider=provider,
        api_key=None,
        base_url=base_url,
        extra_headers=provider_entry.get("extra_headers") or {},
        extra_body=provider_entry.get("extra_body") or {},
        source="unconfigured",
        version=0,
    )


def resolve_credentials(
    provider: str,
    *,
    model_name: str | None = None,
) -> ProviderCredentials:
    """解析某 provider 当前生效的凭据（热路径安全：纯内存读取）。

    唯一来源：数据库注册表；未登记的供应商返回未配置快照。
    `model_name` 预留：同一 provider 下不同模型可能绑定不同 Key（P1b）。
    """
    provider_entry = get_provider_entry(provider)
    if provider_entry is None:
        raise UnknownProviderError(
            f"未知 provider: {provider!r}（已知: {sorted(PROVIDERS)}）"
        )

    override = _db_overrides.get(provider)
    if override is not None:
        return override
    return _resolve_unconfigured_credentials(provider, provider_entry)


def credentials_version(provider: str) -> int:
    """凭据版本（DB 轮换计数）；没有 DB 覆盖时返回 0。"""
    override = _db_overrides.get(provider)
    return override.version if override else 0


def missing_key_message(provider: str) -> str | None:
    """密钥缺失时的可操作报错；无需密钥或已配置则返回 None。

    取代 `LLMFactory.set_current` 里 8 个硬编码 if（设计 B.5#5）——
    自建 provider 不会再因为「不在 if 链里」被静默跳过校验。
    """
    if provider == "ollama":
        return None  # 本地部署无需 Key（可用性看 ENV_MODE，见 check_provider_usable）
    api_key_env = PROVIDER_API_KEY_ENV.get(provider)
    if not api_key_env:
        return None  # 未登记密钥的 provider（自建/未知）不做密钥校验
    if resolve_credentials(provider).api_key:
        return None
    return f"供应商 {provider} 未在数据库配置 API Key{_KEY_HINTS.get(provider, '')}"


def check_provider_usable(provider: str) -> str | None:
    """provider 当前是否可用；返回不可用原因，None = 可用。

    仅做**本地可判定**的检查（密钥缺失 / 本地推理被禁用），
    不含网络连通性 —— 后者是管理端「测试」按钮的职责（分级探测，见 B.4）。
    """
    if provider == "ollama":
        if not getattr(_llm, "OLLAMA_ENABLED", False):
            return _OLLAMA_DISABLED_MESSAGE
        return None
    if get_provider_entry(provider) is None:
        return f"未知 provider: {provider}"
    return missing_key_message(provider)


def snapshot() -> dict[str, dict[str, Any]]:
    """全 provider 凭据生效快照（用于零行为变化验收与管理端展示）。

    **不含密钥明文**：只给「是否已配置 + 指纹级别信息」。
    """
    out: dict[str, dict[str, Any]] = {}
    provider_ids = list(dict.fromkeys([*PROVIDERS, *get_provider_ids()]))
    for provider in provider_ids:
        cred = resolve_credentials(provider)
        provider_entry = get_provider_entry(provider) or {}
        out[provider] = {
            "provider": provider,
            "hasApiKey": bool(cred.api_key),
            "baseUrl": cred.base_url,
            "driver": provider_entry.get("driver"),
            "billing": provider_entry.get("billing"),
            "source": cred.source,
            "version": cred.version,
            "usable": check_provider_usable(provider),
        }
    return out
