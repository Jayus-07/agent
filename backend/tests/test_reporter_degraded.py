"""reporter.py 单元测试 — 降级文案防泄漏 + 步骤成功判定。

此前 reporter 零测试覆盖。浏览器实测发现：全步骤失败时降级文案
直接拼接内部步骤描述（direct_executor 生成的 "直接执行 sql.query"），
把内部执行细节暴露给用户。本文件锁定修复后的行为：
- 降级提示只出现用户可读标签，不出现内部描述/step_id/capability 原名
- 技术错误 → "服务暂时不可用"；业务错误原文只进日志不进文案
- _is_step_successful / _is_technical_error 边界
"""
import pytest

# 循环导入规避：先加载 orchestration.graph 入口（server 生产路径同款），
# 避免经 backend.agents.__init__ → planner → tool_registry 的环。
import backend.orchestration.graph  # noqa: F401

from backend.agents.reporter.reporter import (
    generate_final_answer,
    _is_step_successful,
    _is_technical_error,
    _user_step_label,
    reporter_node,
    render_step_results_deterministically,
)


def _failed_step(capability="sql.query", description="直接执行 sql.query",
                 error="", status="failed", output=""):
    return {"capability": capability, "description": description,
            "status": status, "error": error, "output": output}


class TestDegradedMessageNoLeak:
    """全步骤失败 → 降级提示绝不能泄漏内部执行细节。"""

    def test_internal_description_not_leaked(self):
        # 复现浏览器实测场景：direct_executor 的 description="直接执行 sql.query"
        step_results = {"direct_1": _failed_step()}
        answer = generate_final_answer("出口退税税率是多少？", step_results,
                                       context_filter=False)
        assert "## 查询未完成" in answer
        assert "未能完成" in answer
        # 内部细节不得出现
        assert "直接执行" not in answer, "内部步骤描述泄漏到用户文案"
        assert "sql.query" not in answer, "capability 原名泄漏到用户文案"
        assert "direct_1" not in answer, "step_id 泄漏到用户文案"
        # 用户可读标签应出现
        assert "数据库查询" in answer

    def test_technical_error_masked(self):
        step_results = {
            "s1": _failed_step(capability="rag.search",
                               description="知识库检索任务",
                               error="provider connection details: secret",
                               ),
        }
        step_results["s1"].update(tool_status="unavailable", error_type="network")
        answer = generate_final_answer("退货政策", step_results,
                                       context_filter=False)
        assert "服务暂时不可用" in answer
        assert "ChromaDB" not in answer
        assert "Traceback" not in answer
        assert "知识库检索" in answer

    def test_business_error_raw_text_not_shown(self):
        step_results = {
            "s1": _failed_step(capability="sql.query",
                               description="直接执行 sql.query",
                               error="查询超时，请缩小时间范围（内部重试 3 次）"),
        }
        answer = generate_final_answer("上月销量", step_results,
                                       context_filter=False)
        assert "内部重试" not in answer, "业务错误原文可能含内部细节，不应直出"
        assert "数据库查询" in answer

    def test_timeout_is_not_reported_as_missing_data(self):
        answer = generate_final_answer("上月销量", {
            "s1": {
                **_failed_step(error="provider detail: secret-host"),
                "tool_status": "timeout",
                "error_type": "timeout",
                "error_code": "read_timeout",
            },
        }, context_filter=False)

        assert "超时" in answer
        assert "未找到相关信息" not in answer
        assert "secret-host" not in answer

    def test_permission_and_approval_have_distinct_safe_messages(self):
        permission = generate_final_answer("查询订单", {
            "s1": {**_failed_step(), "tool_status": "unauthorized",
                   "error_type": "permission_denied"},
        }, context_filter=False)
        approval = generate_final_answer("提交操作", {
            "s1": {**_failed_step(), "tool_status": "invalid_request",
                   "error_type": "permission", "error_code": "approval_required"},
        }, context_filter=False)

        assert "无权" in permission or "权限" in permission
        assert "需要确认" in approval or "审批" in approval
        assert "未找到相关信息" not in permission + approval

    def test_sql_permission_message_is_direct_and_does_not_guess_a_role(self):
        sql_answer = generate_final_answer("查询上月销量", {
            "s1": {**_failed_step(capability="sql.query"),
                   "tool_status": "unauthorized",
                   "error_type": "permission_denied"},
        }, context_filter=False)
        rag_answer = generate_final_answer("退款怎么处理", {
            "s1": {**_failed_step(capability="rag.search"),
                   "tool_status": "unauthorized",
                   "error_type": "permission_denied"},
        }, context_filter=False)

        assert "当前账号无权执行该查询或访问相关数据" in sql_answer
        assert "请联系管理员申请相应的数据访问权限" in sql_answer
        assert "管理员权限" not in sql_answer
        assert sql_answer.startswith("## 权限不足")
        assert rag_answer.startswith("## 查询未完成")
        assert "当前账号无权执行或访问该内容" in rag_answer

    def test_sql_permission_message_matches_deterministic_stream_fallback(self):
        answer = render_step_results_deterministically({
            "s1": {**_failed_step(capability="sql.query"),
                   "tool_status": "unauthorized",
                   "error_type": "permission_denied"},
        })

        assert answer.startswith("## 权限不足")
        assert "请联系管理员申请相应的数据访问权限" in answer

    def test_invalid_request_is_not_presented_as_permission_denial(self):
        answer = generate_final_answer("退款怎么处理", {
            "s1": {**_failed_step(capability="rag.search"),
                   "tool_status": "invalid_request",
                   "error_type": "validation_error"},
        }, context_filter=False)

        assert "请求参数有误" in answer
        assert "无权执行或访问" not in answer

    def test_degraded_result_discloses_possible_incompleteness(self):
        answer = generate_final_answer("查天气", {
            "s1": {
                "capability": "map.lookup", "description": "天气",
                "status": "success", "output": "福州当前多云。",
                "tool_status": "degraded", "degraded": True,
                "fallback_used": "circuit_breaker",
            },
        }, context_filter=False)

        assert "福州当前多云" in answer
        assert "降级" in answer
        assert "可能不完整" in answer

    def test_question_truncated_in_degraded_message(self):
        long_q = "很长的问题" * 20
        answer = generate_final_answer(long_q, {"s": _failed_step()},
                                       context_filter=False)
        assert long_q not in answer, "超长问题必须截断"
        assert "## 查询未完成" in answer


