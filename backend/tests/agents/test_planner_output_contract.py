"""Planner 评测入口绕过缓存并保留结构化输出来源。"""

import json
from types import SimpleNamespace

import pytest

from backend.agents.planner import planner
from backend.infra.llm.budget import RequestBudgetExceeded
from backend.infra.llm.error_taxonomy import ModelProviderError


class _Cache:
    def __init__(self, cached=None):
        self.cached = cached
        self.reads = 0
        self.writes = []

    def get_json(self, _key):
        self.reads += 1
        return self.cached

    def set_json(self, _key, value):
        self.writes.append(value)


class _PromptService:
    def get_version_for_cache_key(self, _key):
        return 7

    def render_sync(self, *_args, **_kwargs):
        return SimpleNamespace(text="Planner test prompt")


class _FakeLlm:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def invoke(self, *_args, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def _valid_raw_plan() -> dict:
    return {
        "nodes": [{
            "step_id": "1",
            "capability": "rag.search",
            "description": "查找库存规范",
            "params": {"question": "库存预警处理规范"},
        }],
        "edges": {},
    }


def _setup(
    monkeypatch,
    response=None,
    error=None,
    cache=None,
    capabilities=("rag.search",),
    apply_knowledge_policy=False,
):
    from backend.prompts import service as prompt_service_module

    capabilities = tuple(capabilities)
    cache = cache or _Cache()
    fake_llm = _FakeLlm(response=response, error=error)
    monkeypatch.setattr(planner, "_planner_cache", cache)
    monkeypatch.setattr(planner, "llm", fake_llm)
    monkeypatch.setattr(planner, "_authorized_visible_capabilities",
                        lambda *_args: capabilities)
    monkeypatch.setattr(planner, "_format_capabilities_schema", lambda *_args: "")
    monkeypatch.setattr(planner, "_format_schema_overview", lambda: "")
    monkeypatch.setattr(planner, "_extract_json", json.loads)

    def normalize(raw, allowed):
        assert allowed == set(capabilities)
        return {
            "nodes": {node["step_id"]: node for node in raw["nodes"]},
            "edges": raw["edges"],
        }

    monkeypatch.setattr(planner, "_normalize_plan", normalize)
    monkeypatch.setattr(planner, "_filter_plan", lambda plan, _question: plan)
    if not apply_knowledge_policy:
        monkeypatch.setattr(
            planner, "_ensure_knowledge_step", lambda plan, _question, **_kwargs: plan
        )
    monkeypatch.setattr(prompt_service_module, "prompt_service", _PromptService())
    return cache, fake_llm


def test_evaluation_mode_calls_model_and_bypasses_plan_cache(monkeypatch):
    response = SimpleNamespace(
        content=json.dumps(_valid_raw_plan(), ensure_ascii=False),
        additional_kwargs={},
        response_metadata={},
    )
    cache, fake_llm = _setup(monkeypatch, response=response)

    result = planner.planner_node({
        "question": "查找库存预警处理规范",
        "_evaluation_mode": True,
    })

    assert cache.reads == 0
    assert cache.writes == []
    assert fake_llm.calls == 1
    assert result["_planner_evaluation"]["status"] == "success"
    assert result["_planner_evaluation"]["source"] == "model"
    actual_node = result["_planner_evaluation"]["actual_plan"]["nodes"][0]
    assert actual_node["capability"] == "rag.search"
    assert "kb_id" not in actual_node["params"]
    assert result["_planner_evaluation"]["runtime_adjusted"] is True
    assert result["plan"]["nodes"]["1"]["params"]["kb_id"] == "default"


def test_live_evaluation_scores_model_plan_before_runtime_knowledge_injection(monkeypatch):
    raw_plan = {
        "nodes": [{
            "step_id": "1",
            "capability": "sql.query",
            "description": "查询退款订单",
            "params": {"question": "查询本月退款订单"},
        }],
        "edges": {},
    }
    response = SimpleNamespace(
        content=json.dumps(raw_plan, ensure_ascii=False),
        additional_kwargs={},
        response_metadata={},
    )
    cache, _fake_llm = _setup(
        monkeypatch,
        response=response,
        capabilities=("sql.query", "rag.search"),
        apply_knowledge_policy=True,
    )

    result = planner.planner_node({
        "question": "查询本月退款订单，并说明退款处理规范。",
        "_evaluation_mode": True,
    })

    outcome = result["_planner_evaluation"]
    assert result["plan"]["nodes"]["2"]["capability"] == "rag.search"
    assert [node["capability"] for node in outcome["actual_plan"]["nodes"]] == [
        "sql.query",
    ]
    assert outcome["runtime_adjusted"] is True
    assert outcome["status"] == "success"
    assert outcome["source"] == "model"
    assert cache.writes == []


def test_unauthorized_raw_capability_is_rejected_before_normalization(monkeypatch):
    raw = _valid_raw_plan()
    raw["nodes"].append({
        "step_id": "2",
        "capability": "sql.query",
        "description": "未授权查询",
        "params": {},
    })
    response = SimpleNamespace(
        content=json.dumps(raw, ensure_ascii=False),
        additional_kwargs={},
        response_metadata={},
    )
    cache, _fake_llm = _setup(monkeypatch, response=response)

    result = planner.planner_node({
        "question": "查找库存预警处理规范",
        "_evaluation_mode": True,
    })

    outcome = result["_planner_evaluation"]
    assert outcome["status"] == "invalid_output"
    assert outcome["error_category"] == "contract_violation"
    assert any("not authorized" in error for error in outcome["errors"])
    assert cache.writes == []


def test_degraded_proxy_response_is_provider_error_not_model_text(monkeypatch):
    response = SimpleNamespace(
        content="服务暂不可用",
        additional_kwargs={"llm_degraded": True, "llm_error_type": "auth_failed"},
        response_metadata={"llm_degraded": True, "llm_error_type": "auth_failed"},
    )
    cache, _fake_llm = _setup(monkeypatch, response=response)

    result = planner.planner_node({
        "question": "查找库存预警处理规范",
        "_evaluation_mode": True,
    })

    outcome = result["_planner_evaluation"]
    assert outcome["status"] == "error"
    assert outcome["source"] == "provider_degradation"
    assert outcome["error_category"] == "provider"
    assert outcome["error_code"] == "auth_failed"
    assert cache.writes == []


@pytest.mark.parametrize(
    ("error", "category", "code"),
    [
        (RequestBudgetExceeded("planner"), "budget", "BUDGET_EXCEEDED"),
        (ModelProviderError("failed", error_type="rate_limited"), "provider", "rate_limited"),
        (TimeoutError("request timed out"), "timeout", "timeout"),
    ],
)
def test_live_runtime_errors_keep_structured_category(monkeypatch, error, category, code):
    cache, _fake_llm = _setup(monkeypatch, error=error)

    result = planner.planner_node({
        "question": "查找库存预警处理规范",
        "_evaluation_mode": True,
    })

    outcome = result["_planner_evaluation"]
    assert outcome["status"] == "error"
    assert outcome["error_category"] == category
    assert outcome["error_code"] == code
    assert cache.writes == []


def test_normal_mode_still_uses_existing_plan_cache(monkeypatch):
    cached = {
        "nodes": {"1": _valid_raw_plan()["nodes"][0]},
        "edges": {},
    }
    cache, fake_llm = _setup(monkeypatch, cache=_Cache(cached=cached))

    result = planner.planner_node({"question": "普通对话问题"})

    assert result["plan"] is cached
    assert cache.reads == 1
    assert fake_llm.calls == 0


def test_stale_unauthorized_cache_is_discarded_and_regenerated(monkeypatch):
    response = SimpleNamespace(
        content=json.dumps(_valid_raw_plan(), ensure_ascii=False),
        additional_kwargs={},
        response_metadata={},
    )
    stale = {
        "nodes": {
            "1": {
                "step_id": "1",
                "capability": "sql.query",
                "description": "旧授权缓存",
                "params": {},
            },
        },
        "edges": {},
    }
    cache, fake_llm = _setup(monkeypatch, response=response, cache=_Cache(cached=stale))

    result = planner.planner_node({"question": "查找库存预警处理规范"})

    assert result["plan"]["nodes"]["1"]["capability"] == "rag.search"
    assert cache.reads == 1
    assert fake_llm.calls == 1
    assert cache.writes


def test_invalid_model_output_uses_scoped_fallback_and_is_not_cached(monkeypatch):
    raw = _valid_raw_plan()
    raw["nodes"][0]["capability"] = "sql.query"
    response = SimpleNamespace(
        content=json.dumps(raw, ensure_ascii=False),
        additional_kwargs={},
        response_metadata={},
    )
    cache, _fake_llm = _setup(monkeypatch, response=response)

    result = planner.planner_node({
        "question": "查找库存预警处理规范",
        "kb_id": "tenant-kb",
    })

    node = result["plan"]["nodes"]["1"]
    assert node["capability"] == "rag.search"
    assert node["params"]["kb_id"] == "tenant-kb"
    assert cache.writes == []


def test_empty_model_plan_fallback_is_not_cached_or_scored_as_model(monkeypatch):
    response = SimpleNamespace(
        content=json.dumps({"nodes": [], "edges": {}}, ensure_ascii=False),
        additional_kwargs={},
        response_metadata={},
    )
    cache, _fake_llm = _setup(monkeypatch, response=response)

    result = planner.planner_node({
        "question": "查找库存预警处理规范",
        "_evaluation_mode": True,
    })

    outcome = result["_planner_evaluation"]
    assert outcome["status"] == "fallback"
    assert outcome["source"] == "runtime_policy"
    assert outcome["actual_plan"]["nodes"] == []
    assert cache.writes == []
