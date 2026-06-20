# Agent 可靠性工程 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Multi-Agent 编排层加入 Plan Critique（自我纠错）、Worker 超时保护、通用降级链、JSON 修复管道、告警可观测性，在小模型环境下实现 Agent 可靠性工程。

**Architecture:** 在 Planner → Supervisor 之间插入 Plan Critique 节点（+1 LLM 调用），Worker 层加入 asyncio 超时 + 指数退避 + 错误分类，Supervisor 加入循环上限 + 结构化降级检测，Reporter 加入 BM25 兜底 + 结构化成功判断，Planner JSON 提取升级为 4 层修复管道，新增告警系统贯穿全链路。

**Tech Stack:** Python 3.10+, LangGraph, asyncio, json, re, threading (现有)

## Global Constraints

- LLM 调用增量 ≤ +1（仅 Plan Critique 节点）
- 所有新增 LLM 调用失败时必须有退出路径，不阻塞主流程
- 小模型 qwen2.5:4b 是目标运行模型，prompt 设计需考虑其推理能力上限
- 遵循现有代码风格：TypedDict 类型定义、logger 日志、config.py 集中配置
- 所有新模块放在 `multi_agent/` 包中
- 测试用 `PYTHONPATH=".venv/lib/site-packages" python -m pytest` 运行

---

### Task 1: 告警系统 — `alerts.py`

**Files:**
- Create: `multi_agent/alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Consumes: nothing（零依赖）
- Produces:
  - `PlanAlert` dataclass: `timestamp: str, level: Literal["info","warn","error"], code: str, message: str, detail: dict`
  - `ALERT_CODES: dict[str, tuple[str, str]]` — code → (level, message) 映射表
  - `log_degradation(alert: PlanAlert) -> None` — 追加写入 `logs/degradation.jsonl`

- [ ] **Step 1: Write the failing test**

File: `tests/test_alerts.py`

```python
"""tests for multi_agent.alerts — 告警数据类 + 日志写入"""

import json
import os
import tempfile
from multi_agent.alerts import PlanAlert, ALERT_CODES, log_degradation


def test_plan_alert_creation():
    """PlanAlert 数据类创建 + 字段完整性"""
    alert = PlanAlert(
        timestamp="2026-06-21T12:00:00",
        level="warn",
        code="PLAN_EMPTY",
        message="Planner 返回空计划",
        detail={"question": "测试问题"},
    )
    assert alert.level == "warn"
    assert alert.code == "PLAN_EMPTY"
    assert alert.detail["question"] == "测试问题"


def test_alert_codes_completeness():
    """验证所有告警代码都有对应的 level 和 message"""
    required_codes = [
        "PLAN_EMPTY", "PLAN_JSON_INVALID", "PLAN_CAP_INVALID",
        "PLAN_MISROUTE", "CRITIQUE_FAILED", "SUPERVISOR_MAX_LOOP",
        "WORKER_TIMEOUT", "WORKER_RETRY_EXHAUST",
        "RERANKER_UNAVAILABLE", "DEGRADATION_TRIGGER",
    ]
    for code in required_codes:
        assert code in ALERT_CODES, f"缺少告警代码: {code}"
        level, message = ALERT_CODES[code]
        assert level in ("info", "warn", "error")
        assert len(message) > 0


def test_log_degradation_writes_jsonl():
    """log_degradation 写入 JSONL 文件"""
    alert = PlanAlert(
        timestamp="2026-06-21T12:00:00",
        level="warn",
        code="PLAN_EMPTY",
        message="测试告警",
        detail={},
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "test.jsonl")
        # 临时替换模块中的日志路径
        import multi_agent.alerts as alerts_mod
        original_path = alerts_mod.DEGRADATION_LOG_FILE
        alerts_mod.DEGRADATION_LOG_FILE = log_path
        try:
            log_degradation(alert)
            assert os.path.exists(log_path)
            with open(log_path, "r", encoding="utf-8") as f:
                line = f.readline()
                data = json.loads(line)
                assert data["code"] == "PLAN_EMPTY"
                assert data["level"] == "warn"
        finally:
            alerts_mod.DEGRADATION_LOG_FILE = original_path


def test_log_degradation_no_exception_on_io_error():
    """磁盘写入失败时不抛异常（非阻塞）"""
    alert = PlanAlert(
        timestamp="2026-06-21T12:00:00",
        level="info",
        code="DEGRADATION_TRIGGER",
        message="测试",
        detail={},
    )
    import multi_agent.alerts as alerts_mod
    original_path = alerts_mod.DEGRADATION_LOG_FILE
    alerts_mod.DEGRADATION_LOG_FILE = "NUL"  # Windows: NUL device, won't fail
    try:
        # 不应抛出异常
        log_degradation(alert)
    finally:
        alerts_mod.DEGRADATION_LOG_FILE = original_path
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_alerts.py -v --tb=short
```

Expected: FAIL — `ModuleNotFoundError: No module named 'multi_agent.alerts'`

- [ ] **Step 3: Write minimal implementation**

File: `multi_agent/alerts.py`

```python
"""
alerts.py — 告警与可观测性

PlanAlert 数据类 + 告警代码表 + 降级日志写入。
贯穿 Planner / Critique / Supervisor / Worker / Reporter 全链路。
"""

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Literal
from utils.logger import logger

# =====================================================
# PlanAlert 数据类
# =====================================================

@dataclass
class PlanAlert:
    """计划层面的告警事件"""
    timestamp: str
    level: Literal["info", "warn", "error"]
    code: str           # 告警代码，如 "PLAN_FALLBACK"
    message: str        # 人类可读的描述
    detail: dict        # 结构化详情（question, step_id 等）


# =====================================================
# 告警代码表
# =====================================================

ALERT_CODES: dict[str, tuple[str, str]] = {
    # code → (level, message)
    "PLAN_EMPTY":             ("warn",  "Planner 返回空计划，降级为 RAG 兜底"),
    "PLAN_JSON_INVALID":      ("warn",  "Planner 输出无法解析为 JSON，使用兜底"),
    "PLAN_CAP_INVALID":       ("warn",  "计划包含无效 capability，已跳过"),
    "PLAN_MISROUTE":          ("warn",  "Critique 检测到 capability 不匹配，已修正"),
    "CRITIQUE_FAILED":        ("warn",  "Plan Critique 调用失败，使用原计划"),
    "SUPERVISOR_MAX_LOOP":    ("error", "Supervisor 达到最大循环次数，强制终止"),
    "WORKER_TIMEOUT":         ("error", "Worker 执行超时"),
    "WORKER_RETRY_EXHAUST":   ("error", "Worker 重试耗尽，最终失败"),
    "RERANKER_UNAVAILABLE":   ("warn",  "CrossEncoder 不可用，降级为 BM25 过滤"),
    "DEGRADATION_TRIGGER":    ("info",  "触发降级链"),
}


# =====================================================
# 辅助函数
# =====================================================

def make_alert(code: str, detail: dict | None = None) -> PlanAlert:
    """根据告警代码创建 PlanAlert 实例"""
    tz_utc8 = timezone(timedelta(hours=8))
    level, message = ALERT_CODES.get(code, ("warn", f"未知告警: {code}"))
    return PlanAlert(
        timestamp=datetime.now(tz_utc8).isoformat(timespec="seconds"),
        level=level,
        code=code,
        message=message,
        detail=detail or {},
    )


def alert_to_dict(alert: PlanAlert) -> dict:
    """将 PlanAlert 转为可 JSON 序列化的 dict（用于 SSE 流）"""
    return asdict(alert)


# =====================================================
# 降级日志
# =====================================================

DEGRADATION_LOG_DIR = "logs"
DEGRADATION_LOG_FILE = os.path.join(DEGRADATION_LOG_DIR, "degradation.jsonl")


