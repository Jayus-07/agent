"""tests/travel/test_evidence_validator.py — Evidence 与 validator 数据可信面契约（Phase 4 Commit B）

覆盖（v4 §4 冻结口径）：
- evidence_utils：基线定档 / 缺 verified_at 压帽 / enum-datetime 感知序列化 /
  过期判定（无 expire_at 不判 stale）；
- 三组装点：候选池（SEED/LIVE）、天气（status×Freshness→LIVE/CACHE、stale
  降 0.6、expire_at=observed_at+TTL）、知识（RAG/检索时点核实）；
- validator 三 warning（POI_UNVERIFIED/SOURCE_STALE/PREFERENCE_VIOLATION）：
  全 LEVEL_WARNING、零 IO、不翻结论、不误报（旧会话无 evidences 键）；
- state 纪律：evidences 不进 new_travel_graph_input 预置；三节点合并写入
  （无 reducer 键是覆盖语义，后写不得冲掉先写）；
- 补丁缝：fetch_forecast 兼容 wrapper 语义不变。

网络零依赖：provider 一律注入 stub。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from backend.travel.models.brief import TravelBrief

from backend.tests.travel.conftest import (
    make_day,
    make_item,
    make_itinerary,
    make_poi,
)

# ---------- 造数 ----------

JST = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _evidence_dict(**overrides) -> dict:
    """合法 Evidence dict（过期与否由 expire_at 覆盖项决定）。"""
    base = {
        "fact_id": "weather:测试城",
        "value": {"city": "测试城", "days": 3, "served_by": "tencent:lbs"},
        "source": "tencent:lbs",
        "source_type": "live",
        "confidence": 0.95,
        "verified_at": _now_iso(),
        "expire_at": (datetime.now().astimezone()
                      + timedelta(hours=1)).isoformat(),
    }
    base.update(overrides)
    return base


def _state_with_itinerary(**extra) -> dict:
    itinerary = make_itinerary(
        brief=TravelBrief(destination="测试城", days=1,
                          start_date=date(2026, 9, 15)),
        days=[make_day(1, [make_item(poi=make_poi())])],
    )
    state = {"brief": itinerary.brief.model_dump(),
             "itinerary": itinerary.model_dump(), "candidates": []}
    state.update(extra)
    return state


# ---------- evidence_utils ----------

class TestEvidenceUtils:
    def test_make_evidence_defaults_to_source_type_baseline(self):
        from backend.travel.core.contracts import SourceType
        from backend.travel.core.evidence_utils import make_evidence

        now = datetime.now().astimezone()
        ev = make_evidence("f1", value={}, source="s",
                           source_type=SourceType.LIVE, verified_at=now)
        assert ev.confidence == 0.95

    def test_make_evidence_clamps_without_verified_at(self):
        from backend.travel.core.contracts import SourceType
        from backend.travel.core.evidence_utils import make_evidence

        ev = make_evidence("f2", value={}, source="s",
                           source_type=SourceType.LIVE)  # 无 verified_at
        assert ev.confidence == 0.5  # 压到 UNVERIFIED 上限，基线 0.95 不许冒充

    def test_make_evidence_respects_explicit_confidence(self):
        from backend.travel.core.contracts import SourceType
        from backend.travel.core.evidence_utils import make_evidence

        now = datetime.now().astimezone()
        ev = make_evidence("f3", value={}, source="s",
                           source_type=SourceType.CACHE,
                           verified_at=now, confidence=0.6)  # stale 降档
        assert ev.confidence == 0.6

    def test_evidence_to_dict_is_json_safe(self):
        from backend.travel.core.contracts import SourceType
        from backend.travel.core.evidence_utils import (
            evidence_to_dict,
            make_evidence,
        )

        now = datetime.now().astimezone()
        d = evidence_to_dict(make_evidence(
            "f4", value={"k": "v"}, source="s",
            source_type=SourceType.SEED, verified_at=now))
        assert d["source_type"] == "seed"
        assert d["verified_at"] == now.isoformat()
        assert d["expire_at"] is None
        import json

        json.dumps(d, ensure_ascii=False)  # 不抛即可序列化

    def test_is_stale_semantics(self):
        from backend.travel.core.evidence_utils import is_stale

        future = _evidence_dict()  # expire_at 在未来
        past = _evidence_dict(
            expire_at=(datetime.now().astimezone()
                       - timedelta(minutes=1)).isoformat())
        no_expire = _evidence_dict(expire_at=None)
        assert not is_stale(future)
        assert is_stale(past)
        assert not is_stale(no_expire)  # SEED/ESTIMATE 不冒充时效 ≠ 会过期
        assert not is_stale(None)
        assert not is_stale({})


# ---------- 组装点 1：候选池 ----------

class TestCandidateEvidences:
    def test_seed_pois_get_honest_seed_evidence(self):
        from backend.travel.services.poi_service import build_candidate_evidences

        evidences = build_candidate_evidences([make_poi(poi_id="p_seed")])
        ev = evidences["p_seed"]
        assert ev["source_type"] == "seed"
        assert ev["confidence"] == 0.5
        assert ev["expire_at"] is None  # 不冒充时效
        assert ev["source"] == "seed:local"

    def test_tencent_pois_get_live_evidence_with_verified_at(self):
        from backend.travel.services.poi_service import build_candidate_evidences

        poi = make_poi(poi_id="p_tx", source="tencent:lbs")
        poi.verification_status = "unverified"  # 补全点位：坐标权威/事实占位
        poi.observed_at = _now_iso()
        ev = build_candidate_evidences([poi])["p_tx"]
        assert ev["source_type"] == "live"
        assert ev["confidence"] == 0.95
        assert ev["verified_at"] is not None

    def test_tencent_poi_without_observed_at_clamped(self):
        from backend.travel.services.poi_service import build_candidate_evidences

        poi = make_poi(poi_id="p_tx2", source="tencent:lbs")
        poi.observed_at = None
        ev = build_candidate_evidences([poi])["p_tx2"]
        assert ev["confidence"] == 0.5  # 无核实时点不许高置信


# ---------- 组装点 2：天气 ----------

class TestWeatherEvidence:
    def _patch_provider(self, monkeypatch, result):
        from backend.providers.travel import live as L

        class _Stub:
            def forecast_payload(self, city):
                return result

        monkeypatch.setattr(L, "get_weather_provider", lambda: _Stub())

    def test_live_forecast_produces_live_evidence_with_ttl_expiry(
            self, monkeypatch):
        from backend.providers.travel.live.result import success
        from backend.travel.services import weather_service

        self._patch_provider(monkeypatch, success(
            {"kind": "future", "days": [{"date": "2026-09-15"}]},
            provider="tencent:lbs", operation="weather"))
        forecast, reason, evidence = weather_service.fetch_forecast_evidence("测试城")
        assert forecast and reason == ""
        assert evidence["source_type"] == "live"
        assert evidence["confidence"] == 0.95
        assert evidence["source"] == "tencent:lbs"
        expire = datetime.fromisoformat(evidence["expire_at"])
        verified = datetime.fromisoformat(evidence["verified_at"])
        assert (expire - verified).total_seconds() == 600  # TTL(weather)=600s

    def test_cached_and_stale_downgrade_trust(self, monkeypatch):
        from backend.providers.travel.live.result import (
            Freshness,
            success,
        )
        from backend.travel.services import weather_service

        base = success({"kind": "future", "days": []},
                       provider="tencent:lbs", operation="weather")
        self._patch_provider(monkeypatch, base.with_freshness(
            Freshness.CACHED, _now_iso()))
        _, _, ev = weather_service.fetch_forecast_evidence("测试城")
        assert ev["source_type"] == "cache" and ev["confidence"] == 0.85

        self._patch_provider(monkeypatch, base.with_freshness(
            Freshness.STALE, _now_iso()))
        _, _, ev = weather_service.fetch_forecast_evidence("测试城")
        assert ev["source_type"] == "cache" and ev["confidence"] == 0.6

    def test_failures_produce_no_evidence(self, monkeypatch):
        from backend.providers.travel.live.result import (
            ProviderStatus,
            failure,
        )
        from backend.travel.services import weather_service

        self._patch_provider(monkeypatch, failure(
            ProviderStatus.TIMEOUT, provider="tencent:lbs",
            operation="weather"))
        forecast, reason, evidence = weather_service.fetch_forecast_evidence("测试城")
        assert forecast is None and evidence is None
        assert reason == "天气服务响应超时"

    def test_fetch_forecast_wrapper_keeps_two_tuple_seam(self, monkeypatch):
        """兼容 wrapper：签名/返回元数不变（存量消费面与补丁缝语义）。"""
        from backend.travel.services import weather_service

        called = {}

        def fake_triple(city):
            called["n"] = called.get("n", 0) + 1
            return {"days": []}, "", {"fact_id": "x"}

        monkeypatch.setattr(weather_service,
                            "fetch_forecast_evidence", fake_triple)
        assert weather_service.fetch_forecast("杭州") == ({"days": []}, "")
        assert called["n"] == 1


# ---------- 组装点 3：知识 ----------

class TestKnowledgeEvidence:
    def test_chunks_produce_rag_evidence_verified_at_retrieval(self):
        from backend.travel.services.risk_service import build_knowledge_evidence

        evidences = build_knowledge_evidence("福州", ["摘录一", "摘录二"],
                                             "kb:travel")
        assert set(evidences) == {"kb:福州:0", "kb:福州:1"}
        ev = evidences["kb:福州:0"]
        assert ev["source_type"] == "rag"
        assert ev["confidence"] == 0.7
        assert ev["source"] == "rag:kb:travel"
        assert ev["verified_at"] is not None
        assert ev["expire_at"] is None

    def test_empty_chunks_produce_no_evidence(self):
        from backend.travel.services.risk_service import build_knowledge_evidence

        assert build_knowledge_evidence("福州", [], "") == {}


# ---------- validator 三 warning ----------

class TestValidatorTrustWarnings:
    def test_poi_unverified_fires_as_warning(self):
        from backend.travel.validator import check_source_trust

        poi = make_poi(poi_id="p_u", name="补全点", source="tencent:lbs")
        poi.verification_status = "unverified"
        itinerary = make_itinerary(days=[make_day(1, [make_item(poi=poi)])])
        violations = check_source_trust(itinerary, None)
        assert len(violations) == 1
        assert violations[0].code == "POI_UNVERIFIED"
        assert violations[0].level == "warning"

    def test_verified_seed_poi_does_not_fire(self):
        from backend.travel.validator import check_source_trust

        itinerary = make_itinerary(
            days=[make_day(1, [make_item(poi=make_poi())])])  # 默认 verified
        assert check_source_trust(itinerary, None) == []

    def test_source_stale_fires_only_for_expired_evidence(self):
        from backend.travel.validator import check_source_trust

        itinerary = make_itinerary(
            days=[make_day(1, [make_item(poi=make_poi())])])
        past = _evidence_dict(expire_at=(
            datetime.now().astimezone() - timedelta(minutes=1)).isoformat())
        future = _evidence_dict()
        violations = check_source_trust(
            itinerary, {"weather:测试城": past, "kb:x": future})
        assert [v.code for v in violations] == ["SOURCE_STALE"]
        assert violations[0].level == "warning"
        assert violations[0].detail["fact_id"] == "weather:测试城"

    def test_missing_evidences_key_does_not_fire(self):
        """旧 checkpoint 无 evidences 键 = 无证据，SOURCE_STALE 不误报。"""
        from backend.travel.validator import check_source_trust

        itinerary = make_itinerary(
            days=[make_day(1, [make_item(poi=make_poi())])])
        assert check_source_trust(itinerary, None) == []

    def test_preference_violation_fires_as_warning_not_error(self):
        from backend.travel.validator import check_preference

        poi = make_poi(poi_id="p_hill", name="登山步道", tags=["自然"])
        itinerary = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, avoid=["自然"]),
            days=[make_day(1, [make_item(poi=poi)])])
        violations = check_preference(itinerary, itinerary.brief)
        assert len(violations) == 1
        assert violations[0].code == "PREFERENCE_VIOLATION"
        assert violations[0].level == "warning"

    def test_preference_no_conflict_no_fire(self):
        from backend.travel.validator import check_preference

        itinerary = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, avoid=["爬山"]),
            days=[make_day(1, [make_item(poi=make_poi(name="博物馆",
                                                       category="场馆"))])])
        assert check_preference(itinerary, itinerary.brief) == []

    def test_node_appends_warnings_without_flipping_conclusion(self):
        """节点级：三 warning 进同一 violations 列表；无 error → 结论不翻。"""
        from backend.travel.validator import travel_validator_node

        poi = make_poi(poi_id="p_u", name="补全点", source="tencent:lbs")
        poi.verification_status = "unverified"
        itinerary = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1,
                              start_date=date(2026, 9, 15)),
            days=[make_day(1, [make_item(poi=poi)])])
        state = _state_with_itinerary(
            itinerary=itinerary.model_dump(),
            evidences={"weather:测试城": _evidence_dict(
                expire_at=(datetime.now().astimezone()
                           - timedelta(minutes=1)).isoformat())},
        )
        update = travel_validator_node(state)
        validation = update["validation"]
        codes = [v["code"] for v in validation["violations"]]
        levels = {v["code"]: v["level"] for v in validation["violations"]}
        assert "POI_UNVERIFIED" in codes and "SOURCE_STALE" in codes
        assert levels["POI_UNVERIFIED"] == "warning"
        assert levels["SOURCE_STALE"] == "warning"
        assert not [v for v in validation["violations"]
                    if v["level"] == "error"]  # 无 error：结论不翻
        assert update["itinerary"]["status"] != "degraded"


# ---------- state 纪律与节点合并 ----------

class TestStateDiscipline:
    def test_new_input_does_not_preset_evidences(self):
        from backend.travel.graph_state import new_travel_graph_input

        assert "evidences" not in new_travel_graph_input("福州2天行程")

    def test_weather_node_merges_without_clobbering(self, monkeypatch):
        """合并写入：后写节点不得冲掉先写节点的证据（无 reducer 键教训）。"""
        from backend.travel.experts import weather as W

        existing = {"poi_p1": _evidence_dict(fact_id="poi_p1")}
        evidence = {"weather:测试城": _evidence_dict()}
        monkeypatch.setattr(
            W, "fetch_forecast_evidence",
            lambda city: ({"days": []}, "", evidence))
        state = _state_with_itinerary(evidences=dict(existing))
        update = W.weather_expert_node(state)
        merged = update["evidences"]
        assert "poi_p1" in merged and "weather:测试城" in merged

    def test_weather_node_skipped_paths_write_no_evidence(self, monkeypatch):
        from backend.travel.experts import weather as W

        monkeypatch.setattr(
            W, "fetch_forecast_evidence",
            lambda city: (None, "天气服务暂时不可用", None))
        update = W.weather_expert_node(_state_with_itinerary())
        assert "evidences" not in update

    def test_poi_node_writes_seed_evidences(self):
        """种子城市候选池 → evidences 全 seed 档（节点级集成）。"""
        from backend.travel.experts import poi as P
        from backend.travel.graph_state import new_travel_graph_input

        inp = new_travel_graph_input("福州1天行程，1个人")
        state = {"brief": TravelBrief(destination="福州", days=1).model_dump(),
                 "user_message": inp["user_message"], "candidates": []}
        update = P.poi_expert_node(state)
        evidences = update.get("evidences") or {}
        assert evidences, "种子城市应产出候选证据"
        assert all(ev["source_type"] == "seed" for ev in evidences.values())

    def test_risk_node_writes_knowledge_evidence(self, monkeypatch):
        from backend.travel.experts import risk as R

        monkeypatch.setattr(
            R, "retrieve_knowledge",
            lambda dest, prefs: (["摘录：台风季提示"], "kb:travel"))
        update = R.risk_expert_node(_state_with_itinerary())
        evidences = update.get("evidences") or {}
        assert evidences, "检索命中应产出知识证据"
        ev = next(iter(evidences.values()))
        assert ev["source_type"] == "rag" and ev["source"] == "rag:kb:travel"
        assert update.get("knowledge_refs") == ["摘录：台风季提示"]
