# 选品决策 Workflow（Phase 1 决策闭环 MVP）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按可行性方案（`docs/superpowers/specs/2026-08-24-selection-decision-workflow-design.md`）落地 Phase 1：表单任务页提交 → `selection_decision` Workflow 异步执行（感知/分析/决策/验证 4 层 DAG，条件分支 + 有界循环）→ Go/No-Go 决策报告页。

**Architecture:** 扩展现有 Workflow 框架（`StepConfig.run_if` 条件跳过），新增 `backend/selection_decision/` 业务模块（财务规则计算 / AI 评审团 / 任务存储 / 报告组装）与 `backend/orchestration/workflows/selection_decision.py` Workflow 定义；复用现有 watchlist 快照数据、workflow 持久化与 trace；前端新增 `/selection-decision` 表单任务页 + 报告页。

**Tech Stack:** FastAPI + LangGraph 生态既有设施、Workflow DAG 执行器、SQLite、LangChain ChatModel（`backend.infra.llm.llm`）、Next.js 14 + React + react-markdown。

**约束提醒（来自 CLAUDE.md）：**
- 禁止 `except Exception: pass`；具体异常类型 + logger
- Tool/纯函数必须独立可测试；业务代码禁止直接 `os.getenv`
- 前端服务层必须相对路径 + `request()`，禁止 NEXT_PUBLIC_API_URL 绝对路径
- 验证命令：后端 `python -m py_compile <file>` + `pytest <path> -v`；前端 `npx tsc --noEmit`

---

## 文件结构总览

| 操作 | 文件 | 职责 |
|---|---|---|
| Modify | `backend/orchestration/workflow/meta.py` | StepConfig 增加 `run_if` 字段 |
| Modify | `backend/orchestration/workflow/decorator.py` | `@step` 接受 `run_if` 参数 |
| Modify | `backend/orchestration/workflow/executor.py` | 执行前求值 run_if，不满足则跳过并记录 trace |
| Create | `backend/tests/orchestration/workflow/test_run_if.py` | run_if 框架扩展测试 |
| Create | `backend/selection_decision/__init__.py` | 模块入口 |
| Create | `backend/selection_decision/finance.py` | 财务规则计算 + ≤3 轮有界优化循环 |
| Create | `backend/tests/test_selection_finance.py` | finance 测试 |
| Create | `backend/selection_decision/panel.py` | N 角色 AI 评审团独立投票 |
| Create | `backend/tests/test_selection_panel.py` | panel 测试（mock LLM） |
| Create | `backend/selection_decision/store.py` | 决策任务 SQLite 持久化 |
| Create | `backend/tests/test_selection_decision_store.py` | store 测试 |
| Create | `backend/selection_decision/report.py` | Go/No-Go 决策包 Markdown 组装 |
| Create | `backend/tests/test_selection_report.py` | report 测试 |
| Create | `backend/orchestration/workflows/selection_decision.py` | Workflow 定义（4 层 DAG） |
| Modify | `backend/app/server.py` | 注册 SelectionDecision workflow |
| Create | `backend/tests/orchestration/workflow/test_selection_decision_smoke.py` | Workflow 冒烟测试 |
| Create | `backend/app/api/routes/selection_decision.py` | REST API（提交/列表/详情） |
| Modify | `backend/app/api/router.py` | include 新路由 |
| Modify | `backend/app/api/routes/__init__.py` | import 新路由模块 |
| Create | `backend/tests/test_selection_decision_api.py` | API 测试 |
| Create | `frontend/src/services/selectionDecision.ts` | 前端 service |
| Create | `frontend/src/app/selection-decision/page.tsx` | 表单任务页 + 任务列表 |
| Create | `frontend/src/app/selection-decision/[id]/page.tsx` | 决策报告页 |
| Modify | `frontend/src/components/Sidebar.tsx` | 增加「选品决策」入口 |

---

### Task 1: Workflow 框架扩展 — StepConfig.run_if 条件跳过

**Files:**
- Modify: `backend/orchestration/workflow/meta.py`（StepConfig dataclass）
- Modify: `backend/orchestration/workflow/decorator.py`（`step()` 函数）
- Modify: `backend/orchestration/workflow/executor.py`（`_run_step` 方法，span 创建之后）
- Test: `backend/tests/orchestration/workflow/test_run_if.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/orchestration/workflow/test_run_if.py`：

```python
"""run_if 条件跳过 — 框架扩展测试"""
import asyncio

from backend.orchestration.workflow.decorator import workflow, step
from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.orchestration.workflow.registry import WorkflowRegistry


@workflow(name="t_run_if", description="run_if 测试")
class _RunIfWF:
    @step()
    async def gate(self, ctx):
        return {"v": ctx.inputs.get("gate", "go")}

    @step(depends_on=["gate"], run_if=lambda out: out["gate"]["v"] == "go")
    async def on_go(self, ctx):
        return {"ran": True}

    @step(depends_on=["gate"], run_if=lambda out: out["gate"]["v"] == "no")
    async def on_no(self, ctx):
        return {"ran": True}


def _run(inputs: dict):
    reg = WorkflowRegistry()
    reg.register(_RunIfWF)
    ctx = asyncio.run(WorkflowExecutor(registry=reg).run("t_run_if", inputs=inputs))
    return ctx


def test_run_if_true_runs_step():
    ctx = _run({"gate": "go"})
    assert ctx.outputs["on_go"] == {"ran": True}
    assert "on_go" not in ctx.skip_steps


def test_run_if_false_skips_step_and_records_output():
    ctx = _run({"gate": "go"})
    assert "on_no" in ctx.skip_steps
    assert ctx.outputs["on_no"]["skipped"] is True
    assert "run_if" in ctx.outputs["on_no"]["reason"]


def test_run_if_exception_treated_as_skip():
    """run_if 谓词自身抛异常时按跳过处理，不让 workflow 崩溃"""
    @workflow(name="t_run_if_err", description="谓词异常测试")
    class _WF:
        @step()
        async def a(self, ctx):
            return {}

        @step(depends_on=["a"], run_if=lambda out: out["missing_key"])
        async def b(self, ctx):
            return {"ran": True}

    reg = WorkflowRegistry()
    reg.register(_WF)
    ctx = asyncio.run(WorkflowExecutor(registry=reg).run("t_run_if_err"))
    assert "b" in ctx.skip_steps
    assert ctx.status in ("success", "partial")


def test_step_without_run_if_unaffected():
    """无 run_if 的 step 行为不变（回归保护）"""
    ctx = _run({"gate": "no"})
    assert ctx.outputs["gate"] == {"v": "no"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/orchestration/workflow/test_run_if.py -v`
Expected: FAIL — `step() got an unexpected keyword argument 'run_if'`

- [ ] **Step 3: meta.py 增加 run_if 字段**

在 `backend/orchestration/workflow/meta.py` 的 `StepConfig` 中，`display_name` 字段之后新增：

```python
    # DAG：条件执行谓词 — 调度前对 ctx.outputs 求值；返回 False 则跳过本 step。
    # 签名: (outputs: dict[str, Any]) -> bool。None = 无条件（默认，行为不变）
    run_if: Callable[[dict[str, Any]], bool] | None = None
```

（`Callable` / `Any` 已在该文件顶部 import。）

- [ ] **Step 4: decorator.py 接受 run_if 参数**

`backend/orchestration/workflow/decorator.py` 的 `step()` 签名增加参数并校验、透传：

```python
def step(
    *,
    depends_on: list[str] | None = None,
    retry: int = 0,
    timeout_sec: int = 60,
    on_error: str = "abort",
    name: str = "",
    run_if: Callable | None = None,
) -> Callable[[Callable], Callable]:
```

在现有校验块（`timeout_sec <= 0` 之后）追加：

```python
    if run_if is not None and not callable(run_if):
        raise ValueError(f"run_if must be callable or None, got {run_if!r}")
```

`StepConfig(...)` 构造追加 `run_if=run_if`。

- [ ] **Step 5: executor.py 执行 run_if 判定**

在 `backend/orchestration/workflow/executor.py` 的 `_run_step` 中，**trace 子 span 创建块之后、`workflow_instance` 实例化之前**插入：