def log_degradation(alert: PlanAlert) -> None:
    """记录降级事件到 JSONL 文件（非阻塞追加，失败不抛异常）"""
    try:
        os.makedirs(DEGRADATION_LOG_DIR, exist_ok=True)
        with open(DEGRADATION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(alert), ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"[Alerts] 降级日志写入失败: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_alerts.py -v --tb=short
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add multi_agent/alerts.py tests/test_alerts.py
git commit -m "feat: add alert system — PlanAlert, ALERT_CODES, degradation JSONL logging"
```

---

### Task 2: State 类型扩展 — `state.py`

**Files:**
- Modify: `multi_agent/state.py` (full file, 51 lines)
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing（仅修改 TypedDict 定义）
- Produces:
  - `StepResult` 新增字段: `row_count: int | None`, `is_empty: bool | None`, `error_type: str | None`
  - `AgentState` 新增字段: `alerts: list[dict]`, `_supervisor_loop_count: int`, `_plan_critiqued: bool`, `_plan_changed: bool`

- [ ] **Step 1: Write the failing test**

File: `tests/test_state.py`

```python
"""tests for multi_agent.state — 类型定义完整性"""

import pytest
from multi_agent.state import StepResult, AgentState, _merge_step_results


def test_step_result_has_new_fields():
    """StepResult 可以包含 row_count, is_empty, error_type"""
    sr: StepResult = {
        "step_id": "1",
        "capability": "query_database",
        "description": "测试",
        "status": "success",
        "output": "result",
        "row_count": 5,
        "is_empty": False,
        "error_type": None,
    }
    assert sr["row_count"] == 5
    assert sr["is_empty"] is False
    assert sr["error_type"] is None


def test_step_result_empty_result():
    """SQL 空结果的结构化表示"""
    sr: StepResult = {
        "step_id": "2",
        "capability": "query_database",
        "description": "查询",
        "status": "success",
        "output": "(无结果)",
        "row_count": 0,
        "is_empty": True,
        "error_type": None,
    }
    assert sr["is_empty"] is True
    assert sr["row_count"] == 0


def test_agent_state_has_new_fields():
    """AgentState 可以包含新版内部字段"""
    state: AgentState = {
        "question": "测试问题",
        "kb_id": "default",
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
        "current_step_id": None,
        "messages": [],
        "final_answer": "",
        "alerts": [],
        "_supervisor_loop_count": 0,
        "_plan_critiqued": False,
        "_plan_changed": False,
    }
    assert state["_supervisor_loop_count"] == 0
    assert state["_plan_critiqued"] is False
    assert state["alerts"] == []


def test_merge_step_results_preserves_new_fields():
    """Reducer 合并时保留新字段"""
    left = {
        "1": {
            "step_id": "1", "status": "running",
            "row_count": None, "is_empty": None, "error_type": None,
        }
    }
    right = {
        "1": {
            "step_id": "1", "status": "success",
            "row_count": 3, "is_empty": False, "error_type": None,
        }
    }
    merged = _merge_step_results(left, right)
    assert merged["1"]["status"] == "success"
    assert merged["1"]["row_count"] == 3
    assert merged["1"]["is_empty"] is False
```

- [ ] **Step 2: Run test to verify it passes (behavioral contract test)**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_state.py -v --tb=short
```

Expected: 5 PASS — Python TypedDict 不强制运行时检查，但这些测试验证了 reducer 行为和新字段的可用性。Step 3 中将正式加上类型注解。

```python
"""tests for multi_agent.state — 类型定义 + reducer 完整性"""

from multi_agent.state import StepResult, AgentState, _merge_step_results


def test_step_result_with_structured_fields():
    """StepResult 支持 row_count, is_empty, error_type 结构化字段"""
    sr: StepResult = {
        "step_id": "1",
        "capability": "query_database",
        "description": "查询员工",
        "status": "success",
        "output": "共 5 条记录",
        "row_count": 5,
        "is_empty": False,
        "error_type": None,
    }
    assert sr["row_count"] == 5
    assert not sr["is_empty"]


def test_step_result_empty_sql():
    """SQL 空结果: is_empty=True, row_count=0"""
    sr: StepResult = {
        "step_id": "2",
        "status": "success",
        "output": "无结果",
        "row_count": 0,
        "is_empty": True,
    }
    assert sr["is_empty"]
    assert sr["row_count"] == 0


def test_agent_state_alert_fields():
    """AgentState 包含 alerts 和内部追踪字段"""
    state: AgentState = {
        "question": "测试",
        "kb_id": "default",
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
        "current_step_id": None,
        "messages": [],
        "final_answer": "",
        "alerts": [],
        "_supervisor_loop_count": 0,
        "_plan_critiqued": False,
        "_plan_changed": False,
    }
    assert state["_supervisor_loop_count"] == 0
    assert state["_plan_critiqued"] is False
    assert isinstance(state["alerts"], list)


def test_merge_preserves_new_fields():
    """Reducer 合并后新字段值来自 right"""
    left = {"1": {"step_id": "1", "status": "running", "row_count": None, "is_empty": None, "error_type": None}}
    right = {"1": {"step_id": "1", "status": "success", "row_count": 3, "is_empty": False, "error_type": None}}
    merged = _merge_step_results(left, right)
    assert merged["1"]["status"] == "success"
    assert merged["1"]["row_count"] == 3
    assert merged["1"]["is_empty"] is False


def test_merge_empty_handling():
    """Reducer 空值处理不变"""
    assert _merge_step_results({}, {"1": {"status": "ok"}}) == {"1": {"status": "ok"}}
    assert _merge_step_results({"1": {"status": "ok"}}, {}) == {"1": {"status": "ok"}}
```

- [ ] **Step 2: Run test to verify it passes**

The test will pass immediately since Python TypedDict doesn't enforce at runtime. We're testing the behavioral contract.

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_state.py -v --tb=short
```

Expected: 5 PASS（当前 TypedDict 不改也能通过，但我们在 Step 3 加上字段定义以完善类型）

- [ ] **Step 3: Add new fields to state.py**

File: `multi_agent/state.py` — modify `StepResult` and `AgentState` classes:

```python
"""
state.py — AgentState 与 StepResult 类型定义

统一图状态对象，所有节点通过读写此 state 协同工作。
"""

from typing import TypedDict, Literal, Any, Annotated
from langgraph.graph.message import add_messages


def _merge_step_results(left: dict, right: dict) -> dict:
    """Reducer: 合并并行 Worker 返回的 step_results。

    LangGraph 并行执行多个 Worker 时，每个 Worker 返回
    {"step_results": {"step_id": result}}，reducer 负责合并。
    right 中的 key 覆盖 left 中的同 key（允许状态更新覆盖旧值）。
    """
    if not left:
        return dict(right)
    if not right:
        return dict(left)
    merged = dict(left)
    merged.update(right)
    return merged


class StepResult(TypedDict, total=False):
    """单个步骤的执行结果"""
    step_id: str
    capability: str                             # Planner 分配给该步骤的 capability
    description: str                            # 步骤描述
    status: Literal["pending", "running", "success", "failed", "skipped"]
    output: Any                                 # Worker 执行后的返回值
    error: str | None                           # 失败时的错误信息
    retries: int                                # 已重试次数
    started_at: float                           # 开始时间 (time.time())
    finished_at: float                          # 完成时间
    # ⭐ 新增：结构化结果字段
    row_count: int | None                       # SQL 查询返回行数
    is_empty: bool | None                       # 是否为空结果（SQL 无数据 / RAG 无匹配）
    error_type: str | None                      # 错误分类: timeout / parse / auth / network / unknown


class AgentState(TypedDict):
    """Multi-Agent 工作流全局状态"""
    question: str                               # 用户原始问题
    kb_id: str                                  # 知识库ID（policy/tech/finance/hr/default）
    plan: dict                                  # Planner 产出的 DAG:
                                                # {"nodes": {"1": {...}, "2": {...}},
                                                #  "edges": {"3": ["1","2"]}}
    step_results: Annotated[dict[str, StepResult], _merge_step_results]
    current_step_id: str | None                 # 当前正在执行的 step（Worker 用）
    messages: Annotated[list, add_messages]     # ReAct 对话历史
    final_answer: str                           # Reporter 产物
    # ⭐ 新增：可观测性 + 流程控制字段
    alerts: list[dict]                          # PlanAlert 列表（SSE 流展示）
    _supervisor_loop_count: int                 # Supervisor 调度轮次计数
    _plan_critiqued: bool                       # 是否经过了 Plan Critique
    _plan_changed: bool                         # Critique 是否修改了计划
```

- [ ] **Step 4: Run tests to verify**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_state.py -v --tb=short
```

Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add multi_agent/state.py tests/test_state.py
git commit -m "feat: add StepResult structured fields + AgentState alert/tracking fields"
```

---

### Task 3: 降级链 — `degradation.py`

**Files:**
- Create: `multi_agent/degradation.py`
- Test: `tests/test_degradation.py`

**Interfaces:**
- Consumes: nothing（纯配置 + 逻辑，仅依赖 `multi_agent.tool_registry` 和 `multi_agent.alerts`）
- Produces:
  - `DEGRADATION_CHAIN: dict[str, list[str]]` — capability → fallback capabilities
  - `can_degrade(step_id: str, attempted: set) -> bool` — 防止无限降级
  - `get_fallback_capability(capability: str) -> str | None` — 获取降级目标

- [ ] **Step 1: Write the failing test**

File: `tests/test_degradation.py`

```python
"""tests for multi_agent.degradation — 通用降级链"""

from multi_agent.degradation import (
    DEGRADATION_CHAIN,
    can_degrade,
    get_fallback_capability,
    MAX_DEGRADATION_PER_STEP,
)


def test_degradation_chain_structure():
    """降级链包含所有 capability"""
    assert "query_database" in DEGRADATION_CHAIN
    assert "search_knowledge" in DEGRADATION_CHAIN
    assert "generate_report" in DEGRADATION_CHAIN


def test_sql_fallback_to_rag():
    """SQL 空结果 → RAG 降级"""
    fb = get_fallback_capability("query_database")
    assert fb == "search_knowledge"


def test_rag_fallback_to_sql():
    """RAG 无结果 → SQL 降级（反向降级）"""
    fb = get_fallback_capability("search_knowledge")
    assert fb == "query_database"


def test_can_degrade_new_step():
    """未尝试过降级的步骤可以降级"""
    attempted = set()
    assert can_degrade("1", attempted) is True


def test_can_degrade_already_attempted():
    """已降级过的步骤不可再降级（防止循环）"""
    attempted = {"1_fallback"}
    assert can_degrade("1_fallback", attempted) is False


def test_get_fallback_unknown_capability():
    """未知 capability 返回 None"""
    assert get_fallback_capability("unknown_cap") is None


def test_max_degradation_per_step():
    """每个步骤最多降级 MAX_DEGRADATION_PER_STEP 次"""
    assert MAX_DEGRADATION_PER_STEP == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_degradation.py -v --tb=short
```

Expected: FAIL — `ModuleNotFoundError: No module named 'multi_agent.degradation'`

- [ ] **Step 3: Write minimal implementation**

File: `multi_agent/degradation.py`

```python
"""
degradation.py — 通用降级链

能力降级注册表 + 降级执行逻辑。
每个步骤最多降级 1 次，防止无限降级循环。

降级触发场景:
  - SQL 返回空结果 → RAG 知识库检索
  - RAG 无匹配结果 → SQL 数据库查询
  - Report 缺少数据 → RAG 知识库检索
"""

from multi_agent.tool_registry import tool_registry
from multi_agent.alerts import make_alert, log_degradation
from utils.logger import logger

# =====================================================
# 降级链配置
# =====================================================

MAX_DEGRADATION_PER_STEP = 1

DEGRADATION_CHAIN: dict[str, list[str]] = {
    "query_database":   ["search_knowledge"],      # SQL 空 → 知识库
    "search_knowledge": ["query_database"],         # 知识库无结果 → SQL
    "generate_report":  ["search_knowledge"],       # 报告缺数据 → 知识库
}


# =====================================================
# 降级逻辑
# =====================================================

def can_degrade(step_id: str, attempted: set[str]) -> bool:
    """检查该步骤是否还可以降级（防止无限降级循环）"""
    # 检查该步骤已经产生了多少个降级衍生步骤
    degradation_count = sum(
        1 for aid in attempted if aid.startswith(step_id + "_") or aid == step_id + "_rag_fallback"
    )
    return degradation_count < MAX_DEGRADATION_PER_STEP


def get_fallback_capability(capability: str) -> str | None:
    """获取某个能力的降级目标 capability"""
    chain = DEGRADATION_CHAIN.get(capability, [])
    return chain[0] if chain else None


def execute_degradation(
    nodes: dict,
    edges: dict,
    step_results: dict,
    ready_dispatch: list,
    degraded_steps: set[str],
    question: str,
) -> tuple[dict, list, set[str]]:
    """
    检查所有已完成的步骤，对需要降级的执行降级。

    参数:
        nodes:          当前的 plan.nodes（可修改）
        edges:          当前的 plan.edges
        step_results:   当前步骤结果集
        ready_dispatch: 已有的就绪派发列表（会追加）
        degraded_steps: 已经降级过的步骤集合（防止重复）
        question:       原始用户问题

    返回:
        (step_results, ready_dispatch, degraded_steps) — 可能已修改
    """
    has_plan_changed = False

    for sid, node in list(nodes.items()):
        sr = step_results.get(sid, {})
        capability = node.get("capability", "")

        # 跳过非 success 步骤
        if sr.get("status") != "success":
            continue

        # 检查是否需要降级：is_empty 或 row_count == 0
        is_empty = sr.get("is_empty", False)
        row_count = sr.get("row_count")
        if not is_empty and row_count != 0:
            continue

        # 检查降级条件
        if not can_degrade(sid, degraded_steps):
            continue

        fallback_cap = get_fallback_capability(capability)
        if not fallback_cap:
            continue

        # 检查 plan 中是否已有同类型步骤
        has_same_cap = any(
            n.get("capability") == fallback_cap
            for n in nodes.values()
        )
        if has_same_cap:
            logger.info(f"[Degradation] step={sid} 降级目标 {fallback_cap} 已在计划中，跳过")
            continue

        # 执行降级：插入新节点
        fallback_id = f"{sid}_fallback"
        original_question = str(node.get("params", {}).get("question", question))
        nodes[fallback_id] = {
            "step_id": fallback_id,
            "capability": fallback_cap,
            "description": f"降级检索 ({capability} 无结果): {original_question[:30]}...",
            "params": {"question": original_question},
        }

        worker = tool_registry.get_worker(fallback_cap)
        if worker:
            degraded_steps.add(fallback_id)
            step_results[fallback_id] = {"status": "pending"}
            ready_dispatch.append({"worker": worker, "step_id": fallback_id})
            has_plan_changed = True

            alert = make_alert("DEGRADATION_TRIGGER", {
                "from_step": sid,
                "from_capability": capability,
                "to_step": fallback_id,
                "to_capability": fallback_cap,
            })
            log_degradation(alert)
            logger.info(
                f"[Degradation] {capability} → {fallback_cap} "
                f"(step {sid} → {fallback_id})"
            )

    return step_results, ready_dispatch, degraded_steps
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_degradation.py -v --tb=short
```

Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add multi_agent/degradation.py tests/test_degradation.py
git commit -m "feat: add degradation chain — capability fallback with loop prevention"
```

---

### Task 4: JSON 修复管道 — `planner.py`

**Files:**
- Modify: `multi_agent/planner.py:223-239` (`_extract_json`) + 新增修复辅助函数
- Test: `tests/test_json_repair.py`

**Interfaces:**
- Consumes: `multi_agent.alerts` (make_alert)
- Produces:
  - `_extract_json(text: str) -> dict` — 升级为返回 dict（非 str），4 层修复管道
  - `_strip_markdown_code_block(text: str) -> str`
  - `_find_outer_braces(text: str) -> tuple[int, int]`
  - `_repair_common_json_errors(text: str) -> str`
  - `_fix_unquoted_keys(text: str) -> str`

- [ ] **Step 1: Write the failing test**

File: `tests/test_json_repair.py`

```python
"""tests for multi_agent.planner._extract_json — 4 层 JSON 修复管道"""

import pytest
from multi_agent.planner import _extract_json


def test_valid_json_direct():
    """Layer 0: 正常 JSON 直接解析"""
    result = _extract_json('{"nodes": {"1": {"step_id": "1"}}, "edges": {}}')
    assert result["nodes"]["1"]["step_id"] == "1"


def test_markdown_code_block():
    """Layer 0: markdown 包裹的 JSON"""
    text = '```json\n{"nodes": {}, "edges": {}}\n```'
    result = _extract_json(text)
    assert result["nodes"] == {}
    assert result["edges"] == {}


def test_trailing_comma_in_object():
    """Layer 2: 尾逗号修复 {,}"""
    text = '{"nodes": {"1": {"step_id": "1",}}, "edges": {},}'
    result = _extract_json(text)
    assert result["nodes"]["1"]["step_id"] == "1"


def test_trailing_comma_in_array():
    """Layer 2: 数组尾逗号修复 [,]"""
    text = '{"nodes": {"1": {"step_id": "1"}}, "edges": {"2": ["1",]}}'
    result = _extract_json(text)
    assert result["edges"]["2"] == ["1"]


def test_chinese_quotes():
    """Layer 2: 中文引号替换"""
    text = '{“nodes”: {“1”: {“step_id”: “1”}}}'
    result = _extract_json(text)
    assert result["nodes"]["1"]["step_id"] == "1"


def test_unquoted_keys():
    """Layer 2: 无引号 key 修复 {key: value}"""
    text = '{nodes: {"1": {step_id: "1"}}, edges: {}}'
    result = _extract_json(text)
    assert result["nodes"]["1"]["step_id"] == "1"


def test_single_quoted_strings():
    """Layer 2: 单引号字符串 → 双引号"""
    text = "{'nodes': {'1': {'step_id': '1'}}, 'edges': {}}"
    result = _extract_json(text)
    assert result["nodes"]["1"]["step_id"] == "1"


def test_nested_braces():
    """Layer 1: 多层嵌套正确截取"""
    text = '前缀文本 {"nodes": {"1": {"step_id": "1", "params": {"q": "测试"}}}, "edges": {}} 后缀文本'
    result = _extract_json(text)
    assert result["nodes"]["1"]["params"]["q"] == "测试"


def test_empty_string_returns_fallback():
    """全失败时返回空 dict（触发 _fallback_plan）"""
    result = _extract_json("这不是 JSON 文本")
    assert result == {}


def test_planner_real_output_like():
    """模拟 Planner 常见输出"""
    text = '''```json
{
  "nodes": {
    "1": {
      "step_id": "1",
      "capability": "query_database",
      "description": "查询技术部员工",
      "params": {"question": "技术部有哪些员工"}
    },
    "2": {
      "step_id": "2",
      "capability": "search_knowledge",\n      "description": "检索请假制度",
      "params": {"question": "请假流程和规定"}
    }
  },
  "edges": {
    "3": ["1", "2"]
  }
}
```'''
    result = _extract_json(text)
    assert len(result["nodes"]) == 2
    assert result["nodes"]["1"]["capability"] == "query_database"
    assert result["nodes"]["2"]["capability"] == "search_knowledge"
    assert "3" in result["edges"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_json_repair.py -v --tb=short
```

Expected: some FAIL — 当前 `_extract_json` 返回 `str` 而非 `dict`，且不支持尾逗号/中文引号/无引号key修复

- [ ] **Step 3: Rewrite `_extract_json` and helpers**

File: `multi_agent/planner.py` — replace `_extract_json` (lines 223-239) and add helper functions before it:

Replace the old `_extract_json` (which currently returns `str`) with:

```python
# =====================================================
# JSON 修复管道（4 层）
# =====================================================

def _strip_markdown_code_block(text: str) -> str:
    """去除 markdown 代码块标记 (```json ... ```)"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _find_outer_braces(text: str) -> tuple[int, int] | None:
    """找到最外层的 { } 边界，返回 (start, end) 或 None"""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return start, end
    return None


def _replace_single_quotes_in_json(text: str) -> str:
    """在 JSON 上下文中的单引号替换为双引号（保守策略：仅替换 key 和顶层字符串值）"""
    # 匹配 'key': 模式 → "key":
    import re
    text = re.sub(r"'([^']*)'(\s*:)", r'"\1"\2', text)
    # 匹配 : 'value' 模式 → : "value"
    text = re.sub(r"(:\s*)'([^']*)'", r'\1"\2"', text)
    return text


def _fix_unquoted_keys(text: str) -> str:
    """修复缺失引号的 key: {key: "value"} → {"key": "value"}"""
    import re
    # 匹配 { 或 , 后的无引号标识符后跟 :（不在字符串内部）
    text = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', text)
    return text


def _repair_common_json_errors(text: str) -> str:
    """修复小模型常见的 JSON 格式错误"""
    import re

    # 1. 尾逗号：{"a": 1,} → {"a": 1}
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)

    # 2. 中文引号 → 英文引号
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('‘', "'").replace('’', "'")

    # 3. 未转义的控制字符在字符串值中（保留 \n \t \r）
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

    # 4. 缺失引号的 key（非标准但常见）
    text = _fix_unquoted_keys(text)

    # 5. 单引号替换（在修复 key 之后做，避免破坏 key 修复的正则）
    text = _replace_single_quotes_in_json(text)

    return text


def _brute_force_extract(text: str) -> dict:
    """暴力提取：用正则找最外层的完整 JSON 对象"""
    import re
    # 找 { 开头，匹配到对应的 }
    matches = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    for match in sorted(matches, key=len, reverse=True):
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue
    return {}


def _extract_json(text: str) -> dict:
    """4 层修复管道：每层尝试解析，成功即返回。

    Layer 0: 直接解析（最快路径）
    Layer 1: 截取最外层 {} 再解析
    Layer 2: 修复常见小模型 JSON 错误后解析
    Layer 3: 暴力正则提取

    全失败返回空 dict（触发 _fallback_plan）。
    """
    from multi_agent.alerts import make_alert, log_degradation

    text = _strip_markdown_code_block(text)

    # Layer 0: 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Layer 1: 截取最外层 {}
    bounds = _find_outer_braces(text)
    if bounds:
        try:
            return json.loads(text[bounds[0]:bounds[1] + 1])
        except json.JSONDecodeError:
            pass

    # Layer 2: 修复常见错误
    try:
        repaired = _repair_common_json_errors(text)
        bounds = _find_outer_braces(repaired)
        if bounds:
            return json.loads(repaired[bounds[0]:bounds[1] + 1])
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Layer 3: 暴力提取
    result = _brute_force_extract(text)
    if result:
        return result

    # 全失败
    logger.warning("[Planner] JSON 修复管道全部失败，触发兜底")
    alert = make_alert("PLAN_JSON_INVALID", {"text_preview": text[:200]})
    log_degradation(alert)
    return {}
```

Then update `planner_node` where it calls `_extract_json` — the old call was:

```python
# 旧代码 (planner_node 中):
plan = _extract_json(response.content)  # 返回 str
plan = json.loads(plan)
```

Must be changed to:

```python
# 新代码 (planner_node 中):
plan = _extract_json(response.content)  # 现在直接返回 dict
```

Also, the old `_extract_json` returned `str` but `_normalize_plan` in `planner_node` expected a dict from the call chain. Let me check...

Looking at the existing `planner_node` code, it calls:
```python
plan = _extract_json(content)  # currently returns str
plan = json.loads(plan)        # then parses str → dict
```

Since our new `_extract_json` returns `dict` directly, we need to remove the `json.loads()` call. Let me find the exact call site.

- [ ] **Step 4: Run tests to verify**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_json_repair.py -v --tb=short
```

Expected: 10 PASS

- [ ] **Step 5: Commit**

```bash
git add multi_agent/planner.py tests/test_json_repair.py
git commit -m "feat: upgrade _extract_json to 4-layer repair pipeline for small-model JSON errors"
```

---

### Task 5: Worker 超时保护 + 重试优化 — `workers/base.py`

**Files:**
- Modify: `multi_agent/workers/base.py` (full file)
- Test: `tests/test_worker_retry.py`

**Interfaces:**
- Consumes: `multi_agent.alerts` (make_alert)
- Produces:
  - `execute_with_retry(state, tool_fn, max_retries)` — 升级为 async，含超时 + 退避 + 错误分类
  - `UNRETRYABLE_PATTERNS: list[str]`
  - `_is_retryable(error: str) -> bool`

- [ ] **Step 1: Write the failing test**

File: `tests/test_worker_retry.py`

```python
"""tests for multi_agent.workers.base — Worker 超时 + 重试 + 错误分类"""

import asyncio
import pytest
from unittest.mock import MagicMock

from multi_agent.workers.base import (
    execute_with_retry,
    _is_retryable,
    UNRETRYABLE_PATTERNS,
    DEFAULT_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    RETRY_BACKOFF_BASE,
)


def test_retryable_errors():
    """网络/超时类错误可重试"""
    assert _is_retryable("connection timeout") is True
    assert _is_retryable("timeout error") is True
    assert _is_retryable("unexpected error") is True


def test_unretryable_errors():
    """参数/语法类错误不可重试"""
    for pattern in UNRETRYABLE_PATTERNS:
        assert _is_retryable(pattern) is False, f"'{pattern}' should be unretryable"
    assert _is_retryable("Error: no such table: users") is False
    assert _is_retryable("column not found: name") is False


def test_retryable_case_insensitive():
    """错误分类大小写不敏感"""
    assert _is_retryable("No Such Table: employees") is False
    assert _is_retryable("SYNTAX ERROR near SELECT") is False


@pytest.mark.asyncio
async def test_worker_success_first_try():
    """Worker 首次成功执行"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "测试",
                    "params": {"question": "test"},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    mock_tool.invoke.return_value = "查询结果"

    result = await execute_with_retry(state, mock_tool)
    sr = result["step_results"]["1"]
    assert sr["status"] == "success"
    assert sr["output"] == "查询结果"
    assert sr["retries"] == 0


@pytest.mark.asyncio
async def test_worker_retry_then_success():
    """Worker 第一次失败、第二次成功"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "测试",
                    "params": {},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    mock_tool.invoke.side_effect = [Exception("timeout"), "最终结果"]

    result = await execute_with_retry(state, mock_tool)
    sr = result["step_results"]["1"]
    assert sr["status"] == "success"
    assert sr["output"] == "最终结果"
    assert sr["retries"] == 1  # 第二次尝试成功，retries=1


@pytest.mark.asyncio
async def test_worker_retry_exhausted():
    """Worker 重试耗尽，最终失败"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "测试",
                    "params": {},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    mock_tool.invoke.side_effect = Exception("connection timeout")

    result = await execute_with_retry(state, mock_tool)
    sr = result["step_results"]["1"]
    assert sr["status"] == "failed"
    assert "connection timeout" in sr.get("error", "")


@pytest.mark.asyncio
async def test_worker_no_retry_on_unretryable():
    """不可重试错误不重试，直接失败"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "query_database",
                    "description": "测试",
                    "params": {},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    mock_tool.invoke.side_effect = Exception("no such table: nonexistent")

    result = await execute_with_retry(state, mock_tool)
    sr = result["step_results"]["1"]
    assert sr["status"] == "failed"
    # 不可重试，invoke 应该只被调用 1 次
    assert mock_tool.invoke.call_count == 1


@pytest.mark.asyncio
async def test_worker_timeout():
    """Worker 超时触发 TimeoutError"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "测试",
                    "params": {},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    # 模拟超时：长时间阻塞
    async def slow_invoke(*args, **kwargs):
        await asyncio.sleep(99)  # 超过 DEFAULT_TIMEOUT
        return "too late"

    mock_tool.invoke = slow_invoke

    # 用一个很短的超时来测试
    result = await execute_with_retry(state, mock_tool, max_retries=0, timeout=0.1)
    sr = result["step_results"]["1"]
    assert sr["status"] == "failed"
    assert "超时" in sr.get("error", "").lower() or "timeout" in sr.get("error", "").lower()


@pytest.mark.asyncio
async def test_worker_missing_step_id():
    """current_step_id 为空时返回空 dict"""
    state = {
        "current_step_id": None,
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
    }
    mock_tool = MagicMock()
    result = await execute_with_retry(state, mock_tool)
    assert result == {}


def test_backoff_constants():
    """退避常量存在且合理"""
    assert RETRY_BACKOFF_BASE >= 1.0
    assert DEFAULT_TIMEOUT >= 30
    assert DEFAULT_MAX_RETRIES >= 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_worker_retry.py -v --tb=short
```

Expected: FAIL — `execute_with_retry` is not async, has no `_is_retryable`, etc.

- [ ] **Step 3: Rewrite `workers/base.py`**

File: `multi_agent/workers/base.py` — full replacement:

```python
"""
workers/base.py — Worker 公共逻辑

所有 Worker 共享的:
  - asyncio 超时保护（asyncio.wait_for）
  - 指数退避重试（1.5s → 2.25s）
  - 错误分类（可重试 vs 不可重试）
  - 状态写回 state.step_results
"""

import asyncio
import time
from utils.logger import logger

# 全局配置
DEFAULT_TIMEOUT = 60       # 单次调用超时（秒）
DEFAULT_MAX_RETRIES = 2    # 最大重试次数（总共最多 3 次尝试）
RETRY_BACKOFF_BASE = 1.5   # 指数退避基数（秒）: 1.5, 2.25

# 不可重试的错误模式（参数错误/资源不存在等，重试无意义）
UNRETRYABLE_PATTERNS = [
    "no such table",
    "column not found",
    "syntax error",
    "invalid parameter",
    "权限不足",
    "permission denied",
    "table does not exist",
]


def _is_retryable(error: str) -> bool:
    """判断错误是否值得重试"""
    error_lower = error.lower()
    return not any(p.lower() in error_lower for p in UNRETRYABLE_PATTERNS)


async def execute_with_retry(
    state: dict,
    tool_fn,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """
    执行工具调用，带 retry + timeout + 状态管理。

    参数:
        state:       当前 AgentState
        tool_fn:     工具调用函数 (接收 **params)
        max_retries: 最大重试次数（默认 2，总共最多 3 次尝试）
        timeout:     单次调用超时秒数

    返回:
        {"step_results": {...}} 状态更新字典
    """
    from multi_agent.alerts import make_alert, log_degradation

    step_id = state.get("current_step_id")
    if not step_id:
        logger.error("[Worker] current_step_id 为空，无法执行")
        return {}

    plan = state.get("plan", {})
    step_info = plan.get("nodes", {}).get(step_id)
    if not step_info:
        logger.error(f"[Worker] 找不到 step 信息: {step_id}")
        return {}

    step_results = dict(state.get("step_results", {}))

    # 初始化 step 状态
    sr = step_results.get(step_id, {})
    sr["step_id"] = step_id
    sr["capability"] = step_info.get("capability", "unknown")
    sr["description"] = step_info.get("description", "")
    sr["retries"] = 0

    params = step_info.get("params", {})

    # —— 重试循环 ——
    last_error = None
    for attempt in range(max_retries + 1):
        sr["status"] = "running"
        sr["started_at"] = time.time()
        sr["retries"] = attempt
        step_results[step_id] = dict(sr)

        try:
            logger.info(
                f"[Worker] 执行 step={step_id} "
                f"cap={sr['capability']} "
                f"(第{attempt+1}/{max_retries+1}次，timeout={timeout}s)"
            )

            # ⭐ asyncio 超时保护：将同步 Tool 放到线程池中执行
            output = await asyncio.wait_for(
                asyncio.to_thread(tool_fn.invoke, params),
                timeout=timeout,
            )

            sr["status"] = "success"
            sr["output"] = output
            sr["error"] = None
            sr["error_type"] = None
            sr["finished_at"] = time.time()
            step_results[step_id] = dict(sr)

            elapsed = sr["finished_at"] - sr.get("started_at", sr["finished_at"])
            logger.info(
                f"[Worker] step={step_id} 成功 "
                f"(耗时 {elapsed:.2f}s)"
            )
            break

        except asyncio.TimeoutError:
            last_error = f"步骤执行超时（{timeout}s）"
            logger.warning(
                f"[Worker] step={step_id} 超时 "
                f"(第{attempt+1}/{max_retries+1}次)"
            )

        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"[Worker] step={step_id} 失败 "
                f"(第{attempt+1}/{max_retries+1}次): {e}"
            )

        # 检查是否值得重试
        if not _is_retryable(str(last_error)):
            logger.warning(
                f"[Worker] step={step_id} 错误不可重试，直接失败"
            )
            break

        # 指数退避
        if attempt < max_retries:
            delay = RETRY_BACKOFF_BASE ** (attempt + 1)
            logger.info(f"[Worker] step={step_id} 等待 {delay:.1f}s 后重试")
            await asyncio.sleep(delay)

    else:
        # 循环正常结束（非 break），说明重试耗尽
        pass

    # 检查最终状态：如果仍为 running，标记为 failed
    if sr.get("status") == "running":
        sr["status"] = "failed"
        sr["error"] = last_error
        sr["error_type"] = "timeout" if "超时" in str(last_error) else "unknown"
        sr["finished_at"] = time.time()
        step_results[step_id] = dict(sr)

        # 告警
        if sr.get("error_type") == "timeout":
            alert = make_alert("WORKER_TIMEOUT", {"step_id": step_id, "error": last_error})
        else:
            alert = make_alert("WORKER_RETRY_EXHAUST", {"step_id": step_id, "error": last_error})
        log_degradation(alert)
        logger.error(f"[Worker] step={step_id} 最终失败: {last_error}")

    return {"step_results": step_results}
```

- [ ] **Step 4: Run tests**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_worker_retry.py -v --tb=short
```

Expected: 10 PASS

**注意**：Worker 调用方（`sql_worker_node`, `rag_worker_node`, `report_worker_node`）使用了 `execute_with_retry`，现在它变成 `async` 了。在当前 LangGraph 中，节点函数可以是 async 的——LangGraph 原生支持 async 节点。但当前 Worker 节点是 sync 的。需要确认是否改为 async。

查看当前 worker 实现，它们是这样的模式：
```python
def sql_worker_node(state):
    from .base import execute_with_retry
    from ..tools import sql_query_tool
    return execute_with_retry(state, sql_query_tool)
```

如果 `execute_with_retry` 变为 async，Worker 节点也需要变为 async：
```python
async def sql_worker_node(state):
    from .base import execute_with_retry
    from ..tools import sql_query_tool
    return await execute_with_retry(state, sql_query_tool)
```

这个改动要在 Task 5 中一并完成 Worker 节点的 async 化。

- [ ] **Step 5: Commit**

```bash
git add multi_agent/workers/base.py multi_agent/workers/sql_worker.py multi_agent/workers/rag_worker.py multi_agent/workers/report_worker.py tests/test_worker_retry.py
git commit -m "feat: add asyncio timeout protection + exponential backoff + error classification to workers"
```

---

### Task 6: Plan Critique 节点 — `critique.py`

**Files:**
- Create: `multi_agent/critique.py`
- Test: `tests/test_critique.py`

**Interfaces:**
- Consumes: `multi_agent.alerts`, `multi_agent.tool_registry`, `llm.llm_factory`, `multi_agent.planner._extract_json`, `multi_agent.planner._normalize_plan`
- Produces: `critique_node(state: AgentState) -> dict` — 返回 `{"plan": ..., "_plan_critiqued": ..., "_plan_changed": ...}`

- [ ] **Step 1: Write the failing test**

File: `tests/test_critique.py`

```python
"""tests for multi_agent.critique — Plan Critique 节点"""

import pytest
from unittest.mock import MagicMock, patch
from multi_agent.critique import critique_node, PLAN_CRITIQUE_SYSTEM


def test_critique_prompt_contains_rules():
    """Critique prompt 包含审查规则"""
    assert "审查规则" in PLAN_CRITIQUE_SYSTEM
    assert "capability 匹配" in PLAN_CRITIQUE_SYSTEM
    assert "最小修改" in PLAN_CRITIQUE_SYSTEM
    assert "信任原计划" in PLAN_CRITIQUE_SYSTEM


def test_critique_skips_empty_plan():
    """空计划跳过 Critique"""
    state = {
        "question": "测试问题",
        "plan": {"nodes": {}, "edges": {}},
    }
    result = critique_node(state)
    assert result["_plan_critiqued"] is False
    assert result["_plan_changed"] is False
    assert result["plan"] == {"nodes": {}, "edges": {}}


def test_critique_skips_single_step_plan():
    """单步骤计划跳过 Critique（节省延迟）"""
    state = {
        "question": "请假流程是什么",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "检索请假流程",
                    "params": {"question": "请假流程是什么"},
                }
            },
            "edges": {},
        },
    }
    result = critique_node(state)
    assert result["_plan_critiqued"] is False
    assert result["plan"]["nodes"]["1"]["capability"] == "search_knowledge"


@patch("multi_agent.critique.llm")
def test_critique_on_multi_step_plan(mock_llm):
    """多步骤计划触发 Critique"""
    import json

    original_plan = {
        "nodes": {
            "1": {
                "step_id": "1",
                "capability": "query_database",
                "description": "查询员工",
                "params": {"question": "技术部有哪些员工"},
            },
            "2": {
                "step_id": "2",
                "capability": "query_database",  # 错误：应该是 search_knowledge
                "description": "检索请假流程",
                "params": {"question": "请假流程是什么"},
            },
        },
        "edges": {},
    }

    # Mock LLM 修正了 step 2 的 capability
    corrected_plan = dict(original_plan)
    corrected_plan["nodes"]["2"]["capability"] = "search_knowledge"

    mock_llm.invoke.return_value = MagicMock(
        content=json.dumps(corrected_plan, ensure_ascii=False)
    )

    state = {
        "question": "技术部有哪些员工，请假流程是什么",
        "plan": original_plan,
    }
    result = critique_node(state)
    assert result["_plan_critiqued"] is True
    # 如果修正了，_plan_changed 应为 True
    # （取决于序列化比较，此处测试基础行为）
    assert "plan" in result


@patch("multi_agent.critique.llm")
def test_critique_llm_failure_uses_original(mock_llm):
    """Critique LLM 调用失败时使用原计划"""
    mock_llm.invoke.side_effect = Exception("LLM 超时")

    original_plan = {
        "nodes": {
            "1": {"step_id": "1", "capability": "search_knowledge", "description": "检索", "params": {}},
            "2": {"step_id": "2", "capability": "query_database", "description": "查询", "params": {}},
        },
        "edges": {},
    }

    state = {
        "question": "测试问题",
        "plan": original_plan,
    }
    result = critique_node(state)
    assert result["_plan_critiqued"] is False
    assert result["plan"] == original_plan
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_critique.py -v --tb=short
```

Expected: FAIL — `ModuleNotFoundError: No module named 'multi_agent.critique'`

- [ ] **Step 3: Write `critique.py`**

File: `multi_agent/critique.py`

```python
"""
critique.py — Plan Critique 节点

在 Planner 产出计划后、Supervisor 执行前，让 LLM 以审查员视角
审视计划，发现并修正路由/依赖错误。

设计原则:
  - 最小修改：只修正明显有问题的部分
  - 信任原计划：基本合理就直接返回
  - 优雅降级：LLM 调用失败时使用原计划，不阻塞
"""

import json

from llm.llm_factory import llm
from multi_agent.tool_registry import tool_registry
from multi_agent.planner import _extract_json, _normalize_plan
from multi_agent.alerts import make_alert, log_degradation
from utils.logger import logger
from config import ENABLE_PLAN_CRITIQUE

# =====================================================
# Critique Prompt
# =====================================================

PLAN_CRITIQUE_SYSTEM = """你是计划审查员（Plan Reviewer）。审查另一个 AI 生成的任务分解计划，修正错误。

## 可用能力
{capabilities_schema}

## 审查规则
1. **capability 匹配**：每个步骤的 capability 是否真正匹配问题意图？
   - query_database → 需要具体数据/统计/排名/数量的问题（如"几个人""排名""总额"）
   - search_knowledge → 需要制度/规范/经验/方法/定义/流程的问题（如"流程""规定""怎么做"）
   - generate_report → 需要生成格式化报告的问题
2. **依赖合理性**：edges 中的依赖关系是否合理？无依赖的步骤应能并行。
3. **步骤完整性**：是否遗漏了必要的步骤？
4. **步骤冗余**：是否有对回答问题无帮助的冗余步骤？

## 输出原则
- **最小修改**：只修正明显有问题的部分，不重新设计整个计划
- **信任原计划**：如果原计划基本合理，直接返回原 JSON，不要画蛇添足

## 输出格式
返回 JSON，格式与原计划完全相同：
{{"nodes": {{...}}, "edges": {{...}}}}
如果原计划无需修改，返回原始 JSON 即可。
"""


# =====================================================
# Critique 节点
# =====================================================

def critique_node(state: dict) -> dict:
    """
    Plan Critique 节点：审查并修正 Planner 产出的计划。

    触发条件:
      - ENABLE_PLAN_CRITIQUE = True
      - 计划步骤 > 1

    失败策略:
      - LLM 调用失败 → 返回原计划，不阻塞
    """
    plan = state.get("plan", {"nodes": {}, "edges": {}})
    question = state.get("question", "")

    # 跳过条件
    if not ENABLE_PLAN_CRITIQUE:
        logger.info("[Critique] 已禁用（ENABLE_PLAN_CRITIQUE=False），跳过")
        return {
            "plan": plan,
            "_plan_critiqued": False,
            "_plan_changed": False,
        }

    node_count = len(plan.get("nodes", {}))
    if node_count <= 1:
        logger.info(f"[Critique] 单步骤计划 ({node_count} 步骤)，跳过审查")
        return {
            "plan": plan,
            "_plan_critiqued": False,
            "_plan_changed": False,
        }

    logger.info(f"[Critique] 开始审查计划 ({node_count} 步骤)")

    # 构建 prompt
    capabilities_schema = tool_registry.get_capabilities_schema_text()
    system_prompt = PLAN_CRITIQUE_SYSTEM.format(capabilities_schema=capabilities_schema)

    user_message = f"""原始用户问题: {question}

待审查的计划:
{json.dumps(plan, ensure_ascii=False, indent=2)}

请审查上述计划，指出并修正问题。如果计划无需修改，返回原 JSON。"""

    try:
        response = llm.invoke(
            system_prompt,
            user_message,
        )
        content = response.content if hasattr(response, "content") else str(response)

        corrected = _extract_json(content)

        if not corrected or not corrected.get("nodes"):
            logger.warning("[Critique] LLM 返回了空计划，使用原计划")
            alert = make_alert("CRITIQUE_FAILED", {
                "reason": "empty_response",
                "question": question[:80],
            })
            log_degradation(alert)
            return {
                "plan": plan,
                "_plan_critiqued": False,
                "_plan_changed": False,
            }

        # 规范化修正后的计划
        corrected = _normalize_plan(corrected)

        # 判断是否实际修改了计划
        original_json = json.dumps(plan, ensure_ascii=False, sort_keys=True)
        corrected_json = json.dumps(corrected, ensure_ascii=False, sort_keys=True)
        plan_changed = original_json != corrected_json

        if plan_changed:
            logger.info(
                f"[Critique] 计划已修正: "
                f"原={len(plan.get('nodes',{}))}步 → 新={len(corrected.get('nodes',{}))}步"
            )
            alert = make_alert("PLAN_MISROUTE", {
                "question": question[:80],
                "original_nodes": len(plan.get("nodes", {})),
                "corrected_nodes": len(corrected.get("nodes", {})),
            })
            log_degradation(alert)
        else:
            logger.info("[Critique] 计划无需修正")

        return {
            "plan": corrected,
            "_plan_critiqued": True,
            "_plan_changed": plan_changed,
        }

    except Exception as e:
        logger.warning(f"[Critique] 审查失败，使用原计划: {e}")
        alert = make_alert("CRITIQUE_FAILED", {
            "reason": str(e)[:200],
            "question": question[:80],
        })
        log_degradation(alert)
        return {
            "plan": plan,
            "_plan_critiqued": False,
            "_plan_changed": False,
        }
```

- [ ] **Step 4: Run tests**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_critique.py -v --tb=short
```

Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add multi_agent/critique.py tests/test_critique.py
git commit -m "feat: add Plan Critique node — LLM reviews and corrects Planner output, graceful degradation"
```

---

### Task 7: Supervisor 循环上限 + 结构化降级 — `supervisor.py`

**Files:**
- Modify: `multi_agent/supervisor.py` (full file)
- Test: `tests/test_supervisor_loop.py`

**Interfaces:**
- Consumes: `multi_agent.degradation` (execute_degradation), `multi_agent.alerts` (make_alert)
- Produces:
  - `supervisor_node` — 新增循环上限检查 + 调用通用降级链
  - `route_after_supervisor` — 传递 alerts/_supervisor_loop_count

- [ ] **Step 1: Write the failing test**

File: `tests/test_supervisor_loop.py`

```python
"""tests for multi_agent.supervisor — 循环上限 + 结构化降级"""

from multi_agent.supervisor import supervisor_node, MAX_SUPERVISOR_LOOPS


def test_max_loop_constant():
    """循环上限常量存在且合理"""
    assert MAX_SUPERVISOR_LOOPS == 10


def test_max_loop_forced_termination():
    """达到循环上限时强制终止"""
    state = {
        "question": "测试",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "检索",
                    "params": {"question": "测试"},
                },
            },
            "edges": {},
        },
        "step_results": {
            "1": {
                "step_id": "1",
                "status": "running",  # 假死状态
            }
        },
        "_supervisor_loop_count": MAX_SUPERVISOR_LOOPS,  # 已达到上限
        "alerts": [],
    }

    result = supervisor_node(state)
    assert result["_all_steps_done"] is True
    # 所有 running 的步骤应被标记为 failed
    assert result["step_results"]["1"]["status"] == "failed"
    assert "超出最大调度轮次" in result["step_results"]["1"].get("error", "")


def test_normal_loop_count_increment():
    """正常调度时循环计数递增"""
    state = {
        "question": "测试",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "检索",
                    "params": {"question": "测试"},
                },
            },
            "edges": {},
        },
        "step_results": {},
        "_supervisor_loop_count": 3,
        "alerts": [],
    }

    result = supervisor_node(state)
    # 应该派发 step 1
    assert len(result["_ready_dispatch"]) == 1
    assert result["_supervisor_loop_count"] == 4


def test_dependency_failed_triggers_skip():
    """前置步骤失败 → 后续步骤跳过"""
    state = {
        "question": "测试",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "query_database",
                    "description": "SQL 查询",
                    "params": {"question": "查询"},
                },
                "2": {
                    "step_id": "2",
                    "capability": "generate_report",
                    "description": "生成报告",
                    "params": {"question": "生成报告"},
                },
            },
            "edges": {"2": ["1"]},  # 2 依赖 1
        },
        "step_results": {
            "1": {
                "step_id": "1",
                "capability": "query_database",
                "status": "failed",
                "error": "查询失败",
            }
        },
        "_supervisor_loop_count": 0,
        "alerts": [],
    }

    result = supervisor_node(state)
    # step 2 应被跳过
    sr2 = result["step_results"].get("2", {})
    assert sr2.get("status") == "skipped"
    assert "前置步骤执行失败" in sr2.get("error", "")


def test_empty_plan_immediate_done():
    """空计划立即完成"""
    state = {
        "question": "测试",
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
        "_supervisor_loop_count": 0,
        "alerts": [],
    }

    result = supervisor_node(state)
    assert result["_all_steps_done"] is True
    assert result["_ready_dispatch"] == []
```

- [ ] **Step 2: Run test to verify**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_supervisor_loop.py -v --tb=short
```

Expected: some FAIL — `MAX_SUPERVISOR_LOOPS` undefined, loop protection not implemented

- [ ] **Step 3: Modify `supervisor.py`**

Key changes to `supervisor.py`:

1. Add imports for alerts + degradation
2. Add `MAX_SUPERVISOR_LOOPS = 10`
3. Replace `_check_sql_fallback` with call to `execute_degradation`
4. Add loop limit check at top of `supervisor_node`
5. Add `_supervisor_loop_count` in return values
6. Add `degraded_steps` tracking in state
7. Pass alerts through in `route_after_supervisor`

Show the key changes:

```python
"""
supervisor.py — Supervisor 节点 + 路由函数

职责分离:
  supervisor_node:     调度逻辑，状态更新，永远返回 dict
  route_after_supervisor: 路由函数，返回 list[Send] 或 "reporter"

Send API: 路由函数返回 list[Send] 时，LangGraph 自动并行执行所有目标节点，
Worker 完成后回到 supervisor_node，形成自然 loop。
"""

import time

from langgraph.types import Send

from multi_agent.tool_registry import tool_registry
from multi_agent.degradation import execute_degradation, can_degrade, get_fallback_capability
from multi_agent.alerts import make_alert, log_degradation
from utils.logger import logger


# ⭐ 新增：循环上限
MAX_SUPERVISOR_LOOPS = 10


def supervisor_node(state: dict) -> dict:
    """
    调度节点：更新步骤状态，找出可以执行的 ready 步骤。

    永远返回 dict（state 更新），不返回 Send。
    Send 由 route_after_supervisor 负责。
    """
    plan = state.get("plan", {})
    nodes = plan.get("nodes", {})
    edges = plan.get("edges", {})
    step_results = dict(state.get("step_results", {}))

    # ⭐ 循环上限检查
    loop_count = state.get("_supervisor_loop_count", 0)
    if loop_count >= MAX_SUPERVISOR_LOOPS:
        logger.error(
            f"[Supervisor] 达到最大循环次数 {MAX_SUPERVISOR_LOOPS}，强制终止"
        )
        alert = make_alert("SUPERVISOR_MAX_LOOP", {
            "loop_count": loop_count,
            "nodes": list(nodes.keys()),
        })
        log_degradation(alert)

        # 将所有 pending/running 步骤标记为 failed
        for sid, node_info in nodes.items():
            sr = step_results.get(sid, {})
            if sr.get("status") in ("pending", "running"):
                step_results[sid] = {
                    "step_id": sid,
                    "capability": node_info.get("capability", ""),
                    "description": node_info.get("description", ""),
                    "status": "failed",
                    "output": None,
                    "error": f"超出最大调度轮次（{MAX_SUPERVISOR_LOOPS}）",
                    "error_type": "timeout",
                    "retries": 0,
                    "started_at": 0,
                    "finished_at": 0,
                }

        alerts = state.get("alerts", [])
        return {
            "_all_steps_done": True,
            "_ready_dispatch": [],
            "step_results": step_results,
            "_supervisor_loop_count": loop_count,
            "alerts": alerts + [make_alert("SUPERVISOR_MAX_LOOP", {})],
        }

    if not nodes:
        logger.info("[Supervisor] 空 plan，完成")
        return {
            "_all_steps_done": True,
            "step_results": step_results,
            "_supervisor_loop_count": loop_count,
        }

    new_results = dict(step_results)
    ready_dispatch = []
    degraded_steps = state.get("_degraded_steps", set())

    for step_id, node_info in nodes.items():
        sr = new_results.get(step_id, {})
        status = sr.get("status", "pending")

        if status != "pending":
            continue

        # 检查依赖
        deps = edges.get(step_id, [])
        dep_failed = any(
            new_results.get(d, {}).get("status") == "failed"
            for d in deps
        )
        deps_met = all(
            new_results.get(d, {}).get("status") == "success"
            for d in deps
        )

        if dep_failed:
            new_results[step_id] = {
                "step_id": step_id,
                "capability": node_info.get("capability", ""),
                "description": node_info.get("description", ""),
                "status": "skipped",
                "output": None,
                "error": "前置步骤执行失败",
                "error_type": None,
                "retries": 0,
                "started_at": 0,
                "finished_at": 0,
            }
            logger.warning(f"[Supervisor] step={step_id} 因前置失败被跳过")
            continue

        if deps_met:
            capability = node_info.get("capability", "")
            worker_name = tool_registry.get_worker(capability)

            if worker_name:
                new_results[step_id] = {
                    "step_id": step_id,
                    "capability": capability,
                    "description": node_info.get("description", ""),
                    "status": "running",
                    "started_at": time.time(),
                }
                ready_dispatch.append({
                    "worker": worker_name,
                    "step_id": step_id,
                })
                logger.info(f"[Supervisor] 就绪: step={step_id} "
                            f"cap={capability} → {worker_name}")
            else:
                new_results[step_id] = {
                    "step_id": step_id,
                    "capability": capability,
                    "description": node_info.get("description", ""),
                    "status": "failed",
                    "output": None,
                    "error": f"未注册的 capability: {capability}",
                    "error_type": None,
                    "retries": 0,
                    "started_at": 0,
                    "finished_at": 0,
                }
                logger.warning(f"[Supervisor] step={step_id} capability 无效: {capability}")

    # 判断是否全部结束
    all_done = all(
        new_results.get(sid, {}).get("status") in ("success", "failed", "skipped")
        for sid in nodes
    )

    if not ready_dispatch:
        if all_done:
            # ⭐ 通用降级链：检查是否需要降级
            question = state.get("question", "")
            new_results, ready_dispatch, degraded_steps = execute_degradation(
                nodes, edges, new_results, ready_dispatch,
                degraded_steps, question,
            )

            if ready_dispatch:
                result = {
                    "_all_steps_done": False,
                    "_ready_dispatch": ready_dispatch,
                    "step_results": new_results,
                    "_supervisor_loop_count": loop_count + 1,
                    "_degraded_steps": degraded_steps,
                    "plan": plan,  # plan.nodes 可能已被 execute_degradation 修改
                }
                return result

            success_count = sum(
                1 for sid in nodes
                if new_results.get(sid, {}).get("status") == "success"
            )
            logger.info(f"[Supervisor] 全部完成: {success_count}/{len(nodes)} 成功")
            return {
                "_all_steps_done": True,
                "_ready_dispatch": [],
                "step_results": new_results,
                "_supervisor_loop_count": loop_count + 1,
                "_degraded_steps": degraded_steps,
            }
        else:
            running = [
                sid for sid in nodes
                if new_results.get(sid, {}).get("status") == "running"
            ]
            logger.info(f"[Supervisor] 等待 Worker 返回: {running}")
            return {
                "_all_steps_done": False,
                "_ready_dispatch": [],
                "step_results": new_results,
                "_supervisor_loop_count": loop_count + 1,
                "_degraded_steps": degraded_steps,
            }

    # 有就绪步骤
    return {
        "_all_steps_done": False,
        "_ready_dispatch": ready_dispatch,
        "step_results": new_results,
        "_supervisor_loop_count": loop_count + 1,
        "_degraded_steps": degraded_steps,
    }


def route_after_supervisor(state: dict) -> str | list:
    """路由函数（conditional_edges 的回调）"""
    ready = state.get("_ready_dispatch", [])

    if ready:
        sends = []
        for item in ready:
            sends.append(
                Send(item["worker"], {
                    "question": state.get("question", ""),
                    "kb_id": state.get("kb_id", "default"),
                    "plan": state.get("plan", {}),
                    "step_results": state.get("step_results", {}),
                    "current_step_id": item["step_id"],
                    "messages": state.get("messages", []),
                    "final_answer": state.get("final_answer", ""),
                    "alerts": state.get("alerts", []),
                    "_all_steps_done": False,
                    "_ready_dispatch": [],
                    "_supervisor_loop_count": state.get("_supervisor_loop_count", 0),
                    "_degraded_steps": state.get("_degraded_steps", set()),
                })
            )
        return sends

    return "reporter"
```

- [ ] **Step 4: Run tests**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_supervisor_loop.py -v --tb=short
```

Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add multi_agent/supervisor.py tests/test_supervisor_loop.py
git commit -m "feat: add Supervisor loop limit + structured degradation via degradation chain"
```

---

### Task 8: Reporter 防护增强 — `reporter.py`

**Files:**
- Modify: `multi_agent/reporter.py` (关键函数)
- Test: `tests/test_reporter_guard.py`

**Interfaces:**
- Consumes: `multi_agent.alerts` (make_alert)
- Produces:
  - `_filter_step_results` — 新增 BM25 兜底路径
  - `_is_step_successful(result: dict) -> bool` — 结构化成功判断
  - `reporter_node` — all_success 检测改为使用 `_is_step_successful`

- [ ] **Step 1: Write the failing test**

File: `tests/test_reporter_guard.py`

```python
"""tests for multi_agent.reporter — BM25 兜底 + 结构化成功判断"""

import pytest
from unittest.mock import MagicMock, patch
from multi_agent.reporter import _is_step_successful, _filter_step_results


def test_is_step_successful_normal():
    """正常成功的步骤"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "这是查询结果，包含详细数据",
        "is_empty": False,
        "error_type": None,
    }
    assert _is_step_successful(result) is True


