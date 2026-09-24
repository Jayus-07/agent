"""Travel Commerce Runner — Commerce 探针（STOP K7，任务书 §二十四/§二十五）

Golden Cases：Hotel H1-H12 / Flight F1-F12（数据集 datasets/travel-commerce/），
全部离线（fake provider + 场景注入，needs_live=False）。真实供应商链路验收
属 G30/G31——当前无凭据，如实 BLOCKED（K0 §13），探针不冒充。

C1-C12 质量指标由探针结局聚合（_aggregate_c_metrics），随每条 EvalResult
的 actual.c_metrics 输出（报告可见）：
  C1 offer_schema_valid_rate        C2 inventory_fact_supported_rate
  C3 availability_false_positive_rate  C4 price_decimal_correct_rate
  C5 currency_preservation_rate     C6 tax_unknown_preservation_rate
  C7 stale_as_live_rate             C8 unsupported_fact_rate
  C9 deep_link_validation_rate      C10 provider_failure_main_chain_500_rate
  C11 cache_replay_external_call_delta  C12 tenant_scope_violation_rate

缓存/quota 隔离与 tests/travel/commerce/conftest 同纪律：探针禁止写共享
Redis（生产 backend 是 TwoTierCache），cache/quota 换进程内隔离。
"""
from __future__ import annotations

import time
from dataclasses import asdict
from datetime import date, timedelta

from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner

MODULE = "travel-commerce"


# =============================================
# 探针脚手架
# =============================================


def _hotel_req(city="大阪", nights=2, **kw):
    from backend.travel.commerce.request import HotelSearchRequest

    ci = date.today() + timedelta(days=30)
    return HotelSearchRequest(city=city, check_in=ci,
                              check_out=ci + timedelta(days=nights), **kw)


def _flight_req(origin="东京", destination="大阪", **kw):
    from backend.travel.commerce.request import FlightSearchRequest

    return FlightSearchRequest(
        origin=origin, destination=destination,
        departure_date=date.today() + timedelta(days=30), **kw)


def _service():
    from backend.travel.commerce import service as svc

    return svc


def _render(result) -> str:
    from backend.travel.commerce.reporter import render

    return render(result)


def _patch(monkey: dict, name: str, value) -> None:
    monkey[name] = value


class _Ctx:
    """单个探针的执行上下文：service monkeypatch 自动恢复 + 场景注入。"""

    def __init__(self, scenario: dict, kind: str):
        self.scenario = scenario
        self.kind = kind
        self._orig = None

    def __enter__(self):
        import backend.providers.travel.live.fake_commerce as fc

        self._orig_hotels = fc._hotels_for
        self._orig_flights = fc._flights_for
        self._svc = _service()
        if self.kind == "hotel":
            from backend.providers.travel.live.fake_commerce import (
                FakeHotelSearchProvider,
            )

            self.provider = FakeHotelSearchProvider(self.scenario)
            self._orig_resolve = self._svc._resolve_hotel_provider
            self._svc._resolve_hotel_provider = (
                lambda: (self.provider, ""))
        else:
            from backend.providers.travel.live.fake_commerce import (
                FakeFlightSearchProvider,
            )

            self.provider = FakeFlightSearchProvider(self.scenario)
            self._orig_resolve = self._svc._resolve_flight_provider
            self._svc._resolve_flight_provider = (
                lambda: (self.provider, ""))
        return self

    def __exit__(self, *exc):
        if self.kind == "hotel":
            self._svc._resolve_hotel_provider = self._orig_resolve
        else:
            self._svc._resolve_flight_provider = self._orig_resolve
        import backend.providers.travel.live.fake_commerce as fc

        fc._hotels_for = self._orig_hotels
        fc._flights_for = self._orig_flights
        return False

    def run(self, **kw):
        if self.kind == "hotel":
            return self._svc.search_hotels(_hotel_req(**kw))
        return self._svc.search_flights(_flight_req(**kw))


