"""
models.py — Provider 注册表 + 可用模型清单

新增 Provider 只需:
  1. 在 PROVIDERS 注册
  2. 在 AVAILABLE_MODELS 添加模型条目
  3. 在 providers/ 目录实现 build_xxx() 和 get_xxx_balance() 函数

本模块是**代码层静态注册表**。用户自建（BYOK）的模型与厂商实例走 DB 覆盖层，
由 `registry_store.py` 读库后经 `set_dynamic_models()` 注入，统一从
`get_available_models()` / `resolve_provider()` 读取 —— 消费方不要再直接引用
`AVAILABLE_MODELS` 常量（那是 DB 覆盖之前的旧入口）。

设计见 docs/model-config-governance-design.md（§3.2 / 附录 B）。

⚠️ 顶层只允许 stdlib：本模块处在 backend.infra.llm 的高频导入链上，
且被 config 侧间接引用，任何重依赖（langchain / torch / sqlalchemy）都会拖慢冷启动。
"""

from __future__ import annotations

from typing import Any

from backend.shared.logger import logger

# 注：不要在这里顶层 import langchain_ollama —— 实测它连带 torch/transformers
# （~8s），而本模块处在 backend.infra.llm 的高频导入链上。registry 的 class
# 字段无任何消费方（工厂走 build_xxx()），置 None 即可。
#
# driver: 协议适配族（openai | anthropic | ollama）—— 决定用哪个客户端类构建。
#         **代码白名单，用户不可改**（配错即不可用）。
# billing: 计费口径（metered | subscription | local）—— 决定成本估算与预算阻断：
#         - metered      按价格表算 USD，计入预算
#         - subscription 恒 0，但显示「订阅制·不计 token」；不计入预算，仍记用量
#         - local        恒 0（自托管）；不计入预算，仍记用量
#         ⚠️ 语义混淆会让报表无法区分「真没花钱」与「价格没录」，勿合并后两者。
PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "class": None,  # 懒加载（langchain_ollama.ChatOllama，见 providers/ollama.py）
        "default_model": "qwen2.5:3b",
        "needs_api_key": False,
        "driver": "ollama",
        "billing": "local",
    },
    "deepseek": {
        "class": None,  # 懒加载（兼容 OpenAI 协议的 ChatOpenAI）
        "default_model": "deepseek-v4-flash",
        "needs_api_key": True,
        "driver": "openai",
        "billing": "metered",
    },
    "minimax": {
        "class": None,  # Anthropic Messages API（官方推荐路径，见 providers/minimax.py）
        "default_model": "MiniMax-M3",
        "needs_api_key": True,
        "driver": "anthropic",
        "billing": "metered",
    },
    "qwen": {
        "class": None,  # DashScope OpenAI 兼容协议
        "default_model": "qwen3.7-plus",
        "needs_api_key": True,
        "driver": "openai",
        "billing": "metered",
    },
    "qwen_tp": {
        "class": None,  # Qwen Token Plan（模型包端点，注册名带 @tp 后缀）
        "default_model": "qwen3.7-plus@tp",
        "needs_api_key": True,
        "driver": "openai",
        # 模型包按购买量计费，不走 token 计价 —— 是订阅制，不是「metered 且单价 0」
        "billing": "subscription",
    },
    "vllm": {
        "class": None,  # 自托管 vLLM（OpenAI 兼容协议），见 providers/vllm.py
        "default_model": "Qwen/Qwen3-32B-AWQ",
        "needs_api_key": True,
        "driver": "openai",
        "billing": "local",
    },
    "siliconflow": {
        "class": None,  # 硅基流动（OpenAI 兼容协议），见 providers/siliconflow.py
        "default_model": "Qwen/Qwen3-8B",
        "needs_api_key": True,
        "driver": "openai",
        "billing": "metered",
    },
}


