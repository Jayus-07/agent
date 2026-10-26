"""评测方式（evaluation_mode）从创建参数到执行口径的贯通测试。"""

import pytest

import backend.evaluation.managed_execution as me


class _Report:
    results = []
    summaries = []
    metadata = {"run_validity": {"validity": "valid"}}

    def model_dump(self, mode="json"):
        return {"results": [], "metadata": {}}


class FakeService:
    captured = {}

    def evaluate(self, config):
        FakeService.captured = {
            "live": config.live,
            "ragas": config.ragas,
            "no_ragas": config.no_ragas,
        }
        return _Report()


class FakeAssets:
    @staticmethod
    def write_once(*args, **kwargs):
        return None

    @staticmethod
    def digest(*args, **kwargs):
        return "testdigest"

    @staticmethod
    def now():
        return "2026-10-26T00:00:00Z"


@pytest.fixture(autouse=True)
def _patch_service(monkeypatch):
    FakeService.captured = {}
    monkeypatch.setattr(me, "assets", FakeAssets)
    # execute_binding 在函数体内 from ... import EvaluationService，
    # 必须打在源模块上才能被局部 import 取到。
    monkeypatch.setattr(
        "backend.evaluation.service.EvaluationService", FakeService
    )


def test_execute_binding_resolves_mode_into_eval_config():
    execute_binding = me.execute_binding
    execute_binding(
        "binding-x",
        "actor",
        evaluation_mode="ragas",
        refresh_registry_before_run=False,
    )
    assert FakeService.captured["live"] is True
    assert FakeService.captured["ragas"] is True
    assert FakeService.captured["no_ragas"] is False


def test_execute_binding_self_plus_ragas_mode():
    me.execute_binding(
        "binding-s",
        "actor",
        evaluation_mode="self+ragas",
        refresh_registry_before_run=False,
    )
    assert FakeService.captured["live"] is True
    assert FakeService.captured["no_ragas"] is False


def test_execute_binding_default_stays_offline():
    me.execute_binding("binding-y", "actor", refresh_registry_before_run=False)
    assert FakeService.captured["live"] is False
    assert FakeService.captured["no_ragas"] is True


def test_execute_binding_rejects_unknown_mode():
    with pytest.raises(ValueError):
        me.execute_binding(
            "binding-z",
            "actor",
            evaluation_mode="not-a-mode",
            refresh_registry_before_run=False,
        )