def _loader_counter(kind: str):
    """loader 触达计数器（C11 口径：数 _hotels_for/_flights_for）。"""
    import backend.providers.travel.live.fake_commerce as fc

    counter = {"n": 0}

    if kind == "hotel":
        orig = fc._hotels_for

        def wrap(*a, **kw):
            counter["n"] += 1
            return orig(*a, **kw)

        fc._hotels_for = wrap
    else:
        orig = fc._flights_for

        def wrap(*a, **kw):
            counter["n"] += 1
            return orig(*a, **kw)

        fc._flights_for = wrap
    return counter


def _seed_stale(kind: str):
    from backend.providers.travel.live.fake_commerce import seed_stale_cache

    if kind == "hotel":
        from backend.providers.travel.live.fake_commerce import _hotels_for

        req = _hotel_req()
        recs = _hotels_for(req.city, req.check_in.isoformat(),
                           req.check_out.isoformat(), adults=2, children=0,
                           rooms=1, scenario={"type": "success"})
        key_parts = ["hotel_search", req.city.lower(),
                     req.check_in.isoformat(), req.check_out.isoformat(),
                     2, 0, 1, 0]
    else:
        from backend.providers.travel.live.fake_commerce import _flights_for

        req = _flight_req()
        recs = _flights_for(req.origin, req.destination,
                            req.departure_date.isoformat(),
                            {"type": "success"})
        key_parts = ["flight_search", req.origin, req.destination,
                     req.departure_date.isoformat(), "", 1, 0, ""]
    seed_stale_cache(key_parts[0], key_parts[1:],
                     [asdict(r) for r in recs])
    return req


# =============================================
# Hotel 探针（H1-H12）
# =============================================


def _h1():
    """正常搜索：schema 全合法 + 每条事实带溯源（C1/C2/C4）。"""
    with _Ctx({"type": "success"}, "hotel") as ctx:
        r = ctx.run()
        reasons = []
        if r.status != "success" or not r.offers:
            reasons.append(f"status={r.status} offers={len(r.offers)}")
        for o in r.offers:
            if not o.price_snapshot.observed_at or not o.provider:
                reasons.append("offer 缺溯源")
            dumped = o.price_snapshot.amount.model_dump(mode="json")
            if not isinstance(dumped["amount"], str):
                reasons.append("金额 JSON 非 Decimal 字符串")
        return reasons, {"offer_count": len(r.offers)}


def _h2():
    """空结果：语义 = 无结果，不是失败（C3）。"""
    with _Ctx({"type": "empty"}, "hotel") as ctx:
        r = ctx.run()
        text = _render(r)
        reasons = []
        if r.status != "empty" or r.offers:
            reasons.append(f"status={r.status}")
        if "没有找到" not in text or "暂时无法获得" in text:
            reasons.append("空结果话术混淆失败")
        return reasons, {}


def _h3():
    """timeout：零 offer + 不出现售罄话术（C3/C10）。"""
    return _failure_probe("timeout_fast", forbidden=("已订满", "售罄"))


def _h4():
    """unavailable：如实披露（C10）。"""
    return _failure_probe("unavailable", forbidden=("已订满", "没有找到"))


def _h5():
    """stale fallback：可用但必须标 stale（C7）。"""
    _seed_stale("hotel")
    with _Ctx({"type": "timeout_fast"}, "hotel") as ctx:
        r = ctx.run()
        text = _render(r)
        reasons = []
        if r.status != "success" or r.freshness.value != "stale":
            reasons.append(f"freshness={r.freshness.value}")
        if "非实时" not in text or "过期缓存" not in text:
            reasons.append("stale 未披露")
        if "实时价格：" in text.split("快照")[0]:
            reasons.append("stale 伪装 live")
        return reasons, {"freshness": r.freshness.value}


def _h6():
    """invalid price：INVALID_RESPONSE（G10/C8）。"""
    with _Ctx({"type": "invalid_price"}, "hotel") as ctx:
        r = ctx.run()
        reasons = []
        if r.status != "invalid_response" or r.offers:
            reasons.append(f"status={r.status}")
        return reasons, {}


def _h7():
    """currency 保真：大阪→JPY，无隐式换算（C5）。"""
    with _Ctx({"type": "success"}, "hotel") as ctx:
        r = ctx.run()
        reasons = []
        currencies = {o.price_snapshot.amount.currency for o in r.offers}
        if currencies != {"JPY"}:
            reasons.append(f"currency={currencies}")
        if "≈" in _render(r):
            reasons.append("渲染含换算符号")
        return reasons, {"currencies": sorted(currencies)}