# 可用模型清单（前端展示 + set_current 校验用 + cost 估算）
# input_price_per_1m / output_price_per_1m: USD per 1M tokens（cost 估算用）
AVAILABLE_MODELS = [
    {
        "provider": "qwen",
        "name": "qwen3.7-plus",
        "display": "Qwen 3.7 Plus - 在线",
        "description": "阿里云百炼 Qwen3.7-Plus，OpenAI 兼容协议，需要 API Key",
        "input_price_per_1m": 0.4,
        "output_price_per_1m": 1.2,
    },
    {
        "provider": "qwen_tp",
        "name": "qwen3.7-plus@tp",
        "display": "Qwen 3.7 Plus - Token Plan",
        "description": "阿里云百炼模型包端点（sk-sp- Key），需配置 QWEN_TP_API_KEY",
        "input_price_per_1m": 0.0,   # 模型包按购买量计费，不走 token 计价
        "output_price_per_1m": 0.0,
    },
    {
        "provider": "ollama",
        "name": "qwen2.5:3b",
        "display": "Qwen 2.5 (3B) - 本地",
        "description": "本地 Ollama，免费，无需 API Key",
        "input_price_per_1m": 0.0,
        "output_price_per_1m": 0.0,
    },
    {
        "provider": "deepseek",
        "name": "deepseek-v4-flash",
        "display": "DeepSeek V4-Flash - 云端",
        "description": "DeepSeek V4-Flash，高并发低延迟，需要 API Key",
        "input_price_per_1m": 0.14,
        "output_price_per_1m": 0.28,
    },
    {
        "provider": "minimax",
        "name": "MiniMax-M3",
        "display": "MiniMax M3 - 云端",
        "description": "MiniMax-M3，OpenAI 兼容协议，需要 API Key",
        "input_price_per_1m": 3.0,
        "output_price_per_1m": 15.0,
    },
    {
        "provider": "vllm",
        "name": "Qwen/Qwen3-32B-AWQ",
        "display": "Qwen3 32B (AWQ) - 自托管",
        "description": "自托管 vLLM（OpenAI 兼容），需配置 VLLM_API_BASE/VLLM_API_KEY",
        "input_price_per_1m": 0.0,
        "output_price_per_1m": 0.0,
    },
    {
        # 硅基流动价格未核（0 会使成本估算低估），接入后按账单回填
        "provider": "siliconflow",
        "name": "Qwen/Qwen3-32B",
        "display": "Qwen3 32B - 硅基流动",
        "description": "硅基流动 Qwen3-32B，OpenAI 兼容协议，需在供应商页配置 API Key",
        "input_price_per_1m": 0.0,
        "output_price_per_1m": 0.0,
    },
    {
        # 硅基流动价格未核（0 会使成本估算低估），接入后按账单回填
        "provider": "siliconflow",
        "name": "Qwen/Qwen3-8B",
        "display": "Qwen3 8B - 硅基流动",
        "description": "硅基流动 Qwen3-8B，OpenAI 兼容协议，需在供应商页配置 API Key",
        "input_price_per_1m": 0.0,
        "output_price_per_1m": 0.0,
    },
]


def get_model_pricing(model_name: str) -> tuple[float, float]:
    """返回 (input_price_per_1m, output_price_per_1m) USD。未匹配返回 (0, 0)。"""
    for m in get_available_models():
        if m["name"] == model_name:
            return (
                float(m.get("input_price_per_1m", 0.0)),
                float(m.get("output_price_per_1m", 0.0)),
            )
    return 0.0, 0.0


# provider → 历史环境变量名（仅兼容旧 API 字段与文档迁移，不参与运行时取值）。
# provider→是否需要 Key 的兼容事实来源：startup 校验、专用模型配置校验共用；
# 实际 Key 必须从数据库加密凭据表解析。
PROVIDER_API_KEY_ENV = {
    "ollama": None,
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
    "qwen_tp": "QWEN_TP_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "vllm": "VLLM_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
}

# 模型用途类型与协议类型解耦：同一个 OpenAI 兼容供应商可以同时提供
# chat、embedding、rerank，调用端必须按用途选择对应适配器和端点。
MODEL_KINDS = ("chat", "embedding", "rerank", "vision", "speech")
MODEL_KIND_LABELS = {
    "chat": "文本模型",
    "embedding": "向量模型",
    "rerank": "重排模型",
    "vision": "视觉模型",
    "speech": "语音模型",
}
_SPECIALIZED_MODEL_ROLES = {"embedding", "rerank"}


def normalize_model_kind(value: str | None) -> str:
    """规范化模型用途；历史未标注模型按文本模型兼容。"""
    kind = (value or "chat").strip().lower()
    if kind not in MODEL_KINDS:
        raise ValueError(
            f"模型用途必须是 {', '.join(MODEL_KINDS)}，当前为 {value!r}"
        )
    return kind