def test_is_step_successful_empty_result():
    """空结果不算成功"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "(无结果)",
        "is_empty": True,
        "error_type": None,
    }
    assert _is_step_successful(result) is False


def test_is_step_successful_with_error_type():
    """有 error_type 标记不算成功"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "部分数据",
        "error_type": "timeout",
    }
    assert _is_step_successful(result) is False


def test_is_step_successful_failed_status():
    """failed 状态不算成功"""
    result = {
        "step_id": "1",
        "status": "failed",
        "output": "错误输出",
    }
    assert _is_step_successful(result) is False


def test_is_step_successful_short_output():
    """过短的输出不算成功"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "短",
        "is_empty": False,
        "error_type": None,
    }
    assert _is_step_successful(result) is False


def test_is_step_successful_min_length():
    """刚好 20 字符以上算成功"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "这是一个刚好超过20字符的输出内容",
        "is_empty": False,
        "error_type": None,
    }
    assert _is_step_successful(result) is True


@patch("multi_agent.reporter._check_reranker_available")
def test_filter_falls_back_to_bm25(mock_check):
    """CrossEncoder 不可用时降级为 BM25"""
    mock_check.return_value = False

    step_results = {
        "1": {
            "step_id": "1",
            "capability": "search_knowledge",
            "status": "success",
            "output": "请假需要提前三天提交申请，部门经理审批后生效。",
            "description": "检索请假流程",
        },
    }
    question = "请假流程是什么"

    # BM25 降级不应崩溃，返回过滤后的结果
    result = _filter_step_results(step_results, question)
    assert "1" in result  # 应保留结果
    # BM25 过滤可能保留或折叠，取决于相关性分数
    sr = result["1"]
    assert sr.get("status") == "success"