def _h8():
    """tax unknown：未知保真，禁 0 充当（C6）。"""
    with _Ctx({"type": "tax_unknown"}, "hotel") as ctx:
        r = ctx.run()
        text = _render(r)
        reasons = []
        if any(o.price_snapshot.taxes is not None for o in r.offers):
            reasons.append("taxes 非 None")
        if "未知" not in text or "含税 0" in text or "含税0" in text:
            reasons.append("tax unknown 渲染失真")
        return reasons, {}


def _h9():
    """sold out：仅 Provider 明示时成立（C3）。"""
    with _Ctx({"type": "sold_out"}, "hotel") as ctx:
        r = ctx.run()
        text = _render(r)
        reasons = []
        if not all(o.availability.status.value == "sold_out"
                   for o in r.offers):
            reasons.append("availability 非 sold_out")
        if "已订满" not in text:
            reasons.append("sold_out 未如实呈现")
        return reasons, {}


def _h10():
    """invalid deeplink：拒收 → None（C9）。"""
    with _Ctx({"type": "deeplink_invalid"}, "hotel") as ctx:
        r = ctx.run()
        text = _render(r)
        reasons = []
        if any(o.booking_deep_link is not None for o in r.offers):
            reasons.append("非法链接穿透")
        if "javascript:" in text:
            reasons.append("渲染含 scheme 注入")
        return reasons, {}


def _h11():
    """cache replay：loader 触达零增量（C11）。"""
    with _Ctx({"type": "success"}, "hotel") as ctx:
        counter = _loader_counter("hotel")
        try:
            r1 = ctx.run()
            n1 = counter["n"]
            r2 = ctx.run()
            n2 = counter["n"]
        finally:
            pass
        reasons = []
        if n2 != n1:
            reasons.append(f"replay loader {n1}->{n2}")
        if r2.freshness.value != "cached":
            reasons.append(f"replay freshness={r2.freshness.value}")
        return reasons, {"loader_calls": n2}


def _h12():
    """multi-room/occupancy：请求条件完整回显（C1/C2）。"""
    with _Ctx({"type": "success"}, "hotel") as ctx:
        r = ctx.run(rooms=2, adults=3, children=1)
        reasons = []
        for o in r.offers:
            if (o.occupancy.rooms, o.occupancy.adults, o.occupancy.children) != (2, 3, 1):
                reasons.append("occupancy 回显不一致")
            if o.nights != 2:
                reasons.append("nights 与请求不一致")
        return reasons, {}


# =============================================
# Flight 探针（F1-F12）
# =============================================


def _f1():
    with _Ctx({"type": "success"}, "flight") as ctx:
        r = ctx.run()
        reasons = []
        if r.status != "success" or not r.offers:
            reasons.append(f"status={r.status}")
        if any(o.stops != 0 for o in r.offers):
            reasons.append("直飞 stops!=0")
        return reasons, {"offer_count": len(r.offers)}


def _f2():
    with _Ctx({"type": "connection"}, "flight") as ctx:
        r = ctx.run()
        reasons = []
        if any(o.stops != 1 for o in r.offers):
            reasons.append("中转 stops!=1")
        if "中转 1 次" not in _render(r):
            reasons.append("中转未呈现")
        return reasons, {}


def _f3():
    with _Ctx({"type": "empty"}, "flight") as ctx:
        r = ctx.run()
        reasons = []
        if r.status != "empty":
            reasons.append(f"status={r.status}")
        return reasons, {}


def _f4():
    return _failure_probe("timeout_fast", kind="flight",
                          forbidden=("售罄", "已订满"))


def _f5():
    return _failure_probe("unavailable", kind="flight",
                          forbidden=("售罄", "没有找到"))


def _f6():
    _seed_stale("flight")
    with _Ctx({"type": "timeout_fast"}, "flight") as ctx:
        r = ctx.run()
        text = _render(r)
        reasons = []
        if r.status != "success" or r.freshness.value != "stale":
            reasons.append(f"freshness={r.freshness.value}")
        if "非实时" not in text:
            reasons.append("stale 未披露")
        return reasons, {"freshness": r.freshness.value}


