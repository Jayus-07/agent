"""MultiAgentSystem sources 请求级返回（2026-09-23 STOP E3 回归）。

修复前非流式 /chat 经 `getattr(agent, "_last_sources", [])` 读进程单例的
实例属性——并发请求下互相覆盖（A 的响应可能带 B 的引用来源）。锁定：

- ask_result 把本请求的 sources 随返回值带回（barrier 强制交错下
  各自只能拿到自己的）；
- _last_sources 保留为 deprecated 调试兼容，生产路径零读取。
"""
import threading

from backend.orchestration.graph.runner import _ANSWER_EVENT
from backend.orchestration.graph.system import MultiAgentSystem

_ROUNDS = 40


def _make_shared_system() -> MultiAgentSystem:
    """__new__ 绕过重初始化；两个线程共用同一实例（= 进程单例语义）。"""
    return MultiAgentSystem.__new__(MultiAgentSystem)


def _script_iter_events(system: MultiAgentSystem, phase1: threading.Barrier,
                        phase2: threading.Barrier):
    """桩 iter_events：按 marker 产出 answer/done 事件，两处 barrier 强制
    两个请求的 done 消费窗口交错（旧实现在此窗口互相覆盖单例属性）。"""

    def _iter_events(question, session_id, **kwargs):
        marker = session_id
        yield {"event": _ANSWER_EVENT, "data": {"answer": f"答案{marker}"}}
        phase1.wait()
        yield {"event": "done",
               "data": {"answer": f"答案{marker}", "sources": [{"marker": marker}]}}

        # done 已被本请求消费；第二个请求的 done 消费必须等本生成器走到
        # phase2 之后才开始（保证旧单例属性被后写者覆盖的窗口真实存在）
        phase2.wait()

    system._runner = _MagicMockRunner(_iter_events)


class _MagicMockRunner:
    def __init__(self, fn):
        self._fn = fn

    def iter_events(self, question, session_id, **kwargs):
        yield from self._fn(question, session_id, **kwargs)


def test_concurrent_ask_results_sources_never_cross():
    system = _make_shared_system()
    phase1 = threading.Barrier(2)
    phase2 = threading.Barrier(2)
    _script_iter_events(system, phase1, phase2)
    failures: list[str] = []
    b_done = threading.Event()

    barrier_mid = threading.Barrier(2)  # 跨线程共享，循环内可重入

    def _worker(marker: str, my_done: threading.Event,
                partner_done: threading.Event):
        for _ in range(_ROUNDS):
            outcome = system.ask_result(f"问题{marker}", marker)
            my_done.set()  # 通知对方：本请求已返回（旧单例此刻可能已被本方覆盖）
            # 与对方在「双方 outcome 均已返回、尚未断言」处同步后交叉断言
            barrier_mid.wait()
            partner_done.wait()
            if [s["marker"] for s in outcome.sources] != [marker]:
                failures.append(f"{marker} 拿到 {outcome.sources}")
            if outcome.answer != f"答案{marker}":
                failures.append(f"{marker} answer 串扰: {outcome.answer}")
            barrier_mid.wait()

    a_done, b_done = threading.Event(), threading.Event()
    t1 = threading.Thread(target=_worker, args=("A", a_done, b_done))
    t2 = threading.Thread(target=_worker, args=("B", b_done, a_done))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert failures == [], f"并发串扰 {len(failures)} 次: {failures[:3]}"


def test_deprecated_last_sources_still_written_for_debug():
    """deprecated 属性保留调试兼容（被写入），但生产消费点已迁移。"""
    system = _make_shared_system()
    events = [
        {"event": "_answer", "data": {"answer": "答案"}},
        {"event": "done", "data": {"answer": "答案",
                                   "sources": [{"marker": "dbg"}]}},
    ]

    def _iter(question, session_id, **kwargs):
        yield from events

    system._runner = _MagicMockRunner(_iter)
    outcome = system.ask_result("问题", "s-1")
    assert outcome.sources == [{"marker": "dbg"}]
    assert system._last_sources == [{"marker": "dbg"}]
