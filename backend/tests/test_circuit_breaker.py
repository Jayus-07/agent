"""R2 熔断器测试 — CLOSED→OPEN→HALF_OPEN 状态机"""
import time

import pytest

from backend.infra.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    State,
    chroma_circuit_breaker,
    get_all_breakers,
    llm_circuit_breaker,
    pg_circuit_breaker,
)


class TestStateMachine:
    """核心状态机逻辑"""

    def test_normal_call_passes(self):
        cb = CircuitBreaker("test", fail_threshold=3, timeout=0.5)
        assert cb.state == State.CLOSED
        result = cb.call(lambda x: x * 2, 21)
        assert result == 42

    def test_failures_trigger_open(self):
        cb = CircuitBreaker("test", fail_threshold=3, timeout=0.5)
        fail_count = 0
        for _ in range(5):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                fail_count += 1
            except CircuitBreakerOpenError:
                break
        assert cb.state == State.OPEN
        assert fail_count == 3  # 第 3 次失败触发 OPEN

    def test_open_blocks_calls(self):
        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.5)
        for _ in range(2):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        assert cb.state == State.OPEN
        with pytest.raises(CircuitBreakerOpenError) as exc:
            cb.call(lambda: 42)
        assert "test" in str(exc.value)

    def test_half_open_recovery(self):
        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.3)
        for _ in range(2):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        assert cb.state == State.OPEN
        time.sleep(0.4)  # 超过 timeout
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb.state == State.CLOSED  # 恢复

    def test_half_open_failure_returns_to_open(self):
        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.3)
        for _ in range(2):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        time.sleep(0.4)  # HALF_OPEN
        try:
            cb.call(lambda: 1 / 0)  # 试探失败
        except ZeroDivisionError:
            pass
        assert cb.state == State.OPEN  # 回到 OPEN
        # 立即调用应被拦截
        with pytest.raises(CircuitBreakerOpenError):
            cb.call(lambda: 42)

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", fail_threshold=5, timeout=0.5)
        for _ in range(2):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        cb.call(lambda: 42)  # 成功 — 重置计数
        # 还需要 5 次失败才能触发 OPEN（之前 2 次已重置）
        for _ in range(4):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        assert cb.state == State.CLOSED  # 4 < 5，未触发

    def test_exception_passthrough(self):
        """熔断器透传原始异常（仅拦截 OPEN 状态）"""
        cb = CircuitBreaker("test", fail_threshold=5, timeout=0.5)
        with pytest.raises(ValueError, match="test error"):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("test error")))


class TestHalfOpenSingleProbe:
    """HALF_OPEN 单探测放行 — 防探测风暴（#6）"""

    def _trip_to_open(self, cb: CircuitBreaker) -> None:
        for _ in range(2):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        assert cb.state == State.OPEN

    def test_second_call_during_probe_rejected(self):
        """探测执行期间（fn 未返回）的并发第二调用必须被拒"""
        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.3)
        self._trip_to_open(cb)
        time.sleep(0.4)  # 到达 HALF_OPEN 时机

        second_rejected = False

        def probe():
            nonlocal second_rejected
            # 探测 fn 执行中再发起调用 → 等价于并发第二探测
            try:
                cb.call(lambda: 42)
            except CircuitBreakerOpenError:
                second_rejected = True
            return 1

        assert cb.call(probe) == 1
        assert second_rejected is True
        assert cb.state == State.CLOSED  # 探测成功仍正常恢复

    def test_concurrent_probes_single_flight(self):
        """5 线程并发探测：仅 1 个真正放行，其余 4 个被拒"""
        import threading

        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.3)
        self._trip_to_open(cb)
        time.sleep(0.4)

        entered = threading.Event()   # 首个探测已过 _check_state（fn 开始执行）
        release = threading.Event()   # 放行探测完成
        executed: list[int] = []
        rejected: list[int] = []

        def worker() -> None:
            try:
                cb.call(lambda: (entered.set(), release.wait(timeout=2), executed.append(1)))
            except CircuitBreakerOpenError:
                rejected.append(1)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        threads[0].start()
        assert entered.wait(timeout=2), "首个探测未开始执行"
        for t in threads[1:]:
            t.start()
        release.set()
        for t in threads:
            t.join()

        assert len(executed) == 1
        assert len(rejected) == 4
        assert cb.state == State.CLOSED

    def test_probe_failure_releases_flag_and_returns_to_open(self):
        """探测失败回 OPEN 时释放探测位"""
        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.3)
        self._trip_to_open(cb)
        time.sleep(0.4)
        try:
            cb.call(lambda: 1 / 0)  # 探测失败
        except ZeroDivisionError:
            pass
        assert cb.state == State.OPEN
        assert cb._probe_in_flight is False

    def test_reset_clears_probe_flag(self):
        """reset 必须清探测位，且状态机内部一致性可守护"""
        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.3)
        self._trip_to_open(cb)
        time.sleep(0.4)
        cb._check_state()  # 占位（模拟探测在飞，不执行 fn）
        assert cb.state == State.HALF_OPEN
        cb.reset()
        assert cb._probe_in_flight is False
        assert cb.call(lambda: 42) == 42