def model_kind_of(entry: dict | None) -> str:
    """读取目录条目的用途；旧代码/静态条目缺字段时按 chat。"""
    return normalize_model_kind((entry or {}).get("model_kind"))


def expected_model_kind(role: str) -> str:
    """返回角色允许绑定的模型用途。"""
    return role if role in _SPECIALIZED_MODEL_ROLES else "chat"


def is_model_kind_compatible(role: str, model_kind: str | None) -> bool:
    """判断角色与模型用途是否匹配。非法用途不应被管理端保存。"""
    try:
        return expected_model_kind(role) == normalize_model_kind(model_kind)
    except ValueError:
        return False


def is_registered_model(model_name: str) -> bool:
    """模型名是否在当前生效注册表中（代码层 + DB 动态层）。"""
    return any(m["name"] == model_name for m in get_available_models())


def get_provider_api_key_env(model_name: str) -> str | None:
    """模型名 → 启用所需 API key 的环境变量名。

    未注册的模型返回 None（校验方应先过 is_registered_model）；
    ollama 等本地 provider 返回 None（无需 key）。
    """
    for m in get_available_models():
        if m["name"] == model_name:
            return PROVIDER_API_KEY_ENV.get(m["provider"])
    return None


def validate_override_model(
    model: str,
    *,
    ollama_enabled: bool | None = None,
) -> tuple[bool, str]:
    """校验「请求级模型覆盖」是否可用 → (ok, reason)。

    这是覆盖校验的**单一事实来源**：`proxy.set_request_model`（上下文绑定，宽容
    静默）与 API 边界（fail-fast 400）共用同一份判断，避免两套规则漂移
    （见 docs/model-config-admin-ui-design.md §13.1）。

    规则与 set_request_model 既有口径逐条对齐：
      - 未在 AVAILABLE_MODELS 注册            → 拒绝
      - provider == ollama 且 Ollama 未启用    → 拒绝
      - provider 需要 Key 而该 Key 未配置      → 拒绝

    ollama_enabled: 显式注入 Ollama 开关；None = 读运行时配置。调用方（proxy）传
      自己的模块级常量，既保持既有 monkeypatch 路径有效，也让本函数保持可测。

    返回 (True, "") 表示可用；否则 reason 为面向用户的中文原因（可直接进 400 detail）。
    空 model 视为「不覆盖」（调用方自行判断是否需要覆盖），返回 ok。
    """
    name = (model or "").strip()
    if not name:
        return True, ""
    if not is_registered_model(name):
        return False, f"未知模型：{name}"
    entry = next((m for m in get_available_models() if m["name"] == name), None)
    provider = str((entry or {}).get("provider") or "")
    if provider == "ollama":
        enabled = ollama_enabled
        if enabled is None:
            # 延迟导入：config.llm 是上层配置模块，模块级导入会形成循环。
            from backend.config.llm import OLLAMA_ENABLED
            enabled = OLLAMA_ENABLED
        if not enabled:
            return False, "Ollama 当前未启用（cloud 模式禁用本地模型）"
    if provider != "ollama":
        # 凭据唯一来自 DB；不要在请求级模型覆盖校验中重新读取旧 env。
        from backend.infra.llm.credentials import resolve_credentials

        try:
            configured = resolve_credentials(provider).api_key
        except Exception:
            configured = None
        if not configured:
            return False, f"供应商 {provider} 未在数据库配置 API Key，无法使用模型 {name}"
    return True, ""


def compute_cost_usd(model_name: str,
                     prompt_tokens: int, completion_tokens: int) -> float:
    """按 model pricing 表估算单次调用 cost (USD)。"""
    in_p, out_p = get_model_pricing(model_name)
    return round(
        (prompt_tokens / 1_000_000) * in_p +
        (completion_tokens / 1_000_000) * out_p,
        6,
    )


# =====================================================
# Embedding / Reranker 定价（DashScope 官方 CNY → USD 换算）
# =====================================================
# 汇率：1 CNY ≈ 0.138 USD（2026-09 近似值，可按需调整）
_CNY_TO_USD = 0.138

