"""P1-14 测试 — pydantic 启动配置校验（backend.config.startup）

用 monkeypatch 隔离环境变量，覆盖 fatal / warning 两级判定。
"""
import sys

import pytest

from backend.config import model_roles
from backend.config import startup as su
from backend.infra.llm import credentials
from backend.infra.llm import models as llm_models

# §B.15 起模型清单唯一事实来源是 DB（llm_models）；单测环境注册表刷新循环
# 不运行 → 注入内存注册表（形状对齐 registry_store._model_entry），否则
# 所有角色都被判「注册表为空」。条目覆盖本模块引用到的全部模型名。
_REGISTRY_FIXTURE = [
    {"name": "qwen2.5:3b", "provider": "ollama", "model_kind": "chat"},
    {"name": "qwen3.7-plus", "provider": "qwen", "model_kind": "chat"},
    {"name": "deepseek-v4-flash", "provider": "deepseek", "model_kind": "chat"},
    {"name": "MiniMax-M3", "provider": "minimax", "model_kind": "chat"},
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离测试环境变量（清掉宿主 .env 的干扰）。"""
    for var in (
        "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "MEMORY_PGDATABASE",
        "BUSINESS_PGDATABASE", "DB_POOL_MIN_CONN", "DB_POOL_MAX_CONN",
        "LLM_MODEL", "DEEPSEEK_API_KEY", "LLM_MAX_RETRIES",
        "LLM_RETRY_BACKOFF_BASE", "LLM_FALLBACK_MODEL",
        "API_KEY", "ALLOW_UNAUTHENTICATED", "TRUST_USER_HEADER", "USER_ID_HEADER",
        "ALERT_WEBHOOK_URL", "ALERT_WEBHOOK_TYPE", "ALERT_MIN_LEVEL",
        "ALERT_WEBHOOK_COOLDOWN",
        # checkpointer 开关：宿主 .env 里可能已打开，须隔离后再断言
        "MAIN_GRAPH_CHECKPOINTER_ENABLED", "CS_CHECKPOINTER_ENABLED",
        "TRAVEL_CHECKPOINTER_ENABLED", "CHECKPOINTER_ALLOW_DEGRADE",
    ):
        monkeypatch.delenv(var, raising=False)
    model_roles.reset_overrides()
    credentials.reset_credentials_for_tests()
    llm_models.set_dynamic_models([dict(e) for e in _REGISTRY_FIXTURE])
    yield
    model_roles.reset_overrides()
    credentials.reset_credentials_for_tests()
    llm_models.reset_dynamic_models_for_tests()


@pytest.fixture
def valid_env(monkeypatch):
    """一组能通过校验的最小基础环境；模型配置走 DB 内存覆盖。"""
    monkeypatch.setenv("PGPASSWORD", "strong-password-123")
    model_roles.inject_overrides({"main": "deepseek-v4-flash"})
    credentials.set_db_credentials({
        "deepseek": credentials.ProviderCredentials(
            provider="deepseek",
            api_key="db-test-key",
            base_url="https://db.example/v1",
            source="db",
            version=1,
        ),
    })
    monkeypatch.setenv("API_KEY", "x" * 32)


class TestFatalCases:
    """fatal → SettingsValidationError"""

    def test_missing_pgpassword(self, valid_env, monkeypatch):
        """PG 密码缺失 → 降级警告（记忆库有运行时兜底），不阻止启动"""
        monkeypatch.delenv("PGPASSWORD")
        warnings = su.validate_startup_settings()
        assert any("PGPASSWORD" in w for w in warnings)

    def test_bad_port(self, valid_env, monkeypatch):
        monkeypatch.setenv("PGPORT", "not-a-port")
        with pytest.raises(su.SettingsValidationError):
            su.validate_startup_settings()

    def test_port_out_of_range(self, valid_env, monkeypatch):
        monkeypatch.setenv("PGPORT", "99999")
        with pytest.raises(su.SettingsValidationError):
            su.validate_startup_settings()

    def test_deepseek_without_db_key_is_warning(self, valid_env):
        credentials.reset_credentials_for_tests()
        warnings = su.validate_startup_settings()
        assert any("数据库配置 API Key" in w for w in warnings)

    def test_pool_order_inverted(self, valid_env, monkeypatch):
        monkeypatch.setenv("DB_POOL_MIN_CONN", "10")
        monkeypatch.setenv("DB_POOL_MAX_CONN", "2")
        with pytest.raises(su.SettingsValidationError):
            su.validate_startup_settings()

    def test_negative_retries(self, valid_env, monkeypatch):
        monkeypatch.setenv("LLM_MAX_RETRIES", "-1")
        with pytest.raises(su.SettingsValidationError):
            su.validate_startup_settings()

    def test_bad_webhook_type(self, valid_env, monkeypatch):
        monkeypatch.setenv("ALERT_WEBHOOK_TYPE", "sms")
        with pytest.raises(su.SettingsValidationError):
            su.validate_startup_settings()

    def test_bad_min_level(self, valid_env, monkeypatch):
        monkeypatch.setenv("ALERT_MIN_LEVEL", "loud")
        with pytest.raises(su.SettingsValidationError):
            su.validate_startup_settings()

    def test_empty_db_model_does_not_read_legacy_env(self, valid_env, monkeypatch):
        model_roles.inject_overrides({"main": ""})
        monkeypatch.setenv("LLM_MODEL", "env-only-model")
        warnings = su.validate_startup_settings()
        assert isinstance(warnings, list)


class TestWarningCases:
    """warning → 记日志并返回消息列表，不抛异常"""

    def test_valid_env_passes(self, valid_env):
        warnings = su.validate_startup_settings()
        assert isinstance(warnings, list)

    def test_missing_api_key_warns(self, valid_env, monkeypatch):
        monkeypatch.delenv("API_KEY")
        warnings = su.validate_startup_settings()
        assert any("API_KEY" in w for w in warnings)

    def test_weak_api_key_warns(self, valid_env, monkeypatch):
        monkeypatch.setenv("API_KEY", "short")
        warnings = su.validate_startup_settings()
        assert any("长度" in w for w in warnings)

    def test_allow_unauthenticated_warns(self, valid_env, monkeypatch):
        monkeypatch.setenv("ALLOW_UNAUTHENTICATED", "true")
        warnings = su.validate_startup_settings()
        assert any("ALLOW_UNAUTHENTICATED" in w for w in warnings)

    def test_trust_user_header_warns(self, valid_env, monkeypatch):
        monkeypatch.setenv("TRUST_USER_HEADER", "true")
        warnings = su.validate_startup_settings()
        assert any("TRUST_USER_HEADER" in w for w in warnings)

    def test_default_pg_password_warns(self, valid_env, monkeypatch):
        monkeypatch.setenv("PGPASSWORD", "postgres")
        warnings = su.validate_startup_settings()
        assert any("弱口令" in w for w in warnings)

    def test_bad_webhook_url_warns(self, valid_env, monkeypatch):
        monkeypatch.setenv("ALERT_WEBHOOK_URL", "ftp://not-http")
        warnings = su.validate_startup_settings()
        assert any("ALERT_WEBHOOK_URL" in w for w in warnings)

    def test_non_deepseek_model_no_key_needed(self, valid_env, monkeypatch):
        model_roles.inject_overrides({"main": "qwen2.5:7b"})
        credentials.reset_credentials_for_tests()
        warnings = su.validate_startup_settings()  # 不抛
        assert isinstance(warnings, list)


class TestToolSelectorModelValidation:
    """TOOL_SELECTOR_MODEL 启动校验：未注册 / 缺 provider key 都要 warning。

    背景: deepseek 402 余额不足曾静默回退全局模型，配错无法察觉。
    """

    def test_unregistered_model_warns(self, valid_env, monkeypatch):
        model_roles.inject_overrides({"tool_selector": "ghost-model"})
        warnings = su.validate_startup_settings()
        assert any("TOOL_SELECTOR_MODEL" in w and "注册" in w for w in warnings)

    def test_missing_provider_key_warns(self, valid_env, monkeypatch):
        model_roles.inject_overrides({
            "main": "qwen3.7-plus",
            "tool_selector": "deepseek-v4-flash",
        })
        credentials.set_db_credentials({})
        warnings = su.validate_startup_settings()
        assert any("TOOL_SELECTOR_MODEL" in w and "数据库配置 API Key" in w
                   for w in warnings)

    def test_valid_config_no_warning(self, valid_env, monkeypatch):
        model_roles.inject_overrides({"tool_selector": "deepseek-v4-flash"})
        warnings = su.validate_startup_settings()
        assert not any("TOOL_SELECTOR_MODEL" in w for w in warnings)

    def test_empty_no_warning(self, valid_env, monkeypatch):
        model_roles.inject_overrides({"main": "deepseek-v4-flash", "tool_selector": ""})
        warnings = su.validate_startup_settings()
        assert not any("TOOL_SELECTOR_MODEL" in w for w in warnings)

    def test_local_model_needs_no_key(self, valid_env, monkeypatch):
        model_roles.inject_overrides({"tool_selector": "qwen2.5:3b"})
        warnings = su.validate_startup_settings()
        assert not any("TOOL_SELECTOR_MODEL" in w for w in warnings)


class TestCheckpointerBackendValidation:
    """checkpointer 「假开启」必须在启动期被点名。

    背景：LangGraph 的 PostgresSaver 需要 psycopg v3，而本仓依赖里只有
    psycopg2；缺依赖时运行期会静默降级成 MemorySaver —— 看着开启了持久化，
    实际只在进程内。这个校验就是把这种错觉在启动日志里说清楚。
    """

    @staticmethod
    def _no_postgres_driver(monkeypatch):
        # sys.modules 置 None 是让 ``import X`` 直接抛 ImportError 的标准手法
        monkeypatch.setitem(sys.modules, "psycopg", None)
        monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", None)

    def test_driver_probe_reflects_environment(self):
        """探测函数必须与真实环境一致（本仓当前无 psycopg v3）。"""
        import importlib.util

        expected = (
            importlib.util.find_spec("psycopg") is not None
            and importlib.util.find_spec("langgraph.checkpoint.postgres") is not None
        )
        assert su._postgres_checkpointer_available() is expected

    def test_enabled_without_driver_warns_and_names_owner(
        self, valid_env, monkeypatch,
    ):
        self._no_postgres_driver(monkeypatch)
        monkeypatch.setenv("TRAVEL_CHECKPOINTER_ENABLED", "true")

        warnings = su.validate_startup_settings()

        hit = [w for w in warnings if "checkpointer" in w and "Postgres" in w]
        assert hit, f"应给出 checkpointer 后端不可用告警，实际: {warnings}"
        assert "旅游域" in hit[0]
        # 必须点出「会静默降级为 MemorySaver」的后果，否则用户仍以为在持久化
        assert "MemorySaver" in hit[0]
        assert "psycopg" in hit[0]

    def test_all_owners_listed_when_multiple_enabled(self, valid_env, monkeypatch):
        self._no_postgres_driver(monkeypatch)
        monkeypatch.setenv("MAIN_GRAPH_CHECKPOINTER_ENABLED", "true")
        monkeypatch.setenv("CS_CHECKPOINTER_ENABLED", "true")
        monkeypatch.setenv("TRAVEL_CHECKPOINTER_ENABLED", "true")

        warnings = su.validate_startup_settings()
        hit = [w for w in warnings if "checkpointer" in w and "Postgres" in w][0]
        assert "主图" in hit and "客服域" in hit and "旅游域" in hit

    def test_disabled_everywhere_no_warning(self, valid_env, monkeypatch):
        """全关时不打扰 —— 没开启就没有「假开启」问题。"""
        warnings = su.validate_startup_settings()
        assert not any(
            "checkpointer" in w and "Postgres" in w for w in warnings)


class TestCheckpointerProductionFatal:
    """结构病审查 P2-10：生产环境「开关开着 + 后端不可用」必须拒绝启动。

    只有 warning 时，生产上会静默降级为 MemorySaver（跨轮状态只在进程内、
    重启即失、多副本各存一份），而日志里的 "enabled" 让人以为已持久化。
    判据与 backend/config/checkpointer.py 同口径，可用
    CHECKPOINTER_ALLOW_DEGRADE=true 显式接受内存检查点。
    """

    @staticmethod
    def _no_postgres_driver(monkeypatch):
        monkeypatch.setitem(sys.modules, "psycopg", None)
        monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", None)

    def test_enabled_without_driver_is_fatal_in_production(
        self, valid_env, monkeypatch,
    ):
        self._no_postgres_driver(monkeypatch)
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("CS_CHECKPOINTER_ENABLED", "true")

        with pytest.raises(su.SettingsValidationError) as exc_info:
            su.validate_startup_settings()

        text = str(exc_info.value)
        assert "客服域" in text
        assert "MemorySaver" in text
        assert "CHECKPOINTER_ALLOW_DEGRADE=true" in text

    def test_explicit_optin_allows_degrade_in_production(
        self, valid_env, monkeypatch,
    ):
        """显式接受内存检查点 → 不再致命（决定权留给运维，但必须写下来）。"""
        self._no_postgres_driver(monkeypatch)
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("CS_CHECKPOINTER_ENABLED", "true")
        monkeypatch.setenv("CHECKPOINTER_ALLOW_DEGRADE", "true")

        warnings = su.validate_startup_settings()
        assert any("checkpointer" in w and "MemorySaver" in w for w in warnings)

    def test_development_never_fatal(self, valid_env, monkeypatch):
        self._no_postgres_driver(monkeypatch)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("CS_CHECKPOINTER_ENABLED", "true")

        warnings = su.validate_startup_settings()
        assert any("checkpointer" in w and "MemorySaver" in w for w in warnings)

    def test_all_switches_off_never_fatal_in_production(
        self, valid_env, monkeypatch,
    ):
        """全关（明确不使用）不该被拦 —— 只有「开着却不可用」才是问题。"""
        self._no_postgres_driver(monkeypatch)
        monkeypatch.setenv("ENVIRONMENT", "production")

        warnings = su.validate_startup_settings()
        assert not any("checkpointer" in w and "Postgres" in w for w in warnings)


class TestReadonlyPassword:
    """2026-09-21 审查 #12 回归：PG_READONLY_PASSWORD 有内置开发缺省值，
    生产漏配 = 静默使用公开口令。任何环境 warning 点名；production fatal。"""

    def test_missing_warns_in_dev(self, valid_env, monkeypatch):
        monkeypatch.delenv("PG_READONLY_PASSWORD", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        warnings = su.validate_startup_settings()
        assert any("PG_READONLY_PASSWORD" in w for w in warnings)

    def test_missing_fatal_in_production(self, valid_env, monkeypatch):
        monkeypatch.delenv("PG_READONLY_PASSWORD", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        with pytest.raises(su.SettingsValidationError) as exc_info:
            su.validate_startup_settings()
        assert "PG_READONLY_PASSWORD" in str(exc_info.value)

    def test_explicit_no_warning(self, valid_env, monkeypatch):
        monkeypatch.setenv("PG_READONLY_PASSWORD", "prod-readonly-secret")
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        warnings = su.validate_startup_settings()
        assert not any("PG_READONLY_PASSWORD" in w for w in warnings)
