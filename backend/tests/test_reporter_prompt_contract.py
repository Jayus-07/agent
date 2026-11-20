"""reporter prompt 契约回归测试（改造方案 R1）。

修复前事实：
- 全 LLM 路径误用 reporter.summary（变量仅 data_summary）渲染
  question/outputs_text → PromptRenderError → 永远落入 _fallback_summary，
  且 reporter.system 的"禁止编造"规则从未到达 LLM；
- 单步骤 sql.query 输出是 dict（SQLResult），裸透传给用户的是 dict repr；
- 一句话总结 invoke 无 max_tokens 约束。

本文件锁定修复后行为：
1. 全 LLM 路径 system 消息来自 reporter.system（含禁编造规则）
2. 单步骤 dict 输出不透传，走 LLM/渲染路径
3. 一句话总结走 reporter.summary 模板并限制 max_tokens
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

# 循环导入规避：同 test_reporter_degraded.py 的说明
import backend.orchestration.graph  # noqa: F401

from backend.agents.reporter import reporter as reporter_mod
from backend.agents.reporter.reporter import (
    _build_data_summary,
    generate_final_answer,
)


def _sql_step():
    return {
        "capability": "sql.query",
        "description": "查询订单量",
        "status": "success",
        "output": {"columns": ["month"], "rows": [{"month": "2026-08"}],
                   "row_count": 1, "tables": ["orders"]},
    }


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeChunk:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """记录 bind/invoke 调用的假 LLM。"""

    def __init__(self):
        self.calls = []
        self.bound_kwargs = {}

    def bind(self, **kwargs):
        self.bound_kwargs = dict(kwargs)
        return self

    def invoke(self, msgs, **kwargs):
        self.calls.append(msgs)
        return _FakeResp("总结")

    def stream(self, msgs, **kwargs):
        self.calls.append(msgs)
        # ENABLE_TOKEN_STREAMING 下走 stream 分支；chunk 需带 content 属性
        # 供 proxy.extract_chunk_text 提取
        return [_FakeChunk("总结")]


@pytest.fixture
def fake_llm(monkeypatch):
    fl = _FakeLLM()
    monkeypatch.setattr(reporter_mod, "llm", fl)
    return fl


class TestFullLLMPathPromptContract:
    def test_system_prompt_contains_no_fabrication_rule(self, fake_llm):
        """多步骤全 LLM 路径：system 必须来自 reporter.system（禁编造规则生效）。"""
        step_results = {
            "s1": {"capability": "rag.search", "description": "检索",
                   "status": "success", "output": "知识内容" * 20},
            "s2": {"capability": "web.search", "description": "搜索",
                   "status": "success", "output": "网络内容" * 20},
        }
        answer = generate_final_answer("对比两类信息", step_results,
                                       context_filter=False)
        assert fake_llm.calls, "全 LLM 路径必须调用 LLM"
        msgs = fake_llm.calls[0]
        assert msgs[0][0] == "system"
        assert "禁止编造" in msgs[0][1], "reporter.system 禁编造规则未到达 LLM"
        assert "不得新增、改写或借用其他来源编号" in msgs[0][1]
        assert "只能基于以下内容" in msgs[1][1]
        assert answer == "总结" or "总结" in answer

    def test_render_error_no_longer_falls_back(self, fake_llm):
        """此前 render_sync('reporter.summary', question=...) 必抛错落降级；
        修复后不应再出现降级文案。"""
        step_results = {
            "s1": {"capability": "web.search", "description": "搜索",
                   "status": "success", "output": "内容" * 30},
        }
        # web.search 输出是 str → 单步骤透传；构造多步触发全 LLM 路径
        step_results["s2"] = {"capability": "report", "description": "报告",
                              "status": "success", "output": "报告内容" * 30}
        answer = generate_final_answer("问题", step_results, context_filter=False)
        assert "LLM 汇总失败" not in answer, "全 LLM 路径不应再落入 _fallback_summary"
        assert fake_llm.calls


class TestSingleStepPassthrough:
    @pytest.mark.parametrize("question", [
        "搜索并总结 pgvector 功能", "查找pgvector官方仓库，用一句话说明用途并附链接",
    ])
    def test_requested_web_summary_uses_existing_reporter_and_keeps_source(self, fake_llm, question):
        steps = {"s1": {"capability": "web.search", "status": "success",
                         "output": "向量检索支持说明 https://github.com/pgvector/pgvector"}}
        answer = generate_final_answer(question, steps,
                                       context_filter=False)
        assert fake_llm.calls
        assert "总结" in answer
        assert "https://github.com/pgvector/pgvector" in answer

    def test_link_lookup_keeps_fast_path(self, fake_llm):
        output = "pgvector 项目 https://github.com/pgvector/pgvector"
        answer = generate_final_answer("pgvector GitHub 项目链接", {
            "s1": {"capability": "web.search", "status": "success", "output": output},
        }, context_filter=False)
        assert not fake_llm.calls
        assert output in answer

    def test_sql_dict_output_not_passthrough(self, fake_llm):
        """sql.query 单步输出是 dict，不得裸透传 dict repr。"""
        answer = generate_final_answer("上月订单量", {"s1": _sql_step()},
                                       context_filter=False)
        assert "columns" not in answer, "dict repr 泄漏给用户"

    def test_str_output_still_passthrough(self):
        """字符串输出（RAG 类）单步骤仍走透传快速路径，不调 LLM。"""
        step_results = {
            "s1": {"capability": "rag.search", "description": "检索",
                   "status": "success", "output": "退货政策如下……" * 10},
        }
        answer = generate_final_answer("退货政策", step_results, context_filter=False)
        assert "退货政策" in answer


class TestOneLinerSummary:
    def test_structured_path_uses_summary_template_and_max_tokens(self, fake_llm):
        """结构化路径：一句话总结走 reporter.summary 模板 + max_tokens=64。"""
        sql = _sql_step()
        insight = {"capability": "business.analyze", "description": "分析",
                   "status": "success",
                   "output": {"summary": "整体平稳", "risks": ["库存"], "confidence": 0.8}}
        step_results = {"s1": sql, "s2": insight}
        answer = generate_final_answer("业务状况", step_results, context_filter=False)
        assert fake_llm.bound_kwargs.get("max_tokens") == 64
        # 模板渲染成功（data_summary 变量对齐，含 row_count 摘要）
        assert fake_llm.calls
        assert "## 总结" in answer or "##" in answer

    def test_caller_contract_survives_legacy_active_summary_prompt(
        self, fake_llm, monkeypatch,
    ):
        """旧版数据库模板忽略新变量时，调用层仍传入问题和数据边界。"""
        class LegacyPromptService:
            def render_sync(self, prompt_key, **kwargs):
                assert prompt_key == "reporter.summary"
                return SimpleNamespace(
                    text=f"根据以下数据作摘要：\n{kwargs['data_summary']}"
                )

        monkeypatch.setattr(
            reporter_mod, "prompt_service", LegacyPromptService(),
        )
        sql = {
            "capability": "sql.query", "description": "商品销售额排名",
            "status": "success",
            "output": {
                "columns": ["商品", "销售额"],
                "rows": [{"商品": "商品 A", "销售额": 100}],
                "row_count": 20,
            },
        }
        insight = {
            "capability": "business.analyze", "description": "分析",
            "status": "success", "output": {"summary": "排名待核实"},
        }

        generate_final_answer(
            "哪个商品销售额最高？", {"s1": sql, "s2": insight},
            context_filter=False,
        )

        prompt = fake_llm.calls[-1]
        assert "哪个商品销售额最高？" in prompt
        assert "商品 A" in prompt
        assert "SQL 摘要强制规则" in prompt
        assert "只根据已核实的数据作答" in prompt
        assert "预览或样例不能证明全量排名" in prompt
        assert "只有明确确认总数为 0 才能说没有匹配记录" in prompt

    def test_sql_one_liner_receives_user_question_and_actual_result_values(self, fake_llm):
        sql = {
            "capability": "sql.query", "description": "2026年11月销售额",
            "status": "success",
            "output": {
                "status": "success",
                "data": {
                    "columns": ["月份", "销售额（元）"],
                    "rows": [{"月份": "2026-11", "销售额（元）": 12345}],
                    "total": 1,
                },
            },
        }
        insight = {"capability": "business.analyze", "description": "分析",
                   "status": "success", "output": {"summary": "结果已核对"}}

        generate_final_answer(
            "2026年11月的销售额是多少？", {"s1": sql, "s2": insight},
            context_filter=False,
        )

        prompt = fake_llm.calls[-1]
        assert "2026年11月的销售额是多少？" in prompt
        assert "2026-11" in prompt
        assert "销售额（元）=12345" in prompt
        assert "本次查询返回 1 行" in prompt
        assert "全部结果，共 1 行" not in prompt
        assert "不编造或推算数值、单位、时间范围和趋势" in prompt
        assert "结论要简短重述关键范围" in prompt
        assert "完整排名结果的首行明确时，点明第一名和对应指标值" in prompt

    def test_sql_one_liner_supports_direct_agent_rows(self, fake_llm):
        sql = {
            "capability": "sql.query", "description": "本月订单金额",
            "status": "success",
            "output": {
                "columns": ["订单号", "金额（元）"],
                "rows": [{"订单号": "A-17", "金额（元）": 560}],
                "row_count": 1,
            },
        }
        insight = {"capability": "business.analyze", "description": "分析",
                   "status": "success", "output": {"summary": "结果已核对"}}

        generate_final_answer("A-17 订单金额是多少？", {"s1": sql, "s2": insight},
                              context_filter=False)

        prompt = fake_llm.calls[-1]
        assert "A-17" in prompt
        assert "金额（元）=560" in prompt

    def test_sql_one_liner_does_not_call_unavailable_detail_an_empty_result(self, fake_llm):
        sql = {
            "capability": "sql.query", "description": "订单查询",
            "status": "success", "output": {"summary": "结果已转存"},
        }
        insight = {"capability": "business.analyze", "description": "分析",
                   "status": "success", "output": {"summary": "结果已核对"}}

        generate_final_answer("查一下订单", {"s1": sql, "s2": insight},
                              context_filter=False)

        prompt = fake_llm.calls[-1]
        assert "当前没有可展示明细，无法确认是否有匹配记录" in prompt
        assert "只有数据明确确认总数为 0 时，才说“没有匹配记录”" in prompt
        assert "不得把空明细当作 0 行" in prompt

    def test_confirmed_zero_rows_remain_explicitly_identified(self, fake_llm):
        sql = {
            "capability": "sql.query", "description": "订单查询",
            "status": "success", "output": {"rows": [], "row_count": 0},
        }
        insight = {"capability": "business.analyze", "description": "分析",
                   "status": "success", "output": {"summary": "结果已核对"}}

        generate_final_answer("查一下订单", {"s1": sql, "s2": insight},
                              context_filter=False)

        prompt = fake_llm.calls[-1]
        assert "共 0 行，无匹配记录" in prompt
        assert "只有数据明确确认总数为 0 时，才说“没有匹配记录”" in prompt

    def test_sql_one_liner_does_not_call_missing_preview_no_matches(self, fake_llm):
        sql = {
            "capability": "sql.query", "description": "订单查询",
            "status": "success", "output": {"rows": [], "total": 12},
        }
        insight = {"capability": "business.analyze", "description": "分析",
                   "status": "success", "output": {"summary": "结果已核对"}}

        generate_final_answer("查一下订单", {"s1": sql, "s2": insight},
                              context_filter=False)

        prompt = fake_llm.calls[-1]
        assert "共 12 行，但当前摘要未取得明细" in prompt
        assert "无匹配记录" not in prompt

    def test_preview_rows_cannot_support_full_result_ranking(self, fake_llm):
        sql = {
            "capability": "sql.query", "description": "商品销售排名",
            "status": "success",
            "output": {
                "columns": ["商品", "销售额"],
                "rows": [
                    {"商品": f"商品{i}", "销售额": 100 - i}
                    for i in range(5)
                ],
                "row_count": 20,
            },
        }
        insight = {
            "capability": "business.analyze", "description": "分析",
            "status": "success", "output": {"summary": "排序需求"},
        }

        generate_final_answer(
            "哪个商品销售额最高？", {"s1": sql, "s2": insight},
            context_filter=False,
        )

        prompt = fake_llm.calls[-1]
        assert "预览，仅列出 3 / 20 行" in prompt
        assert "不能据此判断全量排名或趋势" in prompt
        assert "商品4" not in prompt
        assert "标为“预览”或“样例”的行不代表全部结果" in prompt

    def test_reported_total_smaller_than_rows_is_marked_inconsistent(self):
        summary = _build_data_summary({
            "s1": {
                "capability": "sql.query", "description": "订单查询",
                "status": "success",
                "output": {
                    "columns": ["订单号"],
                    "rows": [{"订单号": "A"}, {"订单号": "B"}],
                    "row_count": 1,
                },
            },
        })

        assert "行数不一致（报告 1 行，返回 2 行）" in summary
        assert "范围未确认" in summary
        assert "全部结果" not in summary

    def test_empty_rows_without_total_are_not_called_no_matches(self):
        summary = _build_data_summary({
            "s1": {
                "capability": "sql.query", "description": "订单查询",
                "status": "success",
                "output": {"columns": ["订单号"], "rows": []},
            },
        })

        assert "无法确认是否有匹配记录" in summary
        assert "无匹配记录" not in summary

    def test_sql_one_liner_does_not_use_truncated_long_decimal(self, fake_llm):
        long_amount_text = "12345678901234567890" * 4 + ".1234"
        amount = Decimal(long_amount_text)
        sql = {
            "capability": "sql.query", "description": "高精度金额",
            "status": "success",
            "output": {
                "columns": ["金额"],
                "rows": [{"金额": amount}],
                "row_count": 1,
            },
        }
        insight = {
            "capability": "business.analyze", "description": "分析",
            "status": "success", "output": {"summary": "结果已核对"},
        }

        generate_final_answer(
            "高精度金额是多少？", {"s1": sql, "s2": insight},
            context_filter=False,
        )

        prompt = fake_llm.calls[-1]
        assert "金额=[值过长，未纳入摘要；请查看结果明细]" in prompt
        assert long_amount_text[:80] not in prompt
        assert "不得猜测、推算或引用被省略的内容" in prompt

    def test_data_summary_budget_drops_whole_facts_and_marks_omission(self):
        value = "1234567890" * 8
        columns = [f"指标{i}" for i in range(6)]
        row = {column: value for column in columns}
        step_results = {
            f"s{i}": {
                "capability": "sql.query",
                "description": f"查询{i}",
                "status": "success",
                "output": {
                    "columns": columns,
                    "rows": [row, row, row],
                    "row_count": 3,
                },
            }
            for i in range(4)
        }

        summary = _build_data_summary(step_results)

        assert len(summary) <= 4000
        assert summary.endswith("后续结果因摘要长度限制未纳入，请查看结果明细")
        assert "查询3：" not in summary

    def test_long_business_summary_is_omitted_as_a_whole(self):
        long_summary = "销售额为 123 元；" * 12
        summary = _build_data_summary({
            "insight": {
                "capability": "business.analyze",
                "status": "success",
                "output": {"summary": long_summary},
            },
        })

        assert "摘要内容过长，未纳入一句话总结；请查看结果明细" in summary
        assert long_summary[:80] not in summary
