"""business-mock 内部令牌启动守卫（2026-11-08 安全审计 T7a）。

指纹：``business-mock.x-internal-token.fail-open-default``。

production 缺 INTERNAL_API_TOKEN 必须拒启动；development 允许无令牌
（compose 只绑 127.0.0.1，用于本地联调）。
"""
from __future__ import annotations

import pytest

from backend.mock.business_service import main as mock_main


def test_production_without_token_refuses_boot(monkeypatch):
    monkeypatch.setattr(mock_main, "_INTERNAL_TOKEN", "")
    with pytest.raises(RuntimeError) as exc:
        mock_main.require_internal_boot("production")
    assert "INTERNAL_API_TOKEN" in str(exc.value)


def test_production_with_token_boots(monkeypatch):
    monkeypatch.setattr(mock_main, "_INTERNAL_TOKEN", "tok-1")
    mock_main.require_internal_boot("production")  # 不抛异常


def test_development_without_token_boots(monkeypatch):
    monkeypatch.setattr(mock_main, "_INTERNAL_TOKEN", "")
    mock_main.require_internal_boot("development")  # 仅 warning


def test_check_token_still_requires_match(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(mock_main, "_INTERNAL_TOKEN", "tok-1")
    mock_main._check_token("tok-1")
    with pytest.raises(HTTPException) as exc:
        mock_main._check_token("wrong")
    assert exc.value.status_code == 401
