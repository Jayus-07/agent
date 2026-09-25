"""tests/test_task_phase2_step2_error_taxonomy.py — Phase2 Step2：Timeout/Retry 收口。

覆盖（对齐 Step2 Case A-J 中可测部分）：
  分类器：SoftTimeLimit/429/401/403-quota/400/503/超时/ValueError/Permission/
          LookupError/未知异常 → error_type + retryable + source
  Case C  Validation Error → retryable=false，retry_count=0，直接 FAILED
  Case D  Provider Timeout → retryable=true → TaskRetryScheduled（backoff 延迟）
  Case E  Auth Failed → retryable=false，无 retry
  Case F  Quota Exhausted → 不重试（fallback 由 proxy 层负责，见报告）
  Case A' SoftTimeLimit（分类层）→ timeout/retryable；budget 耗尽 → retry_exhausted
  Case G  Retry Exhausted → FAILED + retry_exhausted=true
  Case I/J PAUSED/CANCELLED 不被 retry 唤醒（impl 短路 + retry 前状态复查）
  signals 兜底不覆盖分类口径；index runtime 分类落 error_type

策略：真实 agent_memory（不可达 skip）；stub 图 monkeypatch build_task_graph；
异常用 status_code/类名/消息特征桩（分类器即按此三通道设计）。
"""
from __future__ import annotations

import uuid

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from backend.models.task import TaskStatus
from backend.tasks.error_taxonomy import (classify_task_error,
                                          retryable_of)
from backend.tasks.retry_policy import (TaskRetryScheduled, RetryPolicy,
                                        compute_delay)


# ═══════════════════════════════════════════════════
# 分类器（纯函数，无 DB）
# ═══════════════════════════════════════════════════

def _stub_exc(name: str, msg: str = "", status: int | None = None) -> Exception:
    """按类名/消息/status_code 构造分类器输入桩（duck-typing 三通道）。"""
    cls = type(name, (Exception,), {})
    err = cls(msg or name)
    if status is not None:
        err.status_code = status
    return err


class TestClassify:
    def test_soft_time_limit_is_runtime_timeout_retryable(self):
        c = classify_task_error(SoftTimeLimitExceeded("任务执行超时"))
        assert (c.error_type, c.retryable, c.source) == ("timeout", True, "runtime")

    def test_rate_limit_429(self):
        c = classify_task_error(_stub_exc("RateLimitError", "rate limit", 429))
        assert (c.error_type, c.retryable) == ("rate_limited", True)

    def test_auth_401_non_retryable(self):
        c = classify_task_error(_stub_exc("AuthenticationError", "bad key", 401))
        assert (c.error_type, c.retryable) == ("auth_failed", False)

    def test_quota_403_non_retryable(self):
        c = classify_task_error(_stub_exc(
            "PermissionDeniedError",
            "Free quota exhausted. add funds", 403))
        assert (c.error_type, c.retryable) == ("quota_exhausted", False)

    def test_bad_request_400_is_validation(self):
        c = classify_task_error(_stub_exc("BadRequestError", "bad input", 400))
        assert (c.error_type, c.retryable) == ("validation_error", False)

    def test_503_provider_error_retryable(self):
        c = classify_task_error(_stub_exc("InternalServerError", "boom", 503))
        assert (c.error_type, c.retryable) == ("provider_error", True)

    def test_provider_timeout_by_message(self):
        c = classify_task_error(_stub_exc("APIConnectionError",
                                          "request timed out"))
        assert (c.error_type, c.retryable) == ("provider_timeout", True)

    def test_model_provider_error_carries_provider_model(self):
        from backend.infra.llm.error_taxonomy import ModelProviderError

        err = ModelProviderError("overloaded", error_type="timeout",
                                 provider="dashscope", model="qwen")
        c = classify_task_error(err)
        assert (c.error_type, c.retryable) == ("provider_timeout", True)
        assert c.provider == "dashscope" and c.model == "qwen"

    def test_value_error_is_validation(self):
        c = classify_task_error(ValueError("illegal input"))
        assert (c.error_type, c.retryable) == ("validation_error", False)

    def test_permission_error_non_retryable(self):
        c = classify_task_error(PermissionError("denied"))
        assert (c.error_type, c.retryable) == ("permission_denied", False)

    def test_lookup_error_non_retryable(self):
        c = classify_task_error(LookupError("missing"))
        assert (c.error_type, c.retryable) == ("internal_error", False)

    def test_unknown_is_internal_error_budget_capped(self):
        c = classify_task_error(RuntimeError("mystery"))
        assert (c.error_type, c.retryable) == ("internal_error", True)

    def test_chunking_empty_is_terminal_validation_error(self):
        c = classify_task_error(_stub_exc("ChunkingEmptyError", "0 chunks"))
        assert (c.error_type, c.retryable) == ("validation_error", False)

    def test_retryable_of(self):
        assert retryable_of("provider_timeout") is True
        assert retryable_of("quota_exhausted") is False
        assert retryable_of("nonsense") is False


