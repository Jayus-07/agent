"""config/model_roles.py — 模型角色注册表与统一解析入口

背景与完整设计见 `docs/model-config-governance-design.md`。

## 这一层解决什么

模型选型原先散落在 `.env` 的十几个变量名里，靠「变量名后缀」表达语义
（`LLM_MODEL` / `DOC_LLM_MODEL` / `TOOL_SELECTOR_MODEL` / `LLM_FALLBACK_MODEL`
/ `EMBEDDING_MODEL` / `RERANK_MODEL` / `EVAL_GEN_MODEL` / `RAG_OCR_DASHSCOPE_MODEL`）。
本模块把它们收敛为显式的 **role（角色）**，并提供唯一解析入口，
使「当前哪个角色用哪个模型、值从哪来」可被程序回答，而不是靠人读 .env。

## 当前范围（2026-09-19）

解析链 = DB 覆盖 → 代码默认。
模型配置已切换为数据库唯一来源；环境变量名只作为管理端兼容展示字段保留，
不会再参与模型选择。`config/llm.py` / `config/rag.py` 的既有常量仍由本模块物化，
但没有数据库覆盖时只代表未配置的代码默认值。

## 硬约束（勿破）

1. **纯 stdlib、零 IO、零重依赖。** 本模块处在 `backend.config` 的导入链上
   （`config/__init__.py` → `config.llm` → 本模块），而 `backend.config` 几乎被
   所有模块导入。此处引入 SQLAlchemy / langchain / torch 会让每个进程每次启动
   都付这笔钱 —— 仓库已在 `config/llm.py` 记录过 torch 导入 6.3s 的教训
   （`_EVAL_DEVICE_RAW` 的延迟解析注释）。

2. **模块级不得 import `backend.infra.llm.*`。** 那会先执行
   `backend.infra.llm/__init__.py` → `factory` + `proxy` → langchain，
   既把重依赖拖进配置导入链，又与 `proxy.py` 形成循环导入
   （`proxy` → `backend.config.llm` → 本模块 → `infra.llm` → `proxy`）。
   需要 `infra.llm` 的数据一律**函数内延迟 import**（见 `provider_of` /
   `get_secret`），这些函数只在 `infra.llm` 已加载的调用方里被用。

3. **大小写敏感。** 模型名 `MiniMax-M3` / `Qwen/Qwen3-32B` / `BAAI/bge-m3` 一律
   不得被 `lower()` 破坏。这是 `services/sys_config.py` 的 `_normalize()` 不能
   直接复用于模型角色的原因（它对守卫开关强制小写）。

4. **不猜测继承。** 见下方 `resolve_raw` / `resolve_effective` 的区分说明。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "MODEL_ROLES",
    "RoleSpec",
    "resolve_raw",
    "resolve_effective",
    "resolve_name",
    "resolve_runtime_name",
    "provider_of",
    "get_secret",
    "effective_snapshot",
    "validate_roles",
    "inject_overrides",
    "set_override",
    "reset_overrides",
]


# =====================================================
# 角色注册表（唯一事实来源）
# =====================================================

@dataclass(frozen=True)
class RoleSpec:
    """一个模型角色的静态定义。

    env_key:    历史环境变量名（仅兼容展示，不再作为运行时取值来源）
    default:    代码默认值 —— 必须与改造前 config/*.py 里的默认值逐位一致
    desc:       用途说明（管理端展示）
    inherit:    本角色 env 为空时跟随的角色（None = 无继承概念）
    has_inherit_semantics:
                env 为空是否具有**语义**（而非仅表示"没配"）。
                True 时 legacy 常量必须保留空值，不能物化成继承后的模型名。
    validator:  校验器名（见 validate_roles）；None = 无注册表校验
               （embedding/rerank/ocr 的模型不在 AVAILABLE_MODELS 内）
    requires_reindex:
                改值后必须全量重建向量索引（与向量索引语义空间强绑定）
    """
    env_key: str
    default: str = ""
    desc: str = ""
    inherit: str | None = None
    has_inherit_semantics: bool = False
    validator: str | None = None
    requires_reindex: bool = False


MODEL_ROLES: dict[str, RoleSpec] = {
    "main": RoleSpec(
        env_key="LLM_MODEL",
        default="MiniMax-M3",
        desc="主问答 LLM（问答/RAG 生成链路，前端运行时可切换）",
        validator="registered_model",
    ),
    "doc": RoleSpec(
        env_key="DOC_LLM_MODEL",
        default="",
        desc="入库链路文档级关键词抽取（空 = 走在线代理，不启用本地 Ollama）",
        inherit="main",
        # ⚠️ 空值有语义：消费方用 `if DOC_LLM_MODEL:` 判断是否启用本地 Ollama，
        #    物化成 main 的模型名会让"未配置"变成"配了"，行为改变。
        has_inherit_semantics=True,
        validator="registered_model",
    ),
    "metadata_extract": RoleSpec(
        env_key="METADATA_EXTRACT_MODEL",
        default="",
        desc="入库链路文档级统一元数据结构化抽取",
        inherit="main",
        has_inherit_semantics=True,
        validator="registered_model",
    ),
    "question_gen": RoleSpec(
        env_key="QUESTION_GEN_MODEL",
        default="",
        desc="入库链路模拟问题生成（Document Expansion）",
        inherit="main",
        has_inherit_semantics=True,
        validator="registered_model",
    ),
    "table_describe": RoleSpec(
        env_key="TABLE_DESCRIBE_MODEL",
        default="",
        desc="入库链路表格行语义描述",
        inherit="main",
        has_inherit_semantics=True,
        validator="registered_model",
    ),
    "tool_selector": RoleSpec(
        env_key="TOOL_SELECTOR_MODEL",
        default="",
        desc="FC 工具选择与填参（direct 模式门控；空 = 跟随主问答模型）",
        inherit="main",
        has_inherit_semantics=True,
        validator="registered_model",
    ),
    "fallback": RoleSpec(
        env_key="LLM_FALLBACK_MODEL",
        default="",
        desc="熔断开路 / 重试耗尽后的备用模型（空 = 不切备用，按降级策略处理）",
        has_inherit_semantics=True,
        validator="registered_model",
    ),
    "ocr": RoleSpec(
        env_key="RAG_OCR_DASHSCOPE_MODEL",
        default="qwen-vl-max",
        desc="扫描件在线 OCR（dashscope 供应商逐页识别）",
    ),
    "embedding": RoleSpec(
        env_key="EMBEDDING_MODEL",
        default="text-embedding-v3",
        desc="向量化模型（与向量索引语义空间强绑定）",
        requires_reindex=True,
    ),
    "rerank": RoleSpec(
        env_key="RERANK_MODEL",
        default="qwen3-rerank",
        desc="检索结果重排模型",
    ),
    "eval_gen": RoleSpec(
        env_key="EVAL_GEN_MODEL",
        default="",
        desc="评测答案生成 / RAGAS（需绑定已登记且可用的模型）",
        validator="registered_model",
    ),
}

# 合法来源标注（供管理端与排障展示）
SOURCE_DB = "db"
SOURCE_ENV = "env"
SOURCE_INHERIT = "inherit"
SOURCE_DEFAULT = "code-default"


# =====================================================
# 覆盖层（P1 由 services/sys_config 注入）
# =====================================================
# 由 registry_store.refresh_registry() 注入；本模块的 resolve_* 仍只读内存，
# 保持热路径零 IO。
_overrides: dict[str, str] = {}
_override_meta: dict[str, dict[str, Any]] = {}


def inject_overrides(values: dict[str, str],
                     meta: dict[str, dict[str, Any]] | None = None) -> None:
    """注入 DB 覆盖值（仅登记在 MODEL_ROLES 内的 role 生效）。"""
    _overrides.clear()
    _override_meta.clear()
    for role, value in (values or {}).items():
        if role in MODEL_ROLES and value is not None:
            _overrides[role] = value
    if meta:
        for role, info in meta.items():
            if role in _overrides:
                _override_meta[role] = dict(info)


def set_override(
    role: str,
    value: str,
    meta: dict[str, Any] | None = None,
) -> None:
    """增量写入一个 DB 覆盖，不影响其它角色的热缓存。

    管理端单角色保存后会先调用该函数，再等待后台注册表刷新；不能用
    ``inject_overrides`` 直接替换整张表，否则一次保存会把其它角色的覆盖
    短暂清空，造成并发请求看到混合配置。
    """
    if role not in MODEL_ROLES:
        raise KeyError(f"未注册的模型角色: {role!r}")
    _overrides[role] = value
    if meta is None:
        _override_meta.pop(role, None)
    else:
        _override_meta[role] = dict(meta)


def reset_overrides() -> None:
    """测试态注入点：清空数据库覆盖层，恢复代码默认语义。"""
    _overrides.clear()
    _override_meta.clear()


# =====================================================
# 解析
# =====================================================

def _env_of(role: str) -> str:
    """兼容保留的 env 读取入口；模型配置已禁止从 env 取值。"""
    del role
    return ""


def resolve_raw(role: str) -> dict[str, Any]:
    """解析角色的**字面**生效值：DB 覆盖 → 代码默认。

    不做 inherit 展开。用于物化 legacy 常量（`config/llm.py` 的 `LLM_MODEL`
    等）—— 这些常量的空值在消费方手里有语义（`if DOC_LLM_MODEL:` 之类），
    展开成继承值会改变行为。需要"实际会用哪个模型"时用 `resolve_effective`。
    """
    spec = MODEL_ROLES.get(role)
    if spec is None:
        raise KeyError(f"未注册的模型角色: {role!r}（已注册: {sorted(MODEL_ROLES)}）")

    if role in _overrides:
        return {"role": role, "value": _overrides[role], "source": SOURCE_DB,
                "env_key": spec.env_key, "explicit": True,
                "updated_by": (_override_meta.get(role) or {}).get("updatedBy"),
                "updated_at": (_override_meta.get(role) or {}).get("updatedAt")}

    return {"role": role, "value": spec.default, "source": SOURCE_DEFAULT,
            "env_key": spec.env_key, "explicit": False,
            "updated_by": None, "updated_at": None}


def resolve_effective(role: str) -> dict[str, Any]:
    """解析角色的**实际会用**的模型：DB 覆盖 → inherit → 代码默认。

    与 `resolve_raw` 的唯一差别是多了 inherit 展开。新代码（管理端、启动校验）
    用这个；物化 legacy 常量用 `resolve_raw`。
    """
    info = resolve_raw(role)
    spec = MODEL_ROLES[role]
    # 有非空值（DB 覆盖 / env / 非空代码默认）→ 直接用，不展开继承。
    # 落到这里即"空值"：可能来自 env 留空 + 代码默认为空串。
    if info["value"] or not spec.inherit:
        return {**info, "inherited_from": None}
    parent = resolve_effective(spec.inherit)
    return {
        "role": role,
        "value": parent["value"],
        "source": f"{SOURCE_INHERIT}:{spec.inherit}",
        "env_key": spec.env_key,
        "explicit": False,
        "inherited_from": spec.inherit,
        "updated_by": parent.get("updated_by"),
        "updated_at": parent.get("updated_at"),
    }


def resolve_name(role: str) -> str:
    """便捷取字面值（P0 物化 legacy 常量用）。"""
    return resolve_raw(role)["value"]


def resolve_runtime_name(role: str, legacy_value: str | None = None) -> str:
    """读取运行时模型名，优先 DB 覆盖并兼容历史模块常量。

    绝大多数旧调用点在模块导入时已经拿到环境变量常量，不能直接把它们全部
    替换成 ``resolve_effective``，否则测试桩和现有的环境变量语义会发生变化。
    该入口只在确有 DB 覆盖时切到热配置；没有 DB 覆盖时返回代码默认解析结果。
    `legacy_value` 只作为旧调用方的显式空值兼容参数，不再承载 env 模型。
    """
    info = resolve_effective(role)
    if info.get("source") == SOURCE_DB:
        return str(info.get("value") or "")
    if legacy_value is not None and not info.get("value"):
        return legacy_value
    return str(info.get("value") or "")


# =====================================================
# provider 归属 / 密钥
# =====================================================

def provider_of(model_name: str) -> str | None:
    """模型名 → provider 名。未注册的模型（embedding/rerank/ocr）返回 None。

    延迟 import：见模块头硬约束 2。
    """
    if not model_name:
        return None
    from backend.infra.llm.models import get_available_models
    for item in get_available_models():
        if item["name"] == model_name:
            return item["provider"]
    return None


def get_secret(provider: str) -> str | None:
    """取 provider 的出站密钥。

    **fail-loud 契约**：无法取得密钥时返回 None（= 视为未配置），
    绝不返回密文、占位符或任何非密钥内容。理由：出站密钥会被原样发给第三方
    provider，一旦返回 `enc:gAAAA...` 这类密文，对方回一个
    `401 Invalid API key`，而真因（主密钥缺失 / 轮换错配）会被埋在 provider
    的错误信息之下 —— 与 `config/llm.py` 记录的"真实原因被埋在 N 层语义错误
    之下"是同一类问题。

    只读加密凭据库（provider_credentials），解密失败同样走 None + 告警，
    不降级为读取环境变量或返回密文。
    """
    if not provider:
        return None
    try:
        from backend.infra.llm.credentials import resolve_credentials

        value = resolve_credentials(provider).api_key
    except Exception:
        return None
    return value or None


# =====================================================
# 快照 / 校验
# =====================================================

def effective_snapshot() -> list[dict[str, Any]]:
    """全部角色的生效值与来源（供管理端 GET 与启动校验）。"""
    out: list[dict[str, Any]] = []
    for role, spec in MODEL_ROLES.items():
        raw = resolve_raw(role)
        eff = resolve_effective(role)
        out.append({
            "role": role,
            "description": spec.desc,
            "envKey": spec.env_key,
            "value": eff["value"],
            "rawValue": raw["value"],
            "source": eff["source"],
            "inheritedFrom": eff.get("inherited_from"),
            "explicit": raw["explicit"],
            "default": spec.default,
            "emptyHasMeaning": spec.has_inherit_semantics,
            "requiresReindex": spec.requires_reindex,
            "updatedBy": eff.get("updated_by"),
            "updatedAt": eff.get("updated_at"),
        })
    return out


_VALIDATORS: dict[str, Any] = {}


def _registered_model(value: str) -> tuple[bool, str]:
    """校验模型名已注册。延迟 import（硬约束 2）。"""
    from backend.infra.llm.models import get_available_models
    names = {m["name"] for m in get_available_models()}
    if value in names:
        return True, ""
    return False, f"未在 AVAILABLE_MODELS 注册（可用: {sorted(names)}）"


_VALIDATORS["registered_model"] = _registered_model


def validate_roles() -> list[str]:
    """校验全部角色的生效值，返回警告消息列表（不抛异常）。

    供 `config/startup.py` 启动期调用。相比改造前只校验 `TOOL_SELECTOR_MODEL`
    一处，这里覆盖全部标了 validator 的角色 —— `LLM_FALLBACK_MODEL` 原先
    「须在 infra/llm/models.py 注册」只是 `.env` 里的一句注释，无任何强制。
    """
    warnings: list[str] = []
    for role, spec in MODEL_ROLES.items():
        if spec.validator is None:
            continue
        eff = resolve_effective(role)
        value = eff["value"]
        if not value:
            continue
        ok, reason = _VALIDATORS[spec.validator](value)
        if not ok:
            warnings.append(
                f"模型角色 {role}（{spec.env_key}）的值 {value!r} 非法：{reason}"
            )
    return warnings
