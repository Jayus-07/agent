"""LLM 用量业务归因测试（M5 / 台账 D5）

覆盖四层：
1. llm_context 纯上下文层：默认空 / scope 设置 / 嵌套叠加 / 退出恢复
2. current_usage_attribution() 集成：三键随 scope 生效（记账拼装出口）
3. SafeToolExecutor 装饰器：run 执行栈内 tool_id 可读（含 to_thread 传播）
4. record() SQL 列清单：INSERT 列与值元组长度一致（mock 连接防漂移）
"""
from __future__ import annotations

import asyncio

import pytest

from backend.observability.llm_context import (
    get_llm_attribution,
    llm_attribution_scope,
)


# ==================== 1. 上下文层 ====================

class TestLLMContext:
    def test_default_empty(self):
        attr = get_llm_attribution()
        assert (attr.skill_id, attr.tool_id, attr.agent_domain) == ("", "", "")

    def test_scope_sets_and_restores(self):
        with llm_attribution_scope(skill_id="sql"):
            assert get_llm_attribution().skill_id == "sql"
        assert get_llm_attribution().skill_id == ""  # 退出恢复

    def test_nested_merge_semantics(self):
        """skill → tool 嵌套：未传键沿用上层（skill 保持、tool 收窄）。"""
        with llm_attribution_scope(skill_id="sql", agent_domain="travel"):
            with llm_attribution_scope(tool_id="execute_sql_tool"):
                attr = get_llm_attribution()
                assert attr.skill_id == "sql"          # 沿用
                assert attr.tool_id == "execute_sql_tool"
                assert attr.agent_domain == "travel"   # 沿用
        assert get_llm_attribution().skill_id == ""

    def test_scope_exception_propagates_and_resets(self):
        with pytest.raises(RuntimeError):
            with llm_attribution_scope(skill_id="x"):
                raise RuntimeError("boom")
        assert get_llm_attribution().skill_id == ""

    def test_concurrent_tasks_isolated(self):
        """并发 task 各自绑定互不串扰（ContextVar 语义）。"""
        results: dict[str, str] = {}

        async def worker(tag: str):
            with llm_attribution_scope(skill_id=tag):
                await asyncio.sleep(0.01)
                results[tag] = get_llm_attribution().skill_id

        async def main():
            await asyncio.gather(worker("a"), worker("b"))

        asyncio.run(main())
        assert results == {"a": "a", "b": "b"}


# ==================== 2. attribution 集成出口 ====================

class TestAttributionIntegration:
    def test_current_usage_attribution_has_three_keys(self):
        from backend.observability.llm_usage_store import current_usage_attribution

        base = current_usage_attribution()
        assert base["skill_id"] == "" and base["tool_id"] == "" and base["agent_domain"] == ""

        with llm_attribution_scope(skill_id="rag", tool_id="search_knowledge_tool",
                                   agent_domain="customer_service"):
            attr = current_usage_attribution()
            assert attr["skill_id"] == "rag"
            assert attr["tool_id"] == "search_knowledge_tool"
            assert attr["agent_domain"] == "customer_service"
            # 既有键不受影响
            assert "user_id" in attr and "trace_id" in attr and "run_id" in attr


# ==================== 3. executor 装饰器 ====================

class TestExecutorAttribution:
    def test_run_binds_tool_id(self):
        from backend.core.tool_runtime.executor import safe_tool_executor

        seen: dict[str, str] = {}

        def probe():
            seen["tool_id"] = get_llm_attribution().tool_id
            return {"ok": True}

        result = asyncio.run(safe_tool_executor.run(tool_key="map.geo", call=probe))
        assert result.status.value == "success"
        assert seen["tool_id"] == "map.geo"

    def test_run_propagates_through_to_thread(self):
        """Skill 实际传 call=lambda: asyncio.to_thread(...)——归因必须穿透。"""
        from backend.core.tool_runtime.executor import safe_tool_executor

        seen: dict[str, str] = {}

        def probe():
            seen["tool_id"] = get_llm_attribution().tool_id
            return {"ok": True}

        def call():
            return asyncio.to_thread(probe)

        asyncio.run(safe_tool_executor.run(tool_key="sql.query", call=call))
        assert seen["tool_id"] == "sql.query"


# ==================== 4. record() 列漂移防护 ====================

class TestRecordSqlColumns:
    def test_insert_columns_match_values(self, monkeypatch):
        """INSERT 列清单与值元组等长、含三归因列（防后续加列漏改一处）。"""
        from backend.observability import llm_usage_store_pg as store_mod

        captured: dict = {}

        class _FakeCursor:
            def execute(self, sql, params=None):
                captured.setdefault("sql", []).append(sql)
                captured.setdefault("params", []).append(params)

        class _FakeConn:
            def cursor(self, *args, **kwargs):
                return _FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(store_mod, "_cfg_enabled", lambda: True)
        # 注意：不能用子类 override _conn——LLMUsageStore.__new__ 会把实例
        # 偷换成 PostgresLLMUsageStore，override 失效后连真库。实例属性遮蔽。
        store = object.__new__(store_mod.PostgresLLMUsageStore)
        store._db_path = ""
        store._lock = __import__("threading").Lock()
        store._table = "llm_usage"
        store._write_count = 0
        store._conn = lambda: _FakeConn()
        ok = store.record({"skill_id": "rag", "tool_id": "t", "agent_domain": "travel",
                           "model": "m", "total_tokens": 1})
        assert ok is True
        insert_sql = captured["sql"][0]
        import re

        m = re.search(r"INSERT INTO \S+ \((.*?)\) VALUES", insert_sql, re.S)
        assert m, "未解析到 INSERT 列块"
        columns = [c.strip() for c in m.group(1).split(",")]
        values = captured["params"][0]
        assert len(columns) == len(values), (
            f"INSERT 列数 {len(columns)} != 值数 {len(values)}：\n{columns}"
        )
        for col in ("skill_id", "tool_id", "agent_domain"):
            assert col in columns, f"record() INSERT 缺归因列 {col}"
        # 归因三列的值位置正确；Billing V2 在其后新增多列，不能再用
        # “最后四个”这种会随 schema 演进失效的 positional 断言。
        by_column = dict(zip(columns, values))
        assert by_column["skill_id"] == "rag"
        assert by_column["tool_id"] == "t"
        assert by_column["agent_domain"] == "travel"