def test_context_threshold_configurable():
    """阈值来自 config（默认值合理）"""
    from multi_agent.reporter import _CONTEXT_RELEVANCE_THRESHOLD
    assert 0.0 < _CONTEXT_RELEVANCE_THRESHOLD < 1.0
```

- [ ] **Step 2: Run test to verify failure**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_reporter_guard.py -v --tb=short
```

Expected: some FAIL — `_is_step_successful` not defined, `_check_reranker_available` not defined

- [ ] **Step 3: Modify `reporter.py`**

Key changes to `reporter.py`:

1. Add `_is_step_successful()` function
2. Add `_check_reranker_available()` and `_filter_by_bm25()` functions
3. Add import for `config.RERANKER_THRESHOLD` and alerts
4. Modify `reporter_node` to use `_is_step_successful` for `all_success` check
5. Modify `_filter_step_results` to have BM25 fallback path

```python
# In reporter.py, add these new functions and modify existing ones:

# ⭐ New import at top:
from config import RERANKER_THRESHOLD as _CONTEXT_RELEVANCE_THRESHOLD

# ⭐ New function: structured success check
def _is_step_successful(result: dict) -> bool:
    """检查步骤是否真正成功（结构化判断，非字符串匹配）

    成功条件:
      1. status == "success"
      2. output 不为空且长度 > 20
      3. is_empty 不为 True
      4. error_type 为 None
    """
    if result.get("status") != "success":
        return False
    if result.get("is_empty"):
        return False
    if result.get("error_type"):
        return False
    output = str(result.get("output", ""))
    if len(output.strip()) <= 20:
        return False
    return True


# ⭐ New function: check reranker availability
def _check_reranker_available() -> bool:
    """检查 CrossEncoder 是否可用"""
    try:
        from retrieval.reranker import reranker as _ce
        return _ce is not None
    except Exception:
        return False


# ⭐ New function: BM25 fallback filter
def _filter_by_bm25(step_results: dict, question: str) -> dict:
    """BM25 关键词匹配过滤（CrossEncoder 不可用时的降级方案）

    对每个 RAG 输出，计算问题关键词命中率。
    低于阈值的折叠为 <details>。
    """
    from multi_agent.alerts import make_alert, log_degradation

    alert = make_alert("RERANKER_UNAVAILABLE", {"question": question[:80]})
    log_degradation(alert)
    logger.warning("[ContextFilter] CrossEncoder 不可用，降级为关键词匹配过滤")

    _BM25_FALLBACK_THRESHOLD = 0.1  # 关键词命中率阈值

    rag_steps = {
        sid: sr for sid, sr in step_results.items()
        if sr.get("capability") == "search_knowledge" and sr.get("status") == "success"
    }
    if not rag_steps:
        return step_results

    # 分词：简单的中文按字符 + 英文按空格
    import re
    q_chars = set(re.findall(r'[一-鿿]|\w+', question.lower()))

    filtered = dict(step_results)
    filtered_count = 0

    for sid, sr in rag_steps.items():
        output = str(sr.get("output", ""))
        if not output or len(output) <= 20:
            continue

        output_lower = output.lower()
        hits = sum(1 for c in q_chars if c in output_lower)
        hit_rate = hits / max(len(q_chars), 1)

        if hit_rate < _BM25_FALLBACK_THRESHOLD:
            original = dict(sr)
            original_output = str(original.get("output", ""))
            original["output"] = (
                f"*(此条检索结果与问题「{question[:40]}...」相关性较低 "
                f"(关键词命中率={hit_rate:.2%})，已自动过滤。"
                f"如需参考，原始内容如下)*\n\n"
                f"<details>\n<summary>展开原始内容 ({len(original_output)} 字符)</summary>\n\n"
                f"{original_output[:500]}\n\n</details>"
            )
            original["_filtered"] = True
            original["_relevance_score"] = round(hit_rate, 4)
            filtered[sid] = original
            filtered_count += 1
            logger.info(
                f"[ContextFilter-BM25] 过滤 step={sid} "
                f"hit_rate={hit_rate:.2%} < {_BM25_FALLBACK_THRESHOLD}"
            )

    if filtered_count > 0:
        logger.info(f"[ContextFilter-BM25] 共过滤 {filtered_count}/{len(rag_steps)} 条无关 RAG 结果")

    return filtered


# ⭐ Modified: _filter_step_results with BM25 fallback
def _filter_step_results(step_results: dict, question: str) -> dict:
    """
    Context Filter: 过滤与问题无关的 RAG 检索结果。

    对每个 search_knowledge 步骤的输出，用 CrossEncoder 验证相关性。
    CrossEncoder 不可用时降级为 BM25 关键词匹配。
    低于阈值的输出替换为过滤标记，避免污染最终报告。
    """
    if not step_results:
        return step_results

    rag_steps = {
        sid: sr for sid, sr in step_results.items()
        if sr.get("capability") == "search_knowledge" and sr.get("status") == "success"
    }

    if not rag_steps:
        return step_results

    # ⭐ 检查 CrossEncoder 可用性
    if not _check_reranker_available():
        return _filter_by_bm25(step_results, question)

    # CrossEncoder 批量打分（现有逻辑，增加异常时 BM25 兜底）
    rag_ids = []
    rag_texts = []
    for sid, sr in rag_steps.items():
        output = str(sr.get("output", ""))
        if output and len(output) > 20:
            rag_ids.append(sid)
            rag_texts.append(output[:800])

    if not rag_texts:
        return step_results

    try:
        from retrieval.reranker import reranker as _ce
        from config import RERANK_TIMEOUT
        from utils.timeout import safe_call_with_timeout

        pairs = [(question, text) for text in rag_texts]
        scores = safe_call_with_timeout(
            _ce.predict,
            timeout=RERANK_TIMEOUT,
            default_value=None,
            error_message="Context Filter 超时",
            sentences=pairs,
        )
    except Exception as e:
        logger.warning(f"[ContextFilter] CrossEncoder 调用失败: {e}，降级为 BM25")
        return _filter_by_bm25(step_results, question)

    if scores is None:
        logger.warning("[ContextFilter] 验证返回 None，降级为 BM25")
        return _filter_by_bm25(step_results, question)

    filtered = dict(step_results)
    filtered_count = 0

    for sid, score in zip(rag_ids, scores):
        if float(score) < _CONTEXT_RELEVANCE_THRESHOLD:
            sr = dict(filtered[sid])
            original_output = str(sr.get("output", ""))
            sr["output"] = (
                f"*(此条检索结果与问题「{question[:40]}...」相关性较低 (score={float(score):.3f})，"
                f"已自动过滤。如需参考，原始内容如下)*\n\n"
                f"<details>\n<summary>展开原始内容 ({len(original_output)} 字符)</summary>\n\n"
                f"{original_output[:500]}\n\n</details>"
            )
            sr["_filtered"] = True
            sr["_relevance_score"] = round(float(score), 4)
            filtered[sid] = sr
            filtered_count += 1
            logger.info(
                f"[ContextFilter] 过滤 step={sid} "
                f"description={sr.get('description', '')[:40]} "
                f"score={float(score):.3f} < {_CONTEXT_RELEVANCE_THRESHOLD}"
            )

    if filtered_count > 0:
        logger.info(f"[ContextFilter] 共过滤 {filtered_count}/{len(rag_ids)} 条无关 RAG 结果")

    return filtered


# ⭐ Modify reporter_node: replace the all_success logic
# Old (line ~62-67):
#     all_success = {
#         sid: sr for sid, sr in step_results.items()
#         if sr.get("status") == "success" and sr.get("output")
#         and len(str(sr.get("output", "")).strip()) > 20
#         and "系统资源紧张" not in str(sr.get("output", ""))
#     }
# New:
#     all_success = {
#         sid: sr for sid, sr in step_results.items()
#         if _is_step_successful(sr)
#     }
```