```python
        # run_if 条件跳过：上游输出不满足谓词 → 记录 skipped 输出并收尾 span
        if config.run_if is not None:
            try:
                should_run = bool(config.run_if(ctx.outputs))
            except Exception as e:
                logger.warning(
                    f"[WorkflowExecutor] step {step_name} run_if 求值失败，按跳过处理: {e}"
                )
                should_run = False
            if not should_run:
                ctx.skip_steps.add(step_name)
                ctx.outputs[step_name] = {"skipped": True, "reason": "run_if 条件不满足"}
                if step_span is not None:
                    try:
                        from backend.observability.tracer import trace_collector
                        trace_collector.end_span(
                            step_span, status="skipped",
                            metrics={"reason": "run_if_false"},
                        )
                    except Exception:
                        logger.debug("[P1-10] step span 跳过收尾失败", exc_info=True)
                logger.info(f"[WorkflowExecutor] step {step_name} 被 run_if 跳过")
                return
```

- [ ] **Step 6: 运行测试确认通过 + 回归**

Run: `cd backend && python -m pytest tests/orchestration/workflow/test_run_if.py tests/orchestration/workflow/test_daily_report_smoke.py -v`
Expected: 全部 PASS（含 daily_report 回归）

- [ ] **Step 7: Commit**

```bash
git add backend/orchestration/workflow/meta.py backend/orchestration/workflow/decorator.py backend/orchestration/workflow/executor.py backend/tests/orchestration/workflow/test_run_if.py
git commit -m "feat(workflow): StepConfig.run_if 条件跳过支持（Decision 分支承载）"
```

---

### Task 2: 财务测算规则模块 finance.py

**Files:**
- Create: `backend/selection_decision/__init__.py`
- Create: `backend/selection_decision/finance.py`
- Test: `backend/tests/test_selection_finance.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/selection_decision/__init__.py`（空 docstring 即可）：

```python
"""selection_decision — 选品决策引擎（Phase 1 决策闭环 MVP）"""
```

创建 `backend/tests/test_selection_finance.py`：

```python
"""finance 规则计算 + 有界优化循环测试"""
import pytest

from backend.selection_decision.finance import compute_model, run_finance


BASE_PARAMS = {
    "sell_price": 129.0,
    "unit_cost": 45.0,
    "platform_fee_rate": 0.05,
    "shipping_cost": 6.0,
    "marketing_cost": 10.0,
    "monthly_fixed_cost": 3000.0,
    "min_margin_rate": 0.25,
    "initial_inventory": 100,
    "buffer_rate": 0.15,
}


def test_compute_model_profitable():
    m = compute_model(BASE_PARAMS)
    # net = 129*0.95=122.55; margin = 122.55-45-6-10 = 61.55
    assert m["unit_margin"] == pytest.approx(61.55)
    assert m["margin_rate"] == pytest.approx(61.55 / 129)
    # break_even = ceil(3000/61.55) = 49
    assert m["break_even_units"] == 49
    assert m["first_batch_investment"] == pytest.approx(100 * (45 + 6))
    assert m["risk_buffer"] == pytest.approx(5100 * 0.15)


def test_compute_model_negative_margin():
    m = compute_model({**BASE_PARAMS, "sell_price": 40.0})
    assert m["unit_margin"] < 0
    assert m["break_even_units"] is None


def test_run_finance_pass_first_round():
    result = run_finance(BASE_PARAMS)
    assert result["verdict"] == "pass"
    assert len(result["rounds"]) == 1


def test_run_finance_fail_bounded_3_rounds():
    """明显亏损参数：每轮调价+降本后仍不达标，最多 3 轮后输出 fail"""
    result = run_finance({**BASE_PARAMS, "sell_price": 30.0, "unit_cost": 40.0})
    assert result["verdict"] == "fail"
    assert len(result["rounds"]) == 3
    assert len(result["suggestions"]) == 3


def test_run_finance_recovers_after_adjustment():
    """首轮不达标、调价+降本后达标 → pass 且 rounds>1"""
    # 首轮利润率约 14%，两轮优化后超过 25% 门槛
    result = run_finance({**BASE_PARAMS, "unit_cost": 88.0})
    assert result["verdict"] == "pass"
    assert len(result["rounds"]) >= 2


def test_validation_rejects_bad_params():
    with pytest.raises(ValueError):
        run_finance({**BASE_PARAMS, "sell_price": 0})
    with pytest.raises(ValueError):
        run_finance({**BASE_PARAMS, "unit_cost": -1})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_selection_finance.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.selection_decision.finance`

- [ ] **Step 3: 实现 finance.py**

创建 `backend/selection_decision/finance.py`：

```python
"""selection_decision/finance.py — 财务测算规则计算（spec §2：用户填参数+规则计算）

设计：
- compute_model：纯函数，参数 → 利润模型（单件利润/利润率/盈亏平衡/风险缓冲金）
- run_finance：有界优化循环（≤3 轮）——不达标时按固定策略调价(+5%)/降本(-5%)重算，
  超限输出 fail + 差距分析。数字全部来自入参与公式，全程可溯源（事实锁定）。
"""
from __future__ import annotations

import math
from typing import Any

from backend.shared.logger import logger

MAX_ROUNDS = 3
# 每轮优化调整幅度：提价 5% + 降本 5%（规则建议，不自动执行）
PRICE_STEP = 0.05
COST_STEP = 0.05

DEFAULTS = {
    "platform_fee_rate": 0.05,
    "shipping_cost": 0.0,
    "marketing_cost": 0.0,
    "monthly_fixed_cost": 0.0,
    "min_margin_rate": 0.25,
    "initial_inventory": 100,
    "buffer_rate": 0.15,
}


def _validate(params: dict[str, Any]) -> None:
    if params.get("sell_price", 0) <= 0:
        raise ValueError("sell_price 必须 > 0")
    if params.get("unit_cost", 0) < 0:
        raise ValueError("unit_cost 不能为负")
    if not 0 <= params.get("platform_fee_rate", 0) < 1:
        raise ValueError("platform_fee_rate 必须在 [0, 1) 区间")
    if params.get("initial_inventory", 0) <= 0:
        raise ValueError("initial_inventory 必须 > 0")


def compute_model(params: dict[str, Any]) -> dict[str, Any]:
    """单次利润模型计算（纯函数）"""
    merged = {**DEFAULTS, **params}
    _validate(merged)
    sell = merged["sell_price"]
    net_price = sell * (1 - merged["platform_fee_rate"])
    unit_margin = net_price - merged["unit_cost"] \
        - merged["shipping_cost"] - merged["marketing_cost"]
    margin_rate = unit_margin / sell
    break_even = (math.ceil(merged["monthly_fixed_cost"] / unit_margin)
                  if unit_margin > 0 and merged["monthly_fixed_cost"] > 0 else None)
    first_batch = merged["initial_inventory"] * (
        merged["unit_cost"] + merged["shipping_cost"])
    return {
        "sell_price": sell,
        "unit_cost": merged["unit_cost"],
        "net_price": round(net_price, 2),
        "unit_margin": round(unit_margin, 2),
        "margin_rate": round(margin_rate, 4),
        "break_even_units": break_even,
        "first_batch_investment": round(first_batch, 2),
        "risk_buffer": round(first_batch * merged["buffer_rate"], 2),
    }


def run_finance(params: dict[str, Any], max_rounds: int = MAX_ROUNDS) -> dict[str, Any]:
    """有界优化循环：不达标 → 提价/降本建议 → 重算（≤max_rounds 轮）"""
    current = dict(params)
    rounds: list[dict[str, Any]] = []
    suggestions: list[str] = []
    verdict = "fail"
    for i in range(1, max_rounds + 1):
        model = compute_model(current)
        passed = model["unit_margin"] > 0 and \
            model["margin_rate"] >= current.get("min_margin_rate", DEFAULTS["min_margin_rate"])
        rounds.append({"round": i, "passed": passed, "model": model})
        if passed:
            verdict = "pass"
            break
        # 优化建议（规则固定策略，数字可溯源）
        new_price = round(current["sell_price"] * (1 + PRICE_STEP), 2)
        new_cost = round(current["unit_cost"] * (1 - COST_STEP), 2)
        suggestions.append(
            f"第{i}轮未达标（利润率 {model['margin_rate']:.1%}）：建议提价至 "
            f"{new_price}（+{PRICE_STEP:.0%}）并将采购成本压至 {new_cost}（-{COST_STEP:.0%}）"
        )
        current = {**current, "sell_price": new_price, "unit_cost": new_cost}
    else:
        logger.info("[Finance] 有界优化循环结束仍未达标，输出 fail")
    final = rounds[-1]["model"]
    gap = (current.get("min_margin_rate", DEFAULTS["min_margin_rate"])
           - final["margin_rate"]) if verdict == "fail" else 0.0
    return {
        "verdict": verdict,
        "rounds": rounds,
        "suggestions": suggestions,
        "final_model": final,
        "margin_gap": round(gap, 4),
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_selection_finance.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/selection_decision/__init__.py backend/selection_decision/finance.py backend/tests/test_selection_finance.py
git commit -m "feat(selection-decision): 财务测算规则模块（有界优化循环≤3轮）"
```