class TestUserStepLabel:

    @pytest.mark.parametrize("cap,expected", [
        ("sql.query", "数据库查询"),
        ("rag.search", "知识库检索"),
        ("report", "报告生成"),
        ("business_analysis", "业务分析"),
        ("workflow", "工作流"),
        ("unknown.cap", "信息查询"),
        ("", "信息查询"),
    ])
    def test_capability_label_mapping(self, cap, expected):
        assert _user_step_label({"capability": cap}) == expected


class TestIsStepSuccessful:

    def test_failed_status_rejected(self):
        assert _is_step_successful({"status": "failed", "output": "有内容" * 5}) is False

    def test_is_empty_flag_rejected(self):
        assert _is_step_successful(
            {"status": "success", "is_empty": True, "output": "内容" * 5}) is False

    def test_error_type_rejected(self):
        assert _is_step_successful(
            {"status": "success", "error_type": "timeout", "output": "内容" * 5}) is False

    def test_workflow_always_success(self):
        assert _is_step_successful({"capability": "workflow", "status": "success"}) is True

    def test_short_output_rejected(self):
        # 5 字符及以下视为无实质输出
        assert _is_step_successful({"status": "success", "output": "ok"}) is False
        assert _is_step_successful({"status": "success", "output": "这是有效输出"}) is True


