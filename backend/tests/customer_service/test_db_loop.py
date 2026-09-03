"""test_db_loop.py — Sync→Async 桥接测试"""
import asyncio

from backend.customer_service._db_loop import run_sync


class TestRunSync:
    def test_returns_result(self):
        async def coro():
            return 42

        assert run_sync(coro()) == 42

    def test_returns_complex_result(self):
        async def coro():
            return {"key": "value", "list": [1, 2, 3]}

        result = run_sync(coro())
        assert result == {"key": "value", "list": [1, 2, 3]}

    def test_propagates_exception(self):
        async def coro():
            raise ValueError("boom")

        try:
            run_sync(coro())
            assert False, "should have raised"
        except ValueError as e:
            assert "boom" in str(e)

    def test_multiple_calls(self):
        results = []
        for i in range(5):

            async def coro(n=i):
                return n * 2

            results.append(run_sync(coro()))
        assert results == [0, 2, 4, 6, 8]

    def test_async_sleep_works(self):
        async def coro():
            await asyncio.sleep(0.01)
            return "done"

        assert run_sync(coro()) == "done"