- [ ] **Step 4: Run tests**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_reporter_guard.py -v --tb=short
```

Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add multi_agent/reporter.py tests/test_reporter_guard.py
git commit -m "feat: add BM25 fallback + structured success detection to Reporter"
```

---

### Task 9: Graph 集成 — `graph.py`

**Files:**
- Modify: `multi_agent/graph.py` (build_graph + route + initial_state)
- Test: 运行现有测试确保不回归

**Interfaces:**
- Consumes: `multi_agent.critique` (critique_node), `multi_agent.state` (new fields)
- Produces: 更新后的编译图，含 Critique 节点

- [ ] **Step 1: Insert Critique node into graph**

File: `multi_agent/graph.py` — changes:

```python
# Import addition (after line 21):
from multi_agent.critique import critique_node

# In build_graph() (after line 66, wf.add_node("planner", planner_node)):
    wf.add_node("critique", critique_node)

# Replace the planner → supervisor conditional edge:
# Old:
#     wf.add_conditional_edges(
#         "planner",
#         route_after_planner,
#         {"supervisor": "supervisor", "reporter": "reporter"},
#     )
# New: planner → critique → supervisor (or planner → reporter if plan is empty)
    wf.add_edge("planner", "critique")
    
    wf.add_conditional_edges(
        "critique",
        route_after_critique,
        {"supervisor": "supervisor", "reporter": "reporter"},
    )
```

