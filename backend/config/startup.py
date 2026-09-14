"""startup.py — pydantic Settings 启动校验（P1-14）

此前环境变量错误（端口非整数、依赖缺失、池参数倒挂等）要等到运行期
第一次调用才以晦涩的异常暴露。这里在服务启动时用 pydantic 做一次
集中校验：

  - fatal   → 抛 SettingsValidationError，服务拒绝启动（fail-fast）
  - warning → 记录 warning 日志，服务继续启动

校验范围 = 生产关键路径：数据库连接、LLM 提供方凭据、认证、连接池、
告警外推。非关键项（UI/性能调优）不在此校验。

用法（见 backend/app/server.py 启动钩子）：
    from backend.config.startup import validate_startup_settings
    warnings = validate_startup_settings()   # fatal 直接抛出
"""
from __future__ import annotations

import os
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.shared.logger import logger


class SettingsValidationError(Exception):
    """启动配置校验失败（fatal 级）— 服务应拒绝启动。"""


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SettingsValidationError(f"环境变量 {name}={raw!r} 不是合法整数")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise SettingsValidationError(f"环境变量 {name}={raw!r} 不是合法数字")


class DatabaseSettings(BaseModel):
    pg_host: str = "localhost"
    pg_port: int = Field(ge=1, le=65535)
    pg_user: str
    pg_password: str
    pg_database: str = "agent_memory"
    business_pgdatabase: str = "agent_business"
    pool_min: int = Field(ge=0)
    pool_max: int = Field(ge=1)

    @model_validator(mode="after")
    def _pool_order(self):
        if self.pool_min > self.pool_max:
            raise ValueError(
                f"DB_POOL_MIN_CONN({self.pool_min}) > DB_POOL_MAX_CONN({self.pool_max})"
            )
        return self


class LLMSettings(BaseModel):
    llm_model: str = Field(min_length=1)
    deepseek_api_key: Optional[str] = None
    max_retries: int = Field(ge=0)
    retry_backoff_base: float = Field(gt=0)
    fallback_model: str = ""

    @model_validator(mode="after")
    def _provider_credential(self):
        if self.llm_model.startswith("deepseek") and not (self.deepseek_api_key or "").strip():
            raise ValueError(
                f"LLM_MODEL={self.llm_model} 需要 DEEPSEEK_API_KEY（当前为空）"
            )
        return self


class AuthSettings(BaseModel):
    api_key: Optional[str] = None
    allow_unauthenticated: bool = False
    trust_user_header: bool = False
    user_id_header: str = "X-User-Id"


