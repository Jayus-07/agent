"""WS6: Production auth fail-fast — startup validation + middleware defense."""
from __future__ import annotations

import os
import pytest

from backend.config.startup import (
    SettingsValidationError,
    validate_startup_settings,
)


class TestProductionAuthFailFast:
    """生产环境 + 不安全配置 → SettingsValidationError。"""

    def _patch_env(self, monkeypatch, **kwargs):
        for k, v in kwargs.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)

    def test_prod_unauth_is_fatal(self, monkeypatch):
        self._patch_env(monkeypatch,
            ENVIRONMENT="production",
            ALLOW_UNAUTHENTICATED="true",
            API_KEY="",
            PGHOST="localhost", PGPORT="5432", PGUSER="u", PGPASSWORD="pw",
        )
        with pytest.raises(SettingsValidationError, match="ALLOW_UNAUTHENTICATED"):
            validate_startup_settings()

    def test_prod_no_api_key_is_fatal(self, monkeypatch):
        self._patch_env(monkeypatch,
            ENVIRONMENT="production",
            ALLOW_UNAUTHENTICATED="false",
            API_KEY="",
            PGHOST="localhost", PGPORT="5432", PGUSER="u", PGPASSWORD="pw",
        )
        with pytest.raises(SettingsValidationError, match="API_KEY"):
            validate_startup_settings()

    def test_prod_short_api_key_is_fatal(self, monkeypatch):
        self._patch_env(monkeypatch,
            ENVIRONMENT="production",
            ALLOW_UNAUTHENTICATED="false",
            API_KEY="short",
            PGHOST="localhost", PGPORT="5432", PGUSER="u", PGPASSWORD="pw",
        )
        with pytest.raises(SettingsValidationError, match="API_KEY 长度"):
            validate_startup_settings()

    def test_prod_valid_config_passes(self, monkeypatch):
        self._patch_env(monkeypatch,
            ENVIRONMENT="production",
            ALLOW_UNAUTHENTICATED="false",
            API_KEY="a" * 32,
            PGHOST="localhost", PGPORT="5432", PGUSER="u", PGPASSWORD="pw",
        )
        warnings = validate_startup_settings()
        assert isinstance(warnings, list)

    def test_dev_unauth_is_warning_not_fatal(self, monkeypatch):
        self._patch_env(monkeypatch,
            ENVIRONMENT="development",
            ALLOW_UNAUTHENTICATED="true",
            API_KEY="",
            PGHOST="localhost", PGPORT="5432", PGUSER="u", PGPASSWORD="pw",
        )
        warnings = validate_startup_settings()
        assert any("ALLOW_UNAUTHENTICATED" in w for w in warnings)

    def test_prod_legacy_identity_is_fatal(self, monkeypatch):
        self._patch_env(monkeypatch,
            ENVIRONMENT="production",
            ALLOW_UNAUTHENTICATED="false",
            API_KEY="a" * 32,
            IDENTITY_SOURCE="legacy",
            PGHOST="localhost", PGPORT="5432", PGUSER="u", PGPASSWORD="pw",
        )
        with pytest.raises(SettingsValidationError, match="IDENTITY_SOURCE"):
            validate_startup_settings()

    def test_prod_header_identity_passes(self, monkeypatch):
        self._patch_env(monkeypatch,
            ENVIRONMENT="production",
            ALLOW_UNAUTHENTICATED="false",
            API_KEY="a" * 32,
            IDENTITY_SOURCE="header",
            PGHOST="localhost", PGPORT="5432", PGUSER="u", PGPASSWORD="pw",
        )
        warnings = validate_startup_settings()
        assert isinstance(warnings, list)

    def test_unknown_environment_defaults_to_production(self, monkeypatch):
        self._patch_env(monkeypatch,
            ENVIRONMENT="banana",
            ALLOW_UNAUTHENTICATED="true",
            API_KEY="",
            PGHOST="localhost", PGPORT="5432", PGUSER="u", PGPASSWORD="pw",
        )
        with pytest.raises(SettingsValidationError):
            validate_startup_settings()
