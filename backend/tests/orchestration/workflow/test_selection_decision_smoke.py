"""selection_decision Workflow 冒烟测试（mock LLM + mock 竞品 store）"""
import asyncio
import json

import pytest

from backend.orchestration.workflow.dag import DAG
from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.orchestration.workflow.meta import collect_step_methods
from backend.orchestration.workflow.registry import WorkflowRegistry
from backend.orchestration.workflows.selection_decision import SelectionDecision

FAKE_SNAPSHOTS = [
    {"url": f"https://example.com/p{i}", "title": f"蓝牙耳机{i}", "platform": "jd",
     "price": 99.0 + i * 20, "rating": 4.5, "review_count": 5000 + i * 1000,
     "highlights": "主动降噪 长续航"} for i in range(4)
]


class _FakeResp:
    def __init__(self, content):
        self.content = content


def _fake_llm_invoke(messages):
    """统一桩：按 System Prompt 特征分流三种响应"""
    text = " ".join(str(getattr(m, "content", m)) for m in messages)
    if "差异化" in text or "切入点" in text:
        return _FakeResp(json.dumps({
            "verdict": "go", "gaps": ["续航虚标"], "reason": "痛点集中"},
            ensure_ascii=False))
    if "专长领域" in text:  # 评审团角色 prompt
        return _FakeResp(json.dumps(
            {"score": 80, "verdict": "go", "reason": "测试通过"}, ensure_ascii=False))
    return _FakeResp(json.dumps(["续航虚标", "佩戴不适"], ensure_ascii=False))


@pytest.fixture
def patched_env(monkeypatch, tmp_path):
    import backend.orchestration.workflows.selection_decision as wf_mod
    import backend.selection_decision.panel as panel_mod

    # 竞品快照数据桩
    class _FakeStore:
        def list_watch(self, enabled_only=True):
            return [{"url": s["url"]} for s in FAKE_SNAPSHOTS]
        def latest_snapshot(self, url):
            return next((s for s in FAKE_SNAPSHOTS if s["url"] == url), None)
    monkeypatch.setattr(wf_mod, "get_store", lambda: _FakeStore())
    # LLM 桩（workflow 模块与 panel 模块各自 patch）
    monkeypatch.setattr(wf_mod, "llm", type("L", (), {"invoke": staticmethod(_fake_llm_invoke)}))
    monkeypatch.setattr(panel_mod, "llm", type("L", (), {"invoke": staticmethod(_fake_llm_invoke)}))
    # 选品评分缓存桩（避免真实打分依赖）
    monkeypatch.setattr(wf_mod, "batch_scores", lambda urls: {"scores": {}})
    # 任务库落 tmp
    from backend.selection_decision.store import SelectionDecisionStore
    store = SelectionDecisionStore(db_path=str(tmp_path / "sd.db"))
    monkeypatch.setattr(wf_mod, "get_selection_decision_store", lambda: store)
    return store


def test_dag_layers_structure():
    steps = collect_step_methods(SelectionDecision)
    dag = DAG({name: cfg for name, (_, cfg) in steps.items()})
    layers = dag.layers
    assert layers[0] == ["competitor_data"]
    assert set(layers[1]) == {"market_assess", "competitor_profile", "review_pain"}
    assert layers[-1] == ["decision_report"]


def test_full_run_happy_path_go(patched_env):
    reg = WorkflowRegistry()
    reg.register(SelectionDecision)
    inputs = {"category": "蓝牙耳机", "platforms": ["jd"], "panel_size": 3,
              "task_id": "t-happy",
              "finance": {"sell_price": 129.0, "unit_cost": 45.0}}
    ctx = asyncio.run(WorkflowExecutor(registry=reg).run("selection_decision", inputs=inputs))
    assert ctx.status == "success"
    assert ctx.outputs["decision_report"]["verdict"] == "go"
    assert "🚀 Go" in ctx.outputs["decision_report"]["report_md"]
    row = patched_env.get("t-happy")
    assert row["status"] == "success" and row["verdict"] == "go"


def test_market_no_go_short_circuits(patched_env, monkeypatch):
    """候选不足 3 个 → 市场评估 no_go → 后续决策环节被 run_if 跳过 → No-Go 报告"""
    import backend.orchestration.workflows.selection_decision as wf_mod
    class _TinyStore:
        def list_watch(self, enabled_only=True):
            return [{"url": FAKE_SNAPSHOTS[0]["url"]}]
        def latest_snapshot(self, url):
            return FAKE_SNAPSHOTS[0]
    monkeypatch.setattr(wf_mod, "get_store", lambda: _TinyStore())
    reg = WorkflowRegistry()
    reg.register(SelectionDecision)
    ctx = asyncio.run(WorkflowExecutor(registry=reg).run(
        "selection_decision", inputs={"category": "x", "task_id": "t-short",
                                       "finance": {"sell_price": 1, "unit_cost": 1}}))
    assert ctx.status in ("success", "partial")
    assert "differentiation" in ctx.skip_steps
    assert "finance_model" in ctx.skip_steps
    assert "review_panel" in ctx.skip_steps
    assert ctx.outputs["decision_report"]["verdict"] == "no_go"