class TestRetryPolicy:
    def test_delay_exponential_and_capped(self):
        p = RetryPolicy(initial_delay=5, backoff=2.0, max_delay=60, jitter=False)
        assert [compute_delay(p, i) for i in range(4)] == [5, 10, 20, 40]
        assert compute_delay(p, 10) == 60  # 封顶

    def test_delay_jitter_bounded(self):
        p = RetryPolicy(initial_delay=10, backoff=2.0, max_delay=1000, jitter=True)
        for i in range(6):
            d = compute_delay(p, i)
            assert 0 < d <= 10 * (2 ** i)


# ═══════════════════════════════════════════════════
# DB 集成：impl 失败出口 / retry 决策 / signals / index runtime
# ═══════════════════════════════════════════════════

# ── STOP D P0（2026-09-23）：执行时授权的外部边界 mock ──────
# 本文件测错误分类与 retry 决策，不是授权本身；auth.users 查询是外部
# 边界（只 mock 外部边界），授权解析逻辑仍真实执行。user_id 同步
# 十进制化（auth.users.id 口径）。
@pytest.fixture(autouse=True)
def _task_auth_enabled(monkeypatch):
    import backend.security.task_authorization as _ta

    monkeypatch.setattr(_ta, "_fetch_auth_user",
                        lambda uid: ("editor", 1, "ecom", "default"))


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过 Step2 测试: {e}")
    from backend.services import task_service

    return task_service


class _FakeRedis:
    def set(self, key, value, ex=None):
        pass

    def delete(self, *keys):
        pass

    def exists(self, key):
        return 0

    def publish(self, channel, message):
        pass


@pytest.fixture()
def stub_env(pg, monkeypatch):
    """真实 PG + stub 图（monkeypatch build_task_graph）+ fake 控制标志。"""
    from backend.orchestration.checkpoint import task_executor as te
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    raise_spec = {"exc": None}

    class TState(dict):
        pass

    def step_a(state):
        exc = raise_spec.get("exc")
        if exc is not None:
            raise exc
        return {"final_answer": "done"}

    wf = StateGraph(dict)
    wf.add_node("step_a", step_a)
    wf.add_edge(START, "step_a")
    wf.add_edge("step_a", END)
    graph = wf.compile(checkpointer=MemorySaver())

    monkeypatch.setattr(te, "build_task_graph", lambda: graph)
    monkeypatch.setattr("backend.tasks.task_manager._redis", lambda: _FakeRedis())
    return raise_spec


def _make_task(pg, query="step2 测试") -> str:
    user = f"9003{uuid.uuid4().int % (10 ** 12):012d}"
    return pg.create_task(user, query).id


def _run_impl(pg, task_id: str, *, retries: int = 0):
    from backend.tasks.agent_tasks import execute_agent_task_impl

    return execute_agent_task_impl(task_id, retries=retries)


def test_case_c_validation_error_no_retry(pg, stub_env):
    stub_env["exc"] = ValueError("illegal query")
    task_id = _make_task(pg)
    with pytest.raises(ValueError):
        _run_impl(pg, task_id)
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error_type == "validation_error"
    assert record.retry_count == 0
    assert record.retry_exhausted is False
    assert record.to_public_dict()["retry_exhausted"] is False


def test_case_d_provider_timeout_schedules_retry(pg, stub_env):
    stub_env["exc"] = _stub_exc("APIConnectionError", "request timed out")
    task_id = _make_task(pg)
    with pytest.raises(TaskRetryScheduled) as ei:
        _run_impl(pg, task_id)
    assert ei.value.error_type == "provider_timeout"
    assert ei.value.delay > 0
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.FAILED  # 等待重试 = FAILED + metadata
    assert record.error_type == "provider_timeout"
    assert "等待第 1" in record.progress


def test_case_e_auth_failed_no_retry(pg, stub_env):
    stub_env["exc"] = _stub_exc("AuthenticationError", "invalid key", 401)
    task_id = _make_task(pg)
    with pytest.raises(Exception):  # noqa: B017 — 原始异常直接上抛
        _run_impl(pg, task_id)
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error_type == "auth_failed"
    assert record.retry_exhausted is False


def test_case_f_quota_exhausted_no_retry(pg, stub_env):
    stub_env["exc"] = _stub_exc(
        "PermissionDeniedError", "Free quota exhausted. add funds", 403)
    task_id = _make_task(pg)
    with pytest.raises(Exception):  # noqa: B017
        _run_impl(pg, task_id)
    record = pg.get_task(task_id)
    assert record.error_type == "quota_exhausted"
    assert record.retry_count == 0


