"""P1-14 测试 — pydantic 启动配置校验（backend.config.startup）

用 monkeypatch 隔离环境变量，覆盖 fatal / warning 两级判定。
"""
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
