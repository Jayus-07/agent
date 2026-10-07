"""tests/observability/test_skill_health_severity.py — 健康度三色分诊（2026-10-08 #11）

背景：ECS 实测「系统告警·降级事件流全是错误 tool」的真相是健康度卡把三种
性质不同的失败画成同一种红：①数据源未配置（环境问题）②策略拦截（安全
机制正常工作，如 sql.guard SQL_PERMISSION_DENIED）③真故障。契约：
  - ``is_not_configured_text`` 是「未配置」文案的唯一识别出口
  - skill-health 聚合输出 neutral / real_errors / last_error_kind，
    ``error`` 字段保持原语义（总失败数）向后兼容
  - last_error 从 metrics.error / metrics.error_detail 回退（此前恒空）
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

# 导入顺序敏感（与 tests/test_error_taxonomy_unify.py 同口径）：三侧
# error_taxonomy/tracing 存在模块级硬循环，必须按 core.tool_runtime.models
# → infra.llm.error_taxonomy → observability 顺序加载，否则 partially
# initialized ImportError。
from backend.core.tool_runtime.models import ToolStatus  # noqa: F401
from backend.infra.llm.error_taxonomy import MODEL_ERROR_TYPES  # noqa: F401

from backend.app.api.routes import observability as obs
from backend.observability.error_taxonomy import is_not_configured_text


def _span(name, status, metrics=None, error=None, ts="2026-10-08T00:00:00Z"):
    return SimpleNamespace(
        type="tool_call", name=name, status=status,
        duration_ms=5, events=[], metrics=metrics or {}, error=error, ts=ts,
    )


def _row(spans, ts="2026-10-08T00:00:00Z"):
    return SimpleNamespace(spans=spans, timestamp=ts)


class TestNotConfiguredText:
    def test_markers(self):
        assert is_not_configured_text("12306 车票查询未启用")
        assert is_not_configured_text("腾讯位置服务未配置")
        assert is_not_configured_text("ZHIHU_MCP_API_KEY 缺少配置")
        assert is_not_configured_text("Reranker not configured")

    def test_negative(self):
        assert not is_not_configured_text("")
        assert not is_not_configured_text("connection refused")
        # 「拦截」类有专属分级（denied），不该被 not_configured 误吞
        assert not is_not_configured_text("SQL_PERMISSION_DENIED")


class TestSkillHealthTriage:
    """聚合口径：deny/_DENIED → denied；not_configured 标记/文案 → not_configured；
    其余 → failure。neutral 与 real_errors 分开计数，error 语义不变。"""

    def _setup(self, monkeypatch):
        rows = [
            _row([
                # 未配置（校验分支埋 flag + 文案双保险）
                _span("zhihu_search_tool", "error",
                      metrics={"error_detail": "知乎搜索未启用",
                               "not_configured": True, "error": "validation:semantic"}),
                # 策略拦截：sql.guard deny（metrics 自带 decision/reason_code）
                _span("sql.guard", "error",
                      metrics={"decision": "deny",
                               "reason_code": "SQL_PERMISSION_DENIED"}),
                # 真故障：连接被拒
                _span("rag.search", "error",
                      metrics={"error": "connection refused"}),
                # 成功样本
                _span("sql.guard", "success", metrics={}),
            ]),
        ]
        monkeypatch.setattr(
            obs.trace_collector, "list",
            lambda **_kwargs: rows,
        )

    def test_triage_counts(self, monkeypatch):
        self._setup(monkeypatch)
        out = asyncio.run(obs.skill_health(limit=100))
        by_name = {s["name"]: s for s in out["skills"]}

        zhihu = by_name["zhihu_search_tool"]
        assert zhihu["error"] == 1          # 总失败数语义不变
        assert zhihu["neutral"] == 1
        assert zhihu["real_errors"] == 0
        assert zhihu["last_error_kind"] == "not_configured"
        assert "未启用" in zhihu["last_error"]  # 摘要回退到 error_detail

        guard = by_name["sql.guard"]
        assert guard["error"] == 1
        assert guard["success"] == 1
        assert guard["neutral"] == 1
        assert guard["real_errors"] == 0
        assert guard["last_error_kind"] == "denied"

        rag = by_name["rag.search"]
        assert rag["neutral"] == 0
        assert rag["real_errors"] == 1
        assert rag["last_error_kind"] == "failure"
        assert rag["last_error"] == "connection refused"

    def test_latest_error_wins(self, monkeypatch):
        # 「最近失败」取时间最新的失败（旧实现取第一条且恒空）
        rows = [_row([
            _span("x", "error", metrics={"error": "old failure"},
                  ts="2026-10-08T00:00:01Z"),
            _span("x", "error", metrics={"error": "new failure"},
                  ts="2026-10-08T00:00:05Z"),
        ], ts="2026-10-08T00:00:05Z")]
        monkeypatch.setattr(obs.trace_collector, "list", lambda **_k: rows)
        out = asyncio.run(obs.skill_health(limit=100))
        (item,) = out["skills"]
        assert "new failure" in item["last_error"]
