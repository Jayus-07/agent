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
import pytest

# 循环导入规避：同 test_reporter_degraded.py 的说明
import backend.orchestration.graph  # noqa: F401

from backend.agents.reporter import reporter as reporter_mod
from backend.agents.reporter.reporter import generate_final_answer


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