class TestIsTechnicalError:

    @pytest.mark.parametrize("err", [
        "Expected where value to be a dict",
        "chromadb error",
        "psycopg2.OperationalError",
        "connection refused",
        "request timeout",
        "SQLSTATE 42P01",
        "syntax error at position 10",
        "Traceback (most recent call last)",
        "ModuleNotFoundError: No module named 'x'",
    ])
    def test_technical_patterns_detected(self, err):
        # 自由文本不再决定故障类型，调用方必须提供结构化状态。
        assert _is_technical_error(err) is False

    def test_business_error_not_technical(self):
        assert _is_technical_error("查询结果为空") is False
        assert _is_technical_error("未找到匹配记录") is False

    def test_structured_timeout_is_technical(self):
        assert _is_technical_error({"tool_status": "timeout"}) is True


def test_direct_reporter_renders_deterministically_without_llm(monkeypatch):
    class _CountingLLM:
        calls = 0

        def bind(self, **kwargs):
            self.calls += 1
            return self

        def invoke(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("direct 不应调用 Reporter LLM")

        def stream(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("direct 不应流式调用 Reporter LLM")

    fake_llm = _CountingLLM()
    monkeypatch.setattr("backend.agents.reporter.reporter.llm", fake_llm)
    out = reporter_node({
        "route_mode": "direct",
        "executor_mode": "direct",
        "question": "分析库存",
        "final_answer": '{"summary":"raw"}',
        "step_results": {
            "direct_0": {
                "capability": "sql.query", "description": "库存查询",
                "status": "success",
                "output": {"columns": ["商品", "库存"],
                           "rows": [{"商品": "A", "库存": 12}]},
            },
            "direct_1": {
                "capability": "business.analyze", "description": "库存分析",
                "status": "success",
                "output": {"summary": "库存周转偏慢", "risks": ["积压"],
                           "suggestions": ["优先处理"], "confidence": 0.8},
            },
        },
    })

    assert fake_llm.calls == 0
    assert "| 商品 | 库存 |" in out["final_answer"]
    assert "库存周转偏慢" in out["final_answer"]
    assert '{"summary"' not in out["final_answer"]


def test_workflow_reporter_keeps_existing_summary_without_llm(monkeypatch):
    class _CountingLLM:
        calls = 0

        def bind(self, **kwargs):
            self.calls += 1
            return self

        def invoke(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("workflow 不应调用 Reporter LLM")

        def stream(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("workflow 不应流式调用 Reporter LLM")

    fake_llm = _CountingLLM()
    monkeypatch.setattr("backend.agents.reporter.reporter.llm", fake_llm)
    out = reporter_node({
        "route_mode": "workflow",
        "executor_mode": "workflow",
        "question": "生成日报",
        "final_answer": "## 日报\n\n收入汇总完成。",
        "step_results": {
            "workflow_daily_report": {
                "capability": "workflow", "status": "success",
                "output": "## 日报\n\n收入汇总完成。",
            },
        },
    })

    assert fake_llm.calls == 0
    assert "收入汇总完成" in out["final_answer"]


def test_map_lookup_output_is_readable_and_preserves_values():
    answer = generate_final_answer("福州地址", {
        "s1": {
            "capability": "map.lookup", "description": "地址查询",
            "status": "success",
            "output": {"address": "三坊七巷", "location": {"lat": 26.0,
                                                             "lng": 119.3}},
        },
    }, context_filter=False, allow_llm=False)

    assert "三坊七巷" in answer
    assert "26.0" in answer and "119.3" in answer
    assert not answer.lstrip().startswith("{")


def test_reporter_fallback_hides_provider_details(monkeypatch):
    class _BrokenLLM:
        def stream(self, *args, **kwargs):
            raise RuntimeError("provider=secret-model endpoint=internal-host")

        def invoke(self, *args, **kwargs):
            raise RuntimeError("provider=secret-model endpoint=internal-host")

    monkeypatch.setattr("backend.agents.reporter.reporter.llm", _BrokenLLM())
    answer = generate_final_answer("综合情况", {
        "s1": {"capability": "web.search", "status": "success",
               "output": "来源一内容足够长。" * 5},
        "s2": {"capability": "other.search", "status": "success",
               "output": "来源二内容足够长。" * 5},
    }, context_filter=False)

    assert "secret-model" not in answer
    assert "internal-host" not in answer
    assert "provider=" not in answer
    assert "来源一内容" in answer