---

### Task 3: AI 评审团模块 panel.py

**Files:**
- Create: `backend/selection_decision/panel.py`
- Test: `backend/tests/test_selection_panel.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_selection_panel.py`：

```python
"""panel 评审团测试（mock LLM，不发真实请求）"""
import asyncio
import json

import pytest

import backend.selection_decision.panel as panel_mod
from backend.selection_decision.panel import PERSONAS, aggregate_votes, run_panel

SUMMARY = {"category": "蓝牙耳机", "finance": {"margin_rate": 0.3}}


class _FakeResp:
    def __init__(self, content: str):
        self.content = content


@pytest.fixture
def mock_llm(monkeypatch):
    """默认所有评审投 go/80 分"""
    def invoke(messages):
        return _FakeResp(json.dumps(
            {"score": 80, "verdict": "go", "reason": "测试意见"}, ensure_ascii=False))
    monkeypatch.setattr(panel_mod, "llm", type("L", (), {"invoke": staticmethod(invoke)}))


def test_personas_has_7_roles():
    assert len(PERSONAS) == 7
    for p in PERSONAS:
        assert p["role"] and p["focus"]


def test_run_panel_all_go_passes(mock_llm):
    result = asyncio.run(run_panel(SUMMARY, size=7))
    assert result["verdict"] == "pass"
    assert result["size"] == 7
    assert result["go_count"] == 7
    assert result["avg_score"] == pytest.approx(80)


def test_run_panel_majority_no_go_fails(mock_llm, monkeypatch):
    """多数投 no_go → fail"""
    votes = iter([{"score": 40, "verdict": "no_go", "reason": "x"}] * 4
                 + [{"score": 80, "verdict": "go", "reason": "y"}] * 3)
    def invoke(messages):
        return _FakeResp(json.dumps(next(votes), ensure_ascii=False))
    monkeypatch.setattr(panel_mod, "llm", type("L", (), {"invoke": staticmethod(invoke)}))
    result = asyncio.run(run_panel(SUMMARY, size=7))
    assert result["verdict"] == "fail"


def test_run_panel_llm_error_counts_as_no_go(mock_llm, monkeypatch):
    """单个评审 LLM 失败 → 该票记 no_go/0 分，不让整体崩溃"""
    calls = {"n": 0}
    def invoke(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LLM 超时")
        return _FakeResp(json.dumps(
            {"score": 90, "verdict": "go", "reason": "ok"}, ensure_ascii=False))
    monkeypatch.setattr(panel_mod, "llm", type("L", (), {"invoke": staticmethod(invoke)}))
    result = asyncio.run(run_panel(SUMMARY, size=3))
    assert result["size"] == 3
    assert result["votes"][0]["verdict"] == "no_go"
    assert result["votes"][0]["error"]


def test_aggregate_votes_rules():
    votes = [{"score": 70, "verdict": "go"}] * 4 + [{"score": 50, "verdict": "no_go"}] * 3
    assert aggregate_votes(votes)["verdict"] == "pass"  # 多数 go 且均分 61.4 ≥60
    votes2 = [{"score": 50, "verdict": "go"}] * 4 + [{"score": 50, "verdict": "no_go"}] * 3
    assert aggregate_votes(votes2)["verdict"] == "fail"  # 均分 50 < 60
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_selection_panel.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 panel.py**

创建 `backend/selection_decision/panel.py`：

```python
"""selection_decision/panel.py — N 人 AI 评审团独立投票（spec §4 验证层）

设计：
- 7 个预置角色（独立 System Prompt 视角），按 size 截取
- 每个评审独立调用 LLM（asyncio.to_thread 并行），输出结构化 JSON 票
- 单个评审失败不崩溃：该票记 no_go/0 分并带 error 标记
- 聚合规则：多数票 go 且均分 ≥ 60 → pass
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from backend.infra.llm import llm
from backend.shared.logger import logger

PASS_AVG_SCORE = 60

PERSONAS: list[dict[str, str]] = [
    {"role": "风控官", "focus": "平台风控、封号风险、资金安全"},
    {"role": "供应链专家", "focus": "采购成本、备货周期、断货风险"},
    {"role": "流量操盘手", "focus": "获客成本、流量结构、推广ROI"},
    {"role": "用户研究员", "focus": "用户痛点真实性、需求频次、复购意愿"},
    {"role": "财务分析师", "focus": "利润模型、现金流、盈亏平衡"},
    {"role": "品类战略师", "focus": "竞争格局、差异化空间、品类生命周期"},
    {"role": "合规顾问", "focus": "平台规则、知识产权、资质要求"},
]

_VOTE_SYSTEM = (
    "你是{role}，专长领域：{focus}。基于给定的选品决策材料独立评审，"
    "不受他人意见影响。只回复一个 JSON 对象，不要任何其他文字："
    '{{"score": 0到100的整数, "verdict": "go"或"no_go", "reason": "50字以内理由"}}'
)


def _single_review(persona: dict[str, str], summary: dict[str, Any]) -> dict[str, Any]:
    """单个评审投票（同步，运行在 to_thread 中）"""
    from langchain_core.messages import HumanMessage, SystemMessage
    vote = {"role": persona["role"], "focus": persona["focus"],
            "score": 0, "verdict": "no_go", "reason": "", "error": False}
    try:
        resp = llm.invoke([
            SystemMessage(content=_VOTE_SYSTEM.format(**persona)),
            HumanMessage(content=json.dumps(summary, ensure_ascii=False, default=str)),
        ])
        data = json.loads(resp.content.strip().strip("`").removeprefix("json").strip())
        vote["score"] = int(data["score"])
        vote["verdict"] = "go" if data["verdict"] == "go" else "no_go"
        vote["reason"] = str(data.get("reason", ""))[:200]
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        logger.warning(f"[Panel] {persona['role']} 评审解析失败，记 no_go: {e}")
        vote.update(reason="评审输出解析失败", error=True)
    except Exception as e:
        logger.warning(f"[Panel] {persona['role']} 评审调用失败，记 no_go: {e}")
        vote.update(reason=f"评审调用失败: {e}", error=True)
    return vote


def aggregate_votes(votes: list[dict[str, Any]]) -> dict[str, Any]:
    """投票聚合：多数 go 且均分 ≥ 60 → pass"""
    size = len(votes)
    go_count = sum(1 for v in votes if v["verdict"] == "go")
    avg = sum(v["score"] for v in votes) / size if size else 0.0
    passed = go_count * 2 > size and avg >= PASS_AVG_SCORE
    return {"verdict": "pass" if passed else "fail",
            "go_count": go_count, "avg_score": round(avg, 1), "size": size}


async def run_panel(summary: dict[str, Any], size: int = 7) -> dict[str, Any]:
    """并行执行 N 个独立评审并聚合"""
    size = max(1, min(size, len(PERSONAS)))
    votes = await asyncio.gather(*[
        asyncio.to_thread(_single_review, p, summary) for p in PERSONAS[:size]
    ])
    result = aggregate_votes(list(votes))
    result["votes"] = list(votes)
    logger.info(f"[Panel] 评审完成: {result['verdict']} "
                f"(go {result['go_count']}/{size}, 均分 {result['avg_score']})")
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_selection_panel.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/selection_decision/panel.py backend/tests/test_selection_panel.py
git commit -m "feat(selection-decision): AI评审团模块（多角色独立投票+聚合规则）"
```

---

### Task 4: 决策任务持久化 store.py

**Files:**
- Create: `backend/selection_decision/store.py`
- Test: `backend/tests/test_selection_decision_store.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_selection_decision_store.py`：

```python
"""selection_decision store 测试（tmp_path 隔离）"""
import pytest

from backend.selection_decision.store import SelectionDecisionStore


@pytest.fixture
def store(tmp_path):
    return SelectionDecisionStore(db_path=str(tmp_path / "sd.db"))


def test_create_returns_id_and_running_status(store):
    task_id = store.create({"category": "蓝牙耳机"})
    row = store.get(task_id)
    assert row is not None
    assert row["status"] == "running"
    assert row["inputs"]["category"] == "蓝牙耳机"


def test_update_result(store):
    task_id = store.create({})
    store.update_result(task_id, status="success", verdict="go",
                        report_md="# 报告", trace_id="tr-1")
    row = store.get(task_id)
    assert row["status"] == "success"
    assert row["verdict"] == "go"
    assert row["report_md"] == "# 报告"
    assert row["finished_at"] is not None


def test_list_orders_by_created_desc(store):
    a = store.create({"n": 1})
    b = store.create({"n": 2})
    rows = store.list()
    assert [r["id"] for r in rows][:2] == [b, a]
    assert "report_md" not in rows[0]  # 列表不返回大字段


def test_get_missing_returns_none(store):
    assert store.get("no-such-id") is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_selection_decision_store.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 store.py**

创建 `backend/selection_decision/store.py`（模式对齐 `orchestration/workflow/persistence.py`：SQLite 单表 + threading.Lock + 模块级单例）：

```python
"""selection_decision/store.py — 选品决策任务持久化

