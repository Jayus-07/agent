"""P1-14 测试 — pydantic 启动配置校验（backend.config.startup）

用 monkeypatch 隔离环境变量，覆盖 fatal / warning 两级判定。
"""
import sys

import pytest

from backend.config import startup as su


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离测试环境变量（清掉宿主 .env 的干扰）。"""
    for var in (
        "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE",
        "BUSINESS_PGDATABASE", "DB_POOL_MIN_CONN", "DB_POOL_MAX_CONN",
        "LLM_MODEL", "DEEPSEEK_API_KEY", "LLM_MAX_RETRIES",
        "LLM_RETRY_BACKOFF_BASE", "LLM_FALLBACK_MODEL",
        "API_KEY", "ALLOW_UNAUTHENTICATED", "TRUST_USER_HEADER", "USER_ID_HEADER",
        "ALERT_WEBHOOK_URL", "ALERT_WEBHOOK_TYPE", "ALERT_MIN_LEVEL",
        "ALERT_WEBHOOK_COOLDOWN",
        # checkpointer 开关：宿主 .env 里可能已打开，须隔离后再断言
        "MAIN_GRAPH_CHECKPOINTER_ENABLED", "CS_CHECKPOINTER_ENABLED",
        "TRAVEL_CHECKPOINTER_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def valid_env(monkeypatch):
    """一组能通过校验的最小环境。"""
    monkeypatch.setenv("PGPASSWORD", "strong-password-123")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
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

    def test_deepseek_without_key(self, valid_env, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY")
        with pytest.raises(su.SettingsValidationError):
            su.validate_startup_settings()

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

    def test_empty_llm_model(self, valid_env, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "")
        with pytest.raises(su.SettingsValidationError):
            su.validate_startup_settings()


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
        monkeypatch.setenv("LLM_MODEL", "qwen2.5:7b")
        monkeypatch.delenv("DEEPSEEK_API_KEY")
        warnings = su.validate_startup_settings()  # 不抛
        assert isinstance(warnings, list)


class TestToolSelectorModelValidation:
    """TOOL_SELECTOR_MODEL 启动校验：未注册 / 缺 provider key 都要 warning。

    背景: deepseek 402 余额不足曾静默回退全局模型，配错无法察觉。
    """

    def test_unregistered_model_warns(self, valid_env, monkeypatch):
        monkeypatch.setenv("TOOL_SELECTOR_MODEL", "ghost-model")
        warnings = su.validate_startup_settings()
        assert any("TOOL_SELECTOR_MODEL" in w and "注册" in w for w in warnings)

    def test_missing_provider_key_warns(self, valid_env, monkeypatch):
        # 主模型换 qwen（deepseek 前缀会触发 LLMSettings 的 fatal 校验，
        # 到不了 warning 段），专用模型用 deepseek 验 key 缺失 warning
        monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
        monkeypatch.setenv("TOOL_SELECTOR_MODEL", "deepseek-v4-flash")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        warnings = su.validate_startup_settings()
        assert any("TOOL_SELECTOR_MODEL" in w and "DEEPSEEK_API_KEY" in w
                   for w in warnings)

    def test_valid_config_no_warning(self, valid_env, monkeypatch):
        monkeypatch.setenv("TOOL_SELECTOR_MODEL", "deepseek-v4-flash")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        warnings = su.validate_startup_settings()
        assert not any("TOOL_SELECTOR_MODEL" in w for w in warnings)

    def test_empty_no_warning(self, valid_env, monkeypatch):
        monkeypatch.setenv("TOOL_SELECTOR_MODEL", "")
        warnings = su.validate_startup_settings()
        assert not any("TOOL_SELECTOR_MODEL" in w for w in warnings)

    def test_local_model_needs_no_key(self, valid_env, monkeypatch):
        monkeypatch.setenv("TOOL_SELECTOR_MODEL", "qwen2.5:3b")
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
