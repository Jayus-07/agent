# -*- coding: utf-8 -*-
"""tool_selector（FC 工具选择节点）单测——全部 mock LLM，不依赖外部服务。

覆盖:
  - 门控: fast_path 高置信零 LLM / flag 关闭直通 / 非 direct 模式 / 无候选
  - FC 成功: 选定候选 + 填参 + candidates 重排
  - 降级: 越界选择重试后成功 / 重试仍失败 passthrough / 参数校验失败重试
  - 无 tool_calls（无匹配）→ 保守直通
  - LLM 异常 → passthrough
  - events 层: fc/no_match 发 log 事件，直通不发
"""
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

import backend.orchestration.graph.tool_selector as ts
from backend.orchestration.graph.tool_selector import tool_selector_node


def _tc(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": "call_1"}


class _FakeBound:
    def __init__(self, fake: "_FakeLLM"):
        self._fake = fake

    def invoke(self, input=None, **kwargs):
        # 真实链路经 safe_call_with_timeout 以 input=/max_tokens= 关键字传参
        # （与 llm_router 同模式）
        self._fake.bound_calls += 1
        self._fake.last_messages = input
        resp = self._fake.responses.pop(0) if self._fake.responses else AIMessage(content="")
        if isinstance(resp, Exception):
            raise resp
        return resp


class _FakeLLM:
    """bind_tools 桩：记录绑定的 tools，按序返回 AIMessage / 抛异常。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.bound_calls = 0
        self.bound_tools = None
        self.last_messages = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return _FakeBound(self)


def _state(candidates, question="生成上个月 Amazon US 的销售日报",
           route_mode="direct", **extra):
    st = {
        "question": question,
        "route_mode": route_mode,
        "route_decision": {"execution_mode": "direct", "candidates": candidates,
                           "confidence": 0.7},
    }
    st.update(extra)
    return st


@pytest.fixture(autouse=True)
def _fc_enabled(monkeypatch):
    monkeypatch.setattr(ts, "ENABLE_FC_TOOL_SELECTION", True)


class TestGating:
    def test_fast_path_skips_llm(self):
        """sql.query 高置信（≥0.85）直通：零 LLM 调用，TTFT 不受影响"""
        fake = _FakeLLM()
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "sql.query", "score": 0.9}]))
        assert fake.bound_calls == 0
        assert out["_tool_selection"]["reason"] == "fast_path"
        assert "resolved_params" not in out

    def test_flag_off_passthrough(self, monkeypatch):
        monkeypatch.setattr(ts, "ENABLE_FC_TOOL_SELECTION", False)
        fake = _FakeLLM()
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        assert fake.bound_calls == 0
        assert out["_tool_selection"]["reason"] == "flag_off"

    def test_fast_path_still_triggers_for_grey_zone(self):
        """fast set 能力但置信 0.7（灰区）仍走 FC——校验门控是组合条件"""
        fake = _FakeLLM([AIMessage(content="", tool_calls=[
            _tc("rag__search", {"question": "退款政策"})])])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "rag.search", "score": 0.7}]))
        assert fake.bound_calls == 1
        assert out["resolved_params"] == {"question": "退款政策"}

    def test_not_direct_mode_passthrough(self):
        fake = _FakeLLM()
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "report.generate", "score": 0.7}], route_mode="plan"))
        assert out["_tool_selection"]["reason"] == "not_direct"

    def test_no_candidates_passthrough(self):
        fake = _FakeLLM()
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([]))
        assert out["_tool_selection"]["reason"] == "no_candidates"

    def test_unregistered_candidates_passthrough(self):
        fake = _FakeLLM()
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "ghost.cap", "score": 0.9}]))
        assert out["_tool_selection"]["reason"] == "no_valid_candidates"


class TestFCSelection:
    def test_select_and_fill_params(self):
        """正常路径：模型在候选内选择 + 填 enum 参数，候选重排到首位"""
        fake = _FakeLLM([AIMessage(content="", tool_calls=[
            _tc("report__generate", {"report_type": "daily_sales"})])])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([
                {"name": "report.generate", "score": 0.7},
                {"name": "rag.search", "score": 0.65},
            ]))
        assert fake.bound_calls == 1
        # 只把已注册候选绑给模型（2 个）
        assert len(fake.bound_tools) == 2
        assert out["resolved_params"] == {"report_type": "daily_sales"}
        # candidates[0] 更新为模型选中的能力
        assert out["route_decision"]["candidates"][0]["name"] == "report.generate"
        assert out["route_decision"]["candidates"][1]["name"] == "rag.search"
        assert out["_tool_selection"]["source"] == "fc"
        assert out["_tool_selection"]["attempts"] == 1

    def test_empty_args_falls_back_to_question(self):
        """business.analyze 全 auto 参数 → 模型返回空 args → 回退 question"""
        fake = _FakeLLM([AIMessage(content="", tool_calls=[
            _tc("business__analyze", {})])])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "business.analyze", "score": 0.7}]))
        assert out["resolved_params"] == {"question": "生成上个月 Amazon US 的销售日报"}

    def test_out_of_candidate_retry_then_success(self):
        """越界选择 → 带反馈重试 1 次 → 第二次合法则采纳"""
        fake = _FakeLLM([
            AIMessage(content="", tool_calls=[_tc("email__send", {"to": "x"})]),
            AIMessage(content="", tool_calls=[
                _tc("report__generate", {"report_type": "daily_sales"})]),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([
                {"name": "report.generate", "score": 0.7},
            ]))
        assert fake.bound_calls == 2
        # 重试时反馈已拼进 prompt
        assert "上次尝试失败" in fake.last_messages[1][1]
        assert out["resolved_params"] == {"report_type": "daily_sales"}
        assert out["_tool_selection"]["attempts"] == 2

    def test_invalid_enum_retry_then_give_up(self):
        """参数校验失败重试 1 次仍失败 → passthrough，候选与参数不动"""
        fake = _FakeLLM([
            AIMessage(content="", tool_calls=[
                _tc("report__generate", {"report_type": "bad_type"})]),
            AIMessage(content="", tool_calls=[
                _tc("report__generate", {"report_type": "still_bad"})]),
        ])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        assert fake.bound_calls == 2
        assert out["_tool_selection"]["reason"] == "fc_invalid_after_retry"
        assert "resolved_params" not in out
        # 候选未被改写
        assert out["route_decision"]["candidates"][0]["name"] == "report.generate"

    def test_no_tool_calls_means_no_match(self):
        """模型明确不调工具（无匹配/降级话术）→ 保守直通，不重试"""
        fake = _FakeLLM([AIMessage(content="无匹配工具")])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        assert fake.bound_calls == 1
        assert out["_tool_selection"]["source"] == "no_match"
        assert "resolved_params" not in out

    def test_no_tool_calls_with_multiple_candidates_blocks_execution(self):
        fake = _FakeLLM([AIMessage(content="无匹配工具")])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([
                {"name": "report.generate", "score": 0.7},
                {"name": "web.search", "score": 0.68},
            ]))
        assert out["selection_blocked"] is True
        assert out["_tool_selection"]["reason"] == "model_declined"

    def test_llm_exception_passthrough(self):
        fake = _FakeLLM([RuntimeError("connection refused")])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        assert out["_tool_selection"]["reason"] == "llm_failed"
        assert "resolved_params" not in out

    def test_llm_exception_with_multiple_candidates_requires_clarification(self):
        fake = _FakeLLM([RuntimeError("connection refused")])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([
                {"name": "report.generate", "score": 0.7},
                {"name": "web.search", "score": 0.68},
            ]))
        assert out["selection_blocked"] is True
        assert out["_tool_selection"]["source"] == "clarify"
        assert out["_tool_selection"]["next_action"] == "clarify"

    def test_candidates_truncated_to_max(self):
        """候选超过 MAX_FC_CANDIDATES(3) 截断——防 prompt 膨胀"""
        fake = _FakeLLM([AIMessage(content="", tool_calls=[
            _tc("rag__search", {"question": "q"})])])
        cands = [{"name": c, "score": 0.6} for c in
                 ("report.generate", "rag.search", "web.search", "web.crawl", "data.export")]
        with patch.object(ts, "llm", fake):
            tool_selector_node(_state(cands))
        assert len(fake.bound_tools) == 3


class TestEvents:
    """events 层：FC 介入时发 log 事件，直通不发（避免每条 direct 查询多噪音）"""

    def _build(self, sel):
        from backend.orchestration.graph.events import _build_tool_selector_events
        return list(_build_tool_selector_events({"_tool_selection": sel}))

    def test_fc_emits_info_event(self):
        evts = self._build({"source": "fc", "capability": "report.generate",
                            "candidates": ["report.generate", "rag.search"],
                            "params": {"report_type": "daily_sales"},
                            "attempts": 1, "elapsed_ms": 800})
        assert len(evts) == 1
        assert evts[0]["data"]["level"] == "info"
        assert evts[0]["data"]["node"] == "tool_selector"
        assert "report.generate" in evts[0]["data"]["message"]

    def test_no_match_emits_warn_event(self):
        evts = self._build({"source": "no_match", "candidates": ["report.generate"]})
        assert len(evts) == 1
        assert evts[0]["data"]["level"] == "warn"

    def test_passthrough_emits_nothing(self):
        assert self._build({"source": "passthrough", "reason": "fast_path"}) == []
        assert self._build({}) == []


class TestRollout:
    """灰度放量：白名单 session 优先，其余按 md5 稳定哈希百分比。"""

    def test_zero_percent_all_passthrough(self, monkeypatch):
        monkeypatch.setattr(ts, "FC_TOOL_SELECTION_ROLLOUT_PERCENT", 0)
        fake = _FakeLLM()
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "report.generate", "score": 0.7}], session_id="s1"))
        assert fake.bound_calls == 0
        assert out["_tool_selection"]["reason"] == "rollout_skip"

    def test_whitelist_overrides_zero_percent(self, monkeypatch):
        monkeypatch.setattr(ts, "FC_TOOL_SELECTION_ROLLOUT_PERCENT", 0)
        monkeypatch.setattr(ts, "FC_TOOL_SELECTION_ALLOWLIST", ["vip-session"])
        fake = _FakeLLM([AIMessage(content="", tool_calls=[
            _tc("report__generate", {"report_type": "daily_sales"})])])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state(
                [{"name": "report.generate", "score": 0.7}], session_id="vip-session"))
        assert out["resolved_params"] == {"report_type": "daily_sales"}

    def test_stable_hash_grouping(self, monkeypatch):
        """同一 session_id 多次判定结果稳定（md5 无随机盐）"""
        monkeypatch.setattr(ts, "FC_TOOL_SELECTION_ROLLOUT_PERCENT", 50)
        results = {ts._in_rollout("stable-session") for _ in range(5)}
        assert len(results) == 1

    def test_default_100_percent_passes(self):
        assert ts._in_rollout("any-session") is True


class TestDedicatedModel:
    """TOOL_SELECTOR_MODEL 专用轻量模型：配置时优先，空/失败回退全局。"""

    def test_dedicated_model_used_when_configured(self, monkeypatch):
        monkeypatch.setattr(ts, "TOOL_SELECTOR_MODEL", "deepseek-v4-flash")
        dedicated = _FakeLLM([AIMessage(content="", tool_calls=[
            _tc("report__generate", {"report_type": "daily_sales"})])])
        global_llm = _FakeLLM()
        monkeypatch.setattr(
            ts, "bind_tools_for_model",
            lambda name, tools: dedicated.bind_tools(tools))
        with patch.object(ts, "llm", global_llm):
            out = tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        assert dedicated.bound_calls == 1
        assert global_llm.bound_calls == 0
        assert out["resolved_params"] == {"report_type": "daily_sales"}

    def test_fallback_to_global_when_dedicated_unavailable(self, monkeypatch):
        """专用模型未注册/构建失败（bind_tools_for_model 返回 None）→ 走全局"""
        monkeypatch.setattr(ts, "TOOL_SELECTOR_MODEL", "ghost-model")
        monkeypatch.setattr(ts, "bind_tools_for_model", lambda name, tools: None)
        global_llm = _FakeLLM([AIMessage(content="", tool_calls=[
            _tc("report__generate", {"report_type": "daily_sales"})])])
        with patch.object(ts, "llm", global_llm):
            out = tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        assert global_llm.bound_calls == 1
        assert out["resolved_params"] == {"report_type": "daily_sales"}


class TestTextToolCallFallback:
    """文本兜底：模型把工具调用写成 JSON 文本而非 tool_calls 结构（评测实测）。"""

    def test_json_fence_recovered(self):
        cap_args = ts._parse_text_tool_call(
            '```json\n{"tool": "web__crawl", "parameters": {"url": "https://x.com"}}\n```',
            {"web__crawl": "web.crawl"})
        assert cap_args == ("web.crawl", {"url": "https://x.com"})

    def test_name_arguments_variant(self):
        cap_args = ts._parse_text_tool_call(
            '{"name": "report__generate", "arguments": {"report_type": "daily_sales"}}',
            {"report__generate": "report.generate"})
        assert cap_args == ("report.generate", {"report_type": "daily_sales"})

    def test_unknown_tool_rejected(self):
        assert ts._parse_text_tool_call(
            '{"tool": "email__send", "parameters": {}}',
            {"report__generate": "report.generate"}) is None

    def test_plain_text_rejected(self):
        assert ts._parse_text_tool_call("无匹配工具", {"report__generate": "report.generate"}) is None
        assert ts._parse_text_tool_call("", {}) is None

    def test_end_to_end_recovery(self):
        """no_match 场景 + JSON 文本 → 兜底解析后按 fc 处理"""
        fake = _FakeLLM([AIMessage(
            content='{"tool": "report__generate", "parameters": {"report_type": "daily_sales"}}')])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        assert out["_tool_selection"]["source"] == "fc"
        assert out["resolved_params"] == {"report_type": "daily_sales"}


class TestMetricsRecording:
    def _recorder(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ts, "_record",
                            lambda source, reason="", capability="", t0=None:
                            calls.append((source, reason, capability)))
        return calls

    def test_fc_success_records_capability(self, monkeypatch):
        calls = self._recorder(monkeypatch)
        fake = _FakeLLM([AIMessage(content="", tool_calls=[
            _tc("report__generate", {"report_type": "daily_sales"})])])
        with patch.object(ts, "llm", fake):
            tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        assert ("fc", "ok", "report.generate") in calls

    def test_passthrough_reasons_recorded(self, monkeypatch):
        calls = self._recorder(monkeypatch)
        fake = _FakeLLM()
        with patch.object(ts, "llm", fake):
            tool_selector_node(_state([{"name": "sql.query", "score": 0.9}]))
        assert ("passthrough", "fast_path", "") in calls

    def test_no_match_recorded(self, monkeypatch):
        calls = self._recorder(monkeypatch)
        fake = _FakeLLM([AIMessage(content="无匹配工具")])
        with patch.object(ts, "llm", fake):
            tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        assert ("no_match", "model_declined", "") in calls


class TestTraceObservability:
    """trace 全链路：LLM span + metadata 决策快照（所有路径都写）。"""

    def _fake_trace(self, monkeypatch):
        """替换 trace_collector 为记录桩，返回 (记录列表, 桩)。"""
        import backend.observability.tracer as tracer_mod

        spans = []
        metadata = {}
        trace_inst = type("T", (), {"metadata": metadata})()

        # 真实 API: start_span/end_span 是 trace_collector 的方法，
        # current() 返回的 trace 对象携带 metadata（cs_prefilter 模式）
        class _FakeCollector:
            def current(self):
                return trace_inst

            def start_span(self, span_id, **kw):
                spans.append(("start", span_id, kw))
                return span_id

            def end_span(self, span, **kw):
                spans.append(("end", span, kw))

        # _write_trace_metadata 与 _select_via_fc 内部 import trace_collector，
        # patch 源模块属性（函数内 import 每次执行都会重新取）
        monkeypatch.setattr(tracer_mod, "trace_collector", _FakeCollector())
        return spans, metadata

    def test_metadata_written_on_fc(self, monkeypatch):
        spans, metadata = self._fake_trace(monkeypatch)
        fake = _FakeLLM([AIMessage(content="", tool_calls=[
            _tc("report__generate", {"report_type": "daily_sales"})])])
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        snap = metadata["tool_selection"]
        assert snap["source"] == "fc"
        assert snap["capability"] == "report.generate"
        assert snap["model"]  # 模型名已标注
        # LLM span: 开始 + 结束，挂在 tool_selector 下
        kinds = [s[0] for s in spans]
        assert kinds == ["start", "end"]
        assert spans[0][1] == "tool_selector_llm"
        assert spans[0][2]["parent_id"] == "tool_selector"
        end_kw = spans[1][2]
        assert end_kw["status"] == "success"
        assert end_kw["output"]["capability"] == "report.generate"

    def test_metadata_written_on_passthrough(self, monkeypatch):
        """直通路径（fast_path）也写 metadata——排查需要知道直通原因"""
        spans, metadata = self._fake_trace(monkeypatch)
        fake = _FakeLLM()
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "sql.query", "score": 0.9}]))
        assert metadata["tool_selection"]["source"] == "passthrough"
        assert metadata["tool_selection"]["reason"] == "fast_path"

    def test_llm_failure_span_marked_failed(self, monkeypatch):
        spans, metadata = self._fake_trace(monkeypatch)
        fake = _FakeLLM([RuntimeError("boom")])
        with patch.object(ts, "llm", fake):
            tool_selector_node(_state([{"name": "report.generate", "score": 0.7}]))
        end_kw = spans[1][2]
        assert end_kw["status"] == "failed"

    def test_no_trace_context_is_silent(self, monkeypatch):
        """无 trace 上下文（单测/后台任务）→ 不崩、决策照常返回"""
        import backend.observability.tracer as tracer_mod

        class _NoneCollector:
            def current(self):
                return None

        monkeypatch.setattr(tracer_mod, "trace_collector", _NoneCollector())
        fake = _FakeLLM()
        with patch.object(ts, "llm", fake):
            out = tool_selector_node(_state([{"name": "sql.query", "score": 0.9}]))
        assert out["_tool_selection"]["reason"] == "fast_path"


class TestActiveModelName:
    def test_get_active_model_name_returns_configured(self, monkeypatch):
        from backend.infra.llm.proxy import get_active_model_name
        # 无请求覆盖时返回全局默认（.env/默认值为 MiniMax-M3 或 qwen3.7-plus）
        name = get_active_model_name()
        assert isinstance(name, str) and name
