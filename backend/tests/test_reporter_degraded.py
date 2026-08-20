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
        assert "## 抱歉" in answer
        assert "未找到相关信息" in answer
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
                               error="ChromaDB connection refused Traceback ..."),
        }
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

    def test_question_truncated_in_degraded_message(self):
        long_q = "很长的问题" * 20
        answer = generate_final_answer(long_q, {"s": _failed_step()},
                                       context_filter=False)
        assert long_q not in answer, "超长问题必须截断"
        assert "「" in answer


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
        assert _is_technical_error(err) is True

    def test_business_error_not_technical(self):
        assert _is_technical_error("查询结果为空") is False
        assert _is_technical_error("未找到匹配记录") is False
