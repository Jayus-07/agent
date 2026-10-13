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
    """只读账号只使用显式配置；漏配时开发环境告警、生产环境阻断。"""

    def test_missing_database_password_has_no_fallback(self, monkeypatch):
        from backend.config import database

        monkeypatch.delenv("PG_READONLY_PASSWORD", raising=False)
        assert database._configured_readonly_password() == ""

    def test_forward_migration_revokes_existing_role_password(self):
        from pathlib import Path

        from scripts.init_db import MIGRATION_TARGETS, discover_migrations

        name = "093_revoke_readonly_default_password.sql"
        plan, unregistered = discover_migrations()
        assert not unregistered
        assert MIGRATION_TARGETS[name] == "business"
        filenames = [filename for filename, _target in plan]
        assert filenames.index("004_readonly_role.sql") < filenames.index(
            "043_readonly_public_revoke.sql"
        )
        assert filenames.index("043_readonly_public_revoke.sql") < filenames.index(
            "083_readonly_ai_grant_scope.sql"
        )
        assert filenames.index("083_readonly_ai_grant_scope.sql") < filenames.index(name)
        migration = (
            Path(__file__).resolve().parents[1]
            / "sql"
            / "migrations"
            / name
        ).read_text(encoding="utf-8")
        assert "ALTER ROLE AGENT_READONLY NOLOGIN" in migration.upper()
        assert "STANDALONE EXECUTION FAILS CLOSED" in migration.upper()

    def test_004_creates_role_without_password(self):
        from pathlib import Path

        migration = (
            Path(__file__).resolve().parents[1]
            / "sql"
            / "migrations"
            / "004_readonly_role.sql"
        ).read_text(encoding="utf-8").upper()
        assert "CREATE ROLE AGENT_READONLY WITH LOGIN NOSUPERUSER" in migration

    def test_applied_legacy_004_checksum_is_not_replayed(self):
        from pathlib import Path

        from scripts.init_db import apply_migration

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, _params):
                assert "SELECT CHECKSUM, STATUS" in query.upper()

            def fetchone(self):
                return "b5c27b0ff9ff348c", "applied"

        class _Connection:
            def cursor(self):
                return _Cursor()

        status, detail = apply_migration(
            _Connection(),
            "004_readonly_role.sql",
            (
                Path(__file__).resolve().parents[1]
                / "sql"
                / "migrations"
                / "004_readonly_role.sql"
            ).read_text(encoding="utf-8"),
            force=False,
        )
        assert status == "skipped"
        assert "保持不变" in detail

    def test_applied_legacy_004_rejects_unknown_current_file_version(self):
        from scripts.init_db import apply_migration

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, _params=None):
                assert "SELECT CHECKSUM, STATUS" in query.upper()

            def fetchone(self):
                return "b5c27b0ff9ff348c", "applied"

        class _Connection:
            def cursor(self):
                return _Cursor()

        status, detail = apply_migration(
            _Connection(),
            "004_readonly_role.sql",
            "-- unknown migration content",
            force=False,
        )
        assert status == "error"
        assert "拒绝重放" in detail

    def test_release_preflight_accepts_only_applied_legacy_004_checksum(self):
        from pathlib import Path

        from backend.scripts.verify_migration_state import checksum_is_accepted
        from scripts.init_db import checksum_of

        current_checksum = "31b2b96b9b3a76ff"
        current_migration = (
            Path(__file__).resolve().parents[1]
            / "sql"
            / "migrations"
            / "004_readonly_role.sql"
        ).read_text(encoding="utf-8")
        assert checksum_of(current_migration) == current_checksum
        assert checksum_is_accepted(
            "004_readonly_role.sql",
            {"checksum": "b5c27b0ff9ff348c", "status": "applied"},
            current_checksum,
        )
        assert not checksum_is_accepted(
            "004_readonly_role.sql",
            {"checksum": "b5c27b0ff9ff348c", "status": "error"},
            current_checksum,
        )
        assert not checksum_is_accepted(
            "093_revoke_readonly_default_password.sql",
            {"checksum": "b5c27b0ff9ff348c", "status": "applied"},
            current_checksum,
        )
        assert not checksum_is_accepted(
            "004_readonly_role.sql",
            {"checksum": "b5c27b0ff9ff348c", "status": "applied"},
            "tampered-file-version",
        )

    def test_configured_password_blocks_login_before_terminating_sessions(
        self, monkeypatch,
    ):
        from psycopg2.sql import Composed

        from scripts import init_db

        events = []

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, params=None):
                if isinstance(query, str) and "SELECT 1 FROM pg_catalog.pg_roles" in query:
                    events.append(("role_exists", params[0]))
                elif isinstance(query, Composed):
                    sql_parts = "".join(
                        getattr(part, "string", "") for part in query.seq
                    ).upper()
                    events.append(("ddl", sql_parts))
                elif isinstance(query, str) and "pg_terminate_backend" in query.lower():
                    events.append(("terminate", params[0]))
                elif isinstance(query, str) and "select checksum, status" in query.lower():
                    events.append(("ledger_read", None))
                else:
                    events.append(("execute", type(query).__name__))

            def fetchone(self):
                return None if events[-1][0] == "ledger_read" else (1,)

        class _Connection:
            def cursor(self):
                return _Cursor()

            def commit(self):
                events.append(("commit", None))

            def rollback(self):
                events.append(("rollback", None))

        monkeypatch.setattr(
            init_db,
            "_record",
            lambda _conn, _filename, _sql, _status, *, commit=True: events.append(
                ("record", commit)
            ),
        )

        status, detail = init_db.apply_migration(
            _Connection(),
            "093_revoke_readonly_default_password.sql",
            "ALTER ROLE agent_readonly NOLOGIN;",
            force=False,
            readonly_password="test-only-placeholder",
        )

        assert status == "applied"
        assert "阻断旧登录" in detail
        no_login = next(i for i, event in enumerate(events) if event == ("ddl", "ALTER ROLE AGENT_READONLY NOLOGIN"))
        committed = next(i for i in range(no_login + 1, len(events)) if events[i][0] == "commit")
        terminated = next(i for i, event in enumerate(events) if event[0] == "terminate")
        login = next(i for i, event in enumerate(events) if event[0] == "ddl" and "WITH LOGIN PASSWORD" in event[1])
        assert no_login < committed < terminated < login
        assert events[-2:] == [("record", False), ("commit", None)]

    def test_custom_readonly_user_does_not_enable_legacy_role(self, monkeypatch):
        from scripts import init_db

        events = []

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, params=None):
                if isinstance(query, str) and "SELECT 1 FROM pg_catalog.pg_roles" in query:
                    events.append(("role_exists", params[0]))
                elif isinstance(query, str) and "pg_terminate_backend" in query.lower():
                    events.append(("terminate", params[0]))
                elif isinstance(query, str) and "select checksum, status" in query.lower():
                    events.append(("ledger_read", None))
                else:
                    sql_parts = "".join(
                        getattr(part, "string", "") for part in query.seq
                    ).upper()
                    events.append(("ddl", sql_parts))

            def fetchone(self):
                return None if events[-1][0] == "ledger_read" else (1,)

        class _Connection:
            def cursor(self):
                return _Cursor()

            def commit(self):
                events.append(("commit", None))

            def rollback(self):
                events.append(("rollback", None))

        def _record(*_args, **_kwargs):
            events.append(("record", False))

        monkeypatch.setattr(init_db, "_record", _record)
        status, _detail = init_db.apply_migration(
            _Connection(),
            "093_revoke_readonly_default_password.sql",
            "ALTER ROLE agent_readonly NOLOGIN;",
            force=False,
            readonly_password="test-only-placeholder",
            readonly_user="sql_reader",
        )

        assert status == "applied"
        assert ("terminate", "agent_readonly") in events
        assert ("terminate", "sql_reader") in events
        assert ("ddl", "ALTER ROLE AGENT_READONLY NOLOGIN PASSWORD NULL") in events
        assert ("ddl", "ALTER ROLE SQL_READER WITH LOGIN PASSWORD ") in events
        custom_block = next(
            i for i, event in enumerate(events)
            if event == ("ddl", "ALTER ROLE SQL_READER NOLOGIN")
        )
        custom_commit = next(
            i for i in range(custom_block + 1, len(events))
            if events[i][0] == "commit"
        )
        custom_terminate = next(
            i for i, event in enumerate(events)
            if event == ("terminate", "sql_reader")
        )
        custom_enable = next(
            i for i, event in enumerate(events)
            if event == ("ddl", "ALTER ROLE SQL_READER WITH LOGIN PASSWORD ")
        )
        assert custom_block < custom_commit < custom_terminate < custom_enable

    def test_missing_password_keeps_legacy_role_disabled(self, monkeypatch):
        from scripts import init_db

        events = []

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, params=None):
                if isinstance(query, str) and "SELECT 1 FROM pg_catalog.pg_roles" in query:
                    events.append(("role_exists", params[0]))
                elif isinstance(query, str) and "pg_terminate_backend" in query.lower():
                    events.append(("terminate", params[0]))
                elif isinstance(query, str) and "select checksum, status" in query.lower():
                    events.append(("ledger_read", None))
                else:
                    sql_parts = "".join(
                        getattr(part, "string", "") for part in query.seq
                    ).upper()
                    events.append(("ddl", sql_parts))

            def fetchone(self):
                return None if events[-1][0] == "ledger_read" else (1,)

        class _Connection:
            def cursor(self):
                return _Cursor()

            def commit(self):
                events.append(("commit", None))

            def rollback(self):
                events.append(("rollback", None))

        monkeypatch.setattr(
            init_db,
            "_record",
            lambda *_args, **_kwargs: events.append(("record", False)),
        )
        status, _detail = init_db.apply_migration(
            _Connection(),
            "093_revoke_readonly_default_password.sql",
            "ALTER ROLE agent_readonly NOLOGIN;",
            force=False,
        )

        assert status == "applied"
        no_login = next(
            i for i, event in enumerate(events)
            if event == ("ddl", "ALTER ROLE AGENT_READONLY NOLOGIN")
        )
        committed = next(
            i for i in range(no_login + 1, len(events))
            if events[i][0] == "commit"
        )
        terminated = next(
            i for i, event in enumerate(events)
            if event == ("terminate", "agent_readonly")
        )
        revoked = next(
            i for i, event in enumerate(events)
            if event == ("ddl", "ALTER ROLE AGENT_READONLY NOLOGIN PASSWORD NULL")
        )
        assert no_login < committed < terminated < revoked

    def test_applied_093_checksum_skips_without_terminating_sessions(self):
        from pathlib import Path

        from scripts.init_db import apply_migration, checksum_of

        filename = "093_revoke_readonly_default_password.sql"
        sql = (
            Path(__file__).resolve().parents[1]
            / "sql"
            / "migrations"
            / filename
        ).read_text(encoding="utf-8")
        executed = []

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, _params=None):
                executed.append(query)

            def fetchone(self):
                return checksum_of(sql), "applied"

        class _Connection:
            def cursor(self):
                return _Cursor()

            def commit(self):
                pytest.fail("same-version 093 must not commit or terminate sessions")

        status, detail = apply_migration(
            _Connection(),
            filename,
            sql,
            force=False,
            readonly_password="test-only-placeholder",
        )

        assert status == "skipped"
        assert "已应用" in detail
        assert len(executed) == 1
        assert "SELECT CHECKSUM, STATUS" in executed[0].upper()

    def test_ensure_dbs_applies_password_revocation_after_004(self):
        from scripts.ensure_dbs import BUSINESS_MIGRATIONS

        original = BUSINESS_MIGRATIONS.index("004_readonly_role.sql")
        public_scope = BUSINESS_MIGRATIONS.index("043_readonly_public_revoke.sql")
        ai_scope = BUSINESS_MIGRATIONS.index("083_readonly_ai_grant_scope.sql")
        revocation = BUSINESS_MIGRATIONS.index(
            "093_revoke_readonly_default_password.sql"
        )
        assert original < public_scope < ai_scope < revocation

    def test_ensure_dbs_reuses_tracked_readonly_migration(self, monkeypatch):
        from scripts import init_db
        from scripts.ensure_dbs import _apply_migration

        events = []

        class _Connection:
            def rollback(self):
                events.append(("rollback",))

        def _apply(conn, filename, sql, *, force, readonly_password, readonly_user):
            events.append((
                "apply", conn, filename, bool(sql), force,
                readonly_password, readonly_user,
            ))
            return "applied", ""

        monkeypatch.setattr(init_db, "apply_migration", _apply)

        result = _apply_migration(
            _Connection(),
            "093_revoke_readonly_default_password.sql",
            password="test-only-placeholder",
            user="sql_reader",
        )

        assert result == "applied"
        assert events[0][0] == "apply"
        assert events[0][2] == "093_revoke_readonly_default_password.sql"
        assert events[0][4:] == (False, "test-only-placeholder", "sql_reader")

    def test_ensure_dbs_tracks_all_readonly_authorization_migrations(self, monkeypatch):
        from scripts import init_db
        from scripts.ensure_dbs import _apply_migration

        applied = []

        class _Connection:
            def rollback(self):
                pytest.fail("valid migration must not roll back")

        def _apply(conn, filename, sql, *, force, readonly_password, readonly_user):
            applied.append((filename, force, readonly_user))
            return "applied", ""

        monkeypatch.setattr(init_db, "apply_migration", _apply)
        for name in (
            "004_readonly_role.sql",
            "043_readonly_public_revoke.sql",
            "083_readonly_ai_grant_scope.sql",
        ):
            assert _apply_migration(
                _Connection(), name, password="", user="sql_reader",
            ) == "applied"

        assert applied == [
            ("004_readonly_role.sql", False, "sql_reader"),
            ("043_readonly_public_revoke.sql", False, "sql_reader"),
            ("083_readonly_ai_grant_scope.sql", False, "sql_reader"),
        ]

    def test_ensure_dbs_permission_failure_keeps_role_disabled(self, monkeypatch):
        from scripts import init_db
        from scripts import ensure_dbs

        events = []
        monkeypatch.setattr(
            init_db,
            "block_readonly_roles_for_migrations",
            lambda _conn, *, readonly_user: events.append(("block", readonly_user)),
        )
        monkeypatch.setattr(
            init_db,
            "ensure_migration_tracking_table",
            lambda _conn: events.append(("tracking",)),
        )

        def _apply(_conn, filename, *, password, user):
            events.append(("migration", filename))
            if filename == "083_readonly_ai_grant_scope.sql":
                return "error: permission scope failed"
            return "applied"

        monkeypatch.setattr(ensure_dbs, "_apply_migration", _apply)
        monkeypatch.setattr(
            ensure_dbs,
            "_ensure_readonly_password",
            lambda *_args, **_kwargs: pytest.fail(
                "failed permission chain must not re-enable LOGIN"
            ),
        )

        result = ensure_dbs._apply_business_migrations(
            object(), password="test-only-placeholder", user="sql_reader",
            check=False,
        )

        assert result == 1
        assert events[:2] == [("block", "sql_reader"), ("tracking",)]
        assert (
            "migration", "093_revoke_readonly_default_password.sql"
        ) not in events

    def test_migration_barrier_disables_roles_before_terminating_sessions(self):
        from psycopg2.sql import Composed

        from scripts.init_db import block_readonly_roles_for_migrations

        events = []

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, params=None):
                if isinstance(query, str) and "pg_catalog.pg_roles" in query:
                    events.append(("exists", params[0]))
                elif isinstance(query, str) and "pg_terminate_backend" in query:
                    events.append(("terminate", params[0]))
                elif isinstance(query, Composed):
                    ddl = "".join(
                        getattr(part, "string", "") for part in query.seq
                    ).upper()
                    events.append(("ddl", ddl))

            def fetchone(self):
                return (1,)

        class _Connection:
            def cursor(self):
                return _Cursor()

            def commit(self):
                events.append(("commit",))

        block_readonly_roles_for_migrations(
            _Connection(), readonly_user="sql_reader",
        )

        legacy_no_login = events.index(
            ("ddl", "ALTER ROLE AGENT_READONLY NOLOGIN")
        )
        legacy_commit = events.index(("commit",), legacy_no_login + 1)
        legacy_terminate = events.index(("terminate", "agent_readonly"))
        custom_no_login = events.index(("ddl", "ALTER ROLE SQL_READER NOLOGIN"))
        custom_commit = events.index(("commit",), custom_no_login + 1)
        custom_terminate = events.index(("terminate", "sql_reader"))
        assert legacy_no_login < legacy_commit < legacy_terminate
        assert custom_no_login < custom_commit < custom_terminate

    def test_init_db_permission_failure_never_runs_093_or_reenables(
        self, monkeypatch, tmp_path,
    ):
        import sys

        from scripts import init_db

        events = []

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, *_args, **_kwargs):
                return None

            def fetchone(self):
                return ("test postgres",)

        class _Connection:
            def cursor(self):
                return _Cursor()

            def commit(self):
                return None

            def close(self):
                return None

        admin = _Connection()
        business = _Connection()
        memory = _Connection()
        connections = iter((admin, business, memory))
        monkeypatch.setattr(sys, "argv", ["init_db.py"])
        monkeypatch.setattr(init_db, "load_env_file", lambda *_args: None)
        monkeypatch.setattr(init_db, "connect", lambda *_args: next(connections))
        monkeypatch.setattr(
            init_db, "ensure_database", lambda *_args, **_kwargs: "已存在",
        )
        monkeypatch.setattr(init_db, "MIGRATIONS_DIR", tmp_path)
        migration_names = (
            "001_business.sql",
            "004_readonly_role.sql",
            "043_readonly_public_revoke.sql",
            "083_readonly_ai_grant_scope.sql",
            "093_revoke_readonly_default_password.sql",
        )
        for name in migration_names:
            (tmp_path / name).write_text("-- test migration", encoding="utf-8")
        monkeypatch.setattr(
            init_db,
            "discover_migrations",
            lambda: ([
                ("001_business.sql", "business"),
                ("004_readonly_role.sql", "business"),
                ("043_readonly_public_revoke.sql", "business"),
                ("083_readonly_ai_grant_scope.sql", "business"),
                ("093_revoke_readonly_default_password.sql", "business"),
            ], []),
        )
        monkeypatch.setattr(
            init_db, "ensure_migration_tracking_table", lambda *_args: None,
        )
        monkeypatch.setattr(
            init_db,
            "block_readonly_roles_for_migrations",
            lambda *_args, **kwargs: events.append(
                ("block", kwargs["readonly_user"]),
            ),
        )

        def _apply(_conn, filename, _sql, **_kwargs):
            events.append(("migration", filename))
            if filename == "083_readonly_ai_grant_scope.sql":
                return "error", "synthetic migration failure"
            return "applied", "ok"

        monkeypatch.setattr(init_db, "apply_migration", _apply)
        monkeypatch.setattr(
            init_db,
            "ensure_readonly_role",
            lambda *_args, **_kwargs: pytest.fail(
                "failed permission migrations must not restore LOGIN"
            ),
        )
        monkeypatch.setattr(init_db, "health_check", lambda *_args: "ok")
        monkeypatch.setattr(init_db, "row_counts", lambda *_args: [])
        monkeypatch.setattr(
            init_db, "verify_readonly", lambda *_args, **_kwargs: "skipped",
        )
        monkeypatch.setenv("PG_READONLY_USER", "sql_reader")
        monkeypatch.setenv("PG_READONLY_PASSWORD", "test-only-placeholder")

        assert init_db.main() == 1
        assert events[0] == ("block", "sql_reader")
        assert (
            "migration", "093_revoke_readonly_default_password.sql"
        ) not in events

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