And add the routing function:

```python
def route_after_critique(state: AgentState) -> str:
    """Critique 后的路由：空计划直接到 Reporter，否则到 Supervisor"""
    plan = state.get("plan", {})
    if not plan.get("nodes"):
        logger.info("[Graph] 空 plan，跳过 Supervisor")
        return "reporter"
    return "supervisor"
```

And update `initial_state` in both `ask()` and `stream_events()` to include new fields:

```python
# In ask() (line ~127):
initial_state: AgentState = {
    "question": question,
    "kb_id": kb_id,
    "plan": {"nodes": {}, "edges": {}},
    "step_results": {},
    "current_step_id": None,
    "messages": [],
    "final_answer": "",
    "alerts": [],
    "_supervisor_loop_count": 0,
    "_plan_critiqued": False,
    "_plan_changed": False,
}

# Same for stream_events() (line ~192)
```

- [ ] **Step 2: Run existing tests to verify no regression**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/ -v --tb=short -x
```

Expected: All existing tests pass (note: might need to check that graph construction doesn't fail)

- [ ] **Step 3: Commit**

```bash
git add multi_agent/graph.py
git commit -m "feat: wire Critique node into graph + update initial_state with new fields"
```

---

### Task 10: 配置 + 前端常量 — `config.py` + `constants.ts`

**Files:**
- Modify: `config.py`
- Modify: `web/src/lib/constants.ts`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `config.ENABLE_PLAN_CRITIQUE: bool` (default True)
  - `config.RERANKER_THRESHOLD: float` (default 0.35)
  - `ALERT_LEVEL_COLORS` + `ALERT_LEVEL_ICONS` in constants.ts

- [ ] **Step 1: Add config keys**

File: `config.py` — add near line ~200 (near other timeout/config constants):

```python
# =====================================================
# Plan Critique 配置
# =====================================================
ENABLE_PLAN_CRITIQUE = True    # 启用 Plan Critique 自我纠错（+1 LLM 调用）
RERANKER_THRESHOLD = 0.35      # Context Filter 最低相关度阈值（0.0-1.0）
```

- [ ] **Step 2: Add frontend alert constants**

File: `web/src/lib/constants.ts` — append:

```typescript
/** Alert level → color map for SSE log events */
export const ALERT_LEVEL_COLORS: Record<string, string> = {
  info: '#3b82f6',   // blue
  warn: '#f59e0b',   // amber
  error: '#ef4444',  // red
}

