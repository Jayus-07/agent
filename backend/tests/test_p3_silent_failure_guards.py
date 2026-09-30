"""tests/test_p3_silent_failure_guards.py — 「坏消息被就地吞掉」清理（P3-2 / P3-3 / P3-4）

审查报告 P3 里的三类隐患，共同的病根是**失败伪装成正常值**：

  · P3-2 LBS 取字段 `result_of` 对错误响应不设防 —— 腾讯 HTTP 恒为 200、
    错误藏在响应体 ``status`` 里，原实现只做 None 安全，会把错误体当数据段返回；
  · P3-3 读失败伪装成空值 —— 偏好读取失败、上下文失效失败、审计留痕失败
    三处都落成 ``except: pass`` 或「空值 + warning」，调用方无法区分
    「本来就没有」与「读不出来」；
  · P3-4 死代码常量 —— 全仓引用数为 0，删掉以免「看着有用」误导后来者。

每条都钉住「失败必须留下可查的痕迹」这一点，而不是仅断言不抛异常。
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]


# ============================================================
# P3-2 LBS result_of：错误体不得当数据
# ============================================================
class TestLbsResultOf:
    def test_none_payload(self):
        from backend.infra.http.tencent_lbs import result_of

        assert result_of(None) is None

    def test_success_payload_returns_result(self):
        from backend.infra.http.tencent_lbs import result_of

        assert result_of({"status": 0, "result": {"pois": [1]}}) == {"pois": [1]}

    def test_error_payload_returns_none(self, caplog):
        """status≠0 的错误体必须返回 None，并留下告警。"""
        from backend.infra.http.tencent_lbs import result_of

        with caplog.at_level(logging.WARNING):
            got = result_of({"status": 120, "message": "此 key 当日配额已用尽",
                             "result": None})

        assert got is None, "错误响应不得被当作数据段返回"
        assert any("status=120" in r.getMessage() for r in caplog.records), (
            "错误响应必须留下可查的告警"
        )

    def test_payload_without_status_is_passed_through(self):
        """没有 status 字段 → 维持原语义（原样返回，调用方自取所需）。"""
        from backend.infra.http.tencent_lbs import result_of

        payload = {"data": {"pois": [1]}}
        assert result_of(payload) is payload

    def test_zero_status_without_result_is_passed_through(self):
        from backend.infra.http.tencent_lbs import result_of

        payload = {"status": 0, "data": {"pois": [1]}}
        assert result_of(payload) is payload


# ============================================================
# P3-3 读失败 ≠ 本来就没有
# ============================================================
class TestPreferenceLoadFailureIsVisible:
    def test_missing_file_is_not_degraded(self, tmp_path):
        """文件不存在 = 首次使用，属正常，不算降级。"""
        from backend.business_report.preference import PreferenceStore

        store = PreferenceStore(str(tmp_path / "absent.json"))
        assert store.degraded is False
        assert store.load_error is None

    def test_corrupt_file_marks_degraded_and_logs_error(self, tmp_path, caplog):
        """文件在但解析不了 = 异常，必须可被上层识别（不是「无偏好」）。"""
        from backend.business_report.preference import PreferenceStore

        bad = tmp_path / "broken.json"
        bad.write_text("{ not json", encoding="utf-8")

        with caplog.at_level(logging.ERROR):
            store = PreferenceStore(str(bad))

        assert store.degraded is True
        assert store.load_error and "JSONDecodeError" in store.load_error
        assert any(r.levelno >= logging.ERROR for r in caplog.records)

    def test_healthy_file_is_not_degraded(self, tmp_path):
        from backend.business_report.preference import PreferenceStore

        good = tmp_path / "ok.json"
        good.write_text(json.dumps({"u1": {"k": {"template": "t", "chart": "bar"}}}),
                        encoding="utf-8")
        store = PreferenceStore(str(good))
        assert store.degraded is False
        assert store.get("u1", "k") is not None


class TestContextInvalidationFailureIsVisible:
    def test_cache_failure_is_logged_not_swallowed(self, monkeypatch, caplog):
        import backend.customer_service.context.context_resolver as cr

        class _Broken:
            def set_json(self, *a, **kw):
                raise RuntimeError("redis down")

        monkeypatch.setattr(cr, "_cache", lambda: _Broken())

        with caplog.at_level(logging.WARNING):
            cr.clear_recent_context("t1", "u1", "s1")  # 不得抛

        assert any("清除会话上下文失败" in r.getMessage() for r in caplog.records), (
            "上下文失效失败必须留下告警（否则旧上下文静默生效到 TTL）"
        )


class TestNoSilentSwallowInFixedPaths:
    """防回退：修过的三个文件不得再出现「except 后什么都不做」。"""

    @pytest.mark.parametrize(
        "rel",
        ["app/api/deps.py",
         "customer_service/context/context_resolver.py",
         "business_report/preference.py"],
    )
    def test_no_bare_except_pass(self, rel):
        src = (_BACKEND / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                body = [n for n in node.body
                        if not (isinstance(n, ast.Expr)
                                and isinstance(n.value, ast.Constant))]
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    offenders.append(node.lineno)
        assert not offenders, f"{rel} 出现静默吞掉（except: pass）于行 {offenders}"

    def test_audit_path_logs_before_raising_403(self):
        """deps 的 AUTHZ_DENIED 留痕失败必须有日志（源级核对，403 仍照常返回）。"""
        src = (_BACKEND / "app/api/deps.py").read_text(encoding="utf-8")
        assert "记录 AUTHZ_DENIED 安全事件失败" in src


# ============================================================
# P3-4 死代码常量
# ============================================================
class TestDeadConstantsRemoved:
    @pytest.mark.parametrize(
        "rel,name",
        [("config/budget.py", "BUDGET_CURRENCY"),
         ("rag/preprocessing/ast.py", "VALID_NODE_TYPES"),
         ("seed/utils/constants.py", "ORDER_STATUSES"),
         ("seed/utils/constants.py", "VALID_TRANSITIONS"),
         ("seed/utils/constants.py", "MATCH_TYPES")],
    )
    def test_constant_is_gone(self, rel, name):
        """这些常量全仓引用数为 0（含 tests），已删；回归会在这里变红。"""
        tree = ast.parse((_BACKEND / rel).read_text(encoding="utf-8"))
        assigned = set()
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = node.targets
            assigned.update(t.id for t in targets if isinstance(t, ast.Name))
        assert name not in assigned, f"{rel} 里又定义了无人使用的 {name}"

    def test_reserved_topic_contract_constants_stay(self):
        """反例：跨服务契约常量不是死代码，必须保留（否则契约清单缺一段）。"""
        from backend.config import messaging

        assert messaging.TOPIC_HANDOFF_EVENTS == "cs.handoff.events"
        assert messaging.TOPIC_ACTION_EVENTS == "cs.action.events"

    def test_leaf_types_still_present(self):
        """删的是 VALID_NODE_TYPES，别把真正在用的 LEAF_TYPES 一起删了。"""
        from backend.rag.preprocessing.ast import LEAF_TYPES

        assert LEAF_TYPES