表结构对齐 workflow_runs 惯例：SQLite 单表 + threading.Lock。
列表接口不返回 report_md 大字段，详情接口才返回。
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any

from backend.shared.logger import logger

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS selection_tasks (
    id           TEXT PRIMARY KEY,
    inputs_json  TEXT NOT NULL,
    status       TEXT NOT NULL,
    verdict      TEXT,
    report_md    TEXT,
    trace_id     TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sd_tasks_created ON selection_tasks(created_at DESC);
"""


class SelectionDecisionStore:
    def __init__(self, db_path: str = "data/selection_decision.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._lock, self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def create(self, inputs: dict[str, Any]) -> str:
        task_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO selection_tasks
                   (id, inputs_json, status, created_at) VALUES (?, ?, 'running', ?)""",
                (task_id, _json.dumps(inputs, ensure_ascii=False, default=str),
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        return task_id

    def update_result(self, task_id: str, *, status: str, verdict: str = "",
                      report_md: str = "", trace_id: str = "", error: str = "") -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """UPDATE selection_tasks
                   SET status = ?, verdict = ?, report_md = ?, trace_id = ?,
                       error = ?, finished_at = ?
                   WHERE id = ?""",
                (status, verdict, report_md, trace_id, error,
                 datetime.now().isoformat(timespec="seconds"), task_id),
            )
            conn.commit()

    def list(self, page: int = 1, page_size: int = 20) -> list[dict[str, Any]]:
        offset = (page - 1) * page_size
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT id, status, verdict, trace_id, error, created_at, finished_at,
                          inputs_json
                   FROM selection_tasks ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (page_size, offset),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["inputs"] = _json.loads(d.pop("inputs_json"))
            except (TypeError, ValueError):
                d["inputs"] = {}
            out.append(d)
        return out

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM selection_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["inputs"] = _json.loads(d.pop("inputs_json"))
        except (TypeError, ValueError):
            d["inputs"] = {}
        return d


_store: SelectionDecisionStore | None = None


def get_selection_decision_store() -> SelectionDecisionStore:
    global _store
    if _store is None:
        _store = SelectionDecisionStore()
    return _store
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_selection_decision_store.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/selection_decision/store.py backend/tests/test_selection_decision_store.py
git commit -m "feat(selection-decision): 决策任务SQLite持久化"
```

---

### Task 5: Go/No-Go 决策报告组装 report.py

**Files:**
- Create: `backend/selection_decision/report.py`
- Test: `backend/tests/test_selection_report.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_selection_report.py`：

```python
"""report 组装测试"""
from backend.selection_decision.report import build_report

OUTPUTS = {
    "competitor_data": {"count": 5},
    "market_assess": {"verdict": "go", "metrics": {"candidate_count": 5,
                       "price_min": 59.0, "price_max": 199.0, "total_reviews": 120000},
                       "data_gaps": ["市场体量无免费数据源"]},
    "differentiation": {"verdict": "go", "gaps": ["续航虚标"], "reason": "痛点集中"},
    "finance_model": {"verdict": "pass",
                      "final_model": {"unit_margin": 61.55, "margin_rate": 0.4771,
                                      "break_even_units": 49, "risk_buffer": 765.0},
                      "suggestions": []},
    "review_panel": {"verdict": "pass", "go_count": 6, "size": 7, "avg_score": 78.2,
                     "votes": [{"role": "风控官", "score": 80, "verdict": "go",
                                "reason": "风险可控"}]},
}


def test_go_report_contains_key_sections():
    md = build_report({"category": "蓝牙耳机", "platforms": ["jd", "amazon"]},
                      OUTPUTS, verdict="go", failed_gates=[])
    assert "# 选品决策报告" in md
    assert "🚀 Go" in md
    assert "蓝牙耳机" in md
    assert "续航虚标" in md
    assert "61.55" in md          # 财务数字可溯源
    assert "风控官" in md
    assert "市场体量无免费数据源" in md  # 数据缺口声明


def test_no_go_report_lists_failed_gates():
    md = build_report({"category": "x", "platforms": []}, OUTPUTS,
                      verdict="no_go", failed_gates=["财务测算", "评审团"])
    assert "❌ No-Go" in md
    assert "财务测算" in md and "评审团" in md


def test_skipped_steps_handled():
    """被 run_if 跳过的 step 输出为 {"skipped": True}，报告不应崩溃"""
    outputs = dict(OUTPUTS)
    outputs["finance_model"] = {"skipped": True, "reason": "run_if 条件不满足"}
    outputs["review_panel"] = {"skipped": True, "reason": "run_if 条件不满足"}
    md = build_report({"category": "x", "platforms": []}, outputs,
                      verdict="no_go", failed_gates=["差异化分析"])
    assert "未执行" in md
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_selection_report.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 report.py**

创建 `backend/selection_decision/report.py`：

```python
"""selection_decision/report.py — Go/No-Go 决策包 Markdown 组装

所有数字直接取自 workflow outputs（事实锁定：报告层不做任何推算）。
被 run_if 跳过的 step（outputs 含 skipped=True）渲染为「未执行」。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

GATE_LABELS = {
    "market": "市场评估(Q1)",
    "differentiation": "差异化分析",
    "finance": "财务测算",
    "panel": "评审团",
}


def _skipped(out: dict[str, Any] | None) -> bool:
    return bool(out and out.get("skipped"))


def build_report(inputs: dict[str, Any], outputs: dict[str, Any],
                 verdict: str, failed_gates: list[str]) -> str:
    lines: list[str] = [
        "# 选品决策报告（Go/No-Go 决策包）",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 品类关键词：{inputs.get('category', '-')}",
        f"- 目标平台：{'、'.join(inputs.get('platforms') or []) or '-'}",
        f"- **最终决策：{'🚀 Go — 建议入场' if verdict == 'go' else '❌ No-Go — 不建议入场'}**",
    ]
    if failed_gates:
        lines += ["- 未通过环节：" + "、".join(
            GATE_LABELS.get(g, g) for g in failed_gates), ""]

    # ── 市场评估 ──
    market = outputs.get("market_assess")
    lines += ["", "## 一、市场评估（Q1：这个市场要不要做）", ""]
    if _skipped(market):
        lines.append("本环节未执行。")
    elif market:
        m = market.get("metrics", {})
        lines += [
            f"- 结论：**{'建议继续' if market.get('verdict') == 'go' else '不建议'}**",
            f"- 候选竞品数：{m.get('candidate_count', '-')}（代理指标，非真实市场体量）",
            f"- 价格带：{m.get('price_min', '-')} ~ {m.get('price_max', '-')}",
            f"- 评价总量：{m.get('total_reviews', '-')}（需求热度代理）",
            f"- TOP3 评价集中度：{m.get('top3_review_share', '-')}",
        ]
        if market.get("data_gaps"):
            lines.append("- ⚠️ 数据缺口：" + "；".join(market["data_gaps"]))

    # ── 差异化 ──
    diff = outputs.get("differentiation")
    lines += ["", "## 二、差异化分析（Decision1：是否存在切入点）", ""]
    if _skipped(diff):
        lines.append("本环节未执行（市场评估未通过）。")
    elif diff:
        lines.append(f"- 结论：**{'存在切入点' if diff.get('verdict') == 'go' else '无明显切入点'}**")
        if diff.get("gaps"):
            lines.append("- 需求缺口：" + "、".join(diff["gaps"]))
        if diff.get("reason"):
            lines.append(f"- 依据：{diff['reason']}")
        lines.append("- ⚠️ Phase 1 痛点来源为 LLM 推断（非评论实证），仅供参考。")

    # ── 财务 ──
    fin = outputs.get("finance_model")
    lines += ["", "## 三、财务测算（Decision2：模型是否达标）", ""]
    if _skipped(fin):
        lines.append("本环节未执行（差异化分析未通过）。")
    elif fin:
        fm = fin.get("final_model", {})
        lines += [
            f"- 结论：**{'达标' if fin.get('verdict') == 'pass' else '不达标'}**"
            f"（共 {len(fin.get('rounds', []))} 轮测算）",
            "",
            "| 指标 | 数值 |", "|---|---|",
            f"| 单件利润 | {fm.get('unit_margin', '-')} |",
            f"| 利润率 | {fm.get('margin_rate', '-')} |",
            f"| 盈亏平衡销量 | {fm.get('break_even_units', '-')} 件/月 |",
            f"| 首批投入 | {fm.get('first_batch_investment', '-')} |",
            f"| 风险缓冲金 | {fm.get('risk_buffer', '-')} |",
        ]
        for s in fin.get("suggestions", []):
            lines.append(f"- 优化记录：{s}")

    # ── 评审团 ──
    panel = outputs.get("review_panel")
    lines += ["", "## 四、AI 评审团（Decision3：独立投票）", ""]
    if _skipped(panel):
        lines.append("本环节未执行（财务测算未达标）。")
    elif panel:
        lines += [
            f"- 结论：**{'通过' if panel.get('verdict') == 'pass' else '未通过'}**"
            f"（{panel.get('go_count', 0)}/{panel.get('size', 0)} 票 Go，"
            f"均分 {panel.get('avg_score', '-')}）",
            "",
            "| 角色 | 评分 | 投票 | 理由 |", "|---|---|---|---|",
        ]
        for v in panel.get("votes", []):
            lines.append(f"| {v.get('role')} | {v.get('score')} | "
                         f"{v.get('verdict')} | {v.get('reason', '')} |")

    lines += ["", "---", "*本报告由选品决策 Workflow 自动生成；"
              "代理指标与推断结论已如实标注，请结合人工判断使用。*"]
    return "\n".join(lines)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_selection_report.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/selection_decision/report.py backend/tests/test_selection_report.py
git commit -m "feat(selection-decision): Go/No-Go决策报告组装（事实锁定）"
```

---

### Task 6: selection_decision Workflow 定义 + 冒烟测试

**Files:**
- Create: `backend/orchestration/workflows/selection_decision.py`
- Modify: `backend/app/server.py`（workflow import 处 341-342 行与注册块 344-348 行）
- Test: `backend/tests/orchestration/workflow/test_selection_decision_smoke.py`

- [ ] **Step 1: 写失败测试（DAG 结构 + 全链路冒烟）**

创建 `backend/tests/orchestration/workflow/test_selection_decision_smoke.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/orchestration/workflow/test_selection_decision_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.orchestration.workflows.selection_decision`

- [ ] **Step 3: 实现 Workflow**

创建 `backend/orchestration/workflows/selection_decision.py`：

```python
"""workflows/selection_decision.py — 选品决策 Workflow（Phase 1 决策闭环 MVP）

架构（spec §4.2）：
- Layer 0: competitor_data（watchlist 快照）
- Layer 1（并行）: market_assess / competitor_profile / review_pain
- Layer 2: differentiation（run_if 市场 go）→ finance_model（run_if 差异化 go，内部≤3轮循环）
- Layer 3: review_panel（run_if 财务达标）
- Layer 4: decision_report（恒定执行，组装 Go/No-Go 决策包）

Phase 1 限制（报告中如实标注）：
- 无新数据源：市场评估用代理指标；痛点为 LLM 推断而非评论实证
"""
from __future__ import annotations

import json
from statistics import median
from typing import Any

from backend.competitor.store import get_store
from backend.infra.llm import llm
from backend.orchestration.workflow import workflow, step
from backend.selection.recommender import batch_scores
from backend.selection_decision.finance import run_finance
from backend.selection_decision.panel import run_panel
from backend.selection_decision.report import build_report
from backend.selection_decision.store import get_selection_decision_store
from backend.shared.logger import logger

# ── run_if 谓词（Decision 分支，spec §4.3）──────────────


def _market_go(out: dict[str, Any]) -> bool:
    return (out.get("market_assess") or {}).get("verdict") == "go"


def _diff_go(out: dict[str, Any]) -> bool:
    return (out.get("differentiation") or {}).get("verdict") == "go"


def _finance_pass(out: dict[str, Any]) -> bool:
    return (out.get("finance_model") or {}).get("verdict") == "pass"


def _llm_json(messages) -> Any:
    resp = llm.invoke(messages)
    return json.loads(resp.content.strip().strip("`").removeprefix("json").strip())


@workflow(
    name="selection_decision",
    description="选品决策 Go/No-Go — 市场评估/差异化/财务测算/AI评审团四层流水线",
    objects=["选品", "决策", "入场", "品类"],
    actions=["评估", "分析", "决策"],
    examples=["评估蓝牙耳机品类值不值得做", "帮我做一次选品决策"],
    category="selection",
)
class SelectionDecision:
    """选品决策 Workflow — 8 个 step，5 层 DAG"""

    # ── Layer 0 感知层 ──────────────────────────
    @step(name="竞品数据采集", timeout_sec=120)
    async def competitor_data(self, ctx):
        store = get_store()
        candidates = []
        for item in store.list_watch(enabled_only=True):
            snap = store.latest_snapshot(item["url"])
            if snap and (snap.get("price") is not None or snap.get("title")):
                candidates.append({
                    "url": item["url"], "title": snap.get("title") or item["url"],
                    "platform": snap.get("platform") or "generic",
                    "price": snap.get("price"), "rating": snap.get("rating"),
                    "review_count": snap.get("review_count"),
                    "highlights": snap.get("highlights") or "",
                })
        if not candidates:
            raise ValueError("watchlist 为空或无快照，请先在竞品监控添加商品 URL")
        return {"candidates": candidates, "count": len(candidates)}

    # ── Layer 1 分析层（并行）──────────────────
    @step(depends_on=["competitor_data"], name="市场评估(Q1)", timeout_sec=60)
    async def market_assess(self, ctx):
        """代理指标评估（免费数据源限制，spec R2：如实标注缺口）"""
        cands = ctx.outputs["competitor_data"]["candidates"]
        prices = [c["price"] for c in cands if c.get("price") is not None]
        reviews = sorted([c["review_count"] for c in cands if c.get("review_count")],
                         reverse=True)
        total_reviews = sum(reviews)
        top3_share = round(sum(reviews[:3]) / total_reviews, 3) if total_reviews else 0
        metrics = {
            "candidate_count": len(cands),
            "price_min": min(prices) if prices else None,
            "price_max": max(prices) if prices else None,
            "price_median": round(median(prices), 2) if prices else None,
            "total_reviews": total_reviews,
            "top3_review_share": top3_share,
        }
        # 规则门控：候选≥3 且评价总量≥100 → 视为存在需求（代理判断）
        verdict = "go" if len(cands) >= 3 and total_reviews >= 100 else "no_go"
        return {
            "verdict": verdict,
            "metrics": metrics,
            "data_gaps": [
                "市场体量/增长率/季节性无免费数据源，以候选数与评价量作代理指标",
                "搜索趋势/供需比缺失（Phase 2 接入下拉词采集）",
            ],
        }

    @step(depends_on=["competitor_data"], name="竞品画像", timeout_sec=60)
    async def competitor_profile(self, ctx):
        cands = ctx.outputs["competitor_data"]["candidates"]
        urls = [c["url"] for c in cands]
        scores = batch_scores(urls).get("scores", {})
        profiles = []
        for c in cands:
            breakdown = (scores.get(c["url"]) or {}).get("breakdown") or {}
            profiles.append({**c, "radar": breakdown})
        return {"profiles": profiles}

    @step(depends_on=["competitor_data"], name="痛点推断",
          timeout_sec=180, on_error="skip")
    async def review_pain(self, ctx):
        """Phase 1 降级：无评论数据，LLM 基于卖点/评分推断痛点（spec R3 ②）"""
        from langchain_core.messages import HumanMessage, SystemMessage
        cands = ctx.outputs["competitor_data"]["candidates"]
        material = "\n".join(
            f"- {c['title']}（评分{c.get('rating')}）卖点: {c['highlights']}"
            for c in cands)
        fallback = {"pain_points": [], "source": "none",
                    "note": "痛点推断失败，差异化分析将仅基于结构化数据"}
        try:
            data = _llm_json([
                SystemMessage(content=(
                    "你是电商用户研究员。基于给定商品的标题/卖点/评分，推断该品类"
                    "用户最可能的痛点。只回复 JSON 数组（字符串列表，最多5项）。")),
                HumanMessage(content=material),
            ])
            pains = [str(x) for x in data][:5]
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(f"[SelectionDecision] 痛点推断解析失败: {e}")
            return fallback
        except Exception as e:
            logger.warning(f"[SelectionDecision] 痛点推断调用失败: {e}")
            return fallback
        return {"pain_points": pains, "source": "inferred",
                "note": "非评论实证，基于卖点/评分的 LLM 推断（Phase 1 降级）"}

    # ── Layer 2 决策层 ──────────────────────────
    @step(depends_on=["market_assess", "competitor_profile", "review_pain"],
          name="差异化分析", timeout_sec=180, run_if=_market_go)
    async def differentiation(self, ctx):
        """Decision1：是否存在差异化切入点（LLM 推理 + 保守兕底）"""
        from langchain_core.messages import HumanMessage, SystemMessage
        material = {
            "market": ctx.outputs["market_assess"]["metrics"],
            "profiles": ctx.outputs["competitor_profile"]["profiles"],
            "pain_points": (ctx.outputs.get("review_pain") or {}).get("pain_points", []),
        }
        conservative = {"verdict": "no_go", "gaps": [], "heatmap": [],
                        "reason": "差异化分析不可用（LLM 失败），保守拒绝"}
        try:
            data = _llm_json([
                SystemMessage(content=(
                    "你是选品差异化分析师。基于市场指标、竞品画像与痛点列表，判断是否"
                    "存在差异化切入点。只回复 JSON："
                    '{"verdict": "go"或"no_go", "gaps": ["未被满足的需求"], '
                    '"heatmap": [{"pain": "痛点", "severity": 1到5}], "reason": "50字以内"}')),
                HumanMessage(content=json.dumps(material, ensure_ascii=False, default=str)),
            ])
            if data.get("verdict") not in ("go", "no_go"):
                raise ValueError(f"非法 verdict: {data.get('verdict')}")
            return {"verdict": data["verdict"],
                    "gaps": [str(g) for g in data.get("gaps", [])],
                    "heatmap": data.get("heatmap", []),
                    "reason": str(data.get("reason", ""))[:200]}
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"[SelectionDecision] 差异化分析失败，保守拒绝: {e}")
            return conservative
        except Exception as e:
            logger.warning(f"[SelectionDecision] 差异化分析调用失败: {e}")
            return conservative

    @step(depends_on=["differentiation"], name="财务测算",
          timeout_sec=60, run_if=_diff_go)
    async def finance_model(self, ctx):
        """Decision2：规则测算 + 内部有界优化循环（≤3 轮）"""
        params = ctx.inputs.get("finance") or {}
        return run_finance(params)

    # ── Layer 3 验证层 ──────────────────────────
    @step(depends_on=["finance_model"], name="AI评审团",
          timeout_sec=300, run_if=_finance_pass)
    async def review_panel(self, ctx):
        """Decision3：N 角色独立投票（人数来自任务参数）"""
        summary = {
            "category": ctx.inputs.get("category"),
            "platforms": ctx.inputs.get("platforms"),
            "market": ctx.outputs["market_assess"]["metrics"],
            "differentiation": ctx.outputs["differentiation"],
            "finance": ctx.outputs["finance_model"]["final_model"],
        }
        return await run_panel(summary, size=int(ctx.inputs.get("panel_size", 7)))

    # ── Layer 4 产出 ────────────────────────────
    @step(depends_on=["market_assess", "differentiation",
                       "finance_model", "review_panel"],
          name="决策报告", timeout_sec=60)
    async def decision_report(self, ctx):
        outputs = ctx.outputs
        checks = {
            "market": (outputs.get("market_assess") or {}).get("verdict") == "go",
            "differentiation": (outputs.get("differentiation") or {}).get("verdict") == "go",
            "finance": (outputs.get("finance_model") or {}).get("verdict") == "pass",
            "panel": (outputs.get("review_panel") or {}).get("verdict") == "pass",
        }
        failed = [k for k, ok in checks.items() if not ok]
        verdict = "go" if not failed else "no_go"
        report_md = build_report(ctx.inputs, outputs, verdict=verdict, failed_gates=failed)
        task_id = ctx.inputs.get("task_id")
        if task_id:
            get_selection_decision_store().update_result(
                task_id, status="success", verdict=verdict,
                report_md=report_md, trace_id=ctx.trace_id or "")
        return {"verdict": verdict, "failed_gates": failed, "report_md": report_md}
