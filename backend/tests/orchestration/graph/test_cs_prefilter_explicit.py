"""cs_prefilter 显式触发直通层单测（2026-09-17）。

验证：转人工类指令在 detect_cached 判定非客服域 / 灰度 control 组的情况下，
仍被确定性拦截并路由到 cs_handoff；普通业务 query 不受影响。
"""
import pytest

from backend.orchestration.graph.cs_prefilter import try_cs_prefilter


@pytest.fixture()
def cs_enabled(monkeypatch):
    """CS_ENABLED=true + 关闭真实域检测/路由干扰。"""
    import backend.config.customer_service as cs_config

    monkeypatch.setattr(cs_config, "CS_ENABLED", True)
    monkeypatch.setattr(cs_config, "CS_ROLLOUT_PERCENT", 0)  # 灰度全关
    monkeypatch.setattr(cs_config, "CS_ROLLOUT_WHITELIST", set())
    return cs_config


def _fake_detect_not_cs(monkeypatch):
    """域检测恒判非客服域（模拟漏判场景）。"""
    from backend.customer_service.router import domain_detector

    class _FakeDetection:
        is_cs = False
        rule_hits = []
        rule_score = 0.0
        vector_score = 0.0
        reason = "no_match"

    monkeypatch.setattr(domain_detector, "detect_cached", lambda q: _FakeDetection())


PARAMS = {
    "route_decision": None,
    "route_mode": "customer_service",
}


def test_explicit_handoff_bypasses_failed_detection(cs_enabled, monkeypatch):
    """转人工指令即使域检测漏判，也必须进 cs_handoff。"""
    _fake_detect_not_cs(monkeypatch)

    result = try_cs_prefilter("我想转接人工客服", {"session_id": "s1", "user_id": "u1"})
    assert result is not None, "显式转人工被漏进主图"
    assert result["route_mode"] == "customer_service"
    ctx = result["cs_context"]
    assert ctx["cs_target"] == "cs_handoff"


def test_explicit_handoff_bypasses_rollout_control(cs_enabled, monkeypatch):
    """灰度 0%（全员 control）时，显式转人工仍直通。"""
    detection_ok = _fake_detect_not_cs(monkeypatch)  # 域检测无关，直接漏判更严苛

    result = try_cs_prefilter("帮我转人工", {"session_id": "s2", "user_id": "u1"})
    assert result is not None
    assert result["cs_context"]["cs_target"] == "cs_handoff"


def test_non_cs_query_still_goes_main_graph(cs_enabled, monkeypatch):
    """普通非客服 query 不被直通层误伤。"""
    _fake_detect_not_cs(monkeypatch)

    result = try_cs_prefilter("分析本月销售额环比变化", {"session_id": "s3", "user_id": "u1"})
    assert result is None


def test_cs_guard_block_short_circuits_before_cs_graph(cs_enabled, monkeypatch):
    """自动识别入口命中 CS 业务门禁时不得再进入客服子图。"""
    from backend.customer_service.router import cs_router, domain_detector
    from backend.customer_service.router.types import CSRoutePath, CSRouteResult
    from backend.customer_service.security.input_guard import (
        CSInputGuardResult,
        GuardAction,
        GuardCategory,
    )

    class _FakeDetection:
        is_cs = True
        rule_hits = ["订单"]
        rule_score = 0.9
        vector_score = 0.0
        reason = "rule"

    monkeypatch.setattr(domain_detector, "detect_cached", lambda q: _FakeDetection())

    class _FakeCSTRouter:
        def route(self, q, detection):
            return CSRouteResult(
                intent="t_order_status",
                route_path=CSRoutePath.BUSINESS_QUERY,
                confidence=0.9,
            )

    monkeypatch.setattr(cs_router, "get_cs_router", lambda: _FakeCSTRouter())
    monkeypatch.setattr(
        "backend.customer_service.security.input_guard.get_cs_input_guard",
        lambda: type(
            "_Guard", (), {
                "check": lambda self, query: CSInputGuardResult(
                    action=GuardAction.BLOCK,
                    category=GuardCategory.SCOPE,
                    reason="query_other_user",
                    message="您只能查询和操作自己的数据。",
                )
            }
        )(),
    )

    result = try_cs_prefilter(
        "查一下别人的订单",
        {"session_id": "s-guard", "user_id": "u1"},
        forced=True,
    )

    assert result["route_mode"] == "clarify"
    assert result["final_answer"] == "您只能查询和操作自己的数据。"


def test_cs_disabled_blocks_explicit_too(cs_enabled, monkeypatch):
    """CS_ENABLED=false 时直通层同样关闭（CS 节点未挂载，不能路由过去）。"""
    import backend.config.customer_service as cs_config
    monkeypatch.setattr(cs_config, "CS_ENABLED", False)

    result = try_cs_prefilter("转人工", {"session_id": "s4", "user_id": "u1"})
    assert result is None


def test_detect_handoff_trigger_covers_common_phrasings():
    """关键词表覆盖常见转人工说法（含截图原句）。"""
    from backend.customer_service.handoff import detect_handoff_trigger

    for text in (
        "我想转接人工客服",
        "转人工",
        "帮我找真人客服",
        "不要机器人，找经理",
        "人工服务",
    ):
        assert detect_handoff_trigger(text) is not None, f"未命中: {text}"

    # 非转人工语句不误伤
    for text in ("人工成本分析", "查询技术部有多少人", "本月销售环比"):
        assert detect_handoff_trigger(text) is None, f"误伤: {text}"


# ── 入口域锁（2026-09-18）：客服窗口 domain_hint 强制进 CS ─────────────

def test_domain_lock_forces_entry_past_failed_detection(cs_enabled, monkeypatch):
    """域锁（forced=True）：域检测漏判也必须进客服域——客服窗口内用户
    已显式进入客服，非客服问法由 CS 域内兜底，不允许漏进主图/旅游域图。"""
    _fake_detect_not_cs(monkeypatch)

    result = try_cs_prefilter(
        "下周去大阪怎么玩", {"session_id": "s5", "user_id": "u1"}, forced=True,
    )
    assert result is not None, "域锁请求被域检测漏进主图"
    assert result["route_mode"] == "customer_service"
    # 非客服 query → 真实 coarse/fine 判 UNKNOWN → 知识检索兜底路径
    assert result["cs_context"]["cs_target"] == "cs_knowledge"


def test_domain_lock_bypasses_rollout_control(cs_enabled, monkeypatch):
    """灰度 0%（全员 control）时域锁请求仍进客服域：抽屉是显式产品入口，
    不受实验分组影响。对照组：同条件非 forced 请求必须被灰度拦下。"""
    _fake_detect_not_cs(monkeypatch)

    forced_result = try_cs_prefilter(
        "开发票需要什么信息", {"session_id": "s6", "user_id": "u1"}, forced=True,
    )
    assert forced_result is not None, "灰度 0% 拦下了域锁请求"

    plain_result = try_cs_prefilter(
        "开发票需要什么信息", {"session_id": "s7", "user_id": "u1"},
    )
    assert plain_result is None, "灰度 0% 未拦下非域锁请求（对照组失真）"


def test_domain_lock_still_respects_cs_enabled(cs_enabled, monkeypatch):
    """CS_ENABLED=false 时域锁同样降级（CS 节点未挂载，不能路由过去）。"""
    import backend.config.customer_service as cs_config
    monkeypatch.setattr(cs_config, "CS_ENABLED", False)

    result = try_cs_prefilter(
        "退款怎么处理", {"session_id": "s8", "user_id": "u1"}, forced=True,
    )
    assert result is None