class TestPresetBreakers:
    """预置熔断器实例"""

    def test_llm_breaker_config(self):
        assert llm_circuit_breaker.name == "deepseek"
        assert llm_circuit_breaker.fail_threshold == 5
        assert llm_circuit_breaker.state == State.CLOSED

    def test_pg_breaker_config(self):
        assert pg_circuit_breaker.name == "postgresql"
        assert pg_circuit_breaker.fail_threshold == 3  # 数据库更激进
        assert pg_circuit_breaker.timeout == 60.0  # 更长的恢复期

    def test_chroma_breaker_config(self):
        assert chroma_circuit_breaker.name == "chromadb"
        assert chroma_circuit_breaker.state == State.CLOSED

    def test_get_all_breakers(self):
        all_cb = get_all_breakers()
        assert len(all_cb) == 3
        assert set(all_cb.keys()) == {"deepseek", "postgresql", "chromadb"}


class TestStats:
    """stats() 输出"""

    def test_stats_initial(self):
        s = llm_circuit_breaker.stats()
        assert s["name"] == "deepseek"
        assert s["state"] == "closed"
        assert s["failures"] == 0

    def test_stats_after_failure(self):
        cb = CircuitBreaker("test", fail_threshold=5, timeout=1.0)
        try:
            cb.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass
        s = cb.stats()
        assert s["failures"] == 1
        assert s["last_failure_s"] is not None


# ============================================================
# 跨进程共享态（审查 #13 / docs/2026-09-21-熔断状态Redis共享设计.md）
# ============================================================
class _FakeRedis:
    """最小 Redis 桩：incr/expire/set/get/delete/pipeline。"""

    def __init__(self):
        self.store: dict = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, ttl):
        return True

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, *keys):
        n = 0
        for k in keys:
            if self.store.pop(k, None) is not None:
                n += 1
        return n

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
                self._ops = []

            def incr(self, k):
                self._ops.append(("incr", k))
                return self

            def expire(self, k, t):
                self._ops.append(("expire", k, t))
                return self

            def execute(self):
                out = []
                for op in self._ops:
                    out.append(outer.incr(op[1]) if op[0] == "incr"
                               else outer.expire(op[1], op[2]))
                return out

        return _Pipe()


@pytest.fixture
def shared_redis(monkeypatch):
    """开共享开关 + 注入 fake Redis（拦截 get_redis 调用时导入路径）。"""
    fake = _FakeRedis()
    import backend.config.redis as redis_config
    import backend.infra.redis.client as redis_client

    monkeypatch.setattr(redis_config, "CIRCUIT_BREAKER_SHARED_ENABLED", True)
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    return fake


class TestSharedState:
    def test_remote_count_trips_other_instance(self, shared_redis):
        """A 实例失败 4 次（未达本地阈值），B 实例第 1 次失败即熔断 ——
        系统级计数合并生效，阈值不再被多进程放大。"""
        a = CircuitBreaker("x-llm", fail_threshold=5, timeout=30.0)
        b = CircuitBreaker("x-llm", fail_threshold=5, timeout=30.0)

        def boom():
            raise RuntimeError("down")

        for _ in range(4):
            with pytest.raises(RuntimeError):
                a.call(boom)
        assert a.state is State.CLOSED  # 本地 4/5，未熔断

        with pytest.raises(RuntimeError):
            b.call(boom)  # 远端 INCR 后 = 5 → 系统级超阈
        assert b.state is State.OPEN
        assert a.state is State.CLOSED  # A 尚未感知（轮询/自身失败才收敛）

    def test_open_broadcast_reaches_other_instance(self, shared_redis):
        a = CircuitBreaker("x-pg", fail_threshold=2, timeout=30.0)
        b = CircuitBreaker("x-pg", fail_threshold=2, timeout=30.0)

        def boom():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            a.call(boom)
        with pytest.raises(RuntimeError):
            a.call(boom)
        assert a.state is State.OPEN

        b._poll_remote_state()  # 模拟 B 的节流轮询
        assert b.state is State.OPEN

    def test_recovery_clears_remote_state(self, shared_redis):
        a = CircuitBreaker("x-chroma", fail_threshold=1, timeout=30.0)

        def boom():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            a.call(boom)
        assert a.state is State.OPEN
        assert shared_redis.get(a._open_key()) is not None

        a.reset()  # 运维复位应全局生效：清 fail 计数 + OPEN 广播
        assert shared_redis.get(a._open_key()) is None
        assert shared_redis.get(a._fails_key()) is None

    def test_redis_unavailable_degrades_to_local(self, monkeypatch):
        """Redis 不可用 = 退回进程内语义（现状行为），绝不抛错。"""
        import backend.config.redis as redis_config
        import backend.infra.redis.client as redis_client

        monkeypatch.setattr(redis_config, "CIRCUIT_BREAKER_SHARED_ENABLED", True)
        monkeypatch.setattr(redis_client, "get_redis", lambda: None)

        cb = CircuitBreaker("x-degraded", fail_threshold=2, timeout=30.0)

        def boom():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            cb.call(boom)
        with pytest.raises(RuntimeError):
            cb.call(boom)
        assert cb.state is State.OPEN  # 本地阈值照常生效
        assert cb.stats()["redis_degraded"] is False  # 未发生异常，仅无客户端

    def test_stats_reports_shared_flag(self, shared_redis):
        cb = CircuitBreaker("x-stats", fail_threshold=5, timeout=30.0)
        assert cb.stats()["shared"] is True