# DashScope Embedding/Reranker 定价表（USD per 1M tokens）
# 来源：阿里云百炼官方定价（CNY/M tokens）× 汇率换算
# - qwen3-rerank: 0.5 元/M → 0.069 USD/M
# - text-embedding-v3: 0.125 元/M → 0.01725 USD/M
# - text-embedding-v4: 0.125 元/M → 0.01725 USD/M
# - qwen-vl-embedding text: 0.7 元/M → 0.0966 USD/M
# - qwen-vl-embedding image: 1.8 元/M → 0.2484 USD/M
EMBEDDING_RERANK_PRICING: dict[str, dict] = {
    "qwen3-rerank": {
        "component": "rerank",
        "input_per_1m_usd": round(0.5 * _CNY_TO_USD, 6),   # 0.069
    },
    "text-embedding-v3": {
        "component": "embedding",
        "input_per_1m_usd": round(0.125 * _CNY_TO_USD, 6), # 0.01725
    },
    "text-embedding-v4": {
        "component": "embedding",
        "input_per_1m_usd": round(0.125 * _CNY_TO_USD, 6), # 0.01725
    },
    "qwen-vl-embedding": {
        "component": "embedding",
        "input_per_1m_usd": round(0.7 * _CNY_TO_USD, 6),   # 0.0966 (text)
        "image_per_1m_usd": round(1.8 * _CNY_TO_USD, 6),   # 0.2484 (image)
    },
}


def compute_embedding_cost(model_name: str, total_tokens: int) -> float:
    """按 Embedding/Reranker 定价表估算单次调用 cost (USD)。

    Embedding/Reranker 只有输入 token，无输出 token。
    未匹配的模型返回 0.0（Local 模式无 API 费用）。
    """
    pricing = EMBEDDING_RERANK_PRICING.get(model_name)
    if not pricing:
        return 0.0
    price_per_1m = pricing.get("input_per_1m_usd", 0.0)
    return round((total_tokens / 1_000_000) * price_per_1m, 6)


# =====================================================
# 动态注册表（DB 覆盖层）—— 统一读取入口
# =====================================================
# DB 里 llm_models / llm_providers 的条目由 registry_store.py 读出后注入这里。
# 热路径只读进程内列表（零 IO）：DB 访问在后台刷新循环里做（同 sys_config 模式）。
#
# 动态层由 registry_store 后台刷新注入；数据库不可用时保留上一轮快照，避免热路径
# 因瞬时 DB 故障丢失可用模型。
_dynamic_models: list[dict] = []
_dynamic_providers: dict[str, dict[str, Any]] = {}

# 未注册模型只告警一次，避免热路径刷屏
_warned_unknown_models: set[str] = set()


class ProviderResolutionError(LookupError):
    """模型名无法解析出 provider（既不在注册表，也无法从名称判定）。"""


def set_dynamic_models(entries: list[dict] | None) -> None:
    """注入 DB 覆盖层的模型条目（由 registry_store 的刷新循环调用）。

    条目形状与 AVAILABLE_MODELS 一致，另需 `source`（'db'）与 `provider`。
    同名条目覆盖代码层条目；代码层独有的条目保留（DB 抖动不导致模型消失）。
    """
    global _dynamic_models
    _dynamic_models = list(entries or [])


def reset_dynamic_models_for_tests() -> None:
    """测试态注入点：清空动态层，恢复纯代码层语义。"""
    _dynamic_models.clear()
    _dynamic_providers.clear()
    _warned_unknown_models.clear()


def set_dynamic_providers(entries: list[dict] | None) -> None:
    """注入 DB 供应商元数据，供凭据解析和协议构建使用。

    ``driver`` 是迁移层 CHECK 约束保护的协议族；动态层只补实例地址、
    计费口径等元数据，不允许借此执行任意 Python 代码。
    """
    _dynamic_providers.clear()
    for entry in entries or []:
        provider_id = str(entry.get("id") or "").strip()
        if not provider_id:
            continue
        _dynamic_providers[provider_id] = dict(entry)


def get_provider_entry(provider: str) -> dict[str, Any] | None:
    """按 provider 取当前生效元数据，DB 实例优先于代码默认。"""
    if provider in _dynamic_providers:
        return _dynamic_providers[provider]
    return PROVIDERS.get(provider)