```

- [ ] **Step 4: server.py 注册 workflow**

修改 `backend/app/server.py`：

在现有 import（341-342 行）处追加：

```python
from backend.orchestration.workflows.selection_decision import SelectionDecision
```

在注册块（`reg.register(InventoryAlert)` 所在 if 块之后）追加：

```python
        if reg.get("selection_decision") is None:
            reg.register(SelectionDecision)
```

- [ ] **Step 5: 运行冒烟测试 + 回归**

Run: `cd backend && python -m pytest tests/orchestration/workflow/test_selection_decision_smoke.py tests/orchestration/workflow/test_daily_report_smoke.py tests/inventory/test_workflow_inventory_alert.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/orchestration/workflows/selection_decision.py backend/app/server.py backend/tests/orchestration/workflow/test_selection_decision_smoke.py
git commit -m "feat(selection-decision): 四层DAG决策Workflow（run_if分支+有界财务循环）"
```

---

### Task 7: REST API 路由（提交/列表/详情）

**Files:**
- Create: `backend/app/api/routes/selection_decision.py`
- Modify: `backend/app/api/router.py`（import 块 + include_router）
- Modify: `backend/app/api/routes/__init__.py`（import 行）
- Test: `backend/tests/test_selection_decision_api.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_selection_decision_api.py`：

```python
"""selection_decision API 测试（不真实跑 workflow：_run_task 打桩）"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.app.api.routes.selection_decision as sd_routes

VALID_PAYLOAD = {
    "category": "蓝牙耳机",
    "platforms": ["jd", "amazon"],
    "finance": {"sell_price": 129.0, "unit_cost": 45.0},
    "panel_size": 3,
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from backend.selection_decision.store import SelectionDecisionStore
    store = SelectionDecisionStore(db_path=str(tmp_path / "api.db"))
    monkeypatch.setattr(sd_routes, "get_selection_decision_store", lambda: store)

    async def no_run(task_id, inputs):
        pass
    monkeypatch.setattr(sd_routes, "_run_task", no_run)

    app = FastAPI()
    app.include_router(sd_routes.router)
    return TestClient(app)


def test_post_task_creates_running_task(client):
    resp = client.post("/selection-decision/tasks", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["task_id"]


def test_post_task_validation(client):
    bad = {**VALID_PAYLOAD, "finance": {"sell_price": 0, "unit_cost": 45.0}}
    assert client.post("/selection-decision/tasks", json=bad).status_code == 422


def test_list_and_detail(client):
    task_id = client.post("/selection-decision/tasks", json=VALID_PAYLOAD).json()["task_id"]
    rows = client.get("/selection-decision/tasks").json()["tasks"]
    assert any(r["id"] == task_id for r in rows)
    detail = client.get(f"/selection-decision/tasks/{task_id}").json()
    assert detail["inputs"]["category"] == "蓝牙耳机"


def test_detail_404(client):
    assert client.get("/selection-decision/tasks/no-such").status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_selection_decision_api.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.app.api.routes.selection_decision`

- [ ] **Step 3: 实现路由**

创建 `backend/app/api/routes/selection_decision.py`：

```python
"""selection_decision REST API — 表单任务页入口（spec §8）

