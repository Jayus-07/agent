# -*- coding: utf-8 -*-
"""test_tool_selector_validation.py — Step 2 selector 结果校验灰区测试

覆盖（用户规格）：查询订单 / 统计订单 / 导出订单 / 采集竞品 / 网页抓取 /
数据分析 / RAG 查询 + 越界 / 域冲突 / 文本兜底越界 / 单候选降级 / 无域跳过。
只测校验层（candidate constraints → result validation），不重构 Router。
全部离线（LLM 调用桩）。
"""
import pytest

from backend.orchestration.capability_registry import tool_registry
from backend.orchestration.graph import tool_selector as ts
from backend.orchestration.tool_schema import capability_to_function_name


# 各能力 schema 合规的最小参数（参数校验真实执行，需按 schema 给参）
_DEFAULT_ARGS = {
    "sql.query": {"question": "测试问题"},
    "rag.search": {"question": "测试问题"},
    "data.export": {"question": "测试问题"},
    "web.crawl": {"url": "https://example.com", "mode": "markdown"},
    "data.collect": {
        "source": "competitor_api", "target_table": "competitor_prices",
        "fetcher_type": "http", "dedup_keys": "sku", "idempotency_key": "k1"},
    "business.analyze": {"sql_result": {"rows": []}},
}


class _FakeRaw:
    """FC 返回替身：tool_calls 结构。"""

    def __init__(self, cap: str | None, args: dict | None = None,
                 content: str = ""):
        if cap is not None:
            self.tool_calls = [{
                "name": capability_to_function_name(cap),
                "args": args if args is not None else dict(_DEFAULT_ARGS.get(cap) or {}),
            }]
            self.content = ""
        else:
            self.tool_calls = []
            self.content = content


def _state(query: str, caps: list[tuple[str, float]], domain: str = "data") -> dict:
    return {
        "question": query,
        "session_id": "test-session",
        "domain": domain,
        "route_decision": {
            "candidates": [{"name": c, "score": s} for c, s in caps],
        },
    }


@pytest.fixture
def fc_env(monkeypatch):
    """桩掉 LLM 调用，_fc_decide 决策逻辑真实执行。"""
    holder = {"raw": None}
    monkeypatch.setattr(ts, "bind_tools_for_model", lambda m, t: None)

    class _FakeLLM:
        def bind_tools(self, tools):
            class _Bound:
                def invoke(self, *a, **k):
                    return None
            return _Bound()

    monkeypatch.setattr(ts, "llm", _FakeLLM())
    monkeypatch.setattr(
        ts, "safe_call_with_timeout",
        lambda fn, timeout=0, default_value=None, error_message="", input=None,
        max_tokens=None: holder["raw"])
    return holder


# ── 合法选择（7 类业务场景）──────────────────────────────────

class TestLegalSelections:
    def test_query_order(self, fc_env):
        """查询订单：data 域灰区 FC 选 sql.query → 放行。"""
        fc_env["raw"] = _FakeRaw("sql.query")
        out = ts._fc_decide(
            _state("查一下订单 12345 的物流状态",
                   [("sql.query", 0.70), ("data.collect", 0.62)]), 
            ["sql.query", "data.collect"], 0.0)
        assert out["_tool_selection"]["source"] == "fc"
        assert out["selected_tool"] == "sql.query"

    def test_stats_order(self, fc_env):
        """统计订单：FC 选 sql.query → 放行（校准直通未命中时的灰区兜底）。"""
        fc_env["raw"] = _FakeRaw("sql.query")
        out = ts._fc_decide(
            _state("统计本月订单数",
                   [("sql.query", 0.68), ("data.collect", 0.66)]),
            ["sql.query", "data.collect"], 0.0)
        assert out["_tool_selection"]["capability"] == "sql.query"

    def test_export_order(self, fc_env):
        """导出订单：data.export 合法（data 域）→ 放行。"""
        fc_env["raw"] = _FakeRaw("data.export")
        out = ts._fc_decide(
            _state("导出上个月的订单明细",
                   [("data.export", 0.68), ("sql.query", 0.62)]),
            ["data.export", "sql.query"], 0.0)
        assert out["_tool_selection"]["capability"] == "data.export"

    def test_collect_competitor(self, fc_env):
        """采集竞品：data.collect 放行。"""
        fc_env["raw"] = _FakeRaw("data.collect")
        out = ts._fc_decide(
            _state("采集竞品价格数据",
                   [("data.collect", 0.66), ("web.crawl", 0.61)]),
            ["data.collect", "web.crawl"], 0.0)
        assert out["_tool_selection"]["capability"] == "data.collect"

    def test_web_crawl(self, fc_env):
        """网页抓取：web.crawl 放行。"""
        fc_env["raw"] = _FakeRaw("web.crawl")
        out = ts._fc_decide(
            _state("抓一下这个网页数据",
                   [("web.crawl", 0.70), ("data.collect", 0.62)]),
            ["web.crawl", "data.collect"], 0.0)
        assert out["_tool_selection"]["capability"] == "web.crawl"

    def test_data_analysis(self, fc_env):
        """数据分析：business 域 business.analyze 放行，auto 参数由运行时注入。"""
        fc_env["raw"] = _FakeRaw("business.analyze", args={})
        out = ts._fc_decide(
            _state("分析 SKU001 为什么销量下降",
                   [("business.analyze", 0.72), ("sql.query", 0.60)],
                   domain="business"),
            ["business.analyze", "sql.query"], 0.0)
        assert out["_tool_selection"]["capability"] == "business.analyze"

    def test_rag_query(self, fc_env):
        """RAG 查询：knowledge 域 rag.search 放行。"""
        fc_env["raw"] = _FakeRaw("rag.search")
        out = ts._fc_decide(
            _state("退款审核的时效是怎么规定的",
                   [("rag.search", 0.75), ("web.search", 0.60)],
                   domain="knowledge"),
            ["rag.search", "web.search"], 0.0)
        assert out["_tool_selection"]["capability"] == "rag.search"

    def test_suboptimal_but_legal_not_rejected(self, fc_env):
        """合法但次优（FC 返回域内另一能力）不拦 —— 只拦非法。"""
        fc_env["raw"] = _FakeRaw("data.collect")
        out = ts._fc_decide(
            _state("查一下订单数据",
                   [("sql.query", 0.70), ("data.collect", 0.62)]),
            ["sql.query", "data.collect"], 0.0)
        assert out["_tool_selection"]["source"] == "fc"


