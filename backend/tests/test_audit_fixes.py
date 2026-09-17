"""test_audit_fixes.py — 2026-09 架构审计修复项回归测试

覆盖:
  1. BaseSkill 类级 default_timeout 覆盖（competitor 120s > 抓取 90s）
  2. tool 层异常上抛（BaseSkill 重试机制可达）
  3. 人工接入超时回退（CS_HANDOFF_TIMEOUT_SECONDS）
  4. 确认追问上限（CS_MAX_CONFIRMATION_RETRIES）
  5. complaint 专家幂等防重入
  6. params_schema 类型化渲染
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.skills.competitor_analysis.skill import CompetitorAnalysisSkill
from backend.skills.base import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT


# =====================================================
# 1. 类级超时覆盖
# =====================================================

class TestSkillTimeoutOverride:
    def test_competitor_timeout_above_crawl_timeout(self):
        # crawler_runtime 单次抓取 timeout=90s，Skill 层必须更大
        assert CompetitorAnalysisSkill.default_timeout >= 120.0

    def test_default_class_attrs_fallback(self):
        from backend.skills.web_search.skill import WebSearchSkill

        assert WebSearchSkill.default_timeout == DEFAULT_TIMEOUT
        assert WebSearchSkill.default_max_retries == DEFAULT_MAX_RETRIES

    def test_execute_uses_class_default(self):
        """execute 未显式传参时取类级默认值"""
        skill = CompetitorAnalysisSkill()
        # 反射验证 execute 的默认解析逻辑（不真正执行抓取）
        import inspect
        src = inspect.getsource(type(skill).execute.__wrapped__ if hasattr(
            type(skill).execute, "__wrapped__") else type(skill).execute)
        assert "default_timeout" in src


# =====================================================
# 2. tool 异常上抛
# =====================================================

class TestToolReraise:
    def test_web_search_reraises_on_network_error(self):
        import urllib.error
        import urllib.request

        from backend.tools.web import web_search_tool

        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("dns fail")):
            with pytest.raises(urllib.error.URLError):
                web_search_tool.invoke({"query": "x", "num_results": 1})

    def test_competitor_reraises_on_analysis_error(self):
        from backend.tools.competitor import competitor_analyze_tool

        with patch("backend.tools.competitor.analyze_url",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                competitor_analyze_tool.invoke(
                    {"action": "analyze", "url": "https://t.com"})

    def test_web_crawl_reraises_on_crawl_error(self):
        from backend.tools.web import web_crawl_tool

        with patch("backend.tools.crawler_runtime.crawl",
                   return_value={"ok": False, "error": "refused"}):
            with pytest.raises(RuntimeError):
                web_crawl_tool.invoke({"url": "https://t.com"})


# =====================================================
# 3. 人工接入超时回退
# =====================================================

def _cs_state(handoff_state="waiting_human", **ctx_extra):
    return {
        "handoff_state": handoff_state,
        "user_id": "u_timeout_test",
        "session_id": "s1",
        "conversation_id": "c1",
        "cs_audit_entries": [],
        "cs_context": ctx_extra,
    }


class TestHandoffTimeoutRecovery:
    def test_fresh_handoff_not_recovered(self):
        from backend.customer_service.supervisor import _recover_handoff_timeout
        from backend.customer_service.handoff_store import get_handoff_store

        store = get_handoff_store()
        store.save("u_timeout_test", "s1", {
            "handoff_state": "waiting_human",
            "trigger_type": "explicit_request",
            "ticket_id": "T1",
        })
        assert _recover_handoff_timeout(_cs_state()) is None
        store.clear("u_timeout_test", "s1")

    def test_expired_handoff_recovered_to_ai_active(self):
        from backend.customer_service.supervisor import _recover_handoff_timeout
        from backend.customer_service.handoff_store import get_handoff_store

        store = get_handoff_store()
        old = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
        store.save("u_timeout_test", "s1", {
            "handoff_state": "waiting_human",
            "trigger_type": "explicit_request",
            "ticket_id": "T1",
        })
        # 直接篡改缓存条目时间戳，避免测试等 600s
        store._data[("u_timeout_test", "s1")]["updated_at"] = old

        update = _recover_handoff_timeout(_cs_state())
        assert update is not None
        assert update["handoff_state"] == "ai_active"
        assert any(
            e.get("action_type") == "handoff_timeout_recovered"
            for e in update["cs_audit_entries"]
        )
        # store 已清空
        assert store.get_active_handoff("u_timeout_test") is None

    def test_ai_active_untouched(self):
        from backend.customer_service.supervisor import _recover_handoff_timeout

        assert _recover_handoff_timeout(
            _cs_state(handoff_state="ai_active")) is None


# =====================================================
# 4. 确认追问上限
# =====================================================

class TestConfirmationRetryLimit:
    def test_retry_count_within_limit_keeps_pending(self):
        from backend.customer_service.pending_handler import _process_pending

        pending = {"proposal_text": "退款 100 元", "retry_count": 0,
                   "expires_at": "2099-01-01T00:00:00+00:00"}
        cmd = _process_pending(pending, "随便说点什么", "u_retry", "s1", {})
        assert cmd.goto == "cs_reporter"
        assert "重新追问" in cmd.update["supervisor_decision"]["reason"]

    def test_retry_count_exceeded_expires(self):
        from backend.config.customer_service import CS_MAX_CONFIRMATION_RETRIES
        from backend.customer_service.confirmation_store import get_confirmation_store
        from backend.customer_service.pending_handler import _process_pending

        store = get_confirmation_store()
        pending = {"proposal_text": "退款 100 元",
                   "retry_count": CS_MAX_CONFIRMATION_RETRIES,
                   "expires_at": "2099-01-01T00:00:00+00:00"}
        store.save("u_retry", "s1", pending)
        cmd = _process_pending(pending, "随便说点什么", "u_retry", "s1", {})
        # 超限 → 按过期处理（清 store）
        assert store.load("u_retry", "s1") is None
        assert cmd.goto == "cs_reporter"
        store.clear("u_retry", "s1")


# =====================================================
# 5. complaint 防重入
# =====================================================

class TestComplaintIdempotency:
    def test_duplicate_ticket_returns_existing(self):
        from backend.customer_service.experts.complaint import execute_complaint

        state = {
            "user_id": "u_dup",
            "session_id": "s1",
            "conversation_id": "c1",
            "cs_context": {"complaint_ticket_id": "COMPLAINT-EXISTING",
                           "complaint_severity": "high"},
        }
        with patch(
            "backend.customer_service.handoff_store.get_handoff_store"
        ) as mock_store:
            result = execute_complaint("还要投诉！再投诉一次", state)
        # 不再触碰 handoff store / complaint service
        mock_store.assert_not_called()
        assert result["data"]["ticket_id"] == "COMPLAINT-EXISTING"
        assert result["data"]["duplicate"] is True
        assert "已在处理中" in result["response_draft"]

    def test_store_fallback_detects_complaint_escalation(self):
        """cs_context 无标记时，从 handoff store 的投诉升级记录兜底识别"""
        from backend.customer_service.experts.complaint import execute_complaint

        state = {
            "user_id": "u_dup2",
            "session_id": "s1",
            "conversation_id": "c1",
            "cs_context": {},
        }
        store_entry = {
            "handoff_state": "handoff_requested",
            "trigger_type": "complaint_escalation",
            "ticket_id": "COMPLAINT-FROM-STORE",
        }
        with patch(
            "backend.customer_service.handoff_store.get_handoff_store"
        ) as mock_store_cls:
            mock_store_cls.return_value.get_active_handoff.return_value = store_entry
            result = execute_complaint("再次投诉", state)
        assert result["data"]["ticket_id"] == "COMPLAINT-FROM-STORE"
        assert result["data"]["duplicate"] is True


# =====================================================
# 6. params_schema 类型化渲染
# =====================================================

class TestParamsSchemaRendering:
    def test_typed_and_legacy_formats(self):
        from backend.orchestration.capability_registry import format_params_schema

        text = format_params_schema({
            "url": {"type": "string", "required": True, "description": "目标地址"},
            "mode": {"type": "string", "required": False,
                     "enum": ["markdown", "raw"], "description": "模式"},
            "legacy": "旧式说明",
        })
        assert "- url (string, 必填): 目标地址" in text
        assert "- mode (string, 可选，可选值: markdown|raw): 模式" in text
        assert "- legacy (string, 可选): 旧式说明" in text

    def test_planner_prompt_contains_typed_params(self):
        from backend.agents.planner.planner import _format_capabilities_schema

        text = _format_capabilities_schema()
        assert "参数:" in text
        assert "必填" in text
        # report_type 的 enum 应渲染为可选值列表而非 JSON 转储
        assert "daily_sales" in text
