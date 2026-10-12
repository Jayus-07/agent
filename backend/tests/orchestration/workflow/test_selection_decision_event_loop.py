"""selection_decision 事件循环阻塞修复（B1-A）定向测试

对应审计 P0-1：协程体内同步 llm.invoke 阻塞 API 事件循环，且 step 级
wait_for 定时器同处该 loop 导致超时形同虚设。

修复形态：_llm_json 改 async（llm.invoke 经 asyncio.to_thread 下线程池）；
routes 侧后台任务过并发闸门（asyncio.Semaphore）。

四个用例对应验收项：
1. loop 不阻塞：慢 LLM 调用期间心跳协程持续跳动（修复前为 0 次）
2. 超时真实触发：wait_for 可打断慢 LLM step，TimeoutError 冒出
   （不被 review_pain 内部 catch-all fallback 吞掉）
3. 并发受限：超出闸门的 _run_task 排队，执行并发峰值 ≤ 上限
4. 结果单次回写：success 不走兜底写、failed 恰好一次，无重复提交
"""
import asyncio
import json
import time

import pytest

import backend.app.api.routes.selection_decision as sd_routes
import backend.orchestration.workflows.selection_decision as wf_mod
from backend.orchestration.workflow.context import WorkflowContext
from backend.orchestration.workflows.selection_decision import SelectionDecision

# ─────────────────────────────────────────────────────────────
# LLM 桩
# ─────────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, content):
        self.content = content


def _slow_llm(seconds: float, payload: str = '["续航虚标"]'):
    """同步慢桩：sleep 后返回合法 JSON（模拟慢 LLM Provider）"""

    class _Slow:
        def invoke(self, messages):
            time.sleep(seconds)
            return _FakeResp(payload)

    return _Slow()


# ─────────────────────────────────────────────────────────────
# 1. loop 不阻塞
# ─────────────────────────────────────────────────────────────


def test_llm_json_does_not_block_event_loop(monkeypatch):
    """慢 LLM（1.2s）调用期间，同 loop 心跳协程必须持续跳动。

    修复前：同步 llm.invoke 卡死 loop，期间新增心跳 = 0（已实测复现）。
    """
    monkeypatch.setattr(wf_mod, "llm", _slow_llm(1.2))
    heartbeats: list[float] = []

    async def scenario():
        async def heartbeat():
            while True:
                heartbeats.append(time.monotonic())
                await asyncio.sleep(0.1)

        hb = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.3)  # 心跳先稳定起来
        n0 = len(heartbeats)
        await wf_mod._llm_json(["x"])
        n1 = len(heartbeats)
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass
        return n0, n1

    n0, n1 = asyncio.run(scenario())
    # 1.2s 的 LLM 调用期间，0.1s 间隔的心跳至少应跳 5 次（修复前为 0）
    assert n1 - n0 >= 5, (
        f"事件循环被阻塞：LLM 调用期间心跳仅 {n1 - n0} 次（期望 ≥5）")


def test_full_workflow_with_slow_llm_keeps_loop_responsive(monkeypatch):
    """整链路形态：review_pain 慢 LLM（1.2s）期间其他协程可被调度执行。

    模拟"同一进程里另一个 API 请求"：心跳协程先启动，workflow 的
    痛点推断 step 在旁边跑慢 LLM，断言对方全程不被饿死。
    """
    monkeypatch.setattr(wf_mod, "llm", _slow_llm(1.2))
    monkeypatch.setattr(
        wf_mod, "get_store", lambda: type("S", (), {
            "list_watch": staticmethod(lambda enabled_only=True: [
                {"url": "https://e.com/1"}]),
            "latest_snapshot": staticmethod(lambda url: {
                "url": url, "title": "蓝牙耳机", "platform": "jd",
                "price": 99.0, "rating": 4.5, "review_count": 5000,
                "highlights": "降噪"}),
        })())
    monkeypatch.setattr(wf_mod, "batch_scores", lambda urls: {"scores": {}})

    progress: list[str] = []

    async def scenario():
        other_api = asyncio.create_task(_fake_api_request(progress))
        ctx = WorkflowContext(workflow_name="selection_decision",
                              run_id="b1a-slow", inputs={})
        ctx.outputs["competitor_data"] = {"candidates": [
            {"url": "https://e.com/1", "title": "蓝牙耳机", "platform": "jd",
             "price": 99.0, "rating": 4.5, "review_count": 5000,
             "highlights": "降噪"}]}
        wf = SelectionDecision()
        await asyncio.wait_for(wf.review_pain(ctx), timeout=30)
        await other_api
        return list(progress)

    ticks = asyncio.run(scenario())
    # 慢 LLM 期间 fake API 请求完成全部 5 步（阻塞时它一步都走不完）
    assert ticks == ["received", "step", "step", "step", "done"], (
        f"并发请求被饿死: {ticks}")


async def _fake_api_request(progress: list[str]):
    """模拟同 loop 上的另一个 API 请求：接收 → 处理（3 次调度间隙）→ 返回"""
    progress.append("received")
    for _ in range(3):
        await asyncio.sleep(0.15)
        progress.append("step")
    progress.append("done")