def get_provider_ids() -> list[str]:
    """返回代码层与 DB 动态层的 provider id，保持稳定去重顺序。"""
    return list(dict.fromkeys([*PROVIDERS, *_dynamic_providers]))


def get_available_models() -> list[dict]:
    """可用模型清单的**唯一读取入口**（代码层 + DB 覆盖层）。

    无动态条目时直接返回代码层对象（零拷贝，保持与历史 `AVAILABLE_MODELS`
    完全一致的语义与身份）。有覆盖时才合并，同名以 DB 为准。
    """
    if not _dynamic_models:
        return AVAILABLE_MODELS
    merged = {m["name"]: m for m in AVAILABLE_MODELS}
    for m in _dynamic_models:
        merged[m["name"]] = m
    return list(merged.values())


def get_model_entry(model_name: str) -> dict | None:
    """按模型名取条目（DB 覆盖层优先）。未注册返回 None。"""
    for m in _dynamic_models:
        if m["name"] == model_name:
            return m
    for m in AVAILABLE_MODELS:
        if m["name"] == model_name:
            return m
    return None


def is_known_model(model_name: str) -> bool:
    """模型是否在当前生效的注册表内（含 DB 覆盖层）。"""
    return get_model_entry(model_name) is not None


def resolve_provider(
    model_name: str,
    *,
    strict: bool = False,
    default_provider: str = "ollama",
) -> str:
    """模型名 → provider。

    `strict=False`（默认）：保持历史行为（与 `LLMFactory._get_provider` 逐位一致）——
    注册表未命中时按名称启发式推断，仍判不出则回落 `default_provider` 并**记一次
    warning**（历史实现是完全静默的，静默会让「自建模型被误判成 ollama」无从发现）。

    `strict=True`：判不出即抛 `ProviderResolutionError`。供管理端校验与探测使用。

    ⚠️ 为什么不默认 fail-closed（设计 B.5#4 的完整落地推迟到 P1b）：
    **Ollama 本地模型名不可穷举**（`llama3` / `qwen2.5:7b` / 任意 pull 下来的名字），
    评测生成等受治理的链路现在要求通过 `eval_gen` 显式绑定已登记模型；
    这里仍保留宽松回落，仅兼容 Ollama 的其它历史调用点。贸然全局改成抛错
    会打断尚未迁移的本地链路。自建模型应显式登记到 DB 覆盖层。
    """
    entry = get_model_entry(model_name)
    if entry is not None:
        return entry["provider"]

    # ── 名称启发式（历史行为，勿改判定顺序）──
    if "deepseek" in model_name:
        return "deepseek"
    lowered = model_name.lower()
    if "minimax" in lowered:
        return "minimax"
    if "qwen" in lowered and ":" not in model_name:
        # 带冒号标签（如 qwen2.5:3b）是本地 Ollama 模型，此处只匹配在线 Qwen
        return "qwen"

    if strict:
        raise ProviderResolutionError(
            f"模型 {model_name!r} 既不在模型注册表内，也无法从名称判定 provider。"
            f"请在 AVAILABLE_MODELS 登记，或经管理端新增后重试。"
        )

    if model_name not in _warned_unknown_models:
        _warned_unknown_models.add(model_name)
        logger.warning(
            "[LLMRegistry] 模型 %r 未在注册表内且名称无法判定 provider，"
            "按 %s 处理（若为自建云端模型，请在管理端登记 —— 否则会走错端点）",
            model_name, default_provider,
        )
    return default_provider


def get_provider_driver(provider: str) -> str | None:
    """provider → 协议驱动（openai | anthropic | ollama）；未知 provider 返回 None。"""
    spec = get_provider_entry(provider)
    return spec.get("driver") if spec else None


def get_provider_billing(provider: str) -> str:
    """provider → 计费口径。

    未知 provider 按 `metered` 处理（保守：宁可照常计价，不可静默免费用量）。
    """
    spec = get_provider_entry(provider)
    return spec.get("billing", "metered") if spec else "metered"


def get_model_billing(model_name: str) -> str:
    """模型名 → 计费口径（先解析 provider，再查其口径）。"""
    try:
        return get_provider_billing(resolve_provider(model_name))
    except ProviderResolutionError:
        return "metered"