def _f7():
    with _Ctx({"type": "invalid_price"}, "flight") as ctx:
        r = ctx.run()
        reasons = []
        if r.status != "invalid_response":
            reasons.append(f"status={r.status}")
        return reasons, {}


def _f8():
    with _Ctx({"type": "success"}, "flight") as ctx:
        r = ctx.run()  # 东京→大阪
        reasons = []
        if {o.price_snapshot.amount.currency for o in r.offers} != {"JPY"}:
            reasons.append("currency 未保真")
        return reasons, {}


def _f9():
    """invalid segment（到达早于出发）：模型门拒绝（G10）。"""
    with _Ctx({"type": "invalid_segment"}, "flight") as ctx:
        r = ctx.run()
        reasons = []
        if r.status != "invalid_response":
            reasons.append(f"status={r.status}")
        return reasons, {}


def _f10():
    with _Ctx({"type": "deeplink_invalid"}, "flight") as ctx:
        r = ctx.run()
        reasons = []
        if any(o.booking_deep_link is not None for o in r.offers):
            reasons.append("非法链接穿透")
        return reasons, {}


def _f11():
    with _Ctx({"type": "success"}, "flight") as ctx:
        counter = _loader_counter("flight")
        r1 = ctx.run()
        n1 = counter["n"]
        r2 = ctx.run()
        n2 = counter["n"]
        reasons = []
        if n2 != n1:
            reasons.append(f"replay loader {n1}->{n2}")
        if r2.freshness.value != "cached":
            reasons.append(f"replay freshness={r2.freshness.value}")
        return reasons, {"loader_calls": n2}


def _f12():
    """round-trip 越契约：fail-closed 拒绝（§八 one-way 必持；提取层永不
    产生 return_date，本探针钉死契约防线）。"""
    with _Ctx({"type": "success"}, "flight") as ctx:
        from backend.travel.commerce.request import FlightSearchRequest

        req = FlightSearchRequest(
            origin="东京", destination="大阪",
            departure_date=date.today() + timedelta(days=30),
            return_date=date.today() + timedelta(days=35))
        r = ctx._svc.search_flights(req)
        reasons = []
        if r.status == "success" and r.offers:
            reasons.append("round-trip 越契约未被拒")
        return reasons, {"status": r.status}


def _failure_probe(scenario, kind="hotel", forbidden=()):
    with _Ctx({"type": scenario}, kind) as ctx:
        r = ctx.run()
        text = _render(r)
        reasons = []
        if r.offers:
            reasons.append("失败结局产生了 offer")
        if any(w in text for w in forbidden):
            reasons.append("失败话术混淆（C3）")
        # C10：主链无异常即 500-rate=0（探针本身没抛异常）
        return reasons, {"status": r.status}


# =============================================
# 专项探针（G19 主链 / G26 租户）
# =============================================


def _c10_main_chain():
    """provider 失败经域图全链：不抛异常、如实披露（500-rate=0，G19）。"""
    from backend.providers.travel.live.fake_commerce import (
        FakeHotelSearchProvider,
    )
    from backend.travel.commerce.graph_builder import get_commerce_graph
    from backend.travel.commerce.graph_state import new_commerce_graph_input

    svc = _service()
    orig = svc._resolve_hotel_provider
    svc._resolve_hotel_provider = (
        lambda: (FakeHotelSearchProvider({"type": "timeout_fast"}), ""))
    try:
        result = get_commerce_graph().invoke(new_commerce_graph_input(
            user_message="帮我找大阪10月3日到5日的酒店"))
    finally:
        svc._resolve_hotel_provider = orig
    reasons = []
    answer = result.get("final_answer", "")
    if not answer:
        reasons.append("域图无产出")
    if "暂时无法获得" not in answer:
        reasons.append("provider 失败未如实披露")
    return reasons, {}