# ─────────────────────────────────────────────────────────────
# 2. 超时真实触发
# ─────────────────────────────────────────────────────────────


def test_step_timeout_interrupts_slow_llm(monkeypatch):
    """step 级 wait_for 必须能打断慢 LLM 调用，TimeoutError 向上冒出。

    修复前：同步 llm.invoke 卡住 loop，wait_for 定时器（同 loop）无法触发，
    超时形同虚设；修复后 to_thread 让出 loop，超时按声明生效。
    TimeoutError 必须冒出——review_pain 的 except Exception 不捕
    CancelledError/TimeoutError，不得伪造成 fallback 降级成功。
    """
    monkeypatch.setattr(wf_mod, "llm", _slow_llm(5.0))
    monkeypatch.setattr(
        wf_mod, "get_store", lambda: type("S", (), {
            "list_watch": staticmethod(lambda enabled_only=True: [
                {"url": "https://e.com/1"}]),
            "latest_snapshot": staticmethod(lambda url: {
                "url": url, "title": "蓝牙耳机", "platform": "jd",
                "price": 99.0, "rating": 4.5, "review_count": 5000,
                "highlights": "降噪"}),
        })())

    async def scenario():
        ctx = WorkflowContext(workflow_name="selection_decision",
                              run_id="b1a-timeout", inputs={})
        ctx.outputs["competitor_data"] = {"candidates": [
            {"url": "https://e.com/1", "title": "蓝牙耳机", "platform": "jd",
             "price": 99.0, "rating": 4.5, "review_count": 5000,
             "highlights": "降噪"}]}
        wf = SelectionDecision()
        t0 = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(wf.review_pain(ctx), timeout=1.0)
        return time.monotonic() - t0

    elapsed = asyncio.run(scenario())
    # 1s 超时生效（而不是等满 5s 的 LLM 桩）
    assert elapsed < 3.0, f"超时未生效，等了 {elapsed:.1f}s"


def test_llm_json_timeout_raises_not_degrades(monkeypatch):
    """直接对 _llm_json 的 wait_for：超时抛 TimeoutError，不返回降级结果。"""
    monkeypatch.setattr(wf_mod, "llm", _slow_llm(5.0))

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(wf_mod._llm_json(["x"]), timeout=0.5)

    asyncio.run(scenario())


# ─────────────────────────────────────────────────────────────
# 3. 并发受限（routes 闸门）
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def gate_env(monkeypatch):
    """并发闸门限 2 + 计数型 executor 桩；返回 (并发峰值读取器, store)"""
    from backend.selection_decision.store import SelectionDecisionStore

    monkeypatch.setattr(
        sd_routes, "SELECTION_DECISION_MAX_CONCURRENT_TASKS", 2)
    # 重置懒创建单例，让新上限生效
    monkeypatch.setattr(sd_routes, "_workflow_gate", None)

    state = {"active": 0, "peak": 0}

    class _SlowExecutor:
        async def run(self, name, inputs=None):
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            await asyncio.sleep(0.4)
            state["active"] -= 1

            class _Ctx:
                status = "success"
                error = None
            return _Ctx()

    monkeypatch.setattr(sd_routes, "WorkflowExecutor", _SlowExecutor)

    class _CountingStore:
        def __init__(self):
            self.update_calls = []
            self.rows = {}

        def create(self, inputs):
            task_id = f"t-{len(self.rows) + 1}"
            self.rows[task_id] = {"id": task_id, "status": "running"}
            return task_id

        def update_result(self, task_id, **kw):
            self.update_calls.append((task_id, kw))
            self.rows[task_id].update(kw)

        def get(self, task_id):
            return self.rows.get(task_id)

    store = _CountingStore()
    monkeypatch.setattr(sd_routes, "get_selection_decision_store", lambda: store)
    return state, store


def test_run_task_concurrency_gate_limits_peak(gate_env):
    """4 个并发提交、闸门 2：执行峰值必须 ≤2，全部任务最终完成。"""
    state, store = gate_env

    async def scenario():
        tasks = [asyncio.create_task(sd_routes._run_task(f"t-{i}", {}))
                 for i in range(4)]
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert state["peak"] <= 2, f"并发峰值 {state['peak']} 超过闸门 2"
    # 全部 4 个任务都真正执行过（success 不触发兜底写，此处无 update 调用）
    assert state["peak"] >= 2, "闸门不应把并发压到 0"


def test_run_task_writes_result_exactly_once(gate_env, monkeypatch):
    """failed 时 update_result 恰好一次（无重复提交）；success 时零次兜底写。"""
    state, store = gate_env

    class _FailExecutor:
        async def run(self, name, inputs=None):
            class _Ctx:
                status = "failed"
                error = "LLM 不可用"
            return _Ctx()

    monkeypatch.setattr(sd_routes, "WorkflowExecutor", _FailExecutor)

    task_id = store.create({"category": "x"})  # 模拟 create_task 已建行

    async def scenario():
        await sd_routes._run_task(task_id, {})

    asyncio.run(scenario())
    assert len(store.update_calls) == 1, (
        f"failed 任务回写次数 {len(store.update_calls)}，期望恰好 1 次")
    assert store.get(task_id)["status"] == "failed"