# ── 非法选择（直接拒绝 → 现有 fallback）─────────────────────

class TestIllegalSelections:
    def test_out_of_set_multi_clarify(self, fc_env):
        """越界（候选集合外）多候选 → 直接澄清，不重试。"""
        fc_env["raw"] = _FakeRaw("report.generate")
        out = ts._fc_decide(
            _state("查一下订单", [("sql.query", 0.70), ("data.collect", 0.62)]),
            ["sql.query", "data.collect"], 0.0)
        # fn2cap 结构上只含候选集合 → 越界走既有重试耗尽澄清 fallback
        assert out.get("selection_blocked") is True

    def test_out_of_set_single_candidate_blocks_execution(self, fc_env):
        """越界单候选 → 澄清阻断，不因候选数为一而放行。"""
        fc_env["raw"] = _FakeRaw("report.generate")
        out = ts._fc_decide(
            _state("查一下订单", [("sql.query", 0.70)]),
            ["sql.query"], 0.0)
        assert out["_tool_selection"]["source"] == "clarify"
        assert out["selection_blocked"] is True

    def test_domain_conflict_rejected(self, fc_env):
        """域冲突：data 域请求选中 knowledge 域能力 → 拒绝澄清。"""
        fc_env["raw"] = _FakeRaw("rag.search")
        out = ts._fc_decide(
            _state("查一下订单数据",
                   [("sql.query", 0.66), ("rag.search", 0.62)]),
            ["sql.query", "rag.search"], 0.0)
        assert out.get("selection_blocked") is True
        assert "domain_conflict" in out["_tool_selection"]["reason"]

    def test_no_domain_skips_conflict(self, fc_env):
        """state 无域信息（legacy 路径）→ 跳过域校验，只查候选集合。"""
        fc_env["raw"] = _FakeRaw("rag.search")
        st = _state("查一下订单数据",
                    [("sql.query", 0.66), ("rag.search", 0.62)])
        st["domain"] = ""
        out = ts._fc_decide(st, ["sql.query", "rag.search"], 0.0)
        assert out["_tool_selection"]["source"] == "fc"

    def test_text_fallback_out_of_set_rejected(self, fc_env):
        """文本兜底解析出的越界工具同样被校验拒绝。"""
        fc_env["raw"] = _FakeRaw(
            None,
            content='```json {"tool": "email.send", "parameters": {"question": "x"}}```')
        out = ts._fc_decide(
            _state("查一下订单", [("sql.query", 0.70), ("data.collect", 0.62)]),
            ["sql.query", "data.collect"], 0.0)
        # 文本兜底查 fn2cap 未命中 → 视为模型未调工具 → 多候选澄清
        assert out.get("selection_blocked") is True


# ── _validate_selection 直测（边界）──────────────────────────

class TestValidateSelectionUnit:
    def test_not_in_candidates(self):
        assert ts._validate_selection("email.send", ["sql.query"], "data") \
            == "not_in_candidates"

    def test_domain_conflict_reason(self):
        reason = ts._validate_selection("rag.search", ["sql.query", "rag.search"], "data")
        assert reason is not None and reason.startswith("domain_conflict")

    def test_unknown_capability_is_rejected_for_domain(self):
        """未注册能力即使伪造为候选，也不能通过域内校验。"""
        reason = ts._validate_selection("unknown.cap", ["unknown.cap"], "data")
        assert reason is not None and reason.startswith("domain_conflict")

    def test_all_grey_zone_pairs_domain_consistent(self):
        """规格场景的域内候选组合全部通过校验。"""
        for cap, caps, domain in [
            ("sql.query", ["sql.query", "data.collect"], "data"),
            ("data.export", ["data.export", "sql.query"], "data"),
            ("data.collect", ["data.collect", "web.crawl"], "data"),
            ("web.crawl", ["web.crawl", "data.collect"], "data"),
            ("business.analyze", ["business.analyze", "sql.query"], "business"),
            ("rag.search", ["rag.search", "web.search"], "knowledge"),
        ]:
            assert ts._validate_selection(cap, caps, domain) is None, cap

    def test_registered_caps_resolvable(self):
        """规格涉及的能力均在注册表内（防半注册状态）。"""
        for cap in ("sql.query", "data.export", "data.collect",
                    "web.crawl", "business.analyze", "rag.search"):
            assert tool_registry.get_node(cap) is not None, cap