def test_case_a_soft_limit_retry_then_exhausted(pg, stub_env):
    # budget 内：timeout → TaskRetryScheduled
    stub_env["exc"] = SoftTimeLimitExceeded("任务执行超时")
    task_id = _make_task(pg)
    with pytest.raises(TaskRetryScheduled) as ei:
        _run_impl(pg, task_id)
    assert ei.value.error_type == "timeout"
    assert pg.get_task(task_id).error_type == "timeout"

    # budget 耗尽（retries=max）：FAILED + retry_exhausted
    task_id2 = _make_task(pg)
    with pytest.raises(SoftTimeLimitExceeded):
        _run_impl(pg, task_id2, retries=3)
    record = pg.get_task(task_id2)
    assert record.status == TaskStatus.FAILED
    assert record.error_type == "timeout"
    assert record.retry_exhausted is True


def test_case_g_retry_exhausted_on_provider_timeout(pg, stub_env):
    stub_env["exc"] = _stub_exc("APIConnectionError", "request timed out")
    task_id = _make_task(pg)
    with pytest.raises(Exception):  # noqa: B017
        _run_impl(pg, task_id, retries=3)
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error_type == "provider_timeout"
    assert record.retry_exhausted is True


def test_case_i_j_paused_cancelled_not_awoken_by_retry(pg, stub_env):
    from backend.tasks import task_manager

    stub_env["exc"] = None
    # PAUSED + retry 消息：impl 短路 NO-OP，保持 PAUSED
    paused = _make_task(pg)
    pg.update_status(paused, TaskStatus.PAUSED, progress="用户暂停")
    result = _run_impl(pg, paused)
    assert result["skipped"] is True
    assert pg.get_task(paused).status == TaskStatus.PAUSED

    # CANCELLED + retry 消息：NO-OP
    cancelled = _make_task(pg)
    pg.update_status(cancelled, TaskStatus.RUNNING, progress="x")
    pg.update_status(cancelled, TaskStatus.CANCELLED)
    result = _run_impl(pg, cancelled)
    assert result["status"] == "CANCELLED"
    assert pg.get_task(cancelled).status == TaskStatus.CANCELLED


def test_retry_recheck_blocks_non_failed(pg):
    """§十一：retry 入队前状态复查——非 FAILED（等待重试）态一律放弃重投。"""
    from backend.tasks.agent_tasks import _retry_after_state_recheck

    class _FakeTask:
        def __init__(self):
            self.retry_calls = []

        def retry(self, **kwargs):
            self.retry_calls.append(kwargs)
            raise AssertionError("非 FAILED 态不得 self.retry")

    # 任务被并发改为 PENDING（如用户 resume 抢先）→ 放弃重投
    task_id = _make_task(pg)  # PENDING
    fake = _FakeTask()
    result = _retry_after_state_recheck(
        fake, task_id,
        TaskRetryScheduled(RuntimeError("x"), error_type="timeout",
                           retryable=True, delay=5, retry_count=0,
                           max_retries=3))
    assert result["retry_cancelled"] is True
    assert fake.retry_calls == []


def test_failure_hook_preserves_taxonomy(pg):
    """signals task_failure 兜底：分类 error_type/error_message 已存在时不覆盖。"""
    from backend.tasks import signals

    task_id = _make_task(pg)
    pg.update_status(task_id, TaskStatus.RUNNING, progress="x")
    pg.update_status(task_id, TaskStatus.FAILED,
                     error_message="重试耗尽（provider_timeout）: boom",
                     error_type="provider_timeout", retry_exhausted=True)

    class _FakeTask:
        name = "tasks.execute_agent"

    signals._on_failure(task=_FakeTask(), args=[task_id],
                        exc=RuntimeError("boom"), traceback="tb-line")
    record = pg.get_task(task_id)
    assert record.error_type == "provider_timeout"  # 未被类名覆盖
    assert record.error_message.startswith("重试耗尽")
    assert record.traceback == "tb-line"  # traceback 补充落库


def test_index_runtime_classification(pg):
    """rag_index：失败分类落 TaskState（index registry 为域内状态，TaskState
    为业务终态口径）。"""
    from backend.tasks.index_task_runtime import run_with_task_state

    for exc, expected_type, retries in [
        (ValueError("bad doc"), "validation_error", 0),
        (_stub_exc("APIConnectionError", "timed out"), "provider_timeout", 0),
    ]:
        task_id = _make_task(pg, "index 分类测试")
        with pytest.raises(Exception):  # noqa: B017
            run_with_task_state(task_id, "upload-x", lambda: (_ for _ in ()).throw(exc),
                                retries=retries)
        record = pg.get_task(task_id)
        assert record.error_type == expected_type
        assert record.status == TaskStatus.FAILED

    # budget 耗尽：retry_exhausted=True
    task_id = _make_task(pg, "index 耗尽测试")
    with pytest.raises(Exception):  # noqa: B017
        run_with_task_state(task_id, "upload-y",
                            lambda: (_ for _ in ()).throw(
                                _stub_exc("APIConnectionError", "timed out")),
                            retries=3)
    record = pg.get_task(task_id)
    assert record.retry_exhausted is True
    assert record.error_type == "provider_timeout"