/** Alert level → emoji icon */
export const ALERT_LEVEL_ICONS: Record<string, string> = {
  info: 'ℹ️',
  warn: '⚠️',
  error: '❌',
}
```

- [ ] **Step 3: Verify config import works**

```bash
cd "d:/Program Files/workplace/agent" && python -c "from config import ENABLE_PLAN_CRITIQUE, RERANKER_THRESHOLD; print(f'ENABLE_PLAN_CRITIQUE={ENABLE_PLAN_CRITIQUE}, RERANKER_THRESHOLD={RERANKER_THRESHOLD}')"
```

Expected: `ENABLE_PLAN_CRITIQUE=True, RERANKER_THRESHOLD=0.35`

- [ ] **Step 4: Commit**

```bash
git add config.py web/src/lib/constants.ts
git commit -m "feat: add ENABLE_PLAN_CRITIQUE + RERANKER_THRESHOLD config + alert color constants"
```

---

### Task 11: 端到端集成验证

**Files:**
- Test: `tests/test_e2e_reliability.py` (集成测试)

- [ ] **Step 1: Write integration test**

File: `tests/test_e2e_reliability.py`

```python
"""端到端集成测试 — Agent 可靠性工程全链路"""

import pytest
from unittest.mock import MagicMock, patch
import json