def _c12_tenant_scope():
    """租户隔离：缓存键无身份字段 + 域图 state 不携带跨租户产物（G26）。"""
    from backend.providers.travel.live import cache as pcache

    req = _hotel_req()
    key = pcache.build_key("hotel_search", req.city.lower(),
                           req.check_in.isoformat(),
                           req.check_out.isoformat(), 2, 0, 1, 0)
    reasons = []
    # 缓存键 = 公共 Provider 事实的查询参数，无 tenant/user 身份分量
    for forbidden in ("tenant", "user", "session"):
        if forbidden in key:
            reasons.append(f"缓存键含身份分量 {forbidden}")
    # 域图 state 键集不包含租户私有持久化产物
    from backend.travel.commerce.graph_state import CommerceGraphState

    state_keys = set(CommerceGraphState.__annotations__.keys())
    if any("tenant" in k and k != "tenant_id" for k in state_keys):
        reasons.append("state 含租户产物键")
    return reasons, {}


_PROBES = {
    "h1": _h1, "h2": _h2, "h3": _h3, "h4": _h4, "h5": _h5, "h6": _h6,
    "h7": _h7, "h8": _h8, "h9": _h9, "h10": _h10, "h11": _h11, "h12": _h12,
    "f1": _f1, "f2": _f2, "f3": _f3, "f4": _f4, "f5": _f5, "f6": _f6,
    "f7": _f7, "f8": _f8, "f9": _f9, "f10": _f10, "f11": _f11, "f12": _f12,
    "c10": _c10_main_chain, "c12": _c12_tenant_scope,
}


# =============================================
# C1-C12 聚合
# =============================================


def _aggregate_c_metrics(probe_outcomes: dict) -> dict:
    """探针结局 → C1-C12（质量门：任务书 §二十五阈值）。"""
    def _rate(numerator: int, denominator: int, default: float) -> float:
        return default if denominator == 0 else round(numerator / denominator, 4)

    schema_bad = probe_outcomes.get("schema_violations", 0)
    fact_bad = probe_outcomes.get("fact_violations", 0)
    avail_fp = probe_outcomes.get("availability_false_positive", 0)
    money_bad = probe_outcomes.get("money_violations", 0)
    currency_bad = probe_outcomes.get("currency_violations", 0)
    tax_bad = probe_outcomes.get("tax_violations", 0)
    stale_as_live = probe_outcomes.get("stale_as_live", 0)
    unsupported = probe_outcomes.get("unsupported_fact", 0)
    deeplink_bad = probe_outcomes.get("deeplink_violations", 0)
    chain_500 = probe_outcomes.get("main_chain_500", 0)
    replay_delta = probe_outcomes.get("replay_delta", 0)
    tenant_violation = probe_outcomes.get("tenant_violations", 0)

    return {
        "C1_offer_schema_valid_rate": 1.0 if schema_bad == 0 else 0.0,
        "C2_inventory_fact_supported_rate": 1.0 if fact_bad == 0 else 0.0,
        "C3_availability_false_positive_rate": avail_fp,
        "C4_price_decimal_correct_rate": 1.0 if money_bad == 0 else 0.0,
        "C5_currency_preservation_rate": 1.0 if currency_bad == 0 else 0.0,
        "C6_tax_unknown_preservation_rate": 1.0 if tax_bad == 0 else 0.0,
        "C7_stale_as_live_rate": stale_as_live,
        "C8_unsupported_fact_rate": unsupported,
        "C9_deep_link_validation_rate": 1.0 if deeplink_bad == 0 else 0.0,
        "C10_provider_failure_main_chain_500_rate": chain_500,
        "C11_cache_replay_external_call_delta": replay_delta,
        "C12_tenant_scope_violation_rate": tenant_violation,
    }


_VIOLATION_PROBES = {
    # 探针 → 违规计数键（探针失败即计 1；零违规 = 质量门全绿）
    "h1": "schema_violations", "h12": "schema_violations",
    "f1": "schema_violations", "f2": "schema_violations",
    "h2": "availability_false_positive",
    "h3": "availability_false_positive", "h4": "availability_false_positive",
    "h9": "availability_false_positive",
    "f3": "availability_false_positive", "f4": "availability_false_positive",
    "f5": "availability_false_positive",
    "h6": "unsupported_fact", "f7": "unsupported_fact", "f9": "unsupported_fact",
    "h7": "currency_violations", "f8": "currency_violations",
    "h8": "tax_violations",
    "h5": "stale_as_live", "f6": "stale_as_live",
    "h10": "deeplink_violations", "f10": "deeplink_violations",
    "h11": "replay_delta", "f11": "replay_delta",
    "c10": "main_chain_500", "c12": "tenant_violations",
}