路由前缀: /selection-decision（经 next.config.js rewrite 由 /api 代理）。
POST /tasks 创建任务后立即返回 task_id，workflow 异步执行；
前端通过 GET /tasks 与 GET /tasks/{id} 轮询进度。
"""
import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.selection_decision.store import get_selection_decision_store
from backend.shared.logger import logger

router = APIRouter(prefix="/selection-decision", tags=["选品决策"])


class FinanceParams(BaseModel):
    sell_price: float = Field(..., gt=0, description="预期售价")
    unit_cost: float = Field(..., ge=0, description="单件采购成本")
    platform_fee_rate: float = Field(0.05, ge=0, lt=1)
    shipping_cost: float = Field(0.0, ge=0)
    marketing_cost: float = Field(0.0, ge=0)
    monthly_fixed_cost: float = Field(0.0, ge=0)
    min_margin_rate: float = Field(0.25, ge=0, lt=1)
    initial_inventory: int = Field(100, gt=0)
    buffer_rate: float = Field(0.15, ge=0, le=1)


class TaskRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=64, description="品类关键词")
    platforms: list[str] = Field(
        default_factory=lambda: ["jd", "taobao", "amazon"])
    finance: FinanceParams
    panel_size: int = Field(7, ge=1, le=7, description="评审团人数")


async def _run_task(task_id: str, inputs: dict) -> None:
    """后台执行 workflow；异常/失败时把任务标记为 failed"""
    try:
        ctx = await WorkflowExecutor().run("selection_decision", inputs=inputs)
        if ctx.status == "failed":
            get_selection_decision_store().update_result(
                task_id, status="failed", error=ctx.error or "workflow 执行失败")
    except Exception as e:
        logger.error(f"[SelectionDecision:api] 任务 {task_id} 执行异常: {e}")
        get_selection_decision_store().update_result(
            task_id, status="failed", error=str(e)[:500])


@router.post("/tasks")
async def create_task(req: TaskRequest):
    """提交选品决策任务（异步执行）"""
    store = get_selection_decision_store()
    inputs = {
        "category": req.category,
        "platforms": req.platforms,
        "finance": req.finance.model_dump(),
        "panel_size": req.panel_size,
    }
    task_id = store.create(inputs)
    inputs["task_id"] = task_id
    asyncio.create_task(_run_task(task_id, inputs))
    logger.info(f"[SelectionDecision:api] 任务已提交: {task_id} ({req.category})")
    return {"task_id": task_id, "status": "running"}


@router.get("/tasks")
def list_tasks(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return {"tasks": get_selection_decision_store().list(page=page, page_size=page_size)}


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    row = get_selection_decision_store().get(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return row
```

- [ ] **Step 4: 注册路由**

修改 `backend/app/api/router.py`：import 块追加 `selection_decision`，并在 `api_router.include_router(selection.router)` 之后追加：

```python
api_router.include_router(selection_decision.router)  # 选品决策
```

修改 `backend/app/api/routes/__init__.py`：import 行追加 `selection_decision`。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_selection_decision_api.py -v`
Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/selection_decision.py backend/app/api/router.py backend/app/api/routes/__init__.py backend/tests/test_selection_decision_api.py
git commit -m "feat(selection-decision): 任务提交/列表/详情REST API"
```

---

### Task 8: 前端 — service + 表单任务页 + 报告页 + 侧边栏入口

**Files:**
- Create: `frontend/src/services/selectionDecision.ts`
- Create: `frontend/src/app/selection-decision/page.tsx`
- Create: `frontend/src/app/selection-decision/[id]/page.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: 创建 service**

创建 `frontend/src/services/selectionDecision.ts`（遵守项目约定：相对路径 + request()，禁止 NEXT_PUBLIC_API_URL）：

```ts
/**
 * 选品决策 service
 *
 * 后端为 backend/app/api/routes/selection_decision.py（前缀 /selection-decision，
 * 经 next.config.js rewrite 代理）。相对路径 + request()。
 */
import { request } from '@/lib/fetcher'

const BASE = '/selection-decision'

export interface FinanceParams {
  sell_price: number
  unit_cost: number
  platform_fee_rate?: number
  shipping_cost?: number
  marketing_cost?: number
  monthly_fixed_cost?: number
  min_margin_rate?: number
  initial_inventory?: number
  buffer_rate?: number
}

export interface TaskPayload {
  category: string
  platforms: string[]
  finance: FinanceParams
  panel_size: number
}

export interface SelectionTask {
  id: string
  status: string        // running / success / failed / partial
  verdict: string | null
  trace_id: string
  error: string | null
  created_at: string
  finished_at: string | null
  inputs: { category: string; platforms: string[] }
}

export interface SelectionTaskDetail extends SelectionTask {
  report_md: string | null
}

export const selectionDecisionApi = {
  submit(payload: TaskPayload) {
    return request<{ task_id: string; status: string }>(`${BASE}/tasks`, {
      method: 'POST', body: JSON.stringify(payload), timeout: 30_000,
    })
  },
  list(page = 1, pageSize = 20) {
    return request<{ tasks: SelectionTask[] }>(`${BASE}/tasks?page=${page}&page_size=${pageSize}`)
  },
  get(id: string) {
    return request<SelectionTaskDetail>(`${BASE}/tasks/${id}`)
  },
}
```

- [ ] **Step 2: 创建表单任务页**

创建 `frontend/src/app/selection-decision/page.tsx`：

```tsx
'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { selectionDecisionApi, SelectionTask, TaskPayload } from '@/services/selectionDecision'

const PLATFORMS = [
  { key: 'jd', label: '京东' },
  { key: 'taobao', label: '淘宝' },
  { key: 'amazon', label: '亚马逊' },
]

const STATUS_LABEL: Record<string, { text: string; cls: string }> = {
  running: { text: '执行中', cls: 'bg-blue-100 text-blue-700' },
  success: { text: '完成', cls: 'bg-green-100 text-green-700' },
  partial: { text: '部分完成', cls: 'bg-yellow-100 text-yellow-700' },
  failed: { text: '失败', cls: 'bg-red-100 text-red-700' },
}

export default function SelectionDecisionPage() {
  const [category, setCategory] = useState('')
  const [platforms, setPlatforms] = useState<string[]>(['jd', 'amazon'])
  const [sellPrice, setSellPrice] = useState('129')
  const [unitCost, setUnitCost] = useState('45')
  const [panelSize, setPanelSize] = useState(7)
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState('')
  const [tasks, setTasks] = useState<SelectionTask[]>([])

  const loadTasks = useCallback(async () => {
    try {
      const res = await selectionDecisionApi.list()
      setTasks(res.tasks)
    } catch (e) {
      setMessage(`加载任务列表失败: ${e}`)
    }
  }, [])

  useEffect(() => {
    loadTasks()
    const timer = setInterval(loadTasks, 3000)  // 轮询进度
    return () => clearInterval(timer)
  }, [loadTasks])

  const togglePlatform = (key: string) =>
    setPlatforms(p => p.includes(key) ? p.filter(x => x !== key) : [...p, key])

  const submit = async () => {
    if (!category.trim()) { setMessage('请填写品类关键词'); return }
    const payload: TaskPayload = {
      category: category.trim(),
      platforms,
      finance: { sell_price: Number(sellPrice), unit_cost: Number(unitCost) },
      panel_size: panelSize,
    }
    setSubmitting(true)
    try {
      const res = await selectionDecisionApi.submit(payload)
      setMessage(`任务已提交（${res.task_id}），后台执行中…`)
      loadTasks()
    } catch (e) {
      setMessage(`提交失败: ${e}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <h1 className="text-xl font-semibold">选品决策（Go/No-Go）</h1>
      <p className="text-sm text-gray-500">
        提交后异步执行：市场评估 → 差异化分析 → 财务测算 → AI 评审团 → 决策报告。
        请先在「竞品监控」添加候选商品 URL（Phase 1 数据源）。
      </p>

      <div className="border rounded-lg p-4 space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">品类关键词</label>
          <input className="border rounded px-3 py-2 w-64" value={category}
                 onChange={e => setCategory(e.target.value)} placeholder="例如：蓝牙耳机" />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">目标平台</label>
          <div className="flex gap-4">
            {PLATFORMS.map(p => (
              <label key={p.key} className="flex items-center gap-1 text-sm">
                <input type="checkbox" checked={platforms.includes(p.key)}
                       onChange={() => togglePlatform(p.key)} />
                {p.label}
              </label>
            ))}
          </div>
        </div>
        <div className="flex gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">预期售价</label>
            <input type="number" className="border rounded px-3 py-2 w-32" value={sellPrice}
                   onChange={e => setSellPrice(e.target.value)} />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">单件成本</label>
            <input type="number" className="border rounded px-3 py-2 w-32" value={unitCost}
                   onChange={e => setUnitCost(e.target.value)} />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">评审团人数</label>
            <select className="border rounded px-3 py-2" value={panelSize}
                    onChange={e => setPanelSize(Number(e.target.value))}>
              <option value={3}>3 人</option>
              <option value={5}>5 人</option>
              <option value={7}>7 人</option>
            </select>
          </div>
        </div>
        <button onClick={submit} disabled={submitting}
                className="bg-blue-600 text-white rounded px-4 py-2 disabled:opacity-50">
          {submitting ? '提交中…' : '提交决策任务'}
        </button>
        {message && <p className="text-sm text-gray-600">{message}</p>}
      </div>

      <div>
        <h2 className="font-medium mb-2">任务列表</h2>
        <table className="w-full text-sm border">
          <thead>
            <tr className="bg-gray-50 text-left">
              <th className="p-2 border-b">品类</th>
              <th className="p-2 border-b">提交时间</th>
              <th className="p-2 border-b">状态</th>
              <th className="p-2 border-b">决策</th>
              <th className="p-2 border-b">报告</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map(t => {
              const st = STATUS_LABEL[t.status] ?? { text: t.status, cls: 'bg-gray-100' }
              return (
                <tr key={t.id}>
                  <td className="p-2 border-b">{t.inputs?.category}</td>
                  <td className="p-2 border-b">{t.created_at}</td>
                  <td className="p-2 border-b">
                    <span className={`px-2 py-0.5 rounded text-xs ${st.cls}`}>{st.text}</span>
                  </td>
                  <td className="p-2 border-b">
                    {t.verdict === 'go' ? '🚀 Go' : t.verdict === 'no_go' ? '❌ No-Go' : '-'}
                  </td>
                  <td className="p-2 border-b">
                    {t.finished_at && (
                      <Link className="text-blue-600 underline" href={`/selection-decision/${t.id}`}>
                        查看
                      </Link>
                    )}
                  </td>
                </tr>
              )
            })}
            {tasks.length === 0 && (
              <tr><td colSpan={5} className="p-4 text-center text-gray-400">暂无任务</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: 创建报告页**

创建 `frontend/src/app/selection-decision/[id]/page.tsx`：

```tsx
'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import ReactMarkdown from 'react-markdown'
import { selectionDecisionApi, SelectionTaskDetail } from '@/services/selectionDecision'

export default function SelectionDecisionReportPage() {
  const params = useParams<{ id: string }>()
  const [task, setTask] = useState<SelectionTaskDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null
    const load = async () => {
      try {
        const t = await selectionDecisionApi.get(params.id)
        setTask(t)
        if (t.status === 'running' && !timer) {
          timer = setInterval(load, 3000)  // 运行中轮询
        }
      } catch (e) {
        setError(`加载失败: ${e}`)
      }
    }
    load()
    return () => { if (timer) clearInterval(timer) }
  }, [params.id])

  if (error) return <div className="p-6 text-red-600">{error}</div>
  if (!task) return <div className="p-6 text-gray-500">加载中…</div>
  if (task.status === 'running') {
    return <div className="p-6 text-gray-500">任务执行中，页面自动刷新…</div>
  }
  return (
    <div className="p-6 max-w-3xl">
      {task.status === 'failed' ? (
        <div className="text-red-600">任务失败：{task.error || '未知错误'}</div>
      ) : (
        <div className="prose prose-sm max-w-none">
          <ReactMarkdown>{task.report_md || '无报告内容'}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: 侧边栏入口**

修改 `frontend/src/components/Sidebar.tsx`：

1. 顶部 lucide-react import 中追加 `ClipboardCheck`；
2. 在「智能选品」条目（path: '/selection'）之后插入：

```tsx
  {
    icon: <ClipboardCheck size={18} />, label: '选品决策', path: '/selection-decision',
  },
```

- [ ] **Step 5: 前端类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 6: Commit**

```bash
git add frontend/src/services/selectionDecision.ts frontend/src/app/selection-decision/page.tsx "frontend/src/app/selection-decision/[id]/page.tsx" frontend/src/components/Sidebar.tsx
git commit -m "feat(selection-decision): 表单任务页+决策报告页+侧边栏入口"
```

---

### Task 9: 端到端验收

**Files:** 无新增（验收）

- [ ] **Step 1: 后端全量回归**

Run: `cd backend && python -m pytest tests/test_selection_finance.py tests/test_selection_panel.py tests/test_selection_decision_store.py tests/test_selection_report.py tests/test_selection_decision_api.py tests/orchestration/workflow/test_run_if.py tests/orchestration/workflow/test_selection_decision_smoke.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 启动服务手工验证**

Run: `start_all.bat`（项目一键启动）

浏览器验证：
1. 侧边栏出现「选品决策」入口，点击进入表单页
2. watchlist 有商品时提交任务 → 任务列表出现 running → 数分钟后变为 success/partial，「查看」打开报告页，Markdown 渲染正常
3. 打开链路追踪页能看到 workflow_run.selection_decision trace 与各 step span（含被 run_if 跳过的 step 状态为 skipped）

- [ ] **Step 3: 无快照场景验证**

清空/禁用 watchlist 后提交任务 → 任务应为 failed，错误信息含“watchlist 为空”（错误已如实展示而非静默）。

- [ ] **Step 4: Commit（如有验收中的修复）**

```bash
git add -A
git commit -m "fix(selection-decision): 端到端验收修复"
```

---

## 验收标准汇总

- [ ] 全部新增测试通过（run_if 4 + finance 6 + panel 5 + store 4 + report 3 + smoke 3 + api 4）
- [ ] daily_report / inventory_alert 回归无破坏
- [ ] `npx tsc --noEmit` 无错误
- [ ] watchlist 非空时提交任务能产出 Go 或 No-Go 决策报告，被跳过环节在报告中如实标注
- [ ] trace 页可见完整 workflow span 链，含 run_if skipped 状态
- [ ] Phase 1 限制（代理指标/LLM 推断痛点）在报告中显式标注