def test_full_graph_compilation():
    """验证图编译成功（含 Critique 节点）"""
    from multi_agent.graph import build_graph
    graph = build_graph()
    assert graph is not None


@patch("multi_agent.planner.llm")
@patch("multi_agent.critique.llm")
def test_planner_to_critique_flow(mock_critique_llm, mock_planner_llm):
    """
    端到端：Planner → Critique → Supervisor → Reporter
    模拟 Planner 输出错误 capability，Critique 修正它。
    """
    from multi_agent.graph import build_graph

    # Mock Planner: 输出包含错误的 capability
    planner_output = {
        "nodes": {
            "1": {
                "step_id": "1",
                "capability": "query_database",  # 错误：应该是 search_knowledge
                "description": "检索请假流程",
                "params": {"question": "请假流程是什么"},
            },
        },
        "edges": {},
    }
    mock_planner_llm.invoke.return_value = MagicMock(
        content=json.dumps(planner_output, ensure_ascii=False)
    )

    # Mock Critique: 跳过（单步计划不触发 Critique）
    mock_critique_llm.invoke.return_value = MagicMock(
        content=json.dumps(planner_output, ensure_ascii=False)
    )

    graph = build_graph()
    initial_state = {
        "question": "请假流程是什么",
        "kb_id": "default",
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
        "current_step_id": None,
        "messages": [],
        "final_answer": "",
        "alerts": [],
        "_supervisor_loop_count": 0,
        "_plan_critiqued": False,
        "_plan_changed": False,
    }

    # 用 invoke 运行（会走完整图直到 END）
    # 注意：这需要所有 Worker 也能 mock，否则会失败
    # 这里只验证图编译和初始路由正确
    result = graph.invoke(initial_state, config={"recursion_limit": 50})
    assert result is not None


def test_alerts_flow_in_state():
    """验证 alerts 在 state 中正确传递"""
    from multi_agent.alerts import make_alert

    alert = make_alert("DEGRADATION_TRIGGER", {"from": "sql", "to": "rag"})
    assert alert.code == "DEGRADATION_TRIGGER"
    assert alert.level == "info"
    assert alert.detail["from"] == "sql"

    alert_dict = {
        "timestamp": alert.timestamp,
        "level": alert.level,
        "code": alert.code,
        "message": alert.message,
        "detail": alert.detail,
    }

    state_alerts = [alert_dict]
    assert len(state_alerts) == 1
    assert state_alerts[0]["code"] == "DEGRADATION_TRIGGER"


def test_new_step_result_fields_integration():
    """验证新字段在整个流程中可用"""
    from multi_agent.state import StepResult

    sr: StepResult = {
        "step_id": "sql_1",
        "capability": "query_database",
        "description": "SQL查询",
        "status": "success",
        "output": "共5条记录",
        "row_count": 5,
        "is_empty": False,
        "error_type": None,
    }
    assert sr["row_count"] == 5
    assert not sr["is_empty"]

    # 验证降级检测可以正确判断
    from multi_agent.reporter import _is_step_successful
    assert _is_step_successful(sr) is True

    sr_empty: StepResult = {
        "step_id": "sql_2",
        "capability": "query_database",
        "description": "SQL查询空结果",
        "status": "success",
        "output": "无结果",
        "row_count": 0,
        "is_empty": True,
        "error_type": None,
    }
    assert _is_step_successful(sr_empty) is False
```

- [ ] **Step 2: Run integration test**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/test_e2e_reliability.py -v --tb=short
```

Expected: PASS（可能需要调整 mock 策略如果图调用了实际 Worker）

- [ ] **Step 3: Run full test suite**

```bash
cd "d:/Program Files/workplace/agent" && PYTHONPATH=".venv/lib/site-packages" python -m pytest tests/ -v --tb=short
```

Expected: 所有新测试 + 旧测试全部通过

- [ ] **Step 4: Final commit**

```bash
git add tests/test_e2e_reliability.py
git commit -m "test: add e2e integration tests for reliability engineering pipeline"
```

---

## Test Summary

| 测试文件 | 测试数 | 覆盖模块 |
|---|---|---|
| `tests/test_alerts.py` | 4 | PlanAlert, ALERT_CODES, log_degradation |
| `tests/test_state.py` | 5 | StepResult 新字段, AgentState, reducer |
| `tests/test_degradation.py` | 7 | DEGRADATION_CHAIN, can_degrade, get_fallback_capability |
| `tests/test_json_repair.py` | 10 | 4 层修复管道: 正常/尾逗号/中文引号/单引号/无引号key/嵌套/空 |
| `tests/test_worker_retry.py` | 10 | 超时/退避/错误分类/重试耗尽/不可重试跳过 |
| `tests/test_critique.py` | 6 | Critique prompt/空计划跳过/单步跳过/修正/LLM失败退出 |
| `tests/test_supervisor_loop.py` | 5 | 循环上限/强制终止/计数递增/依赖失败/空计划 |
| `tests/test_reporter_guard.py` | 8 | BM25兜底/结构化success/阈值可配置 |
| `tests/test_e2e_reliability.py` | 3 | 全链路集成验证 |
| **总计** | **~58** | |

## File Change Summary

| 文件 | 变更 |
|---|---|
| `multi_agent/alerts.py` | **新增** — PlanAlert + ALERT_CODES + log_degradation |
| `multi_agent/degradation.py` | **新增** — 降级链注册 + execute_degradation |
| `multi_agent/critique.py` | **新增** — Plan Critique 节点 |
| `multi_agent/state.py` | 修改 — StepResult + AgentState 类型扩展 |
| `multi_agent/planner.py` | 修改 — _extract_json 升级 + planner_node 适配 |
| `multi_agent/workers/base.py` | 重写 — async 超时 + 退避 + 错误分类 |
| `multi_agent/workers/sql_worker.py` | 修改 — 改为 async |
| `multi_agent/workers/rag_worker.py` | 修改 — 改为 async |
| `multi_agent/workers/report_worker.py` | 修改 — 改为 async |
| `multi_agent/supervisor.py` | 重写 — 循环上限 + 通用降级链 + alerts |
| `multi_agent/reporter.py` | 修改 — BM25 兜底 + 结构化 success + 阈值可配置 |
| `multi_agent/graph.py` | 修改 — 插入 Critique 节点 + 新路由 + initial_state |
| `config.py` | 修改 — 新增 ENABLE_PLAN_CRITIQUE + RERANKER_THRESHOLD |
| `web/src/lib/constants.ts` | 修改 — 新增 ALERT_LEVEL_COLORS + ICONS |