def _run_travel_commerce(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    # 缓存/quota 隔离（与 tests/travel/commerce/conftest 同纪律）
    from backend.infra.cache.backend import InMemoryCache
    from backend.providers.travel.live import cache as pcache, quota
    from backend.config import travel_commerce as cfg

    orig_backend = pcache._backend
    orig_budget = quota.daily_budget
    store = InMemoryCache(default_ttl=3600)
    pcache._backend = lambda: store
    quota.daily_budget = lambda provider: 0
    # fake 模式 + 白名单（探针基座；config 属性 monkeypatch 同测试口径）
    orig_enabled = cfg.TRAVEL_COMMERCE_ENABLED
    orig_mode = cfg.TRAVEL_COMMERCE_PROVIDER_MODE
    orig_hosts = cfg.TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS
    cfg.TRAVEL_COMMERCE_ENABLED = True
    cfg.TRAVEL_COMMERCE_PROVIDER_MODE = "fake"
    cfg.TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS = ("fake-commerce.example.com",)
    try:
        return _run_all_cases(cases)
    finally:
        pcache._backend = orig_backend
        quota.daily_budget = orig_budget
        cfg.TRAVEL_COMMERCE_ENABLED = orig_enabled
        cfg.TRAVEL_COMMERCE_PROVIDER_MODE = orig_mode
        cfg.TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS = orig_hosts


def _run_all_cases(cases: list[TestCase]) -> list[EvalResult]:
    from backend.infra.cache.backend import InMemoryCache
    from backend.providers.travel.live import cache as pcache
    from backend.providers.travel.live import quota

    results: list[EvalResult] = []
    probe_outcomes: dict[str, int] = {}
    orig_backend = pcache._backend

    for case in cases:
        t0 = time.time()
        probe = (case.metadata or {}).get("probe", "")
        fn = _PROBES.get(probe)
        if fn is None:
            results.append(EvalResult(
                case_id=case.id, module=MODULE, status="error",
                expected=case.expected, actual={},
                error_msg=f"未知探针: {probe}"))
            continue
        # **每探针独立 fresh 缓存**：探针共用键空间（同 city/dates），
        # 共享 store 会互相污染（H1 的成功缓存被 H2 空场景命中——实测）
        store = InMemoryCache(default_ttl=3600)
        pcache._backend = lambda store=store: store
        quota.reset_local_counters()
        try:
            reasons, extra = fn()
            status = "pass" if not reasons else "fail"
            if reasons and probe in _VIOLATION_PROBES:
                key = _VIOLATION_PROBES[probe]
                probe_outcomes[key] = probe_outcomes.get(key, 0) + 1
        except Exception as e:  # noqa: BLE001 — 单 case 失败不拖垮整批
            reasons = [f"[runner] 异常: {type(e).__name__}: {e}"]
            status = "error"
            extra = {}
            if probe in _VIOLATION_PROBES:
                key = _VIOLATION_PROBES[probe]
                probe_outcomes[key] = probe_outcomes.get(key, 0) + 1
        finally:
            pcache._backend = orig_backend
        results.append(EvalResult(
            case_id=case.id, module=MODULE, status=status,
            expected=case.expected,
            actual={"probe": probe, **extra},
            error_msg="; ".join(reasons) or None,
            duration_ms=int((time.time() - t0) * 1000)))

    # C1-C12 聚合（附到每条结果，报告可见；全绿 = 任务书 §二十五阈值）
    c_metrics = _aggregate_c_metrics(probe_outcomes)
    for r in results:
        r.actual = {**(r.actual or {}), "c_metrics": c_metrics}
    try:
        from backend.travel.commerce import telemetry

        telemetry.event("travel.commerce.eval_c_metrics", **c_metrics)
    except Exception:  # noqa: BLE001
        pass
    return results


register_runner(MODULE, _run_travel_commerce, needs_live=False)
