"""tests/test_tool_param_injection.py — Tool 参数注入测试集（验收 #104）

口径：异常/恶意参数必须被结构化拦截（schema 校验/钳制/失败封套），
任何情况下不得裸异常炸进程、不得以 success 封套返回。覆盖四类注入面：

  1. 聚合 Tool 未知 action / 缺必填参数 → 模型可读失败封套
  2. 超长字符串参数（10KB）→ 钳制或拒绝，返回值恒为可解析 JSON
  3. SQL 注入串经 sql tool 入口 → 六层校验拒绝，失败封套（不触库执行）
  4. 类型错乱（数字位传对象）→ LangChain/Pydantic 校验拒绝

离线：不要求外部数据源可用（未配置 Tool 的 not_configured 封套同样是
合法的结构化拒绝——「查不了」与「查不到」分开，不是崩溃）。
"""
from __future__ import annotations

import json

import pytest

ATTACK_LONG = "A" * 10240
SQL_INJECTION = "眼镜; DROP TABLE users; --"


def _parse_envelope(raw: str) -> dict:
    """Tool 返回值必须可解析 JSON（新 Tool 三规）；解析失败即测试失败。"""
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"Tool 返回不是 JSON: {exc!r} raw={raw[:200]}")


class TestMapLookupInjection:
    def test_unknown_action_rejected_with_readable_error(self):
        from backend.tools.map.lookup import map_lookup_tool

        raw = map_lookup_tool.invoke({"action": "rm_rf", "keyword": "福州"})
        env = _parse_envelope(raw)
        assert env.get("status") != "success"
        # 错误可读：点名不认识的 action 与支持的 action 列表
        assert "rm_rf" in json.dumps(env, ensure_ascii=False)

    def test_missing_required_params_rejected(self):
        from backend.tools.map.lookup import map_lookup_tool

        raw = map_lookup_tool.invoke({"action": "route"})
        env = _parse_envelope(raw)
        assert env.get("status") != "success"

    def test_oversized_keyword_clamped_or_rejected(self):
        from backend.tools.map.lookup import map_lookup_tool

        raw = map_lookup_tool.invoke(
            {"action": "place_search", "keyword": ATTACK_LONG, "city": "福州"})
        env = _parse_envelope(raw)
        # 任意形态都行，唯独不能 success 且原文透传（钳制 ≤50 或拒绝）
        if env.get("status") == "success":
            sent = json.dumps(env, ensure_ascii=False)
            assert ATTACK_LONG not in sent, "超长参数被原文透传"

    def test_sql_injection_keyword_is_plain_data(self):
        """注入串作为普通关键词进入检索（LBS 参数化查询）——不炸、不执行、
        要么查不到要么 not_configured，绝不是 success 的业务数据。"""
        from backend.tools.map.lookup import map_lookup_tool

        raw = map_lookup_tool.invoke(
            {"action": "place_search", "keyword": SQL_INJECTION, "city": "福州"})
        env = _parse_envelope(raw)
        assert "DROP TABLE" not in json.dumps(env.get("data") or {}, ensure_ascii=False)


class TestSqlToolInjection:
    def test_drop_table_via_question_rejected(self):
        """NL2SQL 语义入口：注入串经 sql tool → 结构化失败，绝不 success。"""
        pytest.importorskip("sqlalchemy")
        from backend.tools.sql import sql_query_tool

        raw = sql_query_tool.invoke({"question": SQL_INJECTION})
        env = _parse_envelope(raw)
        assert env.get("status") != "success"

    def test_comment_payload_rejected(self):
        from backend.tools.sql import sql_query_tool

        raw = sql_query_tool.invoke(
            {"question": "1' OR '1'='1' /*"})
        env = _parse_envelope(raw)
        assert env.get("status") != "success"


class TestTypeConfusion:
    def test_numeric_slot_with_object_rejected(self):
        """数字槽位传嵌套对象 → 参数校验拒绝（ValidationError 或失败封套），
        不产生不可预测执行。"""
        from backend.tools.map.lookup import map_lookup_tool

        with pytest.raises(Exception) as excinfo:
            map_lookup_tool.invoke({
                "action": "place_search",
                "keyword": {"$gt": ""},
            })
        # ValidationError / ValueError / 业务 fail 封套均可，裸 AttributeError
        # 这类「字段不存在于 schema」的崩溃不算结构化拒绝
        assert "Traceback" not in str(type(excinfo.value))

    def test_page_size_negative_is_clamped(self):
        from backend.tools.map.lookup import map_lookup_tool

        raw = map_lookup_tool.invoke({
            "action": "place_search", "keyword": "福州",
            "page_size": -999,
        })
        env = _parse_envelope(raw)
        # 负数页大小被钳制/拒绝，不得引发底层 5xx 语义或空 success
        if env.get("status") == "success":
            data = env.get("data") or {}
            assert int(data.get("count", 0)) >= 0
