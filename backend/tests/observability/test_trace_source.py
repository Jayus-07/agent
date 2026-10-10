"""tests/observability/test_trace_source.py — trace 来源三分类（2026-10-08 #12）

口径：旅游域 / 客服 / AI 助手三分类的唯一分类出口是
``observability.trace_source.classify_trace_source``；DTO 直带 source 字段，
列表端点 source 参数与 stats 聚合复用同一函数。
"""
from __future__ import annotations

import pytest

from backend.app.api.routes._trace_dto import stored_dict_to_dto
from backend.observability.trace_source import (
    SOURCE_AI_ASSISTANT,
    SOURCE_CS,
    SOURCE_TRAVEL,
    SOURCE_UNKNOWN,
    classify_trace_source,
)


class TestClassify:
    def test_travel_domain_tag(self):
        assert classify_trace_source("agent", {"runtime_domain": "travel"}) == SOURCE_TRAVEL

    @pytest.mark.parametrize("tags", [
        {"runtime_domain": "customer_service"},
        {"domain": "customer_service"},  # 客服窗口旧标签口径（无 runtime_domain）
        {"runtime_domain": "", "domain": "customer_service"},
    ])
    def test_cs_domain_tag(self, tags):
        assert classify_trace_source("agent", tags) == SOURCE_CS

    def test_main_graph_without_domain_is_ai_assistant(self):
        # 主图问答/知识/数据类流量：runtime_domain=general/knowledge/data/空 都归 AI 助手
        for tags in ({"runtime_domain": "general"}, {"runtime_domain": "data"}, {}):
            assert classify_trace_source("agent", tags) == SOURCE_AI_ASSISTANT

    def test_selection_funnel_not_masquerading_as_ai(self):
        # 选品漏斗是独立域图，不在三分类内——不得冒充 AI 助手
        assert classify_trace_source("agent", {"runtime_domain": "selection_funnel"}) == SOURCE_UNKNOWN

    def test_non_agent_workflows_unclassified(self):
        # 独立 RAG 链 / 索引类 trace 不是「AI 助手对话」
        assert classify_trace_source("rag_agent", {}) == SOURCE_UNKNOWN
        assert classify_trace_source("knowledge_index", {"runtime_domain": "general"}) == SOURCE_UNKNOWN

    def test_tags_as_json_text(self):
        # analytics trace_summary 行的 tags 是 JSON 文本
        assert classify_trace_source("agent", '{"runtime_domain": "travel"}') == SOURCE_TRAVEL
        assert classify_trace_source("agent", "not-json") == SOURCE_AI_ASSISTANT
        assert classify_trace_source("agent", None) == SOURCE_AI_ASSISTANT


    def test_travel_trace_tags_classify_as_travel(self):
        """旅游入口链路必须带 runtime_domain=travel。

        回归：旅游链路绕过主图 Router，没有 prefilter_chain 的
        record_router_decision 写 runtime_* 归因，只写 travel_* 业务标签。
        分类器只认 runtime_domain/domain，于是 workflow_name=="agent"
        的分支把旅游 trace 全判成 AI 助手（线上 28/28 条旅游 trace 全错）。
        """
        travel_tags = {
            "travel_run_id": "travel-9a65d0be11394559bdf2f7c253f86fbb",
            "travel_destination": "杭州",
            "travel_intent": "plan",
            "travel_status": "success",
            "runtime_domain": "travel",
        }
        assert classify_trace_source("agent", travel_tags) == SOURCE_TRAVEL

    def test_travel_without_runtime_domain_is_regression_shape(self):
        """只带 travel_* 但缺 runtime_domain 会被判成 AI 助手。

        固化「写入侧必须补 runtime_domain」这一契约：若哪天旅游入口的
        runtime_domain 写入被删掉，本用例仍通过（记录当前分类器行为），
        但 test_travel_trace_tags_classify_as_travel 会失败——两者一起
        表达「分类器只看 runtime_domain，写入侧负责提供」。
        """
        bare = {"travel_run_id": "travel-x", "travel_destination": "福州"}
        assert classify_trace_source("agent", bare) == SOURCE_AI_ASSISTANT

    def test_cs_beats_generic_when_both_present(self):
        assert classify_trace_source(
            "agent", {"runtime_domain": "customer_service", "domain": "travel"},
        ) == SOURCE_CS


class TestDtoCarriesSource:
    def test_stored_dict_dto_has_source(self):
        dto = stored_dict_to_dto({
            "id": "t1", "workflow_name": "agent",
            "tags": {"runtime_domain": "travel"}, "duration_ms": 100,
        })
        assert dto["source"] == SOURCE_TRAVEL

    def test_stored_dict_dto_unclassified(self):
        dto = stored_dict_to_dto({
            "id": "t2", "workflow_name": "rag_agent", "tags": {},
        })
        assert dto["source"] == SOURCE_UNKNOWN