class AlertSettings(BaseModel):
    webhook_url: str = ""
    webhook_type: str = "generic"
    min_level: str = "warn"
    cooldown: float = Field(gt=0)

    @field_validator("webhook_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in ("generic", "wecom", "dingtalk", "feishu"):
            raise ValueError(
                f"ALERT_WEBHOOK_TYPE={v!r} 不支持（可选 generic/wecom/dingtalk/feishu）"
            )
        return v

    @field_validator("min_level")
    @classmethod
    def _known_level(cls, v: str) -> str:
        if v not in ("info", "warn", "error"):
            raise ValueError(f"ALERT_MIN_LEVEL={v!r} 不支持（可选 info/warn/error）")
        return v


class StartupSettings(BaseModel):
    """启动期集中校验的配置快照。"""

    database: DatabaseSettings
    llm: LLMSettings
    auth: AuthSettings
    alerts: AlertSettings
    environment: str = "development"


_VALID_ENVIRONMENTS = {"development", "testing", "staging", "production"}


def _postgres_checkpointer_available() -> bool:
    """LangGraph PostgresSaver 是否真的可用（驱动 + 后端包都在）。

    只做 import 探测，不建连接 —— 启动校验不该依赖数据库可用性，
    它要回答的是「配了 postgres 后端到底会不会生效」。
    """
    try:
        import psycopg  # noqa: F401
        from langgraph.checkpoint.postgres import PostgresSaver  # noqa: F401
    except Exception:
        return False
    return True


def _normalize_env(raw: str) -> str:
    v = raw.strip().lower()
    return v if v in _VALID_ENVIRONMENTS else "production"


def _build_settings() -> StartupSettings:
    return StartupSettings(
        database=DatabaseSettings(
            pg_host=_env("PGHOST", "localhost"),
            pg_port=_env_int("PGPORT", 5432),
            pg_user=_env("PGUSER", "postgres"),
            pg_password=_env("PGPASSWORD"),
            pg_database=_env("PGDATABASE", "agent_memory"),
            business_pgdatabase=_env("BUSINESS_PGDATABASE", "agent_business"),
            pool_min=_env_int("DB_POOL_MIN_CONN", 2),
            pool_max=_env_int("DB_POOL_MAX_CONN", 10),
        ),
        llm=LLMSettings(
            llm_model=_env("LLM_MODEL", "deepseek-v4-flash"),
            deepseek_api_key=_env("DEEPSEEK_API_KEY") or None,
            max_retries=_env_int("LLM_MAX_RETRIES", 2),
            retry_backoff_base=_env_float("LLM_RETRY_BACKOFF_BASE", 1.5),
            fallback_model=_env("LLM_FALLBACK_MODEL"),
        ),
        auth=AuthSettings(
            api_key=_env("API_KEY") or None,
            allow_unauthenticated=_env("ALLOW_UNAUTHENTICATED", "false").lower()
            in ("1", "true", "yes"),
            trust_user_header=_env("TRUST_USER_HEADER", "false").lower()
            in ("1", "true", "yes"),
            user_id_header=_env("USER_ID_HEADER", "X-User-Id"),
        ),
        alerts=AlertSettings(
            webhook_url=_env("ALERT_WEBHOOK_URL"),
            webhook_type=_env("ALERT_WEBHOOK_TYPE", "generic").lower(),
            min_level=_env("ALERT_MIN_LEVEL", "warn").lower(),
            cooldown=_env_float("ALERT_WEBHOOK_COOLDOWN", 300.0),
        ),
        environment=_normalize_env(_env("ENVIRONMENT", "development")),
    )


def validate_startup_settings() -> List[str]:
    """执行启动校验。

    Returns:
        warning 消息列表（已记录日志）。

    Raises:
        SettingsValidationError: 存在 fatal 配置错误（调用方应阻止服务启动）。
    """
    warnings: List[str] = []

    try:
        s = _build_settings()
    except Exception as e:  # pydantic ValidationError 等
        raise SettingsValidationError(str(e)) from e

    # ── warning 级 ──
    if not s.database.pg_password:
        # 记忆库不可用有运行时兜底（MemoryDatabaseUnavailable handler），
        # 本地开发可不配 PG；但生产环境必须配置
        warnings.append(
            "PGPASSWORD 未配置：PostgreSQL 不可用，记忆/业务库功能将降级。"
            "生产环境必须配置。"
        )
    elif s.database.pg_password == "postgres":
        warnings.append("PGPASSWORD 使用默认弱口令 'postgres'（仅限开发环境）")

    if not s.auth.api_key:
        warnings.append(
            "API_KEY 未配置：认证 fail-closed 生效，所有业务端点将返回 401。"
            "生产环境必须配置。"
        )
    elif len(s.auth.api_key) < 16:
        warnings.append(f"API_KEY 长度仅 {len(s.auth.api_key)} 位（建议 ≥ 32 位随机串）")

    if s.auth.allow_unauthenticated:
        warnings.append(
            "ALLOW_UNAUTHENTICATED=true：认证被显式豁免！仅限本地开发调试，"
            "生产环境必须关闭。"
        )

    if s.auth.trust_user_header:
        warnings.append(
            f"TRUST_USER_HEADER=true：行级安全用户身份取自 {s.auth.user_id_header} 头。"
            "请确保该头只能由可信网关注入（否则行级安全可被伪造绕过）。"
        )

    if s.alerts.webhook_url and not s.alerts.webhook_url.startswith(("http://", "https://")):
        warnings.append(
            f"ALERT_WEBHOOK_URL={s.alerts.webhook_url!r} 不是 http(s) URL，webhook 推送将失败"
        )

    # tool_selector 专用模型配置校验（warning 级：配错安全回退全局模型，
    # 但静默回退会让"以为在用 flash 实际在用主力模型"的问题无法察觉）
    _tool_selector_model = _env("TOOL_SELECTOR_MODEL").strip()
    if _tool_selector_model:
        from backend.infra.llm.models import get_provider_api_key_env, is_registered_model

        if not is_registered_model(_tool_selector_model):
            warnings.append(
                f"TOOL_SELECTOR_MODEL={_tool_selector_model} 未在 AVAILABLE_MODELS "
                f"注册，tool_selector 将回退全局模型"
            )
        else:
            _key_env = get_provider_api_key_env(_tool_selector_model)
            if _key_env and not _env(_key_env):
                warnings.append(
                    f"TOOL_SELECTOR_MODEL={_tool_selector_model} 需要 {_key_env}"
                    f"（当前为空），tool_selector 将回退全局模型"
                )

    # ── checkpointer 后端可用性（warning 级）──
    # LangGraph 的 PostgresSaver 需要 psycopg v3 + langgraph-checkpoint-postgres。
    # 依赖在 pyproject.toml（>=2.0）与 requirements-lock.txt（已钉版本）中**都已声明**，
    # 但运行时环境未必装上（本地 venv 就常缺）。缺依赖时 _build_checkpointer
    # 会静默降级成 MemorySaver：看着「已开启持久化」，实际只存在进程内、重启即失、
    # 多 worker 各存一份。这种「假开启」比明确关闭更危险（排查时会被日志里的
    # "enabled" 误导），所以在启动期直接点名，而不是等运行期 warning 被刷过去。
    _cp_owners = [
        _label for _label, _flag in (
            ("主图", "MAIN_GRAPH_CHECKPOINTER_ENABLED"),
            ("客服域", "CS_CHECKPOINTER_ENABLED"),
            ("旅游域", "TRAVEL_CHECKPOINTER_ENABLED"),
        )
        if _env(_flag, "false").lower() in ("1", "true", "yes")
    ]
    if _cp_owners and not _postgres_checkpointer_available():
        warnings.append(
            "以下子图开启了 checkpointer，但当前环境的 Postgres 后端不可用："
            + "、".join(_cp_owners)
            + "。缺少 psycopg v3 / langgraph-checkpoint-postgres"
              "（pyproject 与 requirements-lock 都已声明，仅本环境未安装），"
              "运行期会静默降级为 MemorySaver：状态仅存于进程内、重启即失、"
              "多 worker 各存一份。补齐：pip install langgraph-checkpoint-postgres"
              "（版本取 requirements-lock.txt 中的锁定值）；"
              "或关闭上述开关，避免误以为已持久化。"
        )

    # ── production fatal ──
    _is_prod = s.environment == "production"
    _fatal: List[str] = []

    if _is_prod and s.auth.allow_unauthenticated:
        _fatal.append(
            "ENVIRONMENT=production + ALLOW_UNAUTHENTICATED=true：生产环境禁止豁免认证。"
            "请设置 ENVIRONMENT=development 或关闭 ALLOW_UNAUTHENTICATED。"
        )

    if _is_prod and not s.auth.api_key:
        _fatal.append(
            "ENVIRONMENT=production + API_KEY 未配置：生产环境必须配置 API_KEY。"
        )

    if _is_prod and s.auth.api_key and len(s.auth.api_key) < 16:
        _fatal.append(
            f"ENVIRONMENT=production + API_KEY 长度仅 {len(s.auth.api_key)} 位"
            "（生产环境要求 ≥ 16 位）。"
        )

    if _fatal:
        for f in _fatal:
            logger.error(f"[Startup 校验] FATAL: {f}")
        raise SettingsValidationError(
            "生产环境配置校验失败:\n" + "\n".join(f"  - {f}" for f in _fatal)
        )

    for w in warnings:
        logger.warning(f"[Startup 校验] {w}")

    logger.info(
        "[Startup 校验] 通过 — "
        f"PG={s.database.pg_host}:{s.database.pg_port}/{s.database.business_pgdatabase}, "
        f"LLM={s.llm.llm_model}, retries={s.llm.max_retries}, "
        f"fallback={'on' if s.llm.fallback_model else 'off'}, "
        f"alerts={'webhook' if s.alerts.webhook_url else 'local-only'}, "
        f"warnings={len(warnings)}"
    )
    return warnings