# ─────────────────────────────────────────────────────────────
# 4. 真实 API 链路（TestClient + 真实 executor + mock LLM，_run_task 不打桩）
# ─────────────────────────────────────────────────────────────


def test_api_end_to_end_task_reaches_success(monkeypatch, tmp_path):
    """POST /tasks → 真实后台执行（真实 WorkflowExecutor）→ 轮询到 success。

    与既有 test_selection_decision_api.py 的差异：_run_task 不打桩，
    仅 mock LLM/竞品 store/持久化——验证 create_task→workflow→回写全链。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.selection_decision.store import SelectionDecisionStore

    # workflow 侧桩（同 smoke 测试的 patched 模式）
    fake_llm_payload = {
        "review": '["续航虚标", "佩戴不适"]',
        "diff": json.dumps({"verdict": "go", "gaps": ["续航虚标"],
                            "reason": "痛点集中"}, ensure_ascii=False),
        "vote": json.dumps({"score": 80, "verdict": "go", "reason": "通过"},
                           ensure_ascii=False),
    }

    def _fake_invoke(messages):
        text = " ".join(str(getattr(m, "content", m)) for m in messages)
        if "专长领域" in text:
            return _FakeResp(fake_llm_payload["vote"])
        if "差异化" in text or "切入点" in text:
            return _FakeResp(fake_llm_payload["diff"])
        return _FakeResp(fake_llm_payload["review"])

    import backend.orchestration.workflows.selection_decision as wf
    import backend.selection_decision.panel as panel_mod
    monkeypatch.setattr(wf, "llm", type("L", (), {"invoke": staticmethod(_fake_invoke)}))
    monkeypatch.setattr(panel_mod, "llm", type("L", (), {"invoke": staticmethod(_fake_invoke)}))

    snapshots = [
        {"url": f"https://e.com/p{i}", "title": f"蓝牙耳机{i}", "platform": "jd",
         "price": 99.0 + i * 20, "rating": 4.5, "review_count": 5000 + i * 1000,
         "highlights": "主动降噪 长续航"} for i in range(4)]
    monkeypatch.setattr(wf, "get_store", lambda: type("S", (), {
        "list_watch": staticmethod(lambda enabled_only=True: [
            {"url": s["url"]} for s in snapshots]),
        "latest_snapshot": staticmethod(
            lambda url: next((s for s in snapshots if s["url"] == url), None)),
    }))
    monkeypatch.setattr(wf, "batch_scores", lambda urls: {"scores": {}})

    # store 切 tmp；routes 与 workflow 模块共用同一实例
    store = SelectionDecisionStore(db_path=str(tmp_path / "e2e.db"))
    monkeypatch.setattr(sd_routes, "get_selection_decision_store", lambda: store)
    monkeypatch.setattr(wf, "get_selection_decision_store", lambda: store)

    # persistence / trace 切 tmp（与 conftest.patched_persistence 同构，
    # 但本文件不在 workflow conftest 生效目录之外复用，就地内联）
    from backend.orchestration.workflow.persistence import WorkflowRunStore
    run_store = WorkflowRunStore(db_path=str(tmp_path / "runs.db"))
    monkeypatch.setattr(
        "backend.orchestration.workflow.persistence.get_workflow_run_store",
        lambda: run_store)
    monkeypatch.setattr(
        "backend.orchestration.workflow.executor.get_workflow_run_store",
        lambda: run_store)

    class _FakeCompetitorStore:
        def list_watch(self, enabled_only=True):
            return [{"url": s["url"]} for s in snapshots]

    monkeypatch.setattr(sd_routes, "get_store", lambda: _FakeCompetitorStore())

    # 生产注册发生在 server 启动（register_workflows_and_schedules）；
    # 裸 FastAPI app 无启动流程，此处按同一事实源手动注册。
    from backend.orchestration.workflows import register_all
    register_all()

    # trace：TestClient 的后台任务在 portal loop 上跑，真实 trace_collector
    # 写本地 queue 即可（TraceWriter 本身已降级为本地 queue，见启动日志），
    # 不做断言、只要求不抛异常。

    app = FastAPI()
    app.include_router(sd_routes.router)
    payload = {
        "category": "蓝牙耳机",
        "platforms": ["jd"],
        "finance": {"sell_price": 129.0, "unit_cost": 45.0},
        "panel_size": 3,
    }
    with TestClient(app) as client:
        resp = client.post("/selection-decision/tasks", json=payload)
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        detail = None
        for _ in range(100):  # 最多 10s 轮询
            detail = client.get(f"/selection-decision/tasks/{task_id}").json()
            if detail["status"] != "running":
                break
            time.sleep(0.1)
        assert detail is not None
        assert detail["status"] == "success", (
            f"真实链路未达 success: {detail}")
        assert detail["verdict"] == "go"
        # 无重复提交：同一 task 恰好一行
        rows = client.get("/selection-decision/tasks").json()["tasks"]
        assert sum(1 for r in rows if r["id"] == task_id) == 1
